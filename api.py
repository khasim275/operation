from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo
from datetime import timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from psycopg import Connection, errors

from config import get_settings
from db import audit_view, get_db, transaction
from patient_cards import _access
from models import (
    AppointmentRequest,
    AppointmentUpdate,
    AllergyInput,
    CallOutcomeInput,
    ConsentInput,
    InvoiceInput,
    MedicalNoteInput,
    MessageInput,
    PatientMedicineInput,
    PaymentInput,
    PatientUpdate,
    PrivacyRequestInput,
    PrivacyRequestUpdate,
    PrescriptionInput,
    UserCreate,
    UserStatusUpdate,
    VisitInput,
)
from security import UserInfo, current_user, require_roles, hash_password
from workers import enqueue_message

router = APIRouter(prefix="/api")
Staff = Annotated[UserInfo, Depends(require_roles("admin", "telecaller", "receptionist", "dentist"))]
ClinicalStaff = Annotated[UserInfo, Depends(require_roles("admin", "dentist"))]
Dentist = Annotated[UserInfo, Depends(require_roles("dentist"))]
CallStaff = Annotated[UserInfo, Depends(require_roles("admin", "telecaller", "receptionist"))]
BillingStaff = Annotated[UserInfo, Depends(require_roles("admin", "accountant"))]
Db = Annotated[Connection, Depends(get_db)]
PortalStaff = Annotated[UserInfo, Depends(current_user)]

PATIENT_ROLES = ("admin", "telecaller", "receptionist", "dentist")


def _branch_filter(user: UserInfo, alias: str = "a") -> tuple[str, list]:
    if user.branch_id:
        return f" AND {alias}.branch_id = %s", [user.branch_id]
    return "", []


def _appointment_in_scope(connection: Connection, appointment_id: str, user: UserInfo):
    branch_clause = " AND a.branch_id = %s" if user.branch_id else ""
    params = [appointment_id, user.organisation_id]
    if user.branch_id:
        params.append(user.branch_id)
    return connection.execute(
        f"""
        SELECT a.id, a.ticket_number, a.patient_id, a.branch_id, a.reason, a.preferred_date,
               a.preferred_time_of_day, a.start_at, a.end_at, a.status, a.status_note,
               a.source, a.created_at, p.full_name, pp.phone_e164, ac.opid
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        LEFT JOIN appointment_cards ac ON ac.appointment_id = a.id
        JOIN branches b ON b.id = a.branch_id
        WHERE a.id = %s AND b.organisation_id = %s AND a.soft_deleted_at IS NULL
        {branch_clause}
        """,
        params,
    ).fetchone()


def _patient_in_scope(connection: Connection, patient_id: str, user: UserInfo):
    return connection.execute(
        """
        SELECT id, patient_number, full_name, email, date_of_birth, gender, occupation,
               preferred_language, status, created_at
        FROM patients
        WHERE id = %s AND organisation_id = %s AND soft_deleted_at IS NULL
        """,
        (patient_id, user.organisation_id),
    ).fetchone()


def _patient_dict(row) -> dict:
    return {
        "id": str(row[0]),
        "patient_number": row[1],
        "full_name": row[2],
        "email": row[3],
        "date_of_birth": row[4],
        "gender": row[5],
        "occupation": row[6],
        "preferred_language": row[7],
        "status": row[8],
        "created_at": row[9],
    }


@router.get("/me")
def me(user: Staff):
    return user


@router.get("/dashboard")
def dashboard(user: Staff, connection: Db):
    branch_clause, branch_params = _branch_filter(user, "a")
    today = connection.execute(
        "SELECT (now() AT TIME ZONE %s)::date",
        (get_settings().clinic_timezone,),
    ).fetchone()[0]
    count_today = connection.execute(
        f"""
        SELECT count(*) FROM appointments a JOIN branches b ON b.id = a.branch_id
        WHERE b.organisation_id = %s AND a.start_at >= (%s::date::timestamp AT TIME ZONE %s)
          AND a.start_at < ((%s::date + 1)::timestamp AT TIME ZONE %s)
          AND a.status IN ('confirmed','rescheduled','checked_in')
          {branch_clause}
        """,
        [user.organisation_id, today, get_settings().clinic_timezone, today, get_settings().clinic_timezone, *branch_params],
    ).fetchone()[0]
    count_requests = connection.execute(
        f"""
        SELECT count(*) FROM appointments a JOIN branches b ON b.id = a.branch_id
        WHERE b.organisation_id = %s AND a.status = 'requested' AND a.soft_deleted_at IS NULL
        {branch_clause}
        """,
        [user.organisation_id, *branch_params],
    ).fetchone()[0]
    followups = connection.execute(
        f"""
        SELECT f.id, f.scheduled_at, f.attempt_number, a.id, a.ticket_number, a.reason,
               p.full_name, pp.phone_e164
        FROM call_followups f
        JOIN appointments a ON a.id = f.appointment_id
        JOIN patients p ON p.id = a.patient_id
        JOIN branches b ON b.id = a.branch_id
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        WHERE b.organisation_id = %s AND f.status = 'pending'
          AND (f.scheduled_at AT TIME ZONE %s)::date = %s
          {branch_clause.replace("a.branch_id", "a.branch_id")}
        ORDER BY f.scheduled_at LIMIT 100
        """,
        [user.organisation_id, get_settings().clinic_timezone, today, *branch_params],
    ).fetchall()
    callback_count = connection.execute(
        f"""
        SELECT count(*) FROM call_followups f
        JOIN appointments a ON a.id = f.appointment_id
        JOIN branches b ON b.id = a.branch_id
        WHERE b.organisation_id = %s AND f.status = 'pending'
          AND (f.scheduled_at AT TIME ZONE %s)::date = %s
          {branch_clause}
        """,
        [user.organisation_id, get_settings().clinic_timezone, today, *branch_params],
    ).fetchone()[0]
    appointments = connection.execute(
        f"""
        SELECT a.id, a.ticket_number, a.start_at, a.reason, a.status, p.full_name,
               pp.phone_e164, u.full_name, ac.opid
        FROM appointments a JOIN patients p ON p.id = a.patient_id
        JOIN branches b ON b.id = a.branch_id
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        LEFT JOIN users u ON u.id = a.dentist_id
        LEFT JOIN appointment_cards ac ON ac.appointment_id = a.id
        WHERE b.organisation_id = %s AND a.start_at >= (%s::date::timestamp AT TIME ZONE %s)
          AND a.start_at < ((%s::date + 1)::timestamp AT TIME ZONE %s)
          AND a.status IN ('confirmed','rescheduled','checked_in')
          {branch_clause}
        ORDER BY a.start_at LIMIT 50
        """,
        [user.organisation_id, today, get_settings().clinic_timezone, today, get_settings().clinic_timezone, *branch_params],
    ).fetchall()
    requests = connection.execute(
        f"""
        SELECT a.id, a.ticket_number, a.created_at, a.reason, a.preferred_date, a.preferred_time_of_day,
               a.source, p.full_name, pp.phone_e164, a.status, ac.opid
        FROM appointments a JOIN patients p ON p.id = a.patient_id
        JOIN branches b ON b.id = a.branch_id
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        LEFT JOIN appointment_cards ac ON ac.appointment_id = a.id
        WHERE b.organisation_id = %s AND a.status = 'requested' AND a.soft_deleted_at IS NULL
        {branch_clause}
        ORDER BY a.created_at LIMIT 100
        """,
        [user.organisation_id, *branch_params],
    ).fetchall()
    return {
        "today": today,
        "metrics": {"appointments_today": count_today, "requests_awaiting_call": count_requests,
                    "callbacks_today": callback_count},
        "appointments": [
            {"id": str(r[0]), "ticket_number": r[1], "start_at": r[2], "reason": r[3], "status": r[4],
             "patient_name": r[5], "phone": r[6], "dentist": r[7], "op_id": r[8]}
            for r in appointments
        ],
        "requests": [
            {"id": str(r[0]), "ticket_number": r[1], "created_at": r[2], "reason": r[3],
             "preferred_date": r[4], "preferred_time_of_day": r[5], "source": r[6],
             "patient_name": r[7], "phone": r[8], "status": r[9], "op_id": r[10] or r[1]}
            for r in requests
        ],
        "followups": [
            {"id": str(r[0]), "scheduled_at": r[1], "attempt_number": r[2], "appointment_id": str(r[3]),
             "ticket_number": r[4], "reason": r[5], "patient_name": r[6], "phone": r[7]}
            for r in followups
        ],
    }


@router.get("/patients/search")
def search_patients(
    user: Staff,
    connection: Db,
    q: str = Query(min_length=2, max_length=120),
):
    term = q.strip()
    phone_term = re.sub(r"\D", "", term)
    if len(term) < 2:
        raise HTTPException(422, "Enter at least 2 characters.")
    rows = connection.execute(
        """
        SELECT p.id, p.patient_number, p.full_name, pp.phone_e164, p.date_of_birth,
               p.preferred_language, p.created_at
        FROM patients p
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        WHERE p.organisation_id = %s AND p.soft_deleted_at IS NULL
          AND (
            p.full_name ILIKE %s OR p.email ILIKE %s
            OR p.search_vector @@ plainto_tsquery('simple', %s)
            OR (%s <> '' AND pp.phone_e164 LIKE %s)
          )
        ORDER BY CASE WHEN pp.phone_e164 = %s THEN 0 ELSE 1 END, p.full_name
        LIMIT 30
        """,
        (
            user.organisation_id,
            f"%{term}%",
            f"%{term}%",
            term,
            phone_term,
            f"%{phone_term}",
            "+" + phone_term,
        ),
    ).fetchall()
    return [
        {"id": str(r[0]), "patient_number": r[1], "full_name": r[2], "phone": r[3],
         "date_of_birth": r[4], "preferred_language": r[5], "created_at": r[6]}
        for r in rows
    ]


@router.get("/patients/{patient_id}")
def patient_profile(patient_id: uuid.UUID, user: Staff, connection: Db):
    row = _patient_in_scope(connection, str(patient_id), user)
    if not row:
        raise HTTPException(404, "Patient not found.")
    patient = _patient_dict(row)
    phones = connection.execute(
        "SELECT phone_e164, label, is_primary, verified_at FROM patient_phones WHERE patient_id = %s ORDER BY is_primary DESC, created_at",
        (patient_id,),
    ).fetchall()
    allergies = connection.execute(
        """
        SELECT allergen_text, reaction, severity, status, recorded_at
        FROM patient_allergies WHERE patient_id = %s ORDER BY
        CASE severity WHEN 'severe' THEN 0 WHEN 'moderate' THEN 1 ELSE 2 END, recorded_at DESC
        """,
        (patient_id,),
    ).fetchall()
    appointments = connection.execute(
        """
        SELECT id, ticket_number, reason, preferred_date, preferred_time_of_day, start_at, end_at,
               status, source, created_at
        FROM appointments WHERE patient_id = %s AND soft_deleted_at IS NULL
        ORDER BY coalesce(start_at, created_at) DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    visits = connection.execute(
        """
        SELECT v.id, v.appointment_id, v.complaint, v.examination_findings, v.treatment_plan,
               v.recorded_at, u.full_name
        FROM visits v JOIN users u ON u.id = v.dentist_id
        WHERE v.patient_id = %s ORDER BY v.recorded_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    documents = connection.execute(
        """
        SELECT id, original_name, mime_type, size_bytes, document_type, uploaded_at
        FROM documents WHERE patient_id = %s AND status = 'active'
        ORDER BY uploaded_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    calls = connection.execute(
        """
        SELECT c.id, c.appointment_id, c.started_at, c.outcome, c.notes, u.full_name
        FROM call_attempts c JOIN users u ON u.id = c.caller_id
        JOIN appointments a ON a.id = c.appointment_id
        WHERE a.patient_id = %s ORDER BY c.started_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    invoices = connection.execute(
        """
        SELECT i.id, i.status, i.currency, i.created_at, b.total_minor, b.paid_minor, b.outstanding_minor
        FROM invoices i JOIN invoice_balances b ON b.invoice_id = i.id
        WHERE i.patient_id = %s ORDER BY i.created_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    medical_notes = connection.execute(
        """
        SELECT n.note_text, n.recorded_at, u.full_name
        FROM medical_notes n JOIN users u ON u.id = n.author_id
        WHERE n.patient_id = %s ORDER BY n.recorded_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    medicines = connection.execute(
        """
        SELECT medicine_name, dose, frequency, start_date, end_date, status, recorded_at
        FROM patient_medicines WHERE patient_id = %s ORDER BY recorded_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    prescriptions = connection.execute(
        """
        SELECT p.id, p.created_at, p.status, u.full_name,
               coalesce(json_agg(json_build_object(
                   'medicine_name', pi.medicine_name, 'dose', pi.dose,
                   'frequency', pi.frequency, 'duration', pi.duration,
                   'instructions', pi.instructions
               )) FILTER (WHERE pi.id IS NOT NULL), '[]'::json) AS items
        FROM prescriptions p JOIN users u ON u.id = p.dentist_id
        LEFT JOIN prescription_items pi ON pi.prescription_id = p.id
        WHERE p.patient_id = %s
        GROUP BY p.id, u.full_name
        ORDER BY p.created_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    _access(connection, user, patient_id, "view")
    return {
        "patient": patient,
        "phones": [{"phone": r[0], "label": r[1], "is_primary": r[2], "verified_at": r[3]} for r in phones],
        "allergies": [
            {"allergen": r[0], "reaction": r[1], "severity": r[2], "status": r[3], "recorded_at": r[4]}
            for r in allergies
        ],
        "appointments": [
            {"id": str(r[0]), "ticket_number": r[1], "reason": r[2], "preferred_date": r[3],
             "preferred_time_of_day": r[4], "start_at": r[5], "end_at": r[6], "status": r[7],
             "source": r[8], "created_at": r[9]}
            for r in appointments
        ],
        "visits": [
            {"id": str(r[0]), "appointment_id": str(r[1]) if r[1] else None, "complaint": r[2],
             "examination_findings": r[3], "treatment_plan": r[4], "recorded_at": r[5], "dentist": r[6]}
            for r in visits
        ],
        "calls": [
            {"id": str(r[0]), "appointment_id": str(r[1]), "started_at": r[2], "outcome": r[3],
             "notes": r[4], "caller": r[5]}
            for r in calls
        ],
        "documents": [
            {"id": str(r[0]), "original_name": r[1], "mime_type": r[2], "size_bytes": r[3],
             "document_type": r[4], "uploaded_at": r[5]}
            for r in documents
        ],
        "invoices": [
            {"id": str(r[0]), "status": r[1], "currency": r[2], "created_at": r[3],
             "total_minor": r[4], "paid_minor": r[5], "outstanding_minor": r[6]}
            for r in invoices
        ],
        "medical_notes": [
            {"note": r[0], "recorded_at": r[1], "author": r[2]} for r in medical_notes
        ],
        "medicines": [
            {"medicine_name": r[0], "dose": r[1], "frequency": r[2], "start_date": r[3],
             "end_date": r[4], "status": r[5], "recorded_at": r[6]}
            for r in medicines
        ],
        "prescriptions": [
            {"id": str(r[0]), "created_at": r[1], "status": r[2], "dentist": r[3], "items": r[4]}
            for r in prescriptions
        ],
    }


@router.post("/patients/{patient_id}/medical-notes", status_code=201)
def add_medical_note(patient_id: uuid.UUID, payload: MedicalNoteInput, user: ClinicalStaff, connection: Db):
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    with transaction(connection, str(user.id)):
        note_id = connection.execute(
            "INSERT INTO medical_notes (patient_id, author_id, note_text) VALUES (%s, %s, %s) RETURNING id",
            (patient_id, user.id, payload.note_text.strip()),
        ).fetchone()[0]
    return {"id": str(note_id)}


@router.post("/patients/{patient_id}/medicines", status_code=201)
def add_patient_medicine(
    patient_id: uuid.UUID,
    payload: PatientMedicineInput,
    user: ClinicalStaff,
    connection: Db,
):
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    with transaction(connection, str(user.id)):
        medicine_id = connection.execute(
            """
            INSERT INTO patient_medicines (patient_id, medicine_name, dose, frequency, start_date, end_date, recorded_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (patient_id, payload.medicine_name.strip(), payload.dose, payload.frequency,
             payload.start_date, payload.end_date, user.id),
        ).fetchone()[0]
    return {"id": str(medicine_id)}


@router.post("/patients/{patient_id}/allergies", status_code=201)
def add_patient_allergy(patient_id: uuid.UUID, payload: AllergyInput, user: ClinicalStaff, connection: Db):
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    with transaction(connection, str(user.id)):
        allergy_id = connection.execute(
            """
            INSERT INTO patient_allergies (patient_id, allergen_text, reaction, severity, recorded_by)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (patient_id, payload.allergen_text.strip(), payload.reaction, payload.severity, user.id),
        ).fetchone()[0]
    return {"id": str(allergy_id)}


@router.patch("/patients/{patient_id}")
def update_patient(patient_id: uuid.UUID, payload: PatientUpdate, user: Staff, connection: Db):
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    address_fields = {"address_line1", "address_line2", "city", "state", "postal_code"}
    address_values = {key: values.pop(key) for key in list(values) if key in address_fields}
    if not values and not address_values:
        raise HTTPException(422, "Provide at least one patient field to update.")
    if "full_name" in values:
        values["full_name"] = values["full_name"].strip()
        if not values["full_name"]:
            raise HTTPException(422, "Patient name cannot be blank.")
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    with transaction(connection, str(user.id)):
        if values:
            assignments = ", ".join(f"{key} = %s" for key in values)
            connection.execute(
                f"UPDATE patients SET {assignments}, updated_at = now() WHERE id = %s",
                [*values.values(), patient_id],
            )
        if address_values:
            current = connection.execute(
                """
                SELECT line1, line2, city, state, postal_code FROM patient_addresses
                WHERE patient_id = %s AND is_current ORDER BY created_at DESC LIMIT 1
                """,
                (patient_id,),
            ).fetchone()
            address = {
                "line1": current[0] if current else "(not provided)",
                "line2": current[1] if current else None,
                "city": current[2] if current else "(not provided)",
                "state": current[3] if current else None,
                "postal_code": current[4] if current else None,
            }
            mapping = {
                "address_line1": "line1",
                "address_line2": "line2",
                "city": "city",
                "state": "state",
                "postal_code": "postal_code",
            }
            for key, value in address_values.items():
                address[mapping[key]] = value.strip() or None
            if not address["line1"]:
                address["line1"] = "(not provided)"
            if not address["city"]:
                address["city"] = "(not provided)"
            connection.execute(
                "UPDATE patient_addresses SET is_current = false WHERE patient_id = %s AND is_current",
                (patient_id,),
            )
            connection.execute(
                """
                INSERT INTO patient_addresses (patient_id, line1, line2, city, state, postal_code)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (patient_id, address["line1"], address["line2"], address["city"], address["state"], address["postal_code"]),
            )
    return {"ok": True}


@router.get("/appointments")
def appointments(
    user: Staff,
    connection: Db,
    day: date = Query(default_factory=date.today),
):
    branch_clause, branch_params = _branch_filter(user)
    rows = connection.execute(
        f"""
        SELECT a.id, a.ticket_number, a.start_at, a.end_at, a.reason, a.status, a.source,
               p.id, p.full_name, pp.phone_e164, u.full_name, r.name, ac.opid
        FROM appointments a JOIN patients p ON p.id = a.patient_id
        JOIN branches b ON b.id = a.branch_id
        LEFT JOIN patient_phones pp ON pp.patient_id = p.id AND pp.is_primary
        LEFT JOIN users u ON u.id = a.dentist_id
        LEFT JOIN rooms r ON r.id = a.room_id
        LEFT JOIN appointment_cards ac ON ac.appointment_id = a.id
        WHERE b.organisation_id = %s AND a.soft_deleted_at IS NULL
          AND ((a.start_at AT TIME ZONE %s)::date = %s OR (a.status = 'requested' AND a.preferred_date = %s))
          {branch_clause}
        ORDER BY a.start_at NULLS LAST, a.created_at
        """,
        [user.organisation_id, get_settings().clinic_timezone, day, day, *branch_params],
    ).fetchall()
    return [
        {"id": str(r[0]), "ticket_number": r[1], "start_at": r[2], "end_at": r[3], "reason": r[4],
         "status": r[5], "source": r[6], "patient_id": str(r[7]), "patient_name": r[8],
         "phone": r[9], "dentist": r[10], "room": r[11], "op_id": r[12] or r[1]}
        for r in rows
    ]


@router.post("/appointments", status_code=201)
def create_appointment(payload: AppointmentRequest, user: CallStaff, connection: Db):
    patient = _patient_in_scope(connection, str(payload.patient_id), user)
    if not patient:
        raise HTTPException(404, "Patient not found.")
    branch_id = user.branch_id
    if not branch_id:
        branch_id = connection.execute(
            """
            SELECT id FROM branches WHERE organisation_id = %s AND is_active ORDER BY created_at LIMIT 1
            """,
            (user.organisation_id,),
        ).fetchone()
        branch_id = branch_id[0] if branch_id else None
    if not branch_id:
        raise HTTPException(409, "No active branch is configured.")
    with transaction(connection, str(user.id)):
        appointment_id, ticket_number = connection.execute(
            """
            INSERT INTO appointments (patient_id, branch_id, created_by, source, reason,
                                      preferred_date, preferred_time_of_day)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, ticket_number
            """,
            (payload.patient_id, branch_id, user.id, payload.source, payload.reason.strip(),
             payload.preferred_date, payload.preferred_time_of_day),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO appointment_events (appointment_id, actor_id, event_type, new_status, details)
            VALUES (%s, %s, 'request_created', 'requested', %s::jsonb)
            """,
            (appointment_id, user.id, json.dumps({"source": payload.source})),
        )
    return {"id": str(appointment_id), "ticket_number": ticket_number}


@router.get("/appointments/{appointment_id}")
def appointment_detail(appointment_id: uuid.UUID, user: Staff, connection: Db):
    row = _appointment_in_scope(connection, str(appointment_id), user)
    if not row:
        raise HTTPException(404, "Ticket not found.")
    patient_id = row[2]
    allergy_rows = connection.execute(
        """
        SELECT allergen_text, reaction, severity FROM patient_allergies
        WHERE patient_id = %s AND status = 'active'
        ORDER BY CASE severity WHEN 'severe' THEN 0 WHEN 'moderate' THEN 1 ELSE 2 END
        """,
        (patient_id,),
    ).fetchall()
    attempts = connection.execute(
        """
        SELECT c.started_at, c.duration_seconds, c.outcome, c.notes, c.next_follow_up_at, u.full_name
        FROM call_attempts c JOIN users u ON u.id = c.caller_id
        WHERE c.appointment_id = %s ORDER BY c.started_at DESC
        """,
        (appointment_id,),
    ).fetchall()
    events = connection.execute(
        "SELECT event_type, old_status, new_status, details, occurred_at FROM appointment_events WHERE appointment_id = %s ORDER BY occurred_at",
        (appointment_id,),
    ).fetchall()
    visits = connection.execute(
        """
        SELECT id, complaint, examination_findings, treatment_plan, recorded_at
        FROM visits WHERE patient_id = %s ORDER BY recorded_at DESC LIMIT 50
        """,
        (patient_id,),
    ).fetchall()
    messages = connection.execute(
        """
        SELECT c.direction, c.message_text, c.occurred_at, c.delivery_status
        FROM communication_log c
        JOIN patient_phones pp ON pp.phone_e164 = c.phone_e164
        WHERE pp.patient_id = %s AND c.channel = 'whatsapp'
        ORDER BY c.occurred_at DESC LIMIT 100
        """,
        (patient_id,),
    ).fetchall()
    audit_view(connection, str(user.id), "appointment", str(appointment_id))
    return {
        "ticket": {
            "id": str(row[0]), "ticket_number": row[1], "patient_id": str(row[2]), "branch_id": str(row[3]),
            "reason": row[4], "preferred_date": row[5], "preferred_time_of_day": row[6],
            "start_at": row[7], "end_at": row[8], "status": row[9], "status_note": row[10],
            "source": row[11], "created_at": row[12], "patient_name": row[13], "phone": row[14],
            "op_id": row[15],
        },
        "allergies": [{"allergen": a[0], "reaction": a[1], "severity": a[2]} for a in allergy_rows],
        "calls": [{"started_at": c[0], "duration_seconds": c[1], "outcome": c[2], "notes": c[3],
                   "next_follow_up_at": c[4], "caller": c[5]} for c in attempts],
        "events": [{"event_type": e[0], "old_status": e[1], "new_status": e[2], "details": e[3],
                    "occurred_at": e[4]} for e in events],
        "visits": [{"id": str(v[0]), "complaint": v[1], "examination_findings": v[2],
                    "treatment_plan": v[3], "recorded_at": v[4]} for v in visits],
        "messages": [
            {"direction": m[0], "message": m[1], "occurred_at": m[2], "delivery_status": m[3]}
            for m in reversed(messages)
        ],
    }


@router.post("/patients/{patient_id}/messages", status_code=202)
def send_patient_message(patient_id: uuid.UUID, payload: MessageInput, user: CallStaff, connection: Db):
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    phone = connection.execute(
        """
        SELECT phone_e164 FROM patient_phones
        WHERE patient_id = %s AND is_primary
        """,
        (patient_id,),
    ).fetchone()
    if not phone:
        raise HTTPException(409, "This patient has no primary mobile number.")
    last_inbound = connection.execute(
        """
        SELECT max(occurred_at)
        FROM communication_log
        WHERE phone_e164 = %s AND channel = 'whatsapp' AND direction = 'inbound'
        """,
        (phone[0],),
    ).fetchone()[0]
    if not last_inbound or last_inbound <= datetime.now(timezone.utc) - timedelta(hours=24):
        raise HTTPException(409, "The WhatsApp customer-service window has closed. Use an approved template instead.")
    with transaction(connection, str(user.id)):
        enqueue_message(
            connection,
            phone[0],
            "freeform",
            {"text": payload.message},
            patient_id,
            window_expires_at=last_inbound + timedelta(hours=24),
        )
    return {"queued": True}


@router.patch("/appointments/{appointment_id}")
def update_appointment(
    appointment_id: uuid.UUID,
    payload: AppointmentUpdate,
    user: CallStaff,
    connection: Db,
):
    row = _appointment_in_scope(connection, str(appointment_id), user)
    if not row:
        raise HTTPException(404, "Ticket not found.")
    with transaction(connection, str(user.id)):
        connection.execute(
            "UPDATE appointments SET status = %s, status_note = %s, updated_at = now() WHERE id = %s",
            (payload.status, payload.status_note, appointment_id),
        )
        connection.execute(
            """
            INSERT INTO appointment_events (appointment_id, actor_id, event_type, old_status, new_status, details)
            VALUES (%s, %s, 'status_changed', %s, %s, %s::jsonb)
            """,
            (appointment_id, user.id, row[9], payload.status, json.dumps({"note": payload.status_note})),
        )
        if payload.status in ("cancelled", "completed", "no_show"):
            connection.execute(
                "UPDATE reminder_jobs SET status = 'cancelled' WHERE appointment_id = %s AND status IN ('pending','processing')",
                (appointment_id,),
            )
    return {"ok": True}


@router.post("/appointments/{appointment_id}/call-outcome", status_code=201)
def record_call_outcome(
    appointment_id: uuid.UUID,
    payload: CallOutcomeInput,
    user: CallStaff,
    connection: Db,
):
    row = _appointment_in_scope(connection, str(appointment_id), user)
    if not row:
        raise HTTPException(404, "Ticket not found.")
    existing_calls = connection.execute(
        "SELECT count(*) FROM call_attempts WHERE appointment_id = %s",
        (appointment_id,),
    ).fetchone()[0]
    if payload.outcome == "confirmed":
        dentist = connection.execute(
            """
            SELECT id FROM users WHERE id = %s AND organisation_id = %s AND is_active
              AND EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                          WHERE ur.user_id = users.id AND r.name = 'dentist')
            """,
            (payload.dentist_id, user.organisation_id),
        ).fetchone()
        if not dentist:
            raise HTTPException(422, "Choose an active dentist in this clinic.")
        if payload.room_id:
            room = connection.execute(
                "SELECT 1 FROM rooms WHERE id = %s AND branch_id = %s AND is_active",
                (payload.room_id, row[3]),
            ).fetchone()
            if not room:
                raise HTTPException(422, "Choose an active room in your branch.")
        new_status = "confirmed"
        next_follow_up = None
    elif payload.outcome == "not_reachable":
        new_status = row[9]
        next_follow_up = payload.next_follow_up_at or datetime.now(ZoneInfo(get_settings().clinic_timezone)) + timedelta(hours=24)
        if next_follow_up.tzinfo is None:
            raise HTTPException(422, "Follow-up time must include a timezone.")
    elif payload.outcome == "reschedule":
        if payload.preferred_date < datetime.now(ZoneInfo(get_settings().clinic_timezone)).date():
            raise HTTPException(422, "The preferred date must be today or later.")
        new_status = "rescheduled"
        next_follow_up = None
    else:
        new_status = "cancelled"
        next_follow_up = None

    try:
        with transaction(connection, str(user.id)):
            connection.execute(
                """
                UPDATE call_followups SET status = 'completed', completed_at = now()
                WHERE appointment_id = %s AND status = 'pending'
                """,
                (appointment_id,),
            )
            call_id = connection.execute(
                """
                INSERT INTO call_attempts (appointment_id, caller_id, duration_seconds, outcome, notes, next_follow_up_at)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (appointment_id, user.id, payload.duration_seconds, payload.outcome, payload.notes, next_follow_up),
            ).fetchone()[0]
            if payload.outcome == "confirmed":
                dentist_name = connection.execute(
                    "SELECT full_name FROM users WHERE id = %s",
                    (payload.dentist_id,),
                ).fetchone()[0]
                connection.execute(
                    """
                    UPDATE appointments SET status = 'confirmed', dentist_id = %s, room_id = %s,
                        start_at = %s, end_at = %s, status_note = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (payload.dentist_id, payload.room_id, payload.start_at, payload.end_at, payload.notes, appointment_id),
                )
                connection.execute(
                    """
                    UPDATE reminder_jobs SET status = 'cancelled'
                    WHERE appointment_id = %s AND status IN ('pending','processing')
                    """,
                    (appointment_id,),
                )
                connection.execute(
                    """
                    INSERT INTO reminder_jobs (appointment_id, reminder_type, due_at)
                    SELECT %s, reminder_type, due_at
                    FROM (VALUES
                        ('appointment_24h', %s - interval '24 hours'),
                        ('appointment_2h', %s - interval '2 hours')
                    ) AS proposed(reminder_type, due_at)
                    WHERE due_at > now()
                    ON CONFLICT (appointment_id, reminder_type) DO UPDATE SET
                        due_at = EXCLUDED.due_at, status = 'pending', attempts = 0, last_error = NULL,
                        processed_at = NULL
                    """,
                    (appointment_id, payload.start_at, payload.start_at),
                )
                if row[14]:
                    local_start = payload.start_at.astimezone(ZoneInfo(get_settings().clinic_timezone))
                    enqueue_message(
                        connection,
                        row[14],
                        "confirmation",
                        {
                            "parameters": [
                                row[13],
                                row[1],
                                local_start.strftime("%d %b %Y"),
                                local_start.strftime("%I:%M %p"),
                                dentist_name,
                            ]
                        },
                        row[2],
                    )
            elif payload.outcome in ("reschedule", "cancelled"):
                if payload.outcome == "reschedule":
                    connection.execute(
                        """
                        UPDATE appointments SET status = %s, preferred_date = %s,
                            preferred_time_of_day = %s, status_note = %s, updated_at = now()
                        WHERE id = %s
                        """,
                        (new_status, payload.preferred_date, payload.preferred_time_of_day,
                         payload.notes, appointment_id),
                    )
                else:
                    connection.execute(
                        "UPDATE appointments SET status = %s, status_note = %s, updated_at = now() WHERE id = %s",
                        (new_status, payload.notes, appointment_id),
                    )
                connection.execute(
                    "UPDATE reminder_jobs SET status = 'cancelled' WHERE appointment_id = %s AND status IN ('pending','processing')",
                    (appointment_id,),
                )
            if payload.outcome == "not_reachable" and existing_calls + 1 < 3:
                connection.execute(
                    """
                    INSERT INTO call_followups (appointment_id, source_call_id, attempt_number, scheduled_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (appointment_id, attempt_number) DO NOTHING
                    """,
                    (appointment_id, call_id, existing_calls + 1, next_follow_up),
                )
            connection.execute(
                """
                INSERT INTO appointment_events (appointment_id, actor_id, event_type, old_status, new_status, details)
                VALUES (%s, %s, 'call_outcome', %s, %s, %s::jsonb)
                """,
                (appointment_id, user.id, row[9], new_status,
                 json.dumps({"outcome": payload.outcome, "call_id": str(call_id)})),
            )
    except errors.ExclusionViolation as exc:
        raise HTTPException(409, "The dentist or chair is already booked for that time.") from exc
    return {
        "ok": True,
        "appointment_status": new_status,
        "retry_scheduled": payload.outcome == "not_reachable" and existing_calls + 1 < 3,
    }


@router.get("/dentists")
def dentists(user: Staff, connection: Db):
    rows = connection.execute(
        """
        SELECT DISTINCT u.id, u.full_name
        FROM users u JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id
        WHERE u.organisation_id = %s AND u.is_active AND u.soft_deleted_at IS NULL AND r.name = 'dentist'
        ORDER BY u.full_name
        """,
        (user.organisation_id,),
    ).fetchall()
    return [{"id": str(row[0]), "full_name": row[1]} for row in rows]


@router.get("/rooms")
def rooms(user: Staff, connection: Db):
    if user.branch_id:
        rows = connection.execute(
            "SELECT id, name FROM rooms WHERE branch_id = %s AND is_active ORDER BY name",
            (user.branch_id,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT r.id, r.name FROM rooms r JOIN branches b ON b.id = r.branch_id
            WHERE b.organisation_id = %s AND r.is_active ORDER BY b.name, r.name
            """,
            (user.organisation_id,),
        ).fetchall()
    return [{"id": str(row[0]), "name": row[1]} for row in rows]


@router.post("/visits", status_code=201)
def create_visit(payload: VisitInput, user: ClinicalStaff, connection: Db):
    if not _patient_in_scope(connection, str(payload.patient_id), user):
        raise HTTPException(404, "Patient not found.")
    if payload.appointment_id:
        appointment = connection.execute(
            "SELECT patient_id FROM appointments WHERE id = %s AND patient_id = %s",
            (payload.appointment_id, payload.patient_id),
        ).fetchone()
        if not appointment:
            raise HTTPException(422, "The appointment does not belong to this patient.")
    branch_id = user.branch_id
    if not branch_id:
        branch_id = connection.execute(
            "SELECT id FROM branches WHERE organisation_id = %s AND is_active ORDER BY created_at LIMIT 1",
            (user.organisation_id,),
        ).fetchone()
        branch_id = branch_id[0] if branch_id else None
    if not branch_id:
        raise HTTPException(409, "No active branch is configured.")
    with transaction(connection, str(user.id)):
        visit_id = connection.execute(
            """
            INSERT INTO visits (patient_id, appointment_id, dentist_id, branch_id, complaint,
                                examination_findings, treatment_plan)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (payload.patient_id, payload.appointment_id, user.id, branch_id, payload.complaint.strip(),
             payload.examination_findings, payload.treatment_plan),
        ).fetchone()[0]
        for diagnosis in payload.diagnoses:
            if diagnosis.strip():
                connection.execute(
                    "INSERT INTO diagnoses (visit_id, description) VALUES (%s, %s)",
                    (visit_id, diagnosis.strip()),
                )
        for procedure in payload.procedures:
            connection.execute(
                """
                INSERT INTO performed_procedures (visit_id, procedure_name, fdi_tooth, surface, notes)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (visit_id, procedure.procedure_name.strip(), procedure.fdi_tooth, procedure.surface, procedure.notes),
            )
        if payload.appointment_id:
            connection.execute(
                "UPDATE appointments SET status = 'completed', updated_at = now() WHERE id = %s",
                (payload.appointment_id,),
            )
            connection.execute(
                "UPDATE reminder_jobs SET status = 'cancelled' WHERE appointment_id = %s AND status IN ('pending','processing')",
                (payload.appointment_id,),
            )
    return {"id": str(visit_id)}


@router.post("/prescriptions", status_code=201)
def create_prescription(payload: PrescriptionInput, user: Dentist, connection: Db):
    if not _patient_in_scope(connection, str(payload.patient_id), user):
        raise HTTPException(404, "Patient not found.")
    visit = connection.execute(
        "SELECT id FROM visits WHERE id = %s AND patient_id = %s",
        (payload.visit_id, payload.patient_id),
    ).fetchone()
    if not visit:
        raise HTTPException(422, "The visit does not belong to this patient.")
    with transaction(connection, str(user.id)):
        prescription_id = connection.execute(
            """
            INSERT INTO prescriptions (visit_id, patient_id, dentist_id)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (payload.visit_id, payload.patient_id, user.id),
        ).fetchone()[0]
        for item in payload.items:
            connection.execute(
                """
                INSERT INTO prescription_items (prescription_id, medicine_name, dose, frequency, duration, instructions)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (prescription_id, item.medicine_name.strip(), item.dose.strip(), item.frequency.strip(),
                 item.duration.strip(), item.instructions),
            )
    return {"id": str(prescription_id)}


def _detect_upload(content: bytes, claimed_type: str) -> tuple[str, str]:
    signatures = [
        (b"%PDF-", "application/pdf", "pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
        (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    ]
    for signature, mime, extension in signatures:
        if content.startswith(signature):
            if claimed_type not in (mime, "application/octet-stream"):
                raise HTTPException(415, "The file content does not match its declared type.")
            return mime, extension
    raise HTTPException(415, "Only PDF, JPEG, and PNG documents are accepted.")


@router.post("/patients/{patient_id}/documents", status_code=201)
async def upload_document(
    patient_id: uuid.UUID,
    user: PortalStaff,
    connection: Db,
    file: UploadFile = File(...),
    document_type: str = Query(pattern="^(xray|photo|report|consent|operation|operation_consent|operation_summary|other)$"),
    op_id: str | None = Query(default=None, min_length=8, max_length=24),
):
    if not set(user.roles).intersection({"admin", "dentist", "receptionist", "telecaller"}):
        raise HTTPException(403, "Staff access required.")
    if document_type in {"xray", "report", "operation", "operation_consent", "operation_summary"} and not set(user.roles).intersection({"admin", "dentist"}):
        raise HTTPException(403, "Only a dentist or administrator can upload clinical files.")
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    appointment_card_id = None
    if op_id:
        op = connection.execute(
            """
            SELECT c.id FROM appointment_cards c
            JOIN appointments a ON a.id = c.appointment_id
            JOIN branches b ON b.id = a.branch_id
            WHERE c.opid = upper(%s) AND c.patient_id = %s
              AND b.organisation_id = %s AND (%s::uuid IS NULL OR a.branch_id = %s::uuid)
            """,
            (op_id, patient_id, user.organisation_id, user.branch_id, user.branch_id),
        ).fetchone()
        if not op:
            raise HTTPException(404, "Appointment card not found for this patient.")
        appointment_card_id = op[0]
    elif document_type.startswith("operation"):
        raise HTTPException(422, "Operation documents must be linked to an OP ID.")
    content = await file.read(get_settings().max_upload_bytes + 1)
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(content) > get_settings().max_upload_bytes:
        raise HTTPException(413, "The file exceeds the configured upload limit.")
    mime_type, extension = _detect_upload(content, file.content_type or "application/octet-stream")
    branch_id = user.branch_id
    if not branch_id:
        branch_id = connection.execute(
            "SELECT id FROM branches WHERE organisation_id = %s AND is_active ORDER BY created_at LIMIT 1",
            (user.organisation_id,),
        ).fetchone()
        branch_id = branch_id[0] if branch_id else None
    if not branch_id:
        raise HTTPException(409, "No active branch is configured.")
    document_id = uuid.uuid4()
    storage_key = f"{document_id}.{extension}"
    storage_dir = get_settings().storage_dir.resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)
    target = storage_dir / storage_key
    persisted = False
    original_name = "".join(
        char for char in (file.filename or "upload").replace("\\", "/").split("/")[-1]
        if char.isprintable()
    )[:255] or "upload"
    file_created = False
    try:
        with target.open("xb") as stored_file:
            file_created = True
            stored_file.write(content)
        with transaction(connection, str(user.id)):
            connection.execute(
                """
                INSERT INTO documents (id, patient_id, branch_id, uploaded_by, storage_key, original_name,
                                       mime_type, size_bytes, sha256, document_type, appointment_card_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (document_id, patient_id, branch_id, user.id, storage_key,
                 original_name, mime_type, len(content),
                 hashlib.sha256(content).hexdigest(), document_type, appointment_card_id),
            )
            if appointment_card_id:
                file_ref = json.dumps({
                    "id": str(document_id), "name": original_name,
                    "type": document_type, "uploaded_at": datetime.now(timezone.utc).isoformat(),
                })
                column = "operation_files" if document_type.startswith("operation") else "medical_files"
                connection.execute(
                    f"UPDATE clinical_records_billing SET {column} = {column} || %s::jsonb, updated_at_ist=now() WHERE op_id=upper(%s)",
                    ("[" + file_ref + "]", op_id),
                )
        persisted = True
    finally:
        if not persisted and file_created and target.exists():
            target.unlink()
    return {"id": str(document_id), "original_name": original_name, "mime_type": mime_type}


@router.get("/documents/{document_id}")
def download_document(document_id: uuid.UUID, user: Staff, connection: Db):
    row = connection.execute(
        """
        SELECT d.patient_id, d.storage_key, d.original_name, d.mime_type, d.document_type
        FROM documents d JOIN patients p ON p.id = d.patient_id
        JOIN branches b ON b.id = d.branch_id
        WHERE d.id = %s AND p.organisation_id = %s AND p.soft_deleted_at IS NULL AND d.status = 'active'
          AND (%s::uuid IS NULL OR b.id = %s::uuid)
        """,
        (document_id, user.organisation_id, user.branch_id, user.branch_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Document not found.")
    if row[4] in {"xray", "report", "operation", "operation_consent", "operation_summary"} and not set(user.roles).intersection({"admin", "dentist"}):
        raise HTTPException(403, "Clinical file access is restricted to a dentist or administrator.")
    path = (get_settings().storage_dir.resolve() / row[1]).resolve()
    if path.parent != get_settings().storage_dir.resolve() or not path.is_file():
        raise HTTPException(404, "The stored document is unavailable.")
    _access(connection, user, row[0], "view")
    audit_view(connection, str(user.id), "document", str(document_id))
    return FileResponse(path, media_type=row[3], filename=row[2])


@router.get("/invoices")
def list_invoices(user: BillingStaff, connection: Db, patient_id: uuid.UUID | None = None):
    params: list = [user.organisation_id]
    patient_filter = ""
    if patient_id:
        patient_filter = " AND i.patient_id = %s"
        params.append(patient_id)
    if user.branch_id:
        patient_filter += " AND i.branch_id = %s"
        params.append(user.branch_id)
    rows = connection.execute(
        f"""
        SELECT i.id, i.patient_id, p.full_name, i.status, i.currency, i.created_at,
               b.total_minor, b.paid_minor, b.outstanding_minor
        FROM invoices i JOIN patients p ON p.id = i.patient_id
        JOIN branches br ON br.id = i.branch_id
        JOIN invoice_balances b ON b.invoice_id = i.id
        WHERE br.organisation_id = %s AND p.soft_deleted_at IS NULL {patient_filter}
        ORDER BY i.created_at DESC LIMIT 200
        """,
        params,
    ).fetchall()
    return [
        {"id": str(r[0]), "patient_id": str(r[1]), "patient_name": r[2], "status": r[3],
         "currency": r[4], "created_at": r[5], "total_minor": r[6], "paid_minor": r[7],
         "outstanding_minor": r[8]}
        for r in rows
    ]


@router.post("/invoices", status_code=201)
def create_invoice(payload: InvoiceInput, user: BillingStaff, connection: Db):
    patient = _patient_in_scope(connection, str(payload.patient_id), user)
    if not patient:
        raise HTTPException(404, "Patient not found.")
    if payload.visit_id and not connection.execute(
        "SELECT 1 FROM visits WHERE id = %s AND patient_id = %s",
        (payload.visit_id, payload.patient_id),
    ).fetchone():
        raise HTTPException(422, "The visit does not belong to this patient.")
    branch_id = user.branch_id
    if not branch_id:
        branch_id = connection.execute(
            "SELECT id FROM branches WHERE organisation_id = %s AND is_active ORDER BY created_at LIMIT 1",
            (user.organisation_id,),
        ).fetchone()
        branch_id = branch_id[0] if branch_id else None
    if not branch_id:
        raise HTTPException(409, "No active branch is configured.")
    for item in payload.items:
        if item.discount_minor > item.unit_amount_minor * item.quantity:
            raise HTTPException(422, "A line-item discount cannot exceed its line total.")
    with transaction(connection, str(user.id)):
        invoice_id = connection.execute(
            """
            INSERT INTO invoices (patient_id, visit_id, branch_id, status, issued_at, created_by)
            VALUES (%s, %s, %s, 'issued', now(), %s) RETURNING id
            """,
            (payload.patient_id, payload.visit_id, branch_id, user.id),
        ).fetchone()[0]
        for item in payload.items:
            connection.execute(
                """
                INSERT INTO invoice_items (invoice_id, description, quantity, unit_amount_minor, discount_minor)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (invoice_id, item.description.strip(), item.quantity, item.unit_amount_minor, item.discount_minor),
            )
    return {"id": str(invoice_id)}


@router.get("/invoices/{invoice_id}/payments")
def invoice_payments(invoice_id: uuid.UUID, user: BillingStaff, connection: Db):
    invoice = connection.execute(
        """
        SELECT i.patient_id FROM invoices i JOIN branches b ON b.id = i.branch_id
        WHERE i.id = %s AND b.organisation_id = %s
        """,
        (invoice_id, user.organisation_id),
    ).fetchone()
    if not invoice or (user.branch_id and not connection.execute(
        "SELECT 1 FROM invoices WHERE id = %s AND branch_id = %s",
        (invoice_id, user.branch_id),
    ).fetchone()):
        raise HTTPException(404, "Invoice not found.")
    rows = connection.execute(
        """
        SELECT id, amount_minor, currency, method, status, reference, received_at
        FROM payments WHERE invoice_id = %s ORDER BY received_at DESC
        """,
        (invoice_id,),
    ).fetchall()
    return [
        {"id": str(r[0]), "amount_minor": r[1], "currency": r[2], "method": r[3],
         "status": r[4], "reference": r[5], "received_at": r[6]}
        for r in rows
    ]


@router.post("/invoices/{invoice_id}/payments", status_code=201)
def create_payment(invoice_id: uuid.UUID, payload: PaymentInput, user: BillingStaff, connection: Db):
    invoice = connection.execute(
        """
        SELECT i.patient_id, i.status, i.currency, b.outstanding_minor
        FROM invoices i JOIN branches br ON br.id = i.branch_id
        JOIN invoice_balances b ON b.invoice_id = i.id
        WHERE i.id = %s AND br.organisation_id = %s
        """,
        (invoice_id, user.organisation_id),
    ).fetchone()
    if not invoice or (user.branch_id and not connection.execute(
        "SELECT 1 FROM invoices WHERE id = %s AND branch_id = %s",
        (invoice_id, user.branch_id),
    ).fetchone()):
        raise HTTPException(404, "Invoice not found.")
    if invoice[1] in ("draft", "void", "refunded"):
        raise HTTPException(409, "Payments cannot be recorded for this invoice status.")
    if payload.amount_minor > invoice[3]:
        raise HTTPException(422, "Payment exceeds the invoice's outstanding balance.")
    with transaction(connection, str(user.id)):
        payment_id = connection.execute(
            """
            INSERT INTO payments (invoice_id, amount_minor, currency, method, status, reference, received_by)
            VALUES (%s, %s, %s, %s, 'posted', %s, %s) RETURNING id
            """,
            (invoice_id, payload.amount_minor, invoice[2], payload.method, payload.reference, user.id),
        ).fetchone()[0]
        balance = connection.execute(
            "SELECT outstanding_minor FROM invoice_balances WHERE invoice_id = %s",
            (invoice_id,),
        ).fetchone()[0]
        connection.execute(
            "UPDATE invoices SET status = %s WHERE id = %s",
            ("paid" if balance == 0 else "part_paid", invoice_id),
        )
    return {"id": str(payment_id)}


@router.post("/patients/{patient_id}/consents", status_code=201)
def create_consent(patient_id: uuid.UUID, payload: ConsentInput, user: Staff, connection: Db):
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    with transaction(connection, str(user.id)):
        consent_id = connection.execute(
            """
            INSERT INTO consents (patient_id, consent_type, policy_version, captured_by, source)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (patient_id, payload.consent_type, payload.policy_version, user.id, payload.source),
        ).fetchone()[0]
    return {"id": str(consent_id)}


@router.post("/patients/{patient_id}/consents/{consent_id}/withdraw")
def withdraw_consent(patient_id: uuid.UUID, consent_id: uuid.UUID, user: Staff, connection: Db):
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    with transaction(connection, str(user.id)):
        changed = connection.execute(
            """
            UPDATE consents SET withdrawn_at = now()
            WHERE id = %s AND patient_id = %s AND withdrawn_at IS NULL
            RETURNING id
            """,
            (consent_id, patient_id),
        ).fetchone()
        if not changed:
            raise HTTPException(404, "Active consent record not found.")
    return {"ok": True}


@router.post("/patients/{patient_id}/privacy-requests", status_code=201)
def create_privacy_request(
    patient_id: uuid.UUID,
    payload: PrivacyRequestInput,
    user: Staff,
    connection: Db,
):
    if not _patient_in_scope(connection, str(patient_id), user):
        raise HTTPException(404, "Patient not found.")
    with transaction(connection, str(user.id)):
        request_id = connection.execute(
            """
            INSERT INTO patient_privacy_requests (patient_id, request_type, details, requested_by)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (patient_id, payload.request_type, payload.details, user.id),
        ).fetchone()[0]
    return {"id": str(request_id), "status": "pending"}


@router.get("/privacy-requests")
def list_privacy_requests(
    user: UserInfo = Depends(require_roles("admin")),
    connection: Connection = Depends(get_db),
    request_status: str = Query(default="pending", alias="status", pattern="^(pending|under_review|fulfilled|rejected|all)$"),
):
    condition = "" if request_status == "all" else "AND pr.status = %s"
    params = [user.organisation_id]
    if request_status != "all":
        params.append(request_status)
    rows = connection.execute(
        f"""
        SELECT pr.id, pr.patient_id, p.patient_number, p.full_name, pr.request_type, pr.details,
               pr.status, pr.requested_at, requester.full_name, pr.decision_notes
        FROM patient_privacy_requests pr JOIN patients p ON p.id = pr.patient_id
        JOIN users requester ON requester.id = pr.requested_by
        WHERE p.organisation_id = %s {condition}
        ORDER BY pr.requested_at
        LIMIT 200
        """,
        params,
    ).fetchall()
    return [
        {"id": str(r[0]), "patient_id": str(r[1]), "patient_number": r[2], "patient_name": r[3],
         "request_type": r[4], "details": r[5], "status": r[6], "requested_at": r[7],
         "requested_by": r[8], "decision_notes": r[9]}
        for r in rows
    ]


@router.patch("/privacy-requests/{request_id}")
def update_privacy_request(
    request_id: uuid.UUID,
    payload: PrivacyRequestUpdate,
    user: UserInfo = Depends(require_roles("admin")),
    connection: Connection = Depends(get_db),
):
    with transaction(connection, str(user.id)):
        request_row = connection.execute(
            """
            SELECT pr.patient_id, pr.request_type, pr.status
            FROM patient_privacy_requests pr JOIN patients p ON p.id = pr.patient_id
            WHERE pr.id = %s AND p.organisation_id = %s
            FOR UPDATE OF pr
            """,
            (request_id, user.organisation_id),
        ).fetchone()
        if not request_row:
            raise HTTPException(404, "Privacy request not found.")
        if request_row[2] in ("fulfilled", "rejected"):
            raise HTTPException(409, "This privacy request has already been resolved.")
        if payload.status == "fulfilled" and request_row[1] == "erasure":
            connection.execute(
                """
                UPDATE patients SET soft_deleted_at = now(), status = 'inactive', updated_at = now()
                WHERE id = %s AND soft_deleted_at IS NULL
                """,
                (request_row[0],),
            )
        connection.execute(
            """
            UPDATE patient_privacy_requests
            SET status = %s, handled_by = %s, decision_notes = %s,
                resolved_at = CASE WHEN %s IN ('fulfilled','rejected') THEN now() ELSE NULL END
            WHERE id = %s
            """,
            (payload.status, user.id, payload.decision_notes, payload.status, request_id),
        )
    return {"ok": True, "status": payload.status}


@router.post("/users", status_code=201)
def create_user(
    payload: UserCreate,
    user: UserInfo = Depends(require_roles("admin")),
    connection: Connection = Depends(get_db),
):
    if payload.branch_id:
        branch = connection.execute(
            "SELECT 1 FROM branches WHERE id = %s AND organisation_id = %s AND is_active",
            (payload.branch_id, user.organisation_id),
        ).fetchone()
        if not branch:
            raise HTTPException(422, "Choose an active branch in this organisation.")
    with transaction(connection, str(user.id)):
        created = connection.execute(
            """
            INSERT INTO users (organisation_id, branch_id, full_name, email, mobile_10_digit,
                               password_hash, password_updated_at, password_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, now(), now() + interval '30 days')
            ON CONFLICT DO NOTHING RETURNING id
            """,
            (user.organisation_id, payload.branch_id, payload.full_name.strip(),
             str(payload.email).lower(), payload.mobile_10_digit, hash_password(payload.password)),
        ).fetchone()
        if not created:
            raise HTTPException(409, "A user with that email already exists.")
        connection.execute(
            "INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE name = %s",
            (created[0], payload.role),
        )
    return {"id": str(created[0])}


@router.get("/users")
def list_users(user: UserInfo = Depends(require_roles("admin")), connection: Connection = Depends(get_db)):
    rows = connection.execute(
        """
        SELECT u.id, u.full_name, u.email, u.branch_id, u.is_active, u.created_at,
               array_remove(array_agg(r.name), NULL)
        FROM users u
        LEFT JOIN user_roles ur ON ur.user_id = u.id
        LEFT JOIN roles r ON r.id = ur.role_id
        WHERE u.organisation_id = %s AND u.soft_deleted_at IS NULL
        GROUP BY u.id ORDER BY u.full_name
        """,
        (user.organisation_id,),
    ).fetchall()
    return [
        {"id": str(r[0]), "full_name": r[1], "email": r[2], "branch_id": str(r[3]) if r[3] else None,
         "is_active": r[4], "created_at": r[5], "roles": list(r[6] or [])}
        for r in rows
    ]


@router.patch("/users/{user_id}")
def set_user_active(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    user: UserInfo = Depends(require_roles("admin")),
    connection: Connection = Depends(get_db),
):
    if str(user_id) == str(user.id) and not payload.is_active:
        raise HTTPException(409, "You cannot deactivate your own account.")
    with transaction(connection, str(user.id)):
        updated = connection.execute(
            """
            UPDATE users SET is_active = %s, updated_at = now()
            WHERE id = %s AND organisation_id = %s AND soft_deleted_at IS NULL
            RETURNING id
            """,
            (payload.is_active, user_id, user.organisation_id),
        ).fetchone()
        if not updated:
            raise HTTPException(404, "User not found.")
    return {"ok": True}
