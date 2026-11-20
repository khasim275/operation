from __future__ import annotations

import hashlib
import hmac
import asyncio
import json
import secrets
from datetime import date, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from psycopg import Connection

from config import get_settings
from db import get_db, get_pool
from messaging import normalize_phone, verify_twilio_signature, generate_otp, send_otp, hash_otp, verify_otp_hash, ProviderError
from security import UserInfo, require_roles

router = APIRouter(prefix="/api/public", tags=["public appointments"])
Db = Annotated[Connection, Depends(get_db)]
ClinicAdmin = Annotated[UserInfo, Depends(require_roles("admin"))]
_booking_capacity = asyncio.Semaphore(8)


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(pattern=r"^[6-9][0-9]{9}$")
    email: EmailStr
    appointment_date: date
    time_slot: str = Field(pattern=r"^(09|1[0-9]):00$")
    problem_summary: str = Field(default="Online appointment booking", min_length=2, max_length=500)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Enter your full name.")
        return value


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hospital_name: str = Field(min_length=2, max_length=120)
    card_finish: Literal["porcelain", "midnight", "sage"]

    @field_validator("hospital_name", mode="before")
    @classmethod
    def clean_hospital_name(cls, value: str) -> str:
        return " ".join(str(value).split())


class PatientHistoryOtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mobile_10_digit: str = Field(pattern=r"^[6-9][0-9]{9}$")
    otp_reason: Literal["Patient Visit History Verification"]


class PatientHistoryOtpVerify(PatientHistoryOtpRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")


class BookingOtpRequest(BookingInput):
    channel: Literal["SMS", "EMAIL", "WHATSAPP"] = "WHATSAPP"
    otp_reason: Literal["Patient Booking & OP Confirmation"]


class BookingOtpVerify(BookingOtpRequest):
    code: str = Field(pattern=r"^[0-9]{6}$")


def validate_appointment_time(appointment_date: date, time_slot: str, now: datetime | None = None) -> None:
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(get_settings().clinic_timezone)
    clinic_now = now.astimezone(zone) if now else datetime.now(zone)
    requested = datetime.combine(appointment_date, time.fromisoformat(time_slot), tzinfo=zone)
    if appointment_date < clinic_now.date() or requested <= clinic_now:
        raise HTTPException(422, "Choose a future appointment time in the clinic timezone.")


def _patient_history_token(patient_id: str, expiry: int) -> str:
    settings = get_settings()
    body = f"{patient_id}.{expiry}"
    signature = hmac.new(settings.app_secret_key.get_secret_value().encode(), body.encode(), hashlib.sha256).hexdigest()
    return body + "." + signature


def _verified_patient(token: str | None) -> str:
    if not token:
        raise HTTPException(401, "Verify your mobile number to view patient history.")
    try:
        patient_id, expiry, signature = token.split(".", 2)
        body = f"{patient_id}.{expiry}"
        expected = hmac.new(get_settings().app_secret_key.get_secret_value().encode(), body.encode(), hashlib.sha256).hexdigest()
        UUID(patient_id)
        if int(expiry) < int(datetime.now().timestamp()) or not hmac.compare_digest(signature, expected):
            raise ValueError
        return patient_id
    except (ValueError, TypeError):
        raise HTTPException(401, "History access expired. Verify your mobile number again.")


def _booking_payload_hash(payload: BookingInput) -> str:
    normalized = {
        "name": payload.name,
        "phone": "+91" + payload.phone,
        "email": str(payload.email).strip().lower(),
        "appointment_date": payload.appointment_date.isoformat(),
        "time_slot": payload.time_slot,
        "problem_summary": payload.problem_summary.strip(),
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


@router.post("/appointments/otp/request")
def request_booking_otp(payload: BookingOtpRequest, connection: Db):
    validate_appointment_time(payload.appointment_date, payload.time_slot)
    phone_e164 = "+91" + payload.phone
    request_hash = _booking_payload_hash(payload)
    settings = get_settings()
    code = generate_otp()
    with connection.transaction():
        recent = connection.execute(
            "SELECT count(*) FROM public_booking_otp_challenges WHERE phone_e164=%s AND created_at>now()-interval '15 minutes'",
            (phone_e164,),
        ).fetchone()[0]
        if recent >= 3:
            raise HTTPException(429, "Too many verification codes. Try again in 15 minutes.")
        connection.execute(
            "UPDATE public_booking_otp_challenges SET code_consumed_at=now() WHERE phone_e164=%s AND code_consumed_at IS NULL",
            (phone_e164,),
        )
        challenge_id = connection.execute(
            """INSERT INTO public_booking_otp_challenges(phone_e164,email,delivery_channel,otp_reason,request_hash,code_hash,expires_at)
               VALUES(%s,%s,%s,%s,%s,%s,now()+interval '10 minutes') RETURNING id""",
            (phone_e164, str(payload.email).strip().lower(), payload.channel, payload.otp_reason,
             request_hash, b"pending"),
        ).fetchone()[0]
        identity = str(payload.email).strip().lower() if payload.channel == "EMAIL" else phone_e164
        connection.execute("UPDATE public_booking_otp_challenges SET code_hash=%s WHERE id=%s",
                           (hash_otp(str(challenge_id), identity, code), challenge_id))

    configured = (
        (payload.channel == "EMAIL" and settings.email_provider.lower() == "smtp" and settings.smtp_host and settings.smtp_from)
        or (payload.channel == "SMS" and (settings.sms_provider.lower() == "twilio" or (settings.sms_api_url and settings.sms_api_token.get_secret_value())))
        or (payload.channel == "WHATSAPP" and bool(settings.meta_access_token.get_secret_value() and settings.meta_phone_number_id))
    )
    demo_code = None
    actual_channel = payload.channel
    status_value = "SENT"
    provider_ref = None
    error = None
    try:
        if settings.allow_test_otp or (settings.app_env.lower() != "production" and settings.demo_mode and not configured):
            demo_code = code
            status_value = "FAILED"
            error = "Development test code displayed; no external delivery was attempted."
        elif payload.channel == "EMAIL":
            provider_ref = send_email_otp(str(payload.email), code, payload.otp_reason)
        elif payload.channel == "SMS":
            provider_ref = send_sms_message(phone_e164, f"Dental Hospital OTP for {payload.otp_reason}: {code}. Valid for 10 minutes. Do not share this OTP.")
        else:
            delivery = send_otp(phone_e164, code)
            provider_ref = delivery.provider_reference or delivery.provider
            if delivery.provider.lower() == "sms": actual_channel = "SMS"
            elif delivery.provider.lower() == "whatsapp": actual_channel = "WHATSAPP"
            else:
                demo_code = delivery.demo_code or code
                status_value = "FAILED"
                error = "Development test code displayed; no external delivery was attempted."
    except ProviderError as exc:
        status_value = "FAILED"
        error = str(exc)[:300]
    with connection.transaction():
        connection.execute(
            """INSERT INTO notification_delivery_log(recipient_mobile_10_digit,recipient_email,message_type,channel,provider_status,provider_reference,error_summary)
               VALUES(%s,%s,'OTP',%s,%s,%s,%s)""",
            (payload.phone, str(payload.email).strip().lower(), actual_channel, status_value, provider_ref, error),
        )
    result = {"ok": True, "otp_reason": payload.otp_reason, "channel": actual_channel,
              "expires_in": 600, "message": f"Verification required for {payload.otp_reason}. Delivery: {actual_channel.lower()}."}
    if demo_code:
        result["demo_code"] = demo_code
        result["message"] += " Local development test code; no message was delivered."
    if status_value == "FAILED" and not demo_code:
        raise HTTPException(503, "The verification code could not be delivered. Try another channel or contact the clinic.")
    return result


@router.post("/appointments/otp/verify")
def verify_booking_otp(payload: BookingOtpVerify, connection: Db):
    request_hash = _booking_payload_hash(payload)
    identity = str(payload.email).strip().lower() if payload.channel == "EMAIL" else "+91" + payload.phone
    invalid_code = False
    with connection.transaction():
        row = connection.execute(
            """SELECT id,code_hash FROM public_booking_otp_challenges WHERE phone_e164=%s AND email=%s
               AND delivery_channel=%s AND otp_reason=%s AND request_hash=%s AND code_consumed_at IS NULL
               AND expires_at>now() AND attempts<5 ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
            ("+91" + payload.phone, str(payload.email).strip().lower(), payload.channel, payload.otp_reason, request_hash),
        ).fetchone()
        if not row:
            raise HTTPException(401, "Code is invalid, expired, or belongs to different booking details.")
        if not verify_otp_hash(str(row[0]), identity, payload.code, row[1]):
            connection.execute("UPDATE public_booking_otp_challenges SET attempts=attempts+1 WHERE id=%s", (row[0],))
            invalid_code = True
            access_token = ""
        else:
            access_token = secrets.token_urlsafe(48)
            connection.execute(
                "UPDATE public_booking_otp_challenges SET code_consumed_at=now(),access_token_hash=%s,access_expires_at=now()+interval '10 minutes' WHERE id=%s",
                (hashlib.sha256(access_token.encode()).digest(), row[0]),
            )
    if invalid_code:
        raise HTTPException(401, "Code is invalid or expired. Request a new one.")
    return {"verified": True, "booking_token": access_token, "expires_in": 600,
            "otp_reason": payload.otp_reason}


def _card(row, extra=None) -> dict:
    extra = extra or {}
    return {
        "id": str(row[0]),
        "opid": row[1],
        "patient_id": str(row[2]),
        "appointment_id": str(row[3]),
        "patient_name": row[4],
        "appointment_date": row[5].isoformat(),
        "time_slot": row[6],
        "token_no": row[7],
        "room": row[8],
        "status": row[9],
        "created_at": row[10].isoformat(),
        "patient_mobile": extra.get("mobile_10_digit"),
        "patient_email": extra.get("email"),
        "patient_public_id": extra.get("patient_public_id"),
        "delivery_channel": row[11] if len(row) > 11 else "PENDING",
        "delivery_status": row[12] if len(row) > 12 else "QUEUED",
    }


@router.get("/settings")
def public_settings(connection: Db):
    row = connection.execute(
        "SELECT hospital_name, card_finish FROM public_site_settings WHERE id = true"
    ).fetchone()
    settings = get_settings()
    return {
        "hospital_name": row[0] if row else settings.clinic_name,
        "card_finish": row[1] if row else "porcelain",
        "time_slots": [f"{hour:02d}:00" for hour in range(9, 20)],
    }


@router.post("/history/otp/request")
def request_patient_history_otp(payload: PatientHistoryOtpRequest, connection: Db):
    phone_e164 = "+91" + payload.mobile_10_digit
    with connection.transaction():
        patient = connection.execute(
            "SELECT id FROM patients WHERE mobile_10_digit=%s AND soft_deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (payload.mobile_10_digit,),
        ).fetchone()
        if not patient:
            return {"ok": True, "otp_reason": payload.otp_reason, "message": "If this mobile is registered, a verification code has been sent."}
        recent = connection.execute(
            "SELECT count(*) FROM patient_history_challenges WHERE patient_id=%s AND created_at > now()-interval '15 minutes'",
            (patient[0],),
        ).fetchone()[0]
        if recent >= 3:
            raise HTTPException(429, "Too many code requests. Try again in 15 minutes.")
        connection.execute("UPDATE patient_history_challenges SET consumed_at=now() WHERE patient_id=%s AND consumed_at IS NULL", (patient[0],))
        code = generate_otp()
        challenge_id = connection.execute(
            "INSERT INTO patient_history_challenges(patient_id,code_hash,expires_at,otp_reason) VALUES(%s,%s,now()+interval '15 minutes',%s) RETURNING id",
            (patient[0], b"pending", payload.otp_reason),
        ).fetchone()[0]
        connection.execute("UPDATE patient_history_challenges SET code_hash=%s WHERE id=%s", (hash_otp(str(challenge_id), phone_e164, code), challenge_id))
        patient_id = str(patient[0])
    try:
        delivery = send_otp(phone_e164, code)
        status, channel, provider_ref, error = "SENT", delivery.provider.upper(), None, None
        demo_code = delivery.demo_code
    except ProviderError as exc:
        status, channel, provider_ref, error, demo_code = "FAILED", "SMS", None, str(exc)[:300], None
    with connection.transaction():
        connection.execute(
            "INSERT INTO notification_delivery_log(recipient_mobile_10_digit,message_type,channel,provider_status,error_summary) VALUES(%s,'OTP',%s,%s,%s)",
            (payload.mobile_10_digit, channel if channel in {"SMS", "WHATSAPP"} else "SMS", status, error),
        )
    response = {"ok": True, "otp_reason": payload.otp_reason, "message": "If this mobile is registered, a verification code has been sent."}
    if demo_code:
        response["demo_code"] = demo_code
        response["message"] = "Local demo code only; configure a delivery provider before production."
    return response


@router.post("/history/otp/verify")
def verify_patient_history_otp(payload: PatientHistoryOtpVerify, connection: Db):
    phone = "+91" + payload.mobile_10_digit
    invalid_code = False
    with connection.transaction():
        row = connection.execute(
            """SELECT c.id,c.patient_id,c.code_hash FROM patient_history_challenges c JOIN patients p ON p.id=c.patient_id
               WHERE p.mobile_10_digit=%s AND c.otp_reason=%s AND c.consumed_at IS NULL AND c.expires_at>now() AND c.attempts<5
               ORDER BY c.created_at DESC LIMIT 1 FOR UPDATE OF c""",
            (payload.mobile_10_digit, payload.otp_reason),
        ).fetchone()
        if not row:
            raise HTTPException(401, "Code is invalid or expired. Request a new one.")
        if not verify_otp_hash(str(row[0]), phone, payload.code, row[2]):
            connection.execute("UPDATE patient_history_challenges SET attempts=attempts+1 WHERE id=%s", (row[0],))
            invalid_code = True
            patient_id = ""
        else:
            connection.execute("UPDATE patient_history_challenges SET consumed_at=now() WHERE id=%s", (row[0],))
            patient_id = str(row[1])
    if invalid_code:
        raise HTTPException(401, "Code is invalid or expired. Request a new one.")
    expiry = int(datetime.now().timestamp()) + 15 * 60
    return {"access_token": _patient_history_token(patient_id, expiry), "expires_in": 900,
            "otp_reason": payload.otp_reason}


@router.get("/history")
def get_patient_history(
    connection: Db,
    token: Annotated[str | None, Header(alias="X-Patient-History-Token")] = None,
    patient_id: str | None = None,
):
    verified_patient_id = _verified_patient(token)
    patient = connection.execute(
        "SELECT patient_public_id FROM patients WHERE id=%s AND soft_deleted_at IS NULL", (verified_patient_id,)
    ).fetchone()
    if not patient:
        raise HTTPException(404, "Patient history not found.")
    if patient_id and not hmac.compare_digest(patient[0], patient_id):
        raise HTTPException(404, "Patient history not found.")
    visits = connection.execute(
        """SELECT opid, appointment_date, time_slot, status FROM appointment_cards
           WHERE patient_id=%s ORDER BY appointment_date DESC, time_slot DESC""",
        (verified_patient_id,),
    ).fetchall()
    safe_status = {"CONFIRMED": "BOOKED", "CANCELLED": "CANCELLED", "COMPLETED": "COMPLETED"}
    return {
        "patient_id": patient[0],
        "visits": [
            {
                "op_id": row[0],
                "appointment_date_ist": row[1].isoformat(),
                "appointment_time_ist": row[2],
                "status": safe_status.get(row[3], "IN_PROGRESS"),
                "instructions": "Please arrive 10 minutes early and bring your appointment card. Contact the clinic for visit-specific guidance.",
            }
            for row in visits
        ],
    }


@router.patch("/settings")
def update_public_settings(payload: SettingsUpdate, user: ClinicAdmin, connection: Db):
    with connection.transaction():
        if connection.execute(
            "SELECT 1 FROM organisations WHERE id = %s", (user.organisation_id,)
        ).fetchone() is None:
            raise HTTPException(404, "Clinic not found.")
        connection.execute(
            "UPDATE organisations SET name = %s WHERE id = %s",
            (payload.hospital_name, user.organisation_id),
        )
        connection.execute(
            """INSERT INTO public_site_settings(id, hospital_name, card_finish)
               VALUES (true, %s, %s)
               ON CONFLICT (id) DO UPDATE SET hospital_name = EXCLUDED.hospital_name,
                 card_finish = EXCLUDED.card_finish, updated_at = now()""",
            (payload.hospital_name, payload.card_finish),
        )
    return {"hospital_name": payload.hospital_name, "card_finish": payload.card_finish}


def _persist_booking(payload: BookingInput, phone: str, idempotency_key: str, request_hash: str, access_token: str) -> dict:
    try:
        with get_pool().connection() as connection:
            with connection.transaction():
                challenge = connection.execute(
                    """SELECT id,request_hash,appointment_card_id,idempotency_key
                       FROM public_booking_otp_challenges
                       WHERE access_token_hash=%s AND access_expires_at>now() FOR UPDATE""",
                    (hashlib.sha256(access_token.encode()).digest(),),
                ).fetchone()
                if not challenge:
                    raise HTTPException(401, "Verify your booking details by OTP before creating an OP.")
                if challenge[1] != request_hash:
                    raise HTTPException(409, "These booking details differ from the details verified by OTP.")
                if challenge[2]:
                    if challenge[3] != idempotency_key:
                        raise HTTPException(409, "This OTP was already used. Request a fresh code for another OP.")
                    saved_card = connection.execute("SELECT * FROM appointment_cards WHERE id=%s", (challenge[2],)).fetchone()
                    extra = connection.execute(
                        "SELECT mobile_10_digit,email,patient_public_id FROM patients WHERE id=%s", (saved_card[2],)
                    ).fetchone()
                    return _card(saved_card, dict(zip(("mobile_10_digit","email","patient_public_id"), extra)))
                connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 3))",
                ("public-booking:" + idempotency_key,),
                )
                existing = connection.execute(
                    """SELECT c.* FROM public_booking_idempotency i
                   JOIN appointment_cards c ON c.id = i.appointment_card_id
                   WHERE i.idempotency_key = %s""",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    saved_hash = connection.execute(
                        "SELECT request_hash FROM public_booking_idempotency WHERE idempotency_key = %s",
                        (idempotency_key,),
                    ).fetchone()[0]
                    if saved_hash != request_hash:
                        raise HTTPException(409, "This booking attempt was already used. Refresh and try again.")
                    extra = connection.execute(
                        "SELECT mobile_10_digit, email, patient_public_id FROM patients WHERE id = %s",
                        (existing[2],),
                    ).fetchone()
                    return _card(existing, dict(zip(("mobile_10_digit", "email", "patient_public_id"), extra)))
                row = connection.execute(
                    "SELECT * FROM book_appointment(%s, %s, %s, %s, %s, %s)",
                    (payload.name, phone, payload.appointment_date, payload.time_slot,
                     str(payload.email).strip().lower(), payload.problem_summary.strip()),
                ).fetchone()
                connection.execute(
                    "INSERT INTO public_appointment_delivery_outbox(appointment_card_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (row[0],),
                )
                connection.execute(
                    """INSERT INTO public_booking_idempotency
                   (idempotency_key, request_hash, appointment_card_id) VALUES (%s, %s, %s)""",
                    (idempotency_key, request_hash, row[0]),
                )
                connection.execute(
                    "UPDATE public_booking_otp_challenges SET appointment_card_id=%s,idempotency_key=%s WHERE id=%s",
                    (row[0], idempotency_key, challenge[0]),
                )
                extra = connection.execute(
                    "SELECT mobile_10_digit, email, patient_public_id FROM patients WHERE id = %s",
                    (row[2],),
                ).fetchone()
            return _card(row, dict(zip(("mobile_10_digit", "email", "patient_public_id"), extra)))
    except HTTPException:
        raise
    except Exception as exc:
        # Keep database internals out of the public response; server logs retain the details.
        import logging

        logging.getLogger(__name__).exception("Public appointment booking failed")
        raise HTTPException(503, "We couldn’t complete the booking. Your details are still here—please retry.") from exc


@router.post("/appointments/book", status_code=201)
async def book_appointment(
    payload: BookingInput,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)],
    access_token: Annotated[str, Header(alias="X-Booking-OTP-Token", min_length=40, max_length=128)],
):
    phone = "+91" + payload.phone
    validate_appointment_time(payload.appointment_date, payload.time_slot)
    request_hash = _booking_payload_hash(payload)
    async with _booking_capacity:
        return await asyncio.to_thread(_persist_booking, payload, phone, idempotency_key, request_hash, access_token)


@router.get("/appointments/{opid}")
def get_appointment(opid: str, connection: Db):
    if len(opid) > 24 or not opid.upper().startswith("OP-"):
        raise HTTPException(404, "Appointment card not found.")
    row = connection.execute("SELECT * FROM get_appointment(%s)", (opid,)).fetchone()
    if not row:
        raise HTTPException(404, "No appointment was found for that OP ID.")
    extra = connection.execute(
        "SELECT mobile_10_digit, email, patient_public_id FROM patients WHERE id = %s", (row[2],)
    ).fetchone()
    return _card(row, dict(zip(("mobile_10_digit", "email", "patient_public_id"), extra)))


@router.post("/webhooks/sms/status")
async def sms_delivery_status(request: Request, connection: Db):
    form = await request.form()
    params = {str(key): str(value) for key, value in form.multi_items()}
    signature = request.headers.get("X-Twilio-Signature")
    base = get_settings().public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        base += "?" + request.url.query
    if not verify_twilio_signature(base, params, signature):
        raise HTTPException(401, "Invalid SMS provider signature.")
    provider_ref = params.get("MessageSid", "")
    status_name = params.get("MessageStatus", "").lower()
    if not provider_ref or status_name not in {"queued", "accepted", "sending", "sent", "delivered", "failed", "undelivered"}:
        raise HTTPException(400, "SMS delivery status is incomplete.")
    normalized = "DELIVERED" if status_name == "delivered" else "FAILED" if status_name in {"failed", "undelivered"} else "SENT"
    with connection.transaction():
        delivery = connection.execute(
            """
            UPDATE notification_delivery_log SET provider_status = %s, timestamp_ist = now(),
              error_summary = CASE WHEN %s = 'FAILED' THEN %s ELSE error_summary END
            WHERE provider_reference = %s AND message_type IN ('APPOINTMENT_CARD','OTP')
            RETURNING appointment_card_id
            """,
            (normalized, normalized, params.get("ErrorMessage", "SMS provider reported delivery failure")[:300], provider_ref),
        ).fetchall()
        for (card_id,) in delivery:
            if card_id:
                connection.execute(
                    """
                    UPDATE appointment_cards SET delivery_status = %s
                    WHERE id = %s AND delivery_status <> 'FAILED'
                    """,
                    (normalized, card_id),
                )
    return {"ok": True}
