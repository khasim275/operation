from __future__ import annotations

import logging
import re
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from db import audit_view, get_db, transaction
from security import UserInfo, current_user

router = APIRouter(prefix="/api", tags=["secure clinical and medicine reference"])
ClinicalUser = Annotated[UserInfo, Depends(current_user)]
logger = logging.getLogger(__name__)


@router.get("/admin/system-errors")
def recent_system_errors(user: ClinicalUser, connection=Depends(get_db), limit: int = Query(default=50, ge=1, le=200)):
    if "admin" not in user.roles:
        raise HTTPException(403, "Administrator access required.")
    rows = connection.execute(
        """SELECT request_id,source,error_type,path,created_at FROM system_error_logs
           ORDER BY created_at DESC LIMIT %s""", (limit,),
    ).fetchall()
    return [{"request_id": r[0], "source": r[1], "error_type": r[2], "path": r[3], "created_at": r[4]} for r in rows]


class MedicineGiven(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    dose: str = Field(default="", max_length=200)
    frequency: str = Field(default="", max_length=200)
    duration: str = Field(default="", max_length=200)


class ClinicalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medicines_given: list[MedicineGiven] | None = Field(default=None, max_length=50)
    is_operation: bool | None = None
    operation_summary: str | None = Field(default=None, max_length=5000)
    amount_inr: Decimal | None = Field(default=None, ge=0, le=9999999999)
    payment_status: Literal["UNPAID", "PARTIAL", "PAID", "WAIVED"] | None = None


def _require_clinical(user: UserInfo):
    if not set(user.roles).intersection({"admin", "dentist"}):
        raise HTTPException(403, "Only a dentist or administrator can edit clinical records.")


def _require_staff(user: UserInfo):
    if not set(user.roles).intersection({"admin", "dentist", "receptionist", "telecaller", "accountant"}):
        raise HTTPException(403, "Staff access required.")


def _require_billing(user: UserInfo):
    if not set(user.roles).intersection({"admin", "accountant"}):
        raise HTTPException(403, "Only billing staff or an administrator can update billing.")


@router.get("/clinical-ops/{op_id}")
def get_clinical_op(op_id: str, user: ClinicalUser, connection=Depends(get_db)):
    _require_clinical(user)
    row = connection.execute(
        """
        SELECT c.op_id, c.patient_id, p.patient_public_id, p.full_name, p.mobile_10_digit,
               c.medicines_given, c.medical_files, c.is_operation, c.operation_files,
               c.operation_summary, c.amount_inr, c.payment_status, c.updated_at_ist
        FROM clinical_records_billing c JOIN patients p ON p.id = c.patient_id
        JOIN appointment_cards ac ON ac.opid = c.op_id
        JOIN appointments a ON a.id = ac.appointment_id
        JOIN branches b ON b.id = a.branch_id
        WHERE c.op_id = upper(%s) AND p.organisation_id = %s AND p.soft_deleted_at IS NULL
          AND (%s::uuid IS NULL OR a.branch_id = %s::uuid)
        """,
        (op_id, user.organisation_id, user.branch_id, user.branch_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Clinical record not found.")
    return {
        "op_id": row[0], "patient_id": str(row[1]), "patient_public_id": row[2],
        "patient_name": row[3], "mobile_10_digit": row[4], "medicines_given": row[5],
        "medical_files": row[6], "is_operation": row[7], "operation_files": row[8],
        "operation_summary": row[9], "amount_inr": str(row[10]),
        "payment_status": row[11], "updated_at": row[12],
    }


@router.get("/clinical-ops")
def list_clinical_ops(
    patient_id: UUID,
    user: ClinicalUser,
    connection=Depends(get_db),
):
    _require_clinical(user)
    patient = connection.execute(
        "SELECT id FROM patients WHERE id=%s AND organisation_id=%s AND soft_deleted_at IS NULL",
        (patient_id, user.organisation_id),
    ).fetchone()
    if not patient:
        raise HTTPException(404, "Patient not found.")
    rows = connection.execute(
        """
        SELECT c.op_id, ac.appointment_date, ac.time_slot, ac.status, c.medicines_given,
               c.medical_files, c.is_operation, c.operation_files, c.operation_summary,
               c.amount_inr, c.payment_status
        FROM clinical_records_billing c JOIN appointment_cards ac ON ac.opid=c.op_id
        JOIN appointments a ON a.id=ac.appointment_id
        WHERE c.patient_id=%s AND (%s::uuid IS NULL OR a.branch_id=%s::uuid)
        ORDER BY ac.appointment_date DESC, ac.time_slot DESC
        """,
        (patient_id, user.branch_id, user.branch_id),
    ).fetchall()
    return [
        {
            "op_id": row[0], "appointment_date": row[1], "time_slot": row[2], "status": row[3],
            "medicines_given": row[4], "medical_files": row[5], "is_operation": row[6],
            "operation_files": row[7], "operation_summary": row[8], "amount_inr": str(row[9]),
            "payment_status": row[10],
        } for row in rows
    ]


@router.patch("/clinical-ops/{op_id}")
def update_clinical_op(op_id: str, payload: ClinicalUpdate, user: ClinicalUser, connection=Depends(get_db)):
    _require_staff(user)
    fields = payload.model_fields_set
    if not fields:
        raise HTTPException(422, "Provide at least one field to update.")
    clinical_fields = {"medicines_given", "is_operation", "operation_summary"}
    billing_fields = {"amount_inr", "payment_status"}
    if fields & clinical_fields:
        _require_clinical(user)
    if fields & billing_fields:
        _require_billing(user)
    existing = connection.execute(
        """
        SELECT c.record_id, c.patient_id FROM clinical_records_billing c
        JOIN appointment_cards ac ON ac.opid = c.op_id
        JOIN appointments a ON a.id = ac.appointment_id
        JOIN patients p ON p.id = c.patient_id
        WHERE c.op_id = upper(%s) AND p.organisation_id = %s
          AND (%s::uuid IS NULL OR a.branch_id = %s::uuid)
        """,
        (op_id, user.organisation_id, user.branch_id, user.branch_id),
    ).fetchone()
    if not existing:
        raise HTTPException(404, "Clinical record not found.")
    assignments: list[str] = ["updated_at_ist = now()"]
    values: list[object] = []
    if payload.medicines_given is not None:
        assignments.append("medicines_given = %s::jsonb")
        values.append([item.model_dump() for item in payload.medicines_given])
    if payload.is_operation is not None:
        assignments.append("is_operation = %s")
        values.append(payload.is_operation)
    if payload.operation_summary is not None:
        assignments.append("operation_summary = %s")
        values.append(payload.operation_summary.strip())
    if payload.amount_inr is not None:
        assignments.append("amount_inr = %s")
        values.append(payload.amount_inr)
    if payload.payment_status is not None:
        assignments.append("payment_status = %s")
        values.append(payload.payment_status)
    with transaction(connection, str(user.id)):
        connection.execute(
            f"UPDATE clinical_records_billing SET {', '.join(assignments)} WHERE record_id = %s",
            (*values, existing[0]),
        )
    result = {"ok": True, "updated_fields": sorted(fields)}
    if set(user.roles).intersection({"admin", "dentist"}):
        result["record"] = get_clinical_op(op_id, user, connection)
    return result


@router.get("/patient-history")
def patient_history(
    user: ClinicalUser,
    connection=Depends(get_db),
    patient_id: str | None = Query(default=None, min_length=1, max_length=64),
    mobile_10_digit: str | None = Query(default=None, pattern=r"^[6-9][0-9]{9}$"),
):
    _require_clinical(user)
    if (patient_id is None) == (mobile_10_digit is None):
        raise HTTPException(422, "Supply exactly one patient_id or 10-digit mobile_10_digit.")
    if mobile_10_digit:
        row = connection.execute(
            "SELECT id FROM patients WHERE organisation_id=%s AND soft_deleted_at IS NULL AND mobile_10_digit=%s LIMIT 1",
            (user.organisation_id, mobile_10_digit),
        ).fetchone()
    else:
        try:
            internal_id = UUID(patient_id)
            row = connection.execute(
                "SELECT id FROM patients WHERE organisation_id=%s AND soft_deleted_at IS NULL AND id=%s LIMIT 1",
                (user.organisation_id, internal_id),
            ).fetchone()
        except ValueError:
            if not re.fullmatch(r"PAT-[0-9]{4}-[0-9]{6}", patient_id or ""):
                raise HTTPException(422, "Use a patient ID or 10-digit mobile number.")
            row = connection.execute(
                "SELECT id FROM patients WHERE organisation_id=%s AND soft_deleted_at IS NULL AND patient_public_id=%s LIMIT 1",
                (user.organisation_id, patient_id),
            ).fetchone()
    if not row:
        raise HTTPException(404, "Patient history not found.")
    history = connection.execute("SELECT get_patient_history(%s, NULL)", (row[0],)).fetchone()[0]
    audit_view(connection, str(user.id), "patient_history", str(row[0]))
    return history


def _first(data: dict, key: str) -> list[str]:
    value = data.get(key) or []
    return [str(item) for item in value[:4] if item]


@lru_cache(maxsize=128)
def _medicine_search(query: str) -> dict:
    rx_items: list[dict] = []
    fda_label: dict = {}
    try:
        rx = httpx.get("https://rxnav.nlm.nih.gov/REST/drugs.json", params={"name": query}, timeout=7)
        rx.raise_for_status()
        groups = rx.json().get("drugGroup", {}).get("conceptGroup", []) or []
        for group in groups:
            for item in (group.get("conceptProperties") or [])[:8]:
                rx_items.append({"name": item.get("name"), "rxcui": item.get("rxcui"), "type": item.get("tty")})
        normalized_terms = query.replace('"', "").replace("\\", "").strip()
        fda = httpx.get(
            "https://api.fda.gov/drug/label.json",
            params={"search": f'openfda.brand_name:"{normalized_terms}" OR openfda.generic_name:"{normalized_terms}"', "limit": 1},
            timeout=8,
        )
        if fda.is_success:
            results = fda.json().get("results") or []
            if results:
                fda_label = results[0]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.info("Medicine reference lookup unavailable: %s", type(exc).__name__)

    openfda = fda_label.get("openfda") or {}
    ingredients = list(dict.fromkeys(
        _first(openfda, "substance_name") + _first(openfda, "generic_name") + _first(openfda, "active_ingredient")
    ))
    return {
        "query": query,
        "found": bool(rx_items or fda_label),
        "compounds": ingredients,
        "uses": _first(fda_label, "indications_and_usage") or _first(fda_label, "purpose"),
        "safety_notes": _first(fda_label, "contraindications") + _first(fda_label, "warnings"),
        "matches": rx_items[:8],
        "sources": [
            "https://rxnav.nlm.nih.gov/",
            "https://open.fda.gov/apis/drug/label/",
        ],
        "disclaimer": "Reference information only. Labels may not cover Indian brands or the complete patient-specific risk profile. Confirm the current product insert, local guidance, allergies, interactions, pregnancy status, renal/hepatic function, and clinician/pharmacist advice before use. This tool does not prescribe or recommend treatment.",
    }


@router.get("/medicine-reference")
def medicine_reference(
    user: ClinicalUser,
    query: str = Query(min_length=2, max_length=100),
):
    if not set(user.roles).intersection({"admin", "dentist", "accountant"}):
        raise HTTPException(403, "Staff verification required.")
    cleaned = re.sub(r"\s+", " ", query).strip()
    if not re.fullmatch(r"[\w .()+-]{2,100}", cleaned, re.UNICODE):
        raise HTTPException(422, "Use a medicine or brand name with letters and numbers.")
    return _medicine_search(cleaned)
