from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatientUpdate(StrictModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=40)
    occupation: str | None = Field(default=None, max_length=120)
    preferred_language: str | None = Field(default=None, min_length=2, max_length=12)
    address_line1: str | None = Field(default=None, max_length=250)
    address_line2: str | None = Field(default=None, max_length=250)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)


class MedicalNoteInput(StrictModel):
    note_text: str = Field(min_length=1, max_length=10000)


class PatientMedicineInput(StrictModel):
    medicine_name: str = Field(min_length=1, max_length=200)
    dose: str | None = Field(default=None, max_length=200)
    frequency: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def dates_are_ordered(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("Medicine end date cannot be before its start date.")
        return self


class AllergyInput(StrictModel):
    allergen_text: str = Field(min_length=1, max_length=300)
    reaction: str | None = Field(default=None, max_length=1000)
    severity: Literal["unknown", "mild", "moderate", "severe"] = "unknown"


class AppointmentUpdate(StrictModel):
    status: Literal["requested", "confirmed", "rescheduled", "checked_in", "completed", "cancelled", "no_show"]
    status_note: str = Field(default="", max_length=1000)


class CallOutcomeInput(StrictModel):
    outcome: Literal["confirmed", "not_reachable", "reschedule", "cancelled"]
    notes: str = Field(default="", max_length=4000)
    duration_seconds: int | None = Field(default=None, ge=0, le=24 * 60 * 60)
    next_follow_up_at: datetime | None = None
    preferred_date: date | None = None
    preferred_time_of_day: Literal["morning", "afternoon", "evening", "any"] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    dentist_id: UUID | None = None
    room_id: UUID | None = None

    @model_validator(mode="after")
    def confirmation_requires_schedule(self):
        if self.outcome == "confirmed":
            if not self.start_at or not self.end_at or not self.dentist_id:
                raise ValueError("Confirming a visit requires a dentist, start time, and end time.")
            if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
                raise ValueError("Appointment times must include a timezone.")
            if self.end_at <= self.start_at:
                raise ValueError("Appointment end time must be after its start time.")
        if self.outcome == "reschedule" and (not self.preferred_date or not self.preferred_time_of_day):
            raise ValueError("Rescheduling requires a preferred date and time of day.")
        return self


class VisitInput(StrictModel):
    patient_id: UUID
    appointment_id: UUID | None = None
    complaint: str = Field(min_length=1, max_length=4000)
    examination_findings: str = Field(default="", max_length=8000)
    treatment_plan: str = Field(default="", max_length=8000)
    diagnoses: list[str] = Field(default_factory=list, max_length=20)
    procedures: list["ProcedureInput"] = Field(default_factory=list, max_length=50)


class ProcedureInput(StrictModel):
    procedure_name: str = Field(min_length=1, max_length=200)
    fdi_tooth: int | None = Field(default=None, ge=11, le=48)
    surface: str | None = Field(default=None, max_length=30)
    notes: str = Field(default="", max_length=2000)


class PrescriptionItemInput(StrictModel):
    medicine_name: str = Field(min_length=1, max_length=200)
    dose: str = Field(min_length=1, max_length=200)
    frequency: str = Field(min_length=1, max_length=200)
    duration: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=2000)


class PrescriptionInput(StrictModel):
    visit_id: UUID
    patient_id: UUID
    items: list[PrescriptionItemInput] = Field(min_length=1, max_length=50)


class InvoiceItemInput(StrictModel):
    description: str = Field(min_length=1, max_length=300)
    quantity: float = Field(default=1, gt=0, le=10000)
    unit_amount_minor: int = Field(ge=0, le=100_000_000)
    discount_minor: int = Field(default=0, ge=0, le=100_000_000)


class InvoiceInput(StrictModel):
    patient_id: UUID
    visit_id: UUID | None = None
    items: list[InvoiceItemInput] = Field(min_length=1, max_length=100)


class PaymentInput(StrictModel):
    amount_minor: int = Field(gt=0, le=100_000_000)
    method: Literal["cash", "card", "upi", "insurance", "bank_transfer", "other"]
    reference: str | None = Field(default=None, max_length=200)


class UserCreate(StrictModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    mobile_10_digit: str | None = Field(default=None, pattern=r"^[6-9][0-9]{9}$")
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "telecaller", "receptionist", "dentist", "accountant"]
    branch_id: UUID | None = None


class UserStatusUpdate(StrictModel):
    is_active: bool


class SimulatorMessage(StrictModel):
    phone: str = Field(min_length=8, max_length=30)
    message: str = Field(min_length=1, max_length=4000)


class SimulatorReset(StrictModel):
    phone: str = Field(min_length=8, max_length=30)


class MessageInput(StrictModel):
    message: str = Field(min_length=1, max_length=4000)


class ConsentInput(StrictModel):
    consent_type: Literal["data_processing", "treatment", "photographs", "marketing"]
    policy_version: str = Field(min_length=1, max_length=50)
    source: str = Field(default="staff", max_length=40)


class PrivacyRequestInput(StrictModel):
    request_type: Literal["correction", "erasure"]
    details: str = Field(default="", max_length=4000)


class PrivacyRequestUpdate(StrictModel):
    status: Literal["under_review", "fulfilled", "rejected"]
    decision_notes: str = Field(min_length=1, max_length=4000)


class AppointmentRequest(StrictModel):
    patient_id: UUID
    reason: str = Field(min_length=1, max_length=500)
    preferred_date: date | None = None
    preferred_time_of_day: Literal["morning", "afternoon", "evening", "any"] = "any"
    source: Literal["whatsapp", "website", "walk_in", "phone", "referral"] = "phone"


class SearchQuery(StrictModel):
    q: str = Field(min_length=2, max_length=120)


class DateRangeQuery(StrictModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Datetime values must include a timezone.")
        return value
