from __future__ import annotations

import json
import logging
import time
from zoneinfo import ZoneInfo

from config import get_settings
from db import get_pool, transaction
from messaging import ProviderError, send_sms_message, send_whatsapp_template, send_whatsapp_text, whatsapp_configured

logger = logging.getLogger(__name__)
_last_partition_maintenance = 0.0
_last_idempotency_cleanup = 0.0


def enqueue_message(
    connection,
    phone: str,
    template_key: str,
    variables: dict,
    patient_id=None,
    reminder_job_id=None,
    window_expires_at=None,
) -> None:
    connection.execute(
        """
        INSERT INTO message_outbox
            (patient_id, phone_e164, template_key, template_variables, reminder_job_id, window_expires_at)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s)
        """,
        (patient_id, phone, template_key, json.dumps(variables), reminder_job_id, window_expires_at),
    )


def _template_for(key: str) -> str:
    settings = get_settings()
    templates = {
        "confirmation": settings.meta_template_confirmation,
        "appointment_24h": settings.meta_template_reminder_24h,
        "appointment_2h": settings.meta_template_reminder_2h,
    }
    return templates.get(key, "")


def process_due_reminders() -> None:
    pool = get_pool()
    with pool.connection() as connection:
        with transaction(connection):
            rows = connection.execute(
                """
                SELECT r.id, r.appointment_id, r.reminder_type, p.id, pp.phone_e164,
                       p.full_name, a.ticket_number, a.start_at, u.full_name
                FROM reminder_jobs r
                JOIN appointments a ON a.id = r.appointment_id
                JOIN patients p ON p.id = a.patient_id
                LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
                LEFT JOIN users u ON u.id = a.dentist_id
                WHERE r.status = 'pending' AND r.due_at <= now()
                  AND a.status IN ('confirmed','rescheduled','checked_in')
                ORDER BY r.due_at
                LIMIT 30 FOR UPDATE OF r SKIP LOCKED
                """
            ).fetchall()
            for row in rows:
                job_id, appointment_id, reminder_type, patient_id, phone, name, ticket, start_at, dentist = row
                if not phone:
                    connection.execute(
                        "UPDATE reminder_jobs SET status = 'failed', attempts = attempts + 1, last_error = 'Patient has no primary phone' WHERE id = %s",
                        (job_id,),
                    )
                    continue
                local_start = start_at.astimezone(ZoneInfo(get_settings().clinic_timezone))
                enqueue_message(
                    connection,
                    phone,
                    reminder_type,
                    {
                        "parameters": [
                            name,
                            ticket,
                            local_start.strftime("%d %b %Y"),
                            local_start.strftime("%I:%M %p"),
                            dentist or "",
                        ]
                    },
                    patient_id,
                    job_id,
                )
                connection.execute(
                    "UPDATE reminder_jobs SET status = 'processing', attempts = attempts + 1 WHERE id = %s",
                    (job_id,),
                )


def process_outbox_batch(batch_size: int = 20) -> None:
    pool = get_pool()
    with pool.connection() as connection:
        with transaction(connection):
            connection.execute(
                """
                UPDATE message_outbox
                SET status = 'failed', last_error = 'WhatsApp customer-service window expired'
                WHERE status = 'pending' AND template_key = 'freeform'
                  AND (window_expires_at IS NULL OR window_expires_at <= now())
                """
            )
            rows = connection.execute(
                """
                SELECT id, patient_id, phone_e164, template_key, template_variables, reminder_job_id, attempts
                FROM message_outbox
                WHERE status = 'pending' AND available_at <= now()
                ORDER BY available_at, created_at
                LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (batch_size,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE message_outbox SET status = 'processing', attempts = attempts + 1 WHERE id = %s",
                    (row[0],),
                )

    for row in rows:
        outbox_id, patient_id, phone, template_key, variables, reminder_job_id, prior_attempts = row
        try:
            if template_key == "freeform":
                text = variables["text"]
                provider_id = (
                    send_whatsapp_text(phone, text)
                    if whatsapp_configured()
                    else ("simulator" if get_settings().demo_mode else send_whatsapp_text(phone, text))
                )
                log_text = text
            else:
                template_name = _template_for(template_key)
                if not whatsapp_configured() and get_settings().demo_mode:
                    provider_id = "simulator"
                else:
                    provider_id = send_whatsapp_template(
                        phone,
                        template_name,
                        get_settings().meta_template_language,
                        variables.get("parameters", []),
                    )
                log_text = f"[approved template: {template_key}]"

            with pool.connection() as connection:
                with transaction(connection):
                    connection.execute(
                        """
                        UPDATE message_outbox
                        SET status = 'sent', sent_at = now(), provider_message_id = %s, last_error = NULL
                        WHERE id = %s
                        """,
                        (provider_id, outbox_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO communication_log
                            (patient_id, phone_e164, channel, direction, provider_message_id, message_text, delivery_status)
                        VALUES (%s, %s, 'whatsapp', 'outbound', %s, %s, 'accepted')
                        """,
                        (patient_id, phone, provider_id if provider_id != "simulator" else None, log_text),
                    )
                    if reminder_job_id:
                        connection.execute(
                            "UPDATE reminder_jobs SET status = 'sent', processed_at = now(), last_error = NULL WHERE id = %s",
                            (reminder_job_id,),
                        )
        except ProviderError as exc:
            logger.warning("Message delivery failed for outbox %s: %s", outbox_id, exc)
            attempts = prior_attempts + 1
            with pool.connection() as connection:
                with transaction(connection):
                    if attempts < 3:
                        connection.execute(
                            """
                            UPDATE message_outbox
                            SET status = 'pending', available_at = now() + (%s * interval '1 minute'), last_error = %s
                            WHERE id = %s
                            """,
                            (attempts * 2, str(exc)[:500], outbox_id),
                        )
                    else:
                        connection.execute(
                            "UPDATE message_outbox SET status = 'failed', last_error = %s WHERE id = %s",
                            (str(exc)[:500], outbox_id),
                        )
                        if reminder_job_id:
                            connection.execute(
                                "UPDATE reminder_jobs SET status = 'failed', last_error = %s, processed_at = now() WHERE id = %s",
                                (str(exc)[:500], reminder_job_id),
                            )


def process_public_appointment_deliveries(batch_size: int = 20) -> None:
    """Best-effort WhatsApp delivery with logged SMS fallback; booking stays committed."""
    pool = get_pool()
    with pool.connection() as connection:
        with transaction(connection):
            rows = connection.execute(
                """
                SELECT o.id, o.appointment_card_id, o.attempts, c.opid, c.patient_name,
                       c.appointment_date, c.time_slot, c.status, c.patient_id,
                       p.mobile_10_digit, s.hospital_name
                FROM public_appointment_delivery_outbox o
                JOIN appointment_cards c ON c.id = o.appointment_card_id
                JOIN patients p ON p.id = c.patient_id
                LEFT JOIN public_site_settings s ON s.id = true
                WHERE o.status = 'PENDING' AND o.available_at <= now()
                ORDER BY o.created_at
                LIMIT %s FOR UPDATE OF o SKIP LOCKED
                """,
                (batch_size,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE public_appointment_delivery_outbox SET status='PROCESSING', attempts=attempts+1 WHERE id=%s",
                    (row[0],),
                )
    settings = get_settings()
    for row in rows:
        (outbox_id, card_id, previous_attempts, opid, patient_name, appt_date, appt_time,
         appt_status, patient_id, mobile, clinic_name) = row
        if not mobile:
            _finish_public_delivery(pool, outbox_id, card_id, None, "FAILED", "Patient record has no valid mobile number.", previous_attempts)
            continue
        phone = "+91" + mobile
        ist = ZoneInfo(settings.clinic_timezone)
        local_date = appt_date.strftime("%d-%m-%Y")
        local_time = appt_time
        message = (
            f"{(clinic_name or settings.clinic_name)[:32]}: OP {opid}; {patient_name[:35]}; "
            f"{local_date} {local_time} IST. Appointment confirmed. Ref {opid}."
        )
        fallback_reason = None
        channel = "WHATSAPP"
        provider_ref = None
        error = None
        try:
            if not whatsapp_configured() or not settings.meta_template_confirmation:
                raise ProviderError("WhatsApp confirmation template is not configured.")
            provider_ref = send_whatsapp_template(
                phone, settings.meta_template_confirmation, settings.meta_template_language,
                [clinic_name or settings.clinic_name, opid, patient_name, local_date, local_time],
            )
            provider_status = "SENT"
        except ProviderError as exc:
            fallback_reason = str(exc)[:220]
            channel = "SMS"
            try:
                provider_ref = send_sms_message(phone, message)
                provider_status = "SENT"
            except ProviderError as sms_error:
                provider_status = "FAILED"
                error = str(sms_error)[:300]

        _finish_public_delivery(pool, outbox_id, card_id, channel, provider_status, error,
                                previous_attempts, fallback_reason, provider_ref, mobile)


def _finish_public_delivery(pool, outbox_id, card_id, channel, provider_status, error,
                            previous_attempts, fallback_reason=None, provider_ref=None, mobile=None):
    with pool.connection() as connection:
        with transaction(connection):
            if mobile:
                connection.execute(
                    """
                    INSERT INTO notification_delivery_log
                      (appointment_card_id, recipient_mobile_10_digit, message_type, channel,
                       provider_status, fallback_reason, provider_reference, error_summary)
                    VALUES (%s, %s, 'APPOINTMENT_CARD', %s, %s, %s, %s, %s)
                    """,
                    (card_id, mobile, channel, provider_status, fallback_reason, provider_ref, error),
                )
            if provider_status == "SENT":
                connection.execute(
                    "UPDATE appointment_cards SET delivery_channel=%s, delivery_status='SENT' WHERE id=%s",
                    (channel, card_id),
                )
                connection.execute(
                    "UPDATE public_appointment_delivery_outbox SET status='COMPLETE', processed_at=now(), last_error=NULL WHERE id=%s",
                    (outbox_id,),
                )
                if provider_ref and provider_ref != "local-demo":
                    connection.execute(
                        """
                        INSERT INTO communication_log
                          (patient_id, phone_e164, channel, direction, provider_message_id, message_text, delivery_status)
                        VALUES ((SELECT patient_id FROM appointment_cards WHERE id=%s), '+91'||%s,
                          lower(%s), 'outbound', %s, '[appointment card confirmation]', 'accepted')
                        """,
                        (card_id, mobile, channel, provider_ref),
                    )
            else:
                next_attempt = previous_attempts + 1
                if next_attempt >= 3:
                    connection.execute(
                        "UPDATE appointment_cards SET delivery_channel='FAILED', delivery_status='FAILED' WHERE id=%s",
                        (card_id,),
                    )
                    connection.execute(
                        "UPDATE public_appointment_delivery_outbox SET status='COMPLETE', processed_at=now(), last_error=%s WHERE id=%s",
                        (error or fallback_reason or "Delivery failed.", outbox_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE public_appointment_delivery_outbox
                        SET status='PENDING', available_at=now() + (%s * interval '1 minute'), last_error=%s
                        WHERE id=%s
                        """,
                        (2 ** next_attempt, error or fallback_reason or "Delivery failed.", outbox_id),
                    )
                    connection.execute(
                        "UPDATE appointment_cards SET delivery_status='QUEUED' WHERE id=%s",
                        (card_id,),
                    )


def process_pending_patient_cards(batch_size: int = 10) -> None:
    from patient_cards import _render_pending_card

    pool = get_pool()
    with pool.connection() as connection:
        with transaction(connection):
            rows = connection.execute(
                """
                SELECT id, patient_id
                FROM patient_cards
                WHERE status = 'pending'
                  AND (last_render_attempt_at IS NULL
                       OR last_render_attempt_at < now() - interval '5 minutes')
                ORDER BY issued_at
                LIMIT %s FOR UPDATE SKIP LOCKED
                """,
                (batch_size,),
            ).fetchall()
            for card_id, _ in rows:
                connection.execute(
                    """
                    UPDATE patient_cards
                    SET render_attempts = render_attempts + 1, last_render_attempt_at = now()
                    WHERE id = %s
                    """,
                    (card_id,),
                )
    for card_id, patient_id in rows:
        _render_pending_card(str(card_id), str(patient_id))


def run_worker_tick() -> None:
    global _last_partition_maintenance, _last_idempotency_cleanup
    now = time.monotonic()
    if now - _last_partition_maintenance >= 24 * 60 * 60:
        with get_pool().connection() as connection:
            connection.execute("SELECT maintain_log_partitions(14)")
            connection.commit()
        _last_partition_maintenance = time.monotonic()
    if now - _last_idempotency_cleanup >= 60 * 60:
        with get_pool().connection() as connection:
            connection.execute("DELETE FROM idempotency_keys WHERE created_at < now() - interval '24 hours'")
            connection.commit()
        _last_idempotency_cleanup = time.monotonic()
    process_due_reminders()
    process_outbox_batch()
    process_public_appointment_deliveries()
    process_pending_patient_cards()
