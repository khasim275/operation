from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from psycopg import Connection

from bot import BotResult, process_message
from config import get_settings
from db import get_db, transaction
from messaging import ProviderError, normalize_phone, send_otp, verify_meta_signature
from patient_cards import _render_card_with_connection
from models import SimulatorMessage, SimulatorReset
from security import UserInfo, require_roles
from workers import enqueue_message

logger = logging.getLogger(__name__)
router = APIRouter()
Db = Annotated[Connection, Depends(get_db)]
SimulatorStaff = Annotated[UserInfo, Depends(require_roles("admin", "telecaller", "receptionist"))]


def _record_message(connection: Connection, phone: str, direction: str, text: str, provider_id: str | None = None, status_value: str = "received") -> None:
    patient = connection.execute(
        """
        SELECT patient_id FROM conversations WHERE phone_e164 = %s
        """,
        (phone,),
    ).fetchone()
    patient_id = patient[0] if patient else None
    if not patient_id:
        patient = connection.execute(
            """
            SELECT CASE WHEN count(DISTINCT patient_id) = 1 THEN min(patient_id::text)::uuid END
            FROM patient_phones WHERE phone_e164 = %s
            """,
            (phone,),
        ).fetchone()
        patient_id = patient[0] if patient else None
    connection.execute(
        """
        INSERT INTO communication_log
            (patient_id, phone_e164, channel, direction, provider_message_id, message_text, delivery_status)
        VALUES (%s, %s, 'whatsapp', %s, %s, %s, %s)
        """,
        (patient_id, phone, direction, provider_id, text[:4000], status_value),
    )


def _queue_replies(connection: Connection, phone: str, replies: list[str]) -> None:
    patient = connection.execute(
        "SELECT patient_id FROM conversations WHERE phone_e164 = %s",
        (phone,),
    ).fetchone()
    patient_id = patient[0] if patient else None
    window_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    for reply in replies:
        enqueue_message(
            connection,
            phone,
            "freeform",
            {"text": reply},
            patient_id,
            window_expires_at=window_expires_at,
        )


def _deliver_bot_card(connection: Connection, result: BotResult) -> bool:
    if not result.patient_card_id or not result.patient_id or not result.card_phone:
        return False
    from messaging import send_whatsapp_card_template

    _render_card_with_connection(connection, result.patient_card_id, result.patient_id)
    card = connection.execute(
        "SELECT png_key, status FROM patient_cards WHERE id = %s",
        (result.patient_card_id,),
    ).fetchone()
    if not card or card[1] != "active" or not card[0]:
        logger.error("A consented WhatsApp patient card is not ready to send.")
        return False
    image_path = get_settings().storage_dir / card[0]
    try:
        provider_id = send_whatsapp_card_template(result.card_phone, image_path)
    except ProviderError:
        logger.warning("WhatsApp patient-card delivery failed; provider details were omitted.")
        return False
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO communication_log
                (patient_id, phone_e164, channel, direction, provider_message_id, message_text, delivery_status)
            VALUES (%s, %s, 'whatsapp', 'outbound', %s, '[patient card template]', 'accepted')
            """,
            (result.patient_id, result.card_phone, None if provider_id == "simulator" else provider_id),
        )
    return True


def _waiting_for_otp(connection: Connection, phone: str) -> bool:
    row = connection.execute(
        "SELECT state = 'awaiting_otp' FROM conversations WHERE phone_e164 = %s",
        (phone,),
    ).fetchone()
    return bool(row and row[0])


def _persist_otp_delivery(connection: Connection, result: BotResult) -> str | None:
    if not result.otp_challenge_id or not result.otp_phone or not result.otp_code:
        return None
    try:
        delivery = send_otp(result.otp_phone, result.otp_code)
    except ProviderError as exc:
        with transaction(connection):
            connection.execute(
                """
                INSERT INTO otp_events (challenge_id, phone_e164, event_type, details)
                VALUES (%s, %s, 'delivery_failed', %s::jsonb)
                """,
                (result.otp_challenge_id, result.otp_phone, json.dumps({"error": str(exc)[:200]})),
            )
        return "We could not deliver a code right now. Please try again later or call the clinic."
    event_type = {
        "whatsapp": "sent_whatsapp",
        "simulator": "sent_simulator",
    }.get(delivery.provider, "sent_sms")
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO otp_events (challenge_id, phone_e164, event_type, details)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (result.otp_challenge_id, result.otp_phone, event_type, json.dumps({"provider": delivery.provider})),
        )
    return None


@router.post("/api/simulator/messages")
def simulator_message(payload: SimulatorMessage, connection: Db, user: SimulatorStaff):
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(404, "The bot simulator is disabled.")
    try:
        phone = normalize_phone(payload.phone)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with transaction(connection):
        redacted = _waiting_for_otp(connection, phone)
        _record_message(
            connection,
            phone,
            "inbound",
            "[OTP redacted]" if redacted else payload.message,
        )
        result = process_message(connection, phone, payload.message)
        for reply in result.replies:
            _record_message(connection, phone, "outbound", reply, status_value="accepted")
        if result.otp_challenge_id and result.otp_phone:
            connection.execute(
                """
                INSERT INTO otp_events (challenge_id, phone_e164, event_type, details)
                VALUES (%s, %s, 'sent_simulator', '{"provider":"simulator"}'::jsonb)
                """,
                (result.otp_challenge_id, result.otp_phone),
            )
    card_sent = _deliver_bot_card(connection, result)
    if card_sent:
        result.replies.append("Your patient card image has been sent.")
    return {
        "phone": phone,
        "replies": result.replies,
        "debug_code": result.debug_code,
        "card_url": f"/api/cards/{result.patient_op_id}.png" if card_sent and result.patient_op_id else None,
    }


@router.post("/api/simulator/reset")
def simulator_reset(payload: SimulatorReset, connection: Db, user: SimulatorStaff):
    if not get_settings().demo_mode:
        raise HTTPException(404, "The bot simulator is disabled.")
    try:
        phone = normalize_phone(payload.phone)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    card_result = None
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO conversations (phone_e164, state, state_data)
            VALUES (%s, 'welcome', '{}'::jsonb)
            ON CONFLICT (phone_e164) DO UPDATE SET state = 'welcome', state_data = '{}'::jsonb, updated_at = now()
            """,
            (phone,),
        )
        connection.execute(
            "UPDATE otp_challenges SET status = 'cancelled' WHERE phone_e164 = %s AND status = 'pending'",
            (phone,),
        )
    return {"ok": True, "phone": phone}


@router.get("/webhook/whatsapp")
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    settings = get_settings()
    if mode == "subscribe" and verify_token and challenge and verify_token == settings.meta_verify_token.get_secret_value():
        return int(challenge)
    raise HTTPException(403, "Webhook verification failed.")


@router.post("/webhook/whatsapp")
async def receive_webhook(
    request: Request,
    connection: Db,
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
):
    raw_body = await request.body()
    if not verify_meta_signature(raw_body, signature):
        raise HTTPException(401, "Invalid webhook signature.")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON payload.") from exc

    messages = []
    statuses = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages.extend(value.get("messages", []))
            statuses.extend(value.get("statuses", []))

    for item in statuses:
        provider_id = item.get("id")
        delivery_status = item.get("status")
        if not provider_id or delivery_status not in {"sent", "delivered", "read", "failed"}:
            continue
        connection.execute(
            """
            UPDATE communication_log SET delivery_status = %s
            WHERE provider_message_id = %s AND occurred_at > now() - interval '30 days'
            """,
            (delivery_status, provider_id),
        )
        normalized_delivery = "DELIVERED" if delivery_status in {"delivered", "read"} else "FAILED" if delivery_status == "failed" else "SENT"
        with transaction(connection):
            deliveries = connection.execute(
                """
                UPDATE notification_delivery_log SET provider_status = %s, timestamp_ist = now()
                WHERE provider_reference = %s AND channel = 'WHATSAPP'
                RETURNING appointment_card_id
                """,
                (normalized_delivery, provider_id),
            ).fetchall()
            for (card_id,) in deliveries:
                if card_id:
                    connection.execute(
                        "UPDATE appointment_cards SET delivery_status=%s WHERE id=%s AND delivery_status <> 'FAILED'",
                        (normalized_delivery, card_id),
                    )

    for message in messages:
        provider_id = message.get("id")
        sender = message.get("from")
        if not provider_id or not sender:
            continue
        try:
            phone = normalize_phone("+" + str(sender))
        except ValueError:
            logger.warning("Ignoring WhatsApp message with invalid sender number.")
            continue
        text = message.get("text", {}).get("body")
        if not text and message.get("interactive"):
            interactive = message["interactive"]
            text = (
                interactive.get("button_reply", {}).get("id")
                or interactive.get("list_reply", {}).get("id")
                or interactive.get("button_reply", {}).get("title")
                or interactive.get("list_reply", {}).get("title")
            )
        if not text:
            text = "Please send a text message so we can help."

        card_result = None
        with transaction(connection):
            redacted = _waiting_for_otp(connection, phone)
            if redacted:
                stored_message = {
                    "id": provider_id,
                    "from": sender,
                    "type": "text",
                    "text": {"body": "[OTP redacted]"},
                }
            else:
                stored_message = dict(message)
            inserted = connection.execute(
                """
                INSERT INTO webhook_inbox (provider_message_id, payload)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (provider_message_id) DO NOTHING
                RETURNING provider_message_id
                """,
                (provider_id, json.dumps(stored_message)),
            ).fetchone()
            if not inserted:
                continue
            _record_message(
                connection,
                phone,
                "inbound",
                "[OTP redacted]" if redacted else str(text),
                provider_id,
                "received",
            )
            result = process_message(connection, phone, str(text))
            card_result = result
            delivery_error = _persist_otp_delivery(connection, result)
            replies = result.replies
            if delivery_error:
                replies = [delivery_error]
            _queue_replies(connection, phone, replies)
            connection.execute(
                "UPDATE webhook_inbox SET processed_at = now() WHERE provider_message_id = %s",
                (provider_id,),
            )
        if card_result:
            _deliver_bot_card(connection, card_result)
    return {"status": "ok"}
