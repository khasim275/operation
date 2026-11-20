from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg import Connection

from config import get_settings
from db import transaction
from messaging import generate_otp, hash_otp, normalize_phone, verify_otp_hash
from patient_cards import _create_card


@dataclass
class BotResult:
    replies: list[str]
    debug_code: str | None = None
    otp_challenge_id: str | None = None
    otp_phone: str | None = None
    otp_code: str | None = None
    patient_card_id: str | None = None
    patient_id: str | None = None
    card_phone: str | None = None
    patient_op_id: str | None = None


WELCOME = "Hello, welcome to {clinic}. How are you today? Have a nice day. Reply Hi to begin."
MENU = "How can we help today?\n1. Book an appointment\n2. Treatments\n3. Clinic timings and location\n4. Talk to our team"
REASONS = "What would you like help with?\n1. Check-up\n2. Tooth pain\n3. Cleaning\n4. Filling\n5. Root canal\n6. Braces\n7. Implants\n8. Other"


def _update_conversation(connection: Connection, phone: str, state: str, data: dict, patient_id=None) -> None:
    connection.execute(
        """
        INSERT INTO conversations (phone_e164, patient_id, branch_id, state, state_data, last_patient_message_at)
        VALUES (%s, %s, (
            SELECT b.id FROM branches b
            JOIN organisations o ON o.id = b.organisation_id
            WHERE o.name = %s AND b.is_active
            ORDER BY b.created_at LIMIT 1
        ), %s, %s::jsonb, now())
        ON CONFLICT (phone_e164) DO UPDATE SET
            patient_id = coalesce(EXCLUDED.patient_id, conversations.patient_id),
            state = EXCLUDED.state,
            state_data = EXCLUDED.state_data,
            last_patient_message_at = now(),
            updated_at = now()
        """,
        (
            phone,
            patient_id,
            get_settings().clinic_name,
            state,
            json.dumps(data),
        ),
    )


def _get_or_create_branch(connection: Connection):
    settings = get_settings()
    row = connection.execute(
        """
        SELECT b.id, b.organisation_id FROM branches b
        JOIN organisations o ON o.id = b.organisation_id
        WHERE o.name = %s AND b.name = %s AND b.is_active
        ORDER BY b.created_at LIMIT 1
        """,
        (settings.clinic_name, settings.branch_name),
    ).fetchone()
    if not row:
        raise RuntimeError("The clinic branch has not been initialized.")
    return row[0], row[1]


def _record_ticket(connection: Connection, phone: str, name: str | None, data: dict) -> tuple[str, str, bool, str, str, str | None]:
    branch_id, organisation_id = _get_or_create_branch(connection)
    patient_id = data.get("patient_id")
    if patient_id:
        phone_row = connection.execute(
            """
            SELECT p.id, p.full_name, p.op_id
            FROM patient_phones pp JOIN patients p ON p.id = pp.patient_id
            WHERE pp.phone_e164 = %s AND p.id = %s AND p.organisation_id = %s
              AND p.soft_deleted_at IS NULL
            """,
            (phone, patient_id, organisation_id),
        ).fetchone()
        phone_rows = [phone_row] if phone_row else []
    else:
        phone_rows = connection.execute(
            """
            SELECT p.id, p.full_name, p.op_id
            FROM patient_phones pp JOIN patients p ON p.id = pp.patient_id
            WHERE pp.phone_e164 = %s AND p.organisation_id = %s AND p.soft_deleted_at IS NULL
            ORDER BY p.created_at DESC
            """,
            (phone, organisation_id),
        ).fetchall()
    if len(phone_rows) > 1:
        raise ValueError("This number belongs to more than one patient. Please choose who the appointment is for.")
    phone_row = phone_rows[0] if phone_rows else None
    existing = bool(phone_row)
    card_id = None
    if existing:
        patient_id, patient_name, op_id = phone_row
    else:
        if not name or len(name.strip()) < 2 or not data.get("data_processing_consent"):
            raise ValueError("Please enter your full name.")
        patient_id, op_id = connection.execute(
            """
            INSERT INTO patients (organisation_id, full_name, referral_source)
            VALUES (%s, %s, 'whatsapp') RETURNING id, op_id
            """,
            (organisation_id, name.strip()),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO patient_phones (patient_id, phone_e164, label, is_primary, verified_at)
            VALUES (%s, %s, 'mobile', true, now())
            """,
            (patient_id, phone),
        )
        connection.execute(
            "INSERT INTO patient_medical_profile (patient_id) VALUES (%s)",
            (patient_id,),
        )
        connection.execute(
            """
            INSERT INTO consents (patient_id, consent_type, policy_version, source)
            VALUES (%s, 'data_processing', 'whatsapp-registration-v1', 'whatsapp')
            """,
            (patient_id,),
        )
        card = _create_card(connection, patient_id, op_id, None, "WhatsApp registration")
        card_id = str(card[0])
        patient_name = name.strip()

    appointment_id, ticket_number = connection.execute(
        """
        INSERT INTO appointments (
            patient_id, branch_id, source, reason, preferred_date, preferred_time_of_day, status
        ) VALUES (%s, %s, 'whatsapp', %s, %s, %s, 'requested')
        RETURNING id, ticket_number
        """,
        (
            patient_id,
            branch_id,
            data["reason"],
            data.get("preferred_date"),
            data.get("preferred_time_of_day", "any"),
        ),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO appointment_events (appointment_id, event_type, new_status, details)
        VALUES (%s, 'request_created', 'requested', %s::jsonb)
        """,
        (appointment_id, json.dumps({"source": "whatsapp"})),
    )
    return str(patient_name), ticket_number, existing, str(op_id), str(patient_id), card_id


def process_message(connection: Connection, sender_phone: str, message: str) -> BotResult:
    settings = get_settings()
    message = message.strip()
    lower = message.lower()
    if not message:
        return BotResult(["Please send a message so we can help."])

    try:
        sender = normalize_phone(sender_phone, settings.default_country_code)
    except ValueError:
        return BotResult(["We could not verify this phone number. Please contact the clinic team."])

    with transaction(connection):
        conversation = connection.execute(
            "SELECT state, state_data FROM conversations WHERE phone_e164 = %s",
            (sender,),
        ).fetchone()
        state = conversation[0] if conversation else "welcome"
        data = conversation[1] if conversation else {}
        if isinstance(data, str):
            data = json.loads(data)

        if state == "welcome" and lower in ("hi", "hello", "hey", "start", "menu"):
            _update_conversation(connection, sender, "menu", {}, None)
            return BotResult([MENU])

        if state == "welcome":
            _update_conversation(connection, sender, "welcome", {}, None)
            return BotResult([WELCOME.format(clinic=settings.clinic_name)])

        if state == "done":
            if lower in ("1", "book", "appointment"):
                state = "menu"
            else:
                _update_conversation(connection, sender, "menu", {}, None)
                return BotResult([MENU])

        if state == "menu":
            if lower in ("1", "book", "appointment", "book an appointment"):
                _update_conversation(connection, sender, "awaiting_reason", {}, None)
                return BotResult([REASONS])
            if lower in ("2", "treatments"):
                return BotResult(["We offer check-ups, cleaning, fillings, root canal care, braces, implants, whitening, and children's dentistry. Reply 1 to request an appointment."])
            if lower in ("3", "timings", "location"):
                return BotResult([f"{settings.clinic_name}, {settings.clinic_city}, {settings.clinic_country}. Please call the clinic for current opening hours and directions. Reply 1 to request an appointment."])
            if lower in ("4", "team", "talk"):
                return BotResult(["Our team can help. Please call the clinic during opening hours or reply 1 to request a call back."])
            return BotResult(["Please reply with 1, 2, 3, or 4.\n" + MENU])

        if state == "awaiting_reason":
            choices = {
                "1": "Check-up", "check-up": "Check-up", "check up": "Check-up",
                "2": "Tooth pain", "tooth pain": "Tooth pain",
                "3": "Cleaning", "cleaning": "Cleaning",
                "4": "Filling", "filling": "Filling", "fillings": "Filling",
                "5": "Root canal", "root canal": "Root canal",
                "6": "Braces", "braces": "Braces",
                "7": "Implants", "implants": "Implants",
                "8": "Other", "other": "Other",
            }
            reason = choices.get(lower)
            if not reason:
                return BotResult(["Please choose one of the listed reasons.\n" + REASONS])
            data["reason"] = reason
            _update_conversation(connection, sender, "awaiting_preferred_date", data, None)
            return BotResult(["What day would you prefer? Please enter a date as YYYY-MM-DD."])

        if state == "awaiting_preferred_date":
            try:
                preferred_day = date.fromisoformat(message)
                if preferred_day < datetime.now(ZoneInfo(settings.clinic_timezone)).date():
                    raise ValueError
            except ValueError:
                return BotResult(["Please enter today or a future date as YYYY-MM-DD, for example 2026-04-20."])
            data["preferred_date"] = preferred_day.isoformat()
            _update_conversation(connection, sender, "awaiting_time", data, None)
            return BotResult(["What time of day works best? Reply 1 Morning, 2 Afternoon, 3 Evening, or 4 Any time."])

        if state == "awaiting_time":
            times = {
                "1": "morning", "morning": "morning",
                "2": "afternoon", "afternoon": "afternoon",
                "3": "evening", "evening": "evening",
                "4": "any", "any": "any", "any time": "any",
            }
            preferred_time = times.get(lower)
            if not preferred_time:
                return BotResult(["Please reply 1 for morning, 2 for afternoon, 3 for evening, or 4 for any time."])
            data["preferred_time_of_day"] = preferred_time
            _update_conversation(connection, sender, "awaiting_phone", data, None)
            return BotResult(["Please enter your mobile number with country code."])

        if state == "awaiting_phone":
            try:
                mobile = normalize_phone(message, settings.default_country_code)
            except ValueError:
                return BotResult(["That mobile number does not look complete. Please include the country code, such as +91."])
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (mobile,),
            )
            hourly_requests = connection.execute(
                """
                SELECT count(*) FROM otp_challenges
                WHERE phone_e164 = %s AND requested_at > now() - interval '1 hour'
                """,
                (mobile,),
            ).fetchone()[0]
            if hourly_requests >= 3:
                return BotResult(["For your security, no more than 3 codes can be requested per hour. Please try again later."])

            otp_code = generate_otp()
            challenge_id = uuid4()
            connection.execute(
                "UPDATE otp_challenges SET status = 'expired' WHERE phone_e164 = %s AND status = 'pending'",
                (mobile,),
            )
            connection.execute(
                """
                INSERT INTO otp_challenges (id, phone_e164, code_hash, expires_at, max_attempts, status)
                VALUES (%s, %s, %s, now() + interval '5 minutes', 3, 'pending')
                """,
                (challenge_id, mobile, hash_otp(str(challenge_id), mobile, otp_code)),
            )
            connection.execute(
                "INSERT INTO otp_events (challenge_id, phone_e164, event_type) VALUES (%s, %s, 'requested')",
                (challenge_id, mobile),
            )
            data["otp_challenge_id"] = str(challenge_id)
            data["verified_phone"] = mobile
            _update_conversation(connection, sender, "awaiting_otp", data, None)
            return BotResult(
                ["We sent a 6-digit code to your mobile number. It expires in 5 minutes."],
                otp_challenge_id=str(challenge_id),
                otp_phone=mobile,
                otp_code=otp_code,
                debug_code=otp_code if settings.demo_mode else None,
            )

        if state == "awaiting_otp":
            challenge_id = data.get("otp_challenge_id")
            mobile = data.get("verified_phone")
            if not challenge_id or not mobile:
                _update_conversation(connection, sender, "awaiting_phone", data, None)
                return BotResult(["Please enter your mobile number again to request a new code."])
            challenge = connection.execute(
                """
                SELECT code_hash, attempts, max_attempts, expires_at, status
                FROM otp_challenges WHERE id = %s AND phone_e164 = %s FOR UPDATE
                """,
                (challenge_id, mobile),
            ).fetchone()
            if not challenge or challenge[4] != "pending":
                _update_conversation(connection, sender, "awaiting_phone", data, None)
                return BotResult(["That code is no longer active. Please enter your mobile number to request a new code."])
            if connection.execute("SELECT %s <= now()", (challenge[3],)).fetchone()[0]:
                connection.execute("UPDATE otp_challenges SET status = 'expired' WHERE id = %s", (challenge_id,))
                connection.execute(
                    "INSERT INTO otp_events (challenge_id, phone_e164, event_type) VALUES (%s, %s, 'expired')",
                    (challenge_id, mobile),
                )
                _update_conversation(connection, sender, "awaiting_phone", data, None)
                return BotResult(["This code has expired. Please enter your mobile number to request a new code."])
            if not message.isdigit() or len(message) != 6 or not verify_otp_hash(
                challenge_id, mobile, message, bytes(challenge[0])
            ):
                attempts = challenge[1] + 1
                locked = attempts >= challenge[2]
                connection.execute(
                    "UPDATE otp_challenges SET attempts = %s, status = %s WHERE id = %s",
                    (attempts, "locked" if locked else "pending", challenge_id),
                )
                connection.execute(
                    "INSERT INTO otp_events (challenge_id, phone_e164, event_type) VALUES (%s, %s, %s)",
                    (challenge_id, mobile, "locked" if locked else "wrong"),
                )
                if locked:
                    _update_conversation(connection, sender, "awaiting_phone", data, None)
                    return BotResult(["That code did not match. For your security, please enter your mobile number to request a new code."])
                return BotResult([f"That code did not match. You have {challenge[2] - attempts} attempt(s) left."])
            connection.execute(
                "UPDATE otp_challenges SET status = 'verified', consumed_at = now() WHERE id = %s",
                (challenge_id,),
            )
            connection.execute(
                "INSERT INTO otp_events (challenge_id, phone_e164, event_type) VALUES (%s, %s, 'verified')",
                (challenge_id, mobile),
            )
            existing = connection.execute(
                """
                SELECT p.id, p.full_name, p.op_id FROM patient_phones pp
                JOIN patients p ON p.id = pp.patient_id
                WHERE pp.phone_e164 = %s AND p.organisation_id = (
                    SELECT organisation_id FROM branches WHERE id = (
                        SELECT branch_id FROM conversations WHERE phone_e164 = %s
                    )
                ) AND p.soft_deleted_at IS NULL
                """,
                (mobile, sender),
            ).fetchall()
            if len(existing) == 1:
                data["patient_id"] = str(existing[0][0])
                patient_name, ticket, _, op_id, patient_id, _ = _record_ticket(connection, mobile, None, data)
                _update_conversation(connection, sender, "done", {}, patient_id)
                return BotResult([
                    f"Welcome back, {patient_name}. Your OP ID is {op_id}.",
                    f"Your request is received. Reference: {ticket}. Our team will call you shortly to confirm your time.",
                ])
            if len(existing) > 1:
                data["family_candidates"] = [
                    {"id": str(row[0]), "name": row[1]} for row in existing
                ]
                _update_conversation(connection, sender, "awaiting_patient_choice", data, None)
                names = ", ".join(row[1] for row in existing)
                return BotResult([
                    f"Who is the appointment for? Reply with the patient's registered name, or NEW to register another person. Patients on this number: {names}."
                ])
            data["verified"] = True
            _update_conversation(connection, sender, "awaiting_name", data, None)
            return BotResult(["Your number is verified. What is your full name?"])

        if state == "awaiting_patient_choice":
            candidates = data.get("family_candidates", [])
            if lower == "new":
                _update_conversation(connection, sender, "awaiting_name", data, None)
                return BotResult(["Please enter the new patient's full name."])
            matches = [item for item in candidates if item["name"].strip().casefold() == message.casefold()]
            if len(matches) != 1:
                names = ", ".join(item["name"] for item in candidates)
                return BotResult([f"Please reply with one registered name ({names}), or NEW to register another person."])
            data["patient_id"] = matches[0]["id"]
            patient_name, ticket, _, op_id, patient_id, _ = _record_ticket(connection, data["verified_phone"], None, data)
            _update_conversation(connection, sender, "done", {}, patient_id)
            return BotResult([
                f"Welcome back, {patient_name}. Your OP ID is {op_id}.",
                f"Your request is received. Reference: {ticket}. Our team will call you shortly to confirm your time.",
            ])

        if state == "awaiting_name":
            mobile = data.get("verified_phone")
            if not data.get("verified") or not mobile:
                _update_conversation(connection, sender, "awaiting_phone", {}, None)
                return BotResult(["Please start again by replying Hi."])
            if len(message) < 2 or len(message) > 200 or any(char.isdigit() for char in message):
                return BotResult(["Please enter your name using letters."])
            data["new_patient_name"] = message.strip()
            _update_conversation(connection, sender, "awaiting_data_consent", data, None)
            return BotResult([
                "To register this patient, do you consent to storing their personal and health details in the clinic record? Reply YES to continue or NO to stop."
            ])

        if state == "awaiting_data_consent":
            if lower not in ("yes", "no"):
                return BotResult(["Please reply YES to consent to registration or NO to stop."])
            if lower == "no":
                _update_conversation(connection, sender, "done", {}, None)
                return BotResult(["No patient record was created. You can contact the clinic team if you need help."])
            mobile = data.get("verified_phone")
            patient_name, ticket, _, op_id, patient_id, card_id = _record_ticket(
                connection, mobile, data.get("new_patient_name"), {**data, "data_processing_consent": True}
            )
            card_data = {
                "patient_id": patient_id,
                "patient_name": patient_name,
                "op_id": op_id,
                "ticket": ticket,
                "card_id": card_id,
                "verified_phone": mobile,
            }
            _update_conversation(connection, sender, "awaiting_card_consent", card_data, patient_id)
            return BotResult([
                f"Thank you, {patient_name}. Your permanent OP ID is {op_id}.",
                f"Your request is received. Reference: {ticket}. Our team will call you shortly to confirm your time.",
                "Would you also like your patient card image sent on WhatsApp? Reply YES or NO. This is optional.",
            ])

        if state == "awaiting_card_consent":
            if lower not in ("yes", "no"):
                return BotResult(["Please reply YES or NO about receiving the card image on WhatsApp."])
            patient_id = data["patient_id"]
            if lower == "yes":
                connection.execute(
                    """
                    INSERT INTO consents (patient_id, consent_type, policy_version, source)
                    VALUES (%s, 'whatsapp_card', 'card-v1', 'whatsapp')
                    """,
                    (patient_id,),
                )
                _update_conversation(connection, sender, "done", {}, patient_id)
                return BotResult(
                    ["Your card image will be sent using the clinic's approved WhatsApp template."],
                    patient_card_id=data["card_id"],
                    patient_id=patient_id,
                    card_phone=data["verified_phone"],
                    patient_op_id=data["op_id"],
                )
            _update_conversation(connection, sender, "done", {}, patient_id)
            return BotResult(["Your OP ID is " + data["op_id"] + ". We will not send the card image on WhatsApp."])

        _update_conversation(connection, sender, "menu", {}, None)
        return BotResult([MENU])
