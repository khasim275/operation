from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import qrcode
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from psycopg import Connection
from psycopg.types.json import Jsonb
from reportlab.lib.colors import HexColor, white
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw, ImageFont

from config import get_settings
from db import get_db, get_pool, transaction
from messaging import normalize_phone
from security import UserInfo, require_roles

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
RecordStaff = Annotated[
    UserInfo,
    Depends(require_roles("admin", "telecaller", "receptionist", "dentist", "accountant")),
]
ReceptionStaff = Annotated[
    UserInfo,
    Depends(require_roles("admin", "receptionist")),
]
ProfileStaff = Annotated[
    UserInfo,
    Depends(require_roles("admin", "receptionist", "dentist")),
]
AdminStaff = Annotated[UserInfo, Depends(require_roles("admin"))]
Db = Annotated[Connection, Depends(get_db)]
OP_ID_PATTERN = re.compile(r"^OP-(\d{6})-(\d)$", re.IGNORECASE)


def luhn_check_digit(value: str) -> int:
    if not value or not value.isdigit():
        raise ValueError("Luhn input must contain digits.")
    total = 0
    double_digit = True
    for character in reversed(value):
        digit = int(character)
        if double_digit:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        double_digit = not double_digit
    return (10 - total % 10) % 10


def is_valid_op_id(value: str) -> bool:
    match = OP_ID_PATTERN.fullmatch(value.strip())
    return bool(match and int(match.group(2)) == luhn_check_digit(match.group(1)))


class RegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=200)
    phone: str = Field(min_length=8, max_length=30)
    email: EmailStr | None = None
    date_of_birth: date | None = None
    gender: str = Field(default="", max_length=40)
    preferred_language: str = Field(default="en", min_length=2, max_length=12)
    address_line1: str = Field(default="", max_length=250)
    address_line2: str = Field(default="", max_length=250)
    city: str = Field(default="", max_length=120)
    state: str = Field(default="", max_length=120)
    postal_code: str = Field(default="", max_length=20)
    blood_group: str = Field(default="", max_length=8)
    allergies: list[str] = Field(default_factory=list, max_length=30)
    chronic_conditions: str = Field(default="", max_length=4000)
    current_medications: str = Field(default="", max_length=4000)
    past_surgeries: str = Field(default="", max_length=4000)
    pregnancy_status: Literal["not_recorded", "not_applicable", "no", "yes", "prefer_not_to_say"] = "not_recorded"
    habits: str = Field(default="", max_length=2000)
    family_history: str = Field(default="", max_length=4000)
    medical_notes: str = Field(default="", max_length=8000)
    emergency_contact_name: str = Field(default="", max_length=200)
    emergency_contact_phone: str = Field(default="", max_length=30)
    emergency_contact_relationship: str = Field(default="", max_length=80)
    data_processing_consent: bool
    whatsapp_card_consent: bool = False
    confirm_different_person: bool = False

    @field_validator("phone")
    @classmethod
    def normalize_mobile(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("full_name", "gender", "preferred_language", "address_line1", "address_line2",
                     "city", "state", "postal_code", "blood_group", "chronic_conditions",
                     "current_medications", "past_surgeries", "habits", "family_history",
                     "medical_notes", "emergency_contact_name", "emergency_contact_relationship")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("allergies")
    @classmethod
    def clean_allergies(cls, values: list[str]) -> list[str]:
        clean = [value.strip() for value in values if value.strip()]
        if any(len(value) > 300 for value in clean):
            raise ValueError("Each allergy must be 300 characters or fewer.")
        return clean

    @model_validator(mode="after")
    def validate_registration(self):
        if not self.data_processing_consent:
            raise ValueError("Data-processing consent must be recorded before registration.")
        if self.date_of_birth and self.date_of_birth > date.today():
            raise ValueError("Date of birth cannot be in the future.")
        if self.emergency_contact_phone:
            try:
                normalize_phone(self.emergency_contact_phone)
            except ValueError as exc:
                raise ValueError("Enter a valid emergency contact number.") from exc
        return self


class ReissueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


class MergeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surviving_patient_id: uuid.UUID


class CardConsentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    granted: bool
    policy_version: str = Field(default="card-v1", min_length=1, max_length=50)


class MedicalProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blood_group: str | None = Field(default=None, max_length=8)
    chronic_conditions: str | None = Field(default=None, max_length=4000)
    current_medications: str | None = Field(default=None, max_length=4000)
    past_surgeries: str | None = Field(default=None, max_length=4000)
    pregnancy_status: Literal["not_recorded", "not_applicable", "no", "yes", "prefer_not_to_say"] | None = None
    habits: str | None = Field(default=None, max_length=2000)
    family_history: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=8000)
    emergency_contact_name: str | None = Field(default=None, max_length=200)
    emergency_contact_phone: str | None = Field(default=None, max_length=30)
    emergency_contact_relationship: str | None = Field(default=None, max_length=80)

    @field_validator("blood_group", "chronic_conditions", "current_medications", "past_surgeries",
                     "habits", "family_history", "notes", "emergency_contact_name",
                     "emergency_contact_phone", "emergency_contact_relationship")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("emergency_contact_phone")
    @classmethod
    def normalize_emergency_number(cls, value: str | None) -> str | None:
        if not value:
            return ""
        try:
            return normalize_phone(value)
        except ValueError as exc:
            raise ValueError("Enter a valid emergency contact number.") from exc


def _card_base_url() -> str:
    return get_settings().public_base_url.rstrip("/")


def _qr_payload(op_id: str) -> str:
    return f"{_card_base_url()}/p/{op_id}"


def _create_card(connection: Connection, patient_id, op_id: str, user_id, reason: str):
    version = connection.execute(
        "SELECT coalesce(max(version), 0) + 1 FROM patient_cards WHERE patient_id = %s",
        (patient_id,),
    ).fetchone()[0]
    return connection.execute(
        """
        INSERT INTO patient_cards (patient_id, op_id, version, status, qr_payload, issued_by, issue_reason)
        VALUES (%s, %s, %s, 'pending', %s, %s, %s)
        RETURNING id, version, issued_at, status
        """,
        (patient_id, op_id, version, _qr_payload(op_id), user_id, reason.strip()),
    ).fetchone()


def _render_card_files(card_id: str, patient: dict, issued_at, qr_payload: str) -> tuple[bytes, bytes]:
    settings = get_settings()
    width_px, height_px = 1011, 638
    scale = width_px / 85.6
    cream = "#F7F5EF"
    teal = "#123E42"
    mint = "#DDEFE8"
    front = Image.new("RGB", (width_px, height_px), cream)
    back = Image.new("RGB", (width_px, height_px), teal)
    front_draw = ImageDraw.Draw(front)
    back_draw = ImageDraw.Draw(back)
    regular = ImageFont.load_default(size=int(2.1 * scale))
    bold = ImageFont.load_default(size=int(2.4 * scale))
    margin = int(5 * scale)

    def fitted(text: str, max_width: int, font: ImageFont.FreeTypeFont) -> str:
        clean = text
        while clean and front_draw.textlength(clean, font=font) > max_width:
            clean = clean[:-1]
        return clean.rstrip() + ("..." if clean != text else "")

    front_draw.rounded_rectangle((14, 14, width_px - 14, height_px - 14), radius=30, outline="#C9DCD5", width=4)
    logo_size = int(5 * scale)
    front_draw.ellipse((margin, margin, margin + logo_size, margin + logo_size), fill=teal)
    front_draw.text((margin + int(1.7 * scale), margin + int(0.7 * scale)), "+", fill=cream, font=bold)
    front_draw.text(
        (margin + int(7 * scale), margin),
        fitted(settings.clinic_name[:50], int(43 * scale), bold),
        fill=teal,
        font=bold,
    )
    front_draw.text((margin, int(13 * scale)), fitted(patient["full_name"][:60], int(44 * scale), bold), fill=teal, font=bold)
    front_draw.text((margin, int(23 * scale)), patient["op_id"], fill=teal, font=bold)
    dob = patient.get("date_of_birth")
    front_draw.text((margin, int(31 * scale)), f"DOB: {dob or 'Not recorded'}", fill=teal, font=regular)
    front_draw.text((margin, int(37 * scale)), f"Gender: {patient.get('gender') or 'Not recorded'}", fill=teal, font=regular)
    front_draw.text((margin, int(43 * scale)), f"Blood group: {patient.get('blood_group') or 'Not recorded'}", fill=teal, font=regular)
    front_draw.text((margin, int(49 * scale)), f"Issued: {issued_at:%Y-%m-%d}", fill=teal, font=regular)
    phone = patient.get("phone") or ""
    masked = f"Mobile: ****{phone[-4:]}" if len(phone) > 4 else "Mobile: not recorded"
    front_draw.text((margin, int(54 * scale)), masked, fill=teal, font=regular)
    qr_image = qrcode.make(qr_payload).convert("RGB").resize((int(25 * scale), int(25 * scale)), Image.Resampling.NEAREST)
    qr_x, qr_y = width_px - int(30 * scale), int(8 * scale)
    front.paste(qr_image, (qr_x, qr_y))

    back_draw.rounded_rectangle((14, 14, width_px - 14, height_px - 14), radius=30, outline="#4F7776", width=4)
    back_draw.text((margin, int(8 * scale)), "MEDICAL ALERT ON FILE" if patient.get("has_allergies") else "PATIENT CARD", fill=mint, font=bold)
    back_draw.text((margin, int(19 * scale)), settings.clinic_address[:100], fill="#FFFFFF", font=regular)
    back_draw.text((margin, int(26 * scale)), f"Phone: {settings.clinic_phone}"[:100], fill="#FFFFFF", font=regular)
    back_draw.text((margin, int(33 * scale)), f"WhatsApp: {settings.clinic_whatsapp}"[:100], fill="#FFFFFF", font=regular)
    back_draw.text((margin, int(41 * scale)), "If found, please return to the clinic.", fill="#FFFFFF", font=regular)
    back_draw.text((margin, int(49 * scale)), "Keep this card private. It contains sensitive health information.", fill=mint, font=regular)

    png = Image.new("RGB", (width_px * 2 + 20, height_px), cream)
    png.paste(front, (0, 0))
    png.paste(back, (width_px + 20, 0))
    png_buffer = io.BytesIO()
    png.save(png_buffer, format="PNG", optimize=False, compress_level=9)

    pdf_buffer = io.BytesIO()
    page_size = (85.6 * mm, 54 * mm)
    pdf = canvas.Canvas(pdf_buffer, pagesize=page_size, invariant=1)
    for image, background in ((front, HexColor(cream)), (back, HexColor(teal))):
        pdf.setFillColor(background)
        pdf.rect(0, 0, *page_size, fill=1, stroke=0)
        if image is front:
            pdf.setFillColor(HexColor(teal))
            pdf.circle(5 * mm + 2.5 * mm, 48 * mm, 2.5 * mm, fill=1, stroke=0)
            pdf.setFillColor(HexColor(cream))
            pdf.setFont("Helvetica-Bold", 8)
            pdf.drawCentredString(7.5 * mm, 46.7 * mm, "+")
            pdf.setFillColor(HexColor(teal))
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(12 * mm, 47 * mm, settings.clinic_name[:40])
            pdf.drawString(5 * mm, 37 * mm, patient["full_name"][:45])
            pdf.setFont("Helvetica-Bold", 13)
            pdf.drawString(5 * mm, 28 * mm, patient["op_id"])
            pdf.setFont("Helvetica", 7)
            pdf.drawString(5 * mm, 21 * mm, f"DOB: {dob or 'Not recorded'}")
            pdf.drawString(5 * mm, 16 * mm, f"Gender: {patient.get('gender') or 'Not recorded'}")
            pdf.drawString(5 * mm, 11 * mm, f"Blood group: {patient.get('blood_group') or 'Not recorded'}")
            pdf.drawString(5 * mm, 6 * mm, f"Issued: {issued_at:%Y-%m-%d}   {masked}")
            qr_buffer = io.BytesIO()
            qr_image.save(qr_buffer, format="PNG")
            qr_buffer.seek(0)
            pdf.drawImage(ImageReader(qr_buffer), 58 * mm, 25 * mm, width=23 * mm, height=23 * mm)
        else:
            pdf.setFillColor(white)
            pdf.setFont("Helvetica-Bold", 9)
            pdf.drawString(5 * mm, 46 * mm, "MEDICAL ALERT ON FILE" if patient.get("has_allergies") else "PATIENT CARD")
            pdf.setFont("Helvetica", 7)
            pdf.drawString(5 * mm, 36 * mm, settings.clinic_address[:85])
            pdf.drawString(5 * mm, 30 * mm, f"Phone: {settings.clinic_phone}"[:85])
            pdf.drawString(5 * mm, 24 * mm, f"WhatsApp: {settings.clinic_whatsapp}"[:85])
            pdf.drawString(5 * mm, 15 * mm, "If found, please return to the clinic.")
            pdf.drawString(5 * mm, 7 * mm, "Keep this card private. It contains sensitive health information.")
        pdf.showPage()
    pdf.save()
    return pdf_buffer.getvalue(), png_buffer.getvalue()


def _render_card_with_connection(connection: Connection, card_id: str, patient_id: str) -> None:
    settings = get_settings()
    try:
        card = connection.execute(
            """
            SELECT c.id, c.op_id, c.qr_payload, c.issued_at, c.status,
                   p.full_name, p.date_of_birth, p.gender, pp.phone_e164,
                   coalesce(mp.blood_group, ''), EXISTS (
                       SELECT 1 FROM patient_allergies a
                       WHERE a.patient_id = p.id AND a.status = 'active'
                   )
            FROM patient_cards c
            JOIN patients p ON p.id = c.patient_id
            LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
            LEFT JOIN patient_medical_profile mp ON mp.patient_id = p.id
            WHERE c.id = %s AND p.id = %s
            """,
            (card_id, patient_id),
        ).fetchone()
        if not card or card[4] != "pending":
            return
        patient = {
            "op_id": card[1],
            "full_name": card[5],
            "date_of_birth": card[6].isoformat() if card[6] else None,
            "gender": card[7],
            "phone": card[8],
            "blood_group": card[9],
            "has_allergies": card[10],
        }
        pdf_bytes, png_bytes = _render_card_files(str(card[0]), patient, card[3], card[2])
        directory = settings.storage_dir / "patient_cards"
        directory.mkdir(parents=True, exist_ok=True)
        pdf_key = str(Path("patient_cards") / f"{card_id}.pdf")
        png_key = str(Path("patient_cards") / f"{card_id}.png")
        (settings.storage_dir / pdf_key).write_bytes(pdf_bytes)
        (settings.storage_dir / png_key).write_bytes(png_bytes)
        digest = hashlib.sha256(pdf_bytes + png_bytes).hexdigest()
        with transaction(connection):
            connection.execute(
                """
                UPDATE patient_cards SET pdf_key = %s, png_key = %s, sha256 = %s, status = 'active'
                WHERE id = %s AND status = 'pending'
                """,
                (pdf_key, png_key, digest, card_id),
            )
    except Exception:
        logger.exception("Patient card rendering failed; the card remains pending for retry.")


def _render_pending_card(card_id: str, patient_id: str) -> None:
    """Worker entry point for a card whose synchronous render needs retrying."""
    with get_pool().connection() as connection:
        _render_card_with_connection(connection, card_id, patient_id)


def _access(connection: Connection, user: UserInfo, patient_id, action: str) -> None:
    connection.execute(
        "INSERT INTO patient_access_log (user_id, patient_id, action) VALUES (%s, %s, %s)",
        (user.id, patient_id, action),
    )
    connection.execute("SELECT set_config('app.user_id', %s, true)", (str(user.id),))
    connection.execute("SELECT record_audit_view('patient', %s)", (str(patient_id),))


def _find_duplicate(connection: Connection, organisation_id: str, payload: RegistrationInput):
    if not payload.date_of_birth:
        return []
    return connection.execute(
        """
        SELECT p.id, p.op_id, p.full_name, p.date_of_birth, pp.phone_e164
        FROM patients p
        JOIN patient_phones pp ON pp.patient_id = p.id
        WHERE p.organisation_id = %s AND p.soft_deleted_at IS NULL
          AND pp.phone_e164 = %s
          AND lower(regexp_replace(p.full_name, '\\s+', '', 'g')) =
              lower(regexp_replace(%s, '\\s+', '', 'g'))
          AND p.date_of_birth = %s
        ORDER BY p.created_at DESC
        LIMIT 5
        """,
        (organisation_id, payload.phone, payload.full_name, payload.date_of_birth),
    ).fetchall()


def _response_patient(connection: Connection, patient_id, card_id) -> dict:
    row = connection.execute(
        """
        SELECT p.id, p.op_id, p.patient_number, p.full_name, p.date_of_birth, p.gender,
               pp.phone_e164, c.id, c.version, c.status, c.issued_at
        FROM patients p
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        JOIN patient_cards c ON c.id = %s AND c.patient_id = p.id
        WHERE p.id = %s
        """,
        (card_id, patient_id),
    ).fetchone()
    return {
        "patient": {
            "id": str(row[0]), "op_id": row[1], "patient_number": row[2],
            "full_name": row[3], "date_of_birth": row[4], "gender": row[5], "phone": row[6],
        },
        "card": {
            "id": str(row[7]), "version": row[8], "status": row[9], "issued_at": row[10],
            "pdf_url": f"/api/cards/{row[1]}.pdf", "png_url": f"/api/cards/{row[1]}.png",
        },
    }


@router.post("/patients/register", status_code=201)
def register_patient(
    payload: RegistrationInput,
    user: ReceptionStaff,
    connection: Db,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
):
    data = payload.model_dump(mode="json", exclude={"confirm_different_person"})
    data["phone"] = payload.phone
    request_hash = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    patient_id = card_id = None
    existing_response = None
    with transaction(connection, str(user.id)):
        connection.execute(
            """
            INSERT INTO idempotency_keys (key, request_hash)
            VALUES (%s, %s)
            ON CONFLICT (key) DO NOTHING
            """,
            (idempotency_key, request_hash),
        )
        key_row = connection.execute(
            "SELECT request_hash, response, created_at FROM idempotency_keys WHERE key = %s FOR UPDATE",
            (idempotency_key,),
        ).fetchone()
        if key_row[2] and key_row[2].timestamp() < time.time() - 86400:
            connection.execute("DELETE FROM idempotency_keys WHERE key = %s", (idempotency_key,))
            connection.execute(
                "INSERT INTO idempotency_keys (key, request_hash) VALUES (%s, %s)",
                (idempotency_key, request_hash),
            )
            key_row = (request_hash, None, None)
        if key_row[0] != request_hash:
            raise HTTPException(409, "This idempotency key was already used for a different registration.")
        if key_row[1] is not None:
            existing_response = key_row[1]
        else:
            if payload.date_of_birth:
                duplicate_key = ":".join((
                    str(user.organisation_id),
                    payload.phone,
                    re.sub(r"\s+", "", payload.full_name).casefold(),
                    payload.date_of_birth.isoformat(),
                ))
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (duplicate_key,),
                )
            duplicates = _find_duplicate(connection, user.organisation_id, payload)
            if duplicates and not payload.confirm_different_person:
                raise HTTPException(
                    409,
                    detail={
                        "code": "possible_duplicate",
                        "message": "A patient with the same mobile number, name and date of birth may already exist.",
                        "matches": [
                            {"id": str(row[0]), "op_id": row[1], "full_name": row[2],
                             "date_of_birth": row[3].isoformat() if row[3] else None}
                            for row in duplicates
                        ],
                    },
                )
            patient_id = connection.execute(
                """
                INSERT INTO patients
                    (organisation_id, full_name, email, mobile_10_digit, date_of_birth, gender, preferred_language, referral_source)
                VALUES (%s, %s, %s,
                    CASE WHEN left(%s, 3) = '+91' AND length(%s) = 13
                              AND substring(%s, 4, 1) BETWEEN '6' AND '9'
                         THEN right(%s, 10) ELSE NULL END,
                    %s, %s, %s, 'walk_in')
                RETURNING id, op_id
                """,
                (
                    user.organisation_id, payload.full_name, str(payload.email) if payload.email else None,
                    payload.phone, payload.phone, payload.phone, payload.phone,
                    payload.date_of_birth, payload.gender or None, payload.preferred_language,
                ),
            ).fetchone()
            patient_id, op_id = patient_id
            connection.execute(
                """
                INSERT INTO patient_phones (patient_id, phone_e164, label, is_primary)
                VALUES (%s, %s, 'mobile', true)
                """,
                (patient_id, payload.phone),
            )
            if payload.address_line1 or payload.city:
                connection.execute(
                    """
                    INSERT INTO patient_addresses
                        (patient_id, line1, line2, city, state, postal_code, country_code)
                    VALUES (%s, %s, %s, %s, %s, %s, 'IN')
                    """,
                    (
                        patient_id, payload.address_line1 or "(not provided)",
                        payload.address_line2 or None, payload.city or "(not provided)",
                        payload.state or None, payload.postal_code or None,
                    ),
                )
            if payload.emergency_contact_phone:
                emergency_phone = normalize_phone(payload.emergency_contact_phone)
            else:
                emergency_phone = ""
            connection.execute(
                """
                INSERT INTO patient_medical_profile
                    (patient_id, blood_group, chronic_conditions, current_medications, past_surgeries,
                     pregnancy_status, habits, family_history, notes, emergency_contact_name,
                     emergency_contact_phone, emergency_contact_relationship)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    patient_id, payload.blood_group, payload.chronic_conditions, payload.current_medications,
                    payload.past_surgeries, payload.pregnancy_status, payload.habits,
                    payload.family_history, payload.medical_notes, payload.emergency_contact_name,
                    emergency_phone, payload.emergency_contact_relationship,
                ),
            )
            for allergen in payload.allergies:
                connection.execute(
                    """
                    INSERT INTO patient_allergies (patient_id, allergen_text, severity, status)
                    VALUES (%s, %s, 'unknown', 'active')
                    """,
                    (patient_id, allergen),
                )
            connection.execute(
                """
                INSERT INTO consents (patient_id, consent_type, policy_version, captured_by, source)
                VALUES (%s, 'data_processing', 'registration-v1', %s, 'staff')
                """,
                (patient_id, user.id),
            )
            if payload.whatsapp_card_consent:
                connection.execute(
                    """
                    INSERT INTO consents (patient_id, consent_type, policy_version, captured_by, source)
                    VALUES (%s, 'whatsapp_card', 'card-v1', %s, 'staff')
                    """,
                    (patient_id, user.id),
                )
            card = _create_card(connection, patient_id, op_id, user.id, "Initial issue")
            card_id = card[0]
            response = _response_patient(connection, patient_id, card_id)
            response = json.loads(json.dumps(response, default=str))
            connection.execute(
                "UPDATE idempotency_keys SET response = %s WHERE key = %s",
                (Jsonb(response), idempotency_key),
            )
    if existing_response is not None:
        return existing_response
    _render_card_with_connection(connection, str(card_id), str(patient_id))
    # Use the request's connection so concurrent registrations cannot exhaust the pool.
    result = _response_patient(connection, patient_id, card_id)
    return result


@router.get("/patients/lookup")
def lookup_patients(
    user: RecordStaff,
    connection: Db,
    q: str = Query(min_length=2, max_length=120),
):
    term = q.strip()
    if term.upper().startswith("OP-") and not is_valid_op_id(term):
        raise HTTPException(422, "This ID is not valid, please check the last digit")
    if is_valid_op_id(term):
        rows = connection.execute(
            """
            SELECT p.id, p.op_id, p.patient_number, p.full_name, p.date_of_birth, pp.phone_e164
            FROM patients p LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
            WHERE p.organisation_id = %s AND p.op_id = %s AND p.soft_deleted_at IS NULL
            """,
            (user.organisation_id, term.upper()),
        ).fetchall()
    else:
        phone = ""
        try:
            phone = normalize_phone(term)
        except ValueError:
            pass
        phone_digits = re.sub(r"\D", "", term)
        phone_suffix = f"%{phone_digits}" if len(phone_digits) >= 4 and len(phone_digits) <= 15 else ""
        rows = connection.execute(
            """
            SELECT p.id, p.op_id, p.patient_number, p.full_name, p.date_of_birth, pp.phone_e164
            FROM patients p
            LEFT JOIN patient_phones pp ON pp.patient_id = p.id
            WHERE p.organisation_id = %s AND p.soft_deleted_at IS NULL
              AND (p.full_name ILIKE %s OR lower(p.full_name) = lower(%s)
                   OR (%s <> '' AND pp.phone_e164 = %s)
                   OR (%s <> '' AND pp.phone_e164 LIKE %s))
            ORDER BY CASE WHEN lower(p.full_name) = lower(%s) THEN 0
                          WHEN %s <> '' AND pp.phone_e164 = %s THEN 1 ELSE 2 END,
                     p.full_name, p.created_at DESC
            LIMIT 30
            """,
            (
                user.organisation_id, f"%{term}%", term, phone, phone, phone_suffix,
                phone_suffix, term, phone, phone,
            ),
        ).fetchall()
    if rows:
        with transaction(connection, str(user.id)):
            for row in rows:
                _access(connection, user, row[0], "search")
    return [
        {"id": str(row[0]), "op_id": row[1], "patient_number": row[2], "full_name": row[3],
         "date_of_birth": row[4], "phone": row[5]}
        for row in rows
    ]


def _full_patient_file(connection: Connection, patient_id, user: UserInfo, notice: str | None = None) -> dict:
    patient = connection.execute(
        """
        SELECT p.id, p.op_id, p.patient_number, p.full_name, p.email, p.date_of_birth, p.gender,
               p.occupation, p.preferred_language, p.status, p.created_at, p.merged_into,
               pp.phone_e164, a.line1, a.line2, a.city, a.state, a.postal_code,
               mp.blood_group, mp.chronic_conditions, mp.current_medications, mp.past_surgeries,
               mp.pregnancy_status, mp.habits, mp.family_history, mp.notes,
               mp.emergency_contact_name, mp.emergency_contact_phone, mp.emergency_contact_relationship
        FROM patients p
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        LEFT JOIN LATERAL (
            SELECT line1, line2, city, state, postal_code FROM patient_addresses
            WHERE patient_id = p.id AND is_current ORDER BY created_at DESC LIMIT 1
        ) a ON true
        LEFT JOIN patient_medical_profile mp ON mp.patient_id = p.id
        WHERE p.id = %s AND p.organisation_id = %s
        """,
        (patient_id, user.organisation_id),
    ).fetchone()
    if not patient:
        raise HTTPException(404, "Patient not found.")
    patient_scope = [
        row[0] for row in connection.execute(
            """
            WITH RECURSIVE linked(id) AS (
                SELECT %s::uuid
                UNION
                SELECT p.id FROM patients p
                JOIN linked l ON p.merged_into = l.id
                WHERE p.organisation_id = %s
            )
            SELECT id FROM linked
            """,
            (patient_id, user.organisation_id),
        ).fetchall()
    ]
    allergies = connection.execute(
        """
        SELECT allergen_text, reaction, severity, status, recorded_at
        FROM patient_allergies WHERE patient_id = ANY(%s) ORDER BY recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    conditions = connection.execute(
        """
        SELECT c.name, pc.status, pc.onset_date, pc.notes, pc.recorded_at
        FROM patient_conditions pc JOIN conditions c ON c.id = pc.condition_id
        WHERE pc.patient_id = ANY(%s) ORDER BY pc.recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    phones = connection.execute(
        """
        SELECT phone_e164, label, is_primary, verified_at
        FROM patient_phones WHERE patient_id = ANY(%s)
        ORDER BY is_primary DESC, created_at
        """,
        (patient_scope,),
    ).fetchall()
    medicines = connection.execute(
        """
        SELECT medicine_name, dose, frequency, start_date, end_date, status, recorded_at
        FROM patient_medicines WHERE patient_id = ANY(%s) ORDER BY recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    surgeries = connection.execute(
        """
        SELECT surgery_name, surgery_date, notes, recorded_at
        FROM patient_surgeries WHERE patient_id = ANY(%s) ORDER BY recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    habits = connection.execute(
        """
        SELECT habit_type, details, status, recorded_at
        FROM patient_habits WHERE patient_id = ANY(%s) ORDER BY recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    family_history = connection.execute(
        """
        SELECT relative, condition_text, recorded_at
        FROM family_history WHERE patient_id = ANY(%s) ORDER BY recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    medical_notes = connection.execute(
        """
        SELECT n.note_text, n.recorded_at, u.full_name
        FROM medical_notes n JOIN users u ON u.id = n.author_id
        WHERE n.patient_id = ANY(%s) ORDER BY n.recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    calls = connection.execute(
        """
        SELECT c.started_at, c.outcome, c.notes, c.next_follow_up_at, u.full_name, a.ticket_number
        FROM call_attempts c
        JOIN appointments a ON a.id = c.appointment_id
        JOIN users u ON u.id = c.caller_id
        WHERE a.patient_id = ANY(%s) ORDER BY c.started_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    relationships = connection.execute(
        """
        SELECT relationship_type, full_name, phone_e164, notes
        FROM patient_relationships WHERE patient_id = ANY(%s) ORDER BY created_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    consents = connection.execute(
        """
        SELECT consent_type, policy_version, granted_at, withdrawn_at, source
        FROM consents WHERE patient_id = ANY(%s) ORDER BY granted_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    messages = connection.execute(
        """
        SELECT channel, direction, message_text, delivery_status, occurred_at
        FROM communication_log
        WHERE patient_id = ANY(%s) ORDER BY occurred_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    tooth_chart = connection.execute(
        """
        SELECT DISTINCT ON (fdi_tooth) fdi_tooth, condition_code, details, recorded_at
        FROM tooth_conditions WHERE patient_id = ANY(%s)
        ORDER BY fdi_tooth, recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    dental_procedures = connection.execute(
        """
        SELECT procedure_name, fdi_tooth, performed_on, details, recorded_at
        FROM dental_procedures WHERE patient_id = ANY(%s) ORDER BY recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    gum_assessments = connection.execute(
        """
        SELECT assessment, recorded_at, u.full_name
        FROM gum_assessments g JOIN users u ON u.id = g.recorded_by
        WHERE g.patient_id = ANY(%s) ORDER BY g.recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    orthodontic_status = connection.execute(
        """
        SELECT status, details, recorded_at, u.full_name
        FROM orthodontic_status o LEFT JOIN users u ON u.id = o.recorded_by
        WHERE o.patient_id = ANY(%s) ORDER BY o.recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    appointment_events = connection.execute(
        """
        SELECT a.ticket_number, e.event_type, e.old_status, e.new_status, e.details, e.occurred_at
        FROM appointment_events e JOIN appointments a ON a.id = e.appointment_id
        WHERE a.patient_id = ANY(%s) ORDER BY e.occurred_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    visits = connection.execute(
        """
        SELECT v.id, v.appointment_id, v.complaint, v.examination_findings, v.treatment_plan,
               v.recorded_at, u.full_name,
               coalesce((SELECT json_agg(json_build_object('description', d.description))
                   FROM diagnoses d WHERE d.visit_id = v.id), '[]'::json),
               coalesce((SELECT json_agg(json_build_object('procedure_name', pr.procedure_name,
                   'fdi_tooth', pr.fdi_tooth, 'surface', pr.surface, 'notes', pr.notes))
                   FROM performed_procedures pr WHERE pr.visit_id = v.id), '[]'::json)
        FROM visits v JOIN users u ON u.id = v.dentist_id
        WHERE v.patient_id = ANY(%s) ORDER BY v.recorded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    appointments = connection.execute(
        """
        SELECT a.id, a.ticket_number, a.reason, a.preferred_date, a.preferred_time_of_day,
               a.start_at, a.end_at, a.status, a.source, a.created_at, d.full_name, r.name
        FROM appointments a LEFT JOIN users d ON d.id = a.dentist_id
        LEFT JOIN rooms r ON r.id = a.room_id
        WHERE a.patient_id = ANY(%s) AND a.soft_deleted_at IS NULL
        ORDER BY coalesce(a.start_at, a.created_at) DESC
        """,
        (patient_scope,),
    ).fetchall()
    followups = connection.execute(
        """
        SELECT f.scheduled_at, f.attempt_number, f.status, a.ticket_number
        FROM call_followups f JOIN appointments a ON a.id = f.appointment_id
        WHERE a.patient_id = ANY(%s) ORDER BY f.scheduled_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    reminders = connection.execute(
        """
        SELECT r.reminder_type, r.due_at, r.status, a.ticket_number
        FROM reminder_jobs r JOIN appointments a ON a.id = r.appointment_id
        WHERE a.patient_id = ANY(%s) ORDER BY r.due_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    prescriptions = connection.execute(
        """
        SELECT p.id, p.created_at, p.status, u.full_name,
               coalesce(json_agg(json_build_object('medicine_name', i.medicine_name, 'dose', i.dose,
                   'frequency', i.frequency, 'duration', i.duration, 'instructions', i.instructions))
                   FILTER (WHERE i.id IS NOT NULL), '[]'::json)
        FROM prescriptions p JOIN users u ON u.id = p.dentist_id
        LEFT JOIN prescription_items i ON i.prescription_id = p.id
        WHERE p.patient_id = ANY(%s) GROUP BY p.id, u.full_name ORDER BY p.created_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    documents = connection.execute(
        """
        SELECT id, original_name, mime_type, size_bytes, document_type, uploaded_at
        FROM documents WHERE patient_id = ANY(%s) AND status = 'active'
        ORDER BY uploaded_at DESC
        """,
        (patient_scope,),
    ).fetchall()
    cards = connection.execute(
        """
        SELECT id, version, status, issued_at, issue_reason, print_count, sha256
        FROM patient_cards WHERE patient_id = ANY(%s) ORDER BY version DESC
        """,
        (patient_scope,),
    ).fetchall()
    payments_allowed = bool(set(user.roles).intersection({"admin", "accountant"}))
    payments = []
    invoices = []
    payment_details = []
    if payments_allowed:
        payments = connection.execute(
            """
            SELECT v.currency, coalesce(sum(v.total_minor), 0), coalesce(sum(v.paid_minor), 0),
                   coalesce(sum(v.outstanding_minor), 0)
            FROM invoice_balances v WHERE v.patient_id = ANY(%s)
            GROUP BY v.currency ORDER BY v.currency
            """,
            (patient_scope,),
        ).fetchall()
        invoices = connection.execute(
            """
            SELECT i.id, i.status, i.currency, i.created_at,
                   coalesce(b.total_minor, 0), coalesce(b.paid_minor, 0), coalesce(b.outstanding_minor, 0)
            FROM invoices i LEFT JOIN invoice_balances b ON b.invoice_id = i.id
            WHERE i.patient_id = ANY(%s) ORDER BY i.created_at DESC
            """,
            (patient_scope,),
        ).fetchall()
        payment_details = connection.execute(
            """
            SELECT p.id, p.invoice_id, p.amount_minor, p.currency, p.method, p.status, p.received_at
            FROM payments p JOIN invoices i ON i.id = p.invoice_id
            WHERE i.patient_id = ANY(%s) ORDER BY p.received_at DESC
            """,
            (patient_scope,),
        ).fetchall()
    return {
        "patient": {
            "id": str(patient[0]), "op_id": patient[1], "patient_number": patient[2],
            "full_name": patient[3], "email": patient[4], "date_of_birth": patient[5],
            "gender": patient[6], "occupation": patient[7], "preferred_language": patient[8],
            "status": patient[9], "created_at": patient[10], "merged_into": str(patient[11]) if patient[11] else None,
            "phone": patient[12], "address": {"line1": patient[13], "line2": patient[14],
                "city": patient[15], "state": patient[16], "postal_code": patient[17]},
        },
        "medical_profile": {
            "blood_group": patient[18] or "", "chronic_conditions": patient[19] or "",
            "current_medications": patient[20] or "", "past_surgeries": patient[21] or "",
            "pregnancy_status": patient[22] or "not_recorded", "habits": patient[23] or "",
            "family_history": patient[24] or "", "notes": patient[25] or "",
            "emergency_contact_name": patient[26] or "", "emergency_contact_phone": patient[27] or "",
            "emergency_contact_relationship": patient[28] or "",
        },
        "allergies": [{"allergen": r[0], "reaction": r[1], "severity": r[2], "status": r[3], "recorded_at": r[4]} for r in allergies],
        "conditions": [{"name": r[0], "status": r[1], "onset_date": r[2], "notes": r[3], "recorded_at": r[4]} for r in conditions],
        "phones": [{"phone": r[0], "label": r[1], "is_primary": r[2], "verified_at": r[3]} for r in phones],
        "medicines": [{"name": r[0], "dose": r[1], "frequency": r[2], "start_date": r[3],
            "end_date": r[4], "status": r[5], "recorded_at": r[6]} for r in medicines],
        "surgeries": [{"name": r[0], "date": r[1], "notes": r[2], "recorded_at": r[3]} for r in surgeries],
        "habits": [{"type": r[0], "details": r[1], "status": r[2], "recorded_at": r[3]} for r in habits],
        "family_history": [{"relative": r[0], "condition": r[1], "recorded_at": r[2]} for r in family_history],
        "medical_notes": [{"note": r[0], "recorded_at": r[1], "author": r[2]} for r in medical_notes],
        "calls": [{"started_at": r[0], "outcome": r[1], "notes": r[2], "next_follow_up_at": r[3],
            "caller": r[4], "ticket_number": r[5]} for r in calls],
        "relationships": [{"type": r[0], "name": r[1], "phone": r[2], "notes": r[3]} for r in relationships],
        "consents": [{"type": r[0], "policy_version": r[1], "granted_at": r[2],
            "withdrawn_at": r[3], "source": r[4]} for r in consents],
        "messages": [{"channel": r[0], "direction": r[1], "message": r[2], "delivery_status": r[3],
            "occurred_at": r[4]} for r in messages],
        "tooth_chart": [{"tooth": r[0], "condition": r[1], "details": r[2], "recorded_at": r[3]} for r in tooth_chart],
        "dental_procedures": [{"name": r[0], "tooth": r[1], "performed_on": r[2], "details": r[3],
            "recorded_at": r[4]} for r in dental_procedures],
        "gum_assessments": [{"assessment": r[0], "recorded_at": r[1], "dentist": r[2]} for r in gum_assessments],
        "orthodontic_status": [{"status": r[0], "details": r[1], "recorded_at": r[2], "dentist": r[3]}
            for r in orthodontic_status],
        "visits": [{"id": str(r[0]), "appointment_id": str(r[1]) if r[1] else None, "complaint": r[2],
            "examination_findings": r[3], "treatment_plan": r[4], "recorded_at": r[5], "dentist": r[6],
            "diagnoses": r[7], "treatments": r[8]} for r in visits],
        "appointments": [{"id": str(r[0]), "ticket_number": r[1], "reason": r[2], "preferred_date": r[3],
            "preferred_time_of_day": r[4], "start_at": r[5], "end_at": r[6], "status": r[7],
            "source": r[8], "created_at": r[9], "dentist": r[10], "chair": r[11]} for r in appointments],
        "appointment_events": [{"ticket_number": r[0], "event": r[1], "old_status": r[2], "new_status": r[3],
            "details": r[4], "occurred_at": r[5]} for r in appointment_events],
        "followups": [{"scheduled_at": r[0], "attempt_number": r[1], "status": r[2], "ticket_number": r[3]} for r in followups],
        "reminders": [{"type": r[0], "due_at": r[1], "status": r[2], "ticket_number": r[3]} for r in reminders],
        "prescriptions": [{"id": str(r[0]), "created_at": r[1], "status": r[2], "dentist": r[3], "items": r[4]} for r in prescriptions],
        "documents": [{"id": str(r[0]), "original_name": r[1], "mime_type": r[2], "size_bytes": r[3],
            "document_type": r[4], "uploaded_at": r[5], "url": f"/api/documents/{r[0]}"} for r in documents],
        "payments": ([{"currency": row[0], "total_minor": row[1], "paid_minor": row[2],
                       "outstanding_minor": row[3]} for row in payments]
                     if payments_allowed else None),
        "invoices": ([{"id": str(r[0]), "status": r[1], "currency": r[2], "created_at": r[3],
            "total_minor": r[4], "paid_minor": r[5], "outstanding_minor": r[6]} for r in invoices]
            if payments_allowed else None),
        "payment_details": ([{"id": str(r[0]), "invoice_id": str(r[1]), "amount_minor": r[2],
            "currency": r[3], "method": r[4], "status": r[5], "received_at": r[6]} for r in payment_details]
            if payments_allowed else None),
        "payments_permitted": payments_allowed,
        "whatsapp_card_consent": connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM consents
                WHERE patient_id = %s AND consent_type = 'whatsapp_card' AND withdrawn_at IS NULL
            )
            """,
            (patient_id,),
        ).fetchone()[0],
        "cards": [{"id": str(r[0]), "version": r[1], "status": r[2], "issued_at": r[3],
            "reason": r[4], "print_count": r[5], "sha256": r[6]} for r in cards],
        "notice": notice,
    }


@router.get("/patients/by-op/{op_id}")
def patient_file_by_op(
    op_id: str,
    user: RecordStaff,
    connection: Db,
    source: Literal["view", "scan"] = Query(default="view"),
):
    if not is_valid_op_id(op_id):
        raise HTTPException(422, "This ID is not valid, please check the last digit")
    row = connection.execute(
        """
        SELECT id, merged_into FROM patients
        WHERE organisation_id = %s AND op_id = %s
        """,
        (user.organisation_id, op_id.upper()),
    ).fetchone()
    if not row:
        raise HTTPException(404, "No patient was found for this OP ID.")
    requested_patient_id, merged_into = row
    patient_id = requested_patient_id
    notice = None
    if merged_into:
        patient_id = merged_into
        notice = f"This OP ID was merged. The patient file for the surviving record is shown; the original ID remains reserved."
    else:
        superseded = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM patient_cards WHERE patient_id = %s AND status = 'superseded')",
            (patient_id,),
        ).fetchone()[0]
        if superseded:
            notice = "A previous card version has been superseded. This OP ID still opens the same patient file."
    with transaction(connection, str(user.id)):
        if patient_id != requested_patient_id:
            _access(connection, user, requested_patient_id, source)
        _access(connection, user, patient_id, source)
    return _full_patient_file(connection, patient_id, user, notice)


@router.patch("/patients/{patient_id}/medical-profile")
def update_medical_profile(
    patient_id: uuid.UUID,
    payload: MedicalProfileInput,
    user: ProfileStaff,
    connection: Db,
):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(422, "Provide at least one medical profile field to update.")
    if "pregnancy_status" in values and values["pregnancy_status"] is None:
        values["pregnancy_status"] = "not_recorded"
    for key, value in values.items():
        if key != "pregnancy_status" and value is None:
            values[key] = ""

    with transaction(connection, str(user.id)):
        patient = connection.execute(
            """
            SELECT 1 FROM patients
            WHERE id = %s AND organisation_id = %s
              AND soft_deleted_at IS NULL AND merged_into IS NULL
            """,
            (patient_id, user.organisation_id),
        ).fetchone()
        if not patient:
            raise HTTPException(404, "Patient was not found.")
        connection.execute(
            """
            INSERT INTO patient_medical_profile (patient_id)
            VALUES (%s)
            ON CONFLICT (patient_id) DO NOTHING
            """,
            (patient_id,),
        )
        assignments = ", ".join(f"{field} = %s" for field in values)
        connection.execute(
            f"""
            UPDATE patient_medical_profile
            SET {assignments}, updated_at = now()
            WHERE patient_id = %s
            """,
            (*values.values(), patient_id),
        )
    return {"ok": True, "message": "Medical profile updated."}


def _card_path(connection: Connection, user: UserInfo, op_id: str, extension: str):
    if not is_valid_op_id(op_id):
        raise HTTPException(422, "This ID is not valid, please check the last digit")
    row = connection.execute(
        """
        SELECT p.id, c.pdf_key, c.png_key, c.status, p.merged_into
        FROM patients p JOIN patient_cards c ON c.patient_id = p.id
        WHERE p.organisation_id = %s AND p.op_id = %s
        ORDER BY c.version DESC LIMIT 1
        """,
        (user.organisation_id, op_id.upper()),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Patient card not found.")
    patient_id, pdf_key, png_key, card_status, merged_into = row
    if merged_into:
        patient_id = merged_into
    if card_status == "pending" or not (pdf_key if extension == "pdf" else png_key):
        raise HTTPException(409, "This patient card is still being prepared. Try again shortly.")
    if card_status != "active":
        raise HTTPException(409, "This patient card is stale or superseded. Reissue it before printing or sharing.")
    settings = get_settings()
    path = (settings.storage_dir / (pdf_key if extension == "pdf" else png_key)).resolve()
    if not path.is_relative_to(settings.storage_dir.resolve()) or not path.is_file():
        raise HTTPException(404, "The patient card file is not available.")
    with transaction(connection, str(user.id)):
        _access(connection, user, patient_id, "print" if extension == "pdf" else "view")
        if extension == "pdf":
            connection.execute(
                "UPDATE patient_cards SET print_count = print_count + 1 WHERE pdf_key = %s",
                (pdf_key,),
            )
    return path


@router.get("/cards/{op_id}.pdf")
def download_patient_card_pdf(op_id: str, user: RecordStaff, connection: Db):
    path = _card_path(connection, user, op_id, "pdf")
    return FileResponse(path, media_type="application/pdf", filename=f"{op_id}.pdf")


@router.get("/cards/{op_id}.png")
def download_patient_card_png(op_id: str, user: RecordStaff, connection: Db):
    path = _card_path(connection, user, op_id, "png")
    return FileResponse(path, media_type="image/png", filename=f"{op_id}.png")


@router.post("/patients/{patient_id}/card/reissue")
def reissue_patient_card(
    patient_id: uuid.UUID,
    payload: ReissueInput,
    user: ReceptionStaff,
    connection: Db,
):
    card_id = None
    with transaction(connection, str(user.id)):
        patient = connection.execute(
            "SELECT op_id FROM patients WHERE id = %s AND organisation_id = %s AND soft_deleted_at IS NULL",
            (patient_id, user.organisation_id),
        ).fetchone()
        if not patient:
            raise HTTPException(404, "Patient not found.")
        connection.execute(
            """
            UPDATE patient_cards SET status = 'superseded'
            WHERE patient_id = %s AND status IN ('active','stale')
            """,
            (patient_id,),
        )
        card = _create_card(connection, patient_id, patient[0], user.id, payload.reason)
        card_id = card[0]
    _render_card_with_connection(connection, str(card_id), str(patient_id))
    card_status = connection.execute("SELECT status FROM patient_cards WHERE id = %s", (card_id,)).fetchone()[0]
    return {
        "id": str(card_id), "op_id": patient[0], "version": card[1], "status": card_status,
        "pdf_url": f"/api/cards/{patient[0]}.pdf", "png_url": f"/api/cards/{patient[0]}.png",
    }


@router.post("/patients/{patient_id}/card/consent")
def set_card_consent(
    patient_id: uuid.UUID,
    payload: CardConsentInput,
    user: ReceptionStaff,
    connection: Db,
):
    with transaction(connection, str(user.id)):
        if not connection.execute(
            "SELECT 1 FROM patients WHERE id = %s AND organisation_id = %s AND soft_deleted_at IS NULL",
            (patient_id, user.organisation_id),
        ).fetchone():
            raise HTTPException(404, "Patient not found.")
        if payload.granted:
            connection.execute(
                """
                INSERT INTO consents (patient_id, consent_type, policy_version, captured_by, source)
                VALUES (%s, 'whatsapp_card', %s, %s, 'staff')
                """,
                (patient_id, payload.policy_version, user.id),
            )
        else:
            connection.execute(
                """
                UPDATE consents SET withdrawn_at = now()
                WHERE patient_id = %s AND consent_type = 'whatsapp_card' AND withdrawn_at IS NULL
                """,
                (patient_id,),
            )
    return {"granted": payload.granted}


@router.post("/patients/{patient_id}/card/send-whatsapp", status_code=202)
def send_patient_card_whatsapp(patient_id: uuid.UUID, user: ReceptionStaff, connection: Db):
    with transaction(connection, str(user.id)):
        patient = connection.execute(
            """
            SELECT p.op_id, pp.phone_e164, c.png_key
            FROM patients p
            JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
            JOIN patient_cards c ON c.patient_id = p.id AND c.status = 'active'
            WHERE p.id = %s AND p.organisation_id = %s AND p.soft_deleted_at IS NULL
            ORDER BY c.version DESC LIMIT 1
            """,
            (patient_id, user.organisation_id),
        ).fetchone()
        if not patient:
            raise HTTPException(409, "An active card and verified mobile number are required.")
        consent = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM consents
                WHERE patient_id = %s AND consent_type = 'whatsapp_card' AND withdrawn_at IS NULL
            )
            """,
            (patient_id,),
        ).fetchone()[0]
        if not consent:
            raise HTTPException(409, "Record the patient's WhatsApp card consent before sending.")
        _access(connection, user, patient_id, "send")
    from messaging import ProviderError, send_whatsapp_card_template
    card_path = get_settings().storage_dir / patient[2]
    try:
        provider_id = send_whatsapp_card_template(patient[1], card_path)
    except ProviderError as exc:
        logger.warning("Patient card delivery failed for patient record %s.", str(patient_id))
        raise HTTPException(502, "The card could not be sent. Check the WhatsApp provider configuration and try again.") from exc
    with transaction(connection, str(user.id)):
        connection.execute(
            """
            INSERT INTO communication_log
                (patient_id, phone_e164, channel, direction, provider_message_id, message_text, delivery_status)
            VALUES (%s, %s, 'whatsapp', 'outbound', %s, '[patient card template]', 'accepted')
            """,
            (patient_id, patient[1], None if provider_id == "simulator" else provider_id),
        )
    return {"ok": True, "delivery": "queued" if provider_id != "simulator" else "simulator"}


@router.post("/patients/{patient_id}/merge")
def merge_patient(
    patient_id: uuid.UUID,
    payload: MergeInput,
    user: AdminStaff,
    connection: Db,
):
    if patient_id == payload.surviving_patient_id:
        raise HTTPException(422, "Choose a different surviving patient.")
    with transaction(connection, str(user.id)):
        patients = connection.execute(
            """
            SELECT id, organisation_id, merged_into FROM patients
            WHERE id = ANY(%s) FOR UPDATE
            """,
            ([patient_id, payload.surviving_patient_id],),
        ).fetchall()
        if len(patients) != 2 or any(str(row[1]) != user.organisation_id for row in patients):
            raise HTTPException(404, "Both patient records must exist in your clinic.")
        duplicate = next(row for row in patients if row[0] == patient_id)
        survivor = next(row for row in patients if row[0] == payload.surviving_patient_id)
        if duplicate[2] or survivor[2]:
            raise HTTPException(409, "Resolve earlier patient merges before merging these records.")
        connection.execute(
            "UPDATE patients SET merged_into = %s, status = 'merged', updated_at = now() WHERE id = %s",
            (payload.surviving_patient_id, patient_id),
        )
        _access(connection, user, patient_id, "view")
    return {"merged_op_id": connection.execute(
        "SELECT op_id FROM patients WHERE id = %s", (patient_id,)
    ).fetchone()[0], "surviving_patient_id": str(payload.surviving_patient_id)}


@router.get("/patients/{patient_id}/card-history")
def card_history(patient_id: uuid.UUID, user: ReceptionStaff, connection: Db):
    rows = connection.execute(
        """
        SELECT c.id, c.version, c.status, c.issued_at, c.issue_reason, c.print_count, c.sha256
        FROM patient_cards c JOIN patients p ON p.id = c.patient_id
        WHERE c.patient_id = %s AND p.organisation_id = %s ORDER BY c.version DESC
        """,
        (patient_id, user.organisation_id),
    ).fetchall()
    return [{"id": str(r[0]), "version": r[1], "status": r[2], "issued_at": r[3],
             "reason": r[4], "print_count": r[5], "sha256": r[6]} for r in rows]

