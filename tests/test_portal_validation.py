from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from fastapi import HTTPException

import public_appointments
from public_appointments import BookingInput, validate_appointment_time


def payload(**overrides):
    value = {
        "name": "Asha Rao", "phone": "9876543210", "email": "asha@example.com",
        "appointment_date": (date.today() + timedelta(days=1)).isoformat(),
        "time_slot": "10:00", "problem_summary": "Dental check-up",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize("phone", ["1234567890", "5876543210", "987654321", "98765432100", "+919876543210", "98765 43210", "abcdefghij"])
def test_booking_rejects_invalid_indian_mobile(phone):
    with pytest.raises(ValidationError):
        BookingInput.model_validate(payload(phone=phone))


def test_booking_accepts_valid_indian_mobile_and_email():
    assert BookingInput.model_validate(payload()).phone == "9876543210"


@pytest.mark.parametrize("email", ["asha", "asha@", "asha@example", "asha@.com"])
def test_booking_rejects_invalid_email(email):
    with pytest.raises(ValidationError):
        BookingInput.model_validate(payload(email=email))


def test_booking_rejects_bad_slot_and_appointment_time():
    with pytest.raises(ValidationError):
        BookingInput.model_validate(payload(time_slot="10:30"))
    with pytest.raises(HTTPException):
        validate_appointment_time(date(2026, 11, 19), "10:00", datetime.fromisoformat("2026-11-20T09:00:00+05:30"))
    with pytest.raises(HTTPException):
        validate_appointment_time(date(2026, 11, 20), "09:00", datetime.fromisoformat("2026-11-20T09:00:00+05:30"))
    validate_appointment_time(date(2026, 11, 20), "10:00", datetime.fromisoformat("2026-11-20T09:00:00+05:30"))


def test_patient_history_response_is_minimal_and_patient_safe(monkeypatch):
    class FakeConnection:
        calls = 0
        def execute(self, query, params):
            self.calls += 1
            if self.calls == 1:
                return self
            return self
        def fetchone(self):
            return ("PAT-2026-000001",)
        def fetchall(self):
            return [("OP-261120-0001", date(2026, 11, 20), "10:00", "CONFIRMED")]

    monkeypatch.setattr(public_appointments, "_verified_patient", lambda _: "patient-uuid")
    result = public_appointments.get_patient_history(FakeConnection(), "signed-token")
    assert set(result) == {"patient_id", "visits"}
    assert set(result["visits"][0]) == {"op_id", "appointment_date_ist", "appointment_time_ist", "status", "instructions"}
    assert result["visits"][0]["status"] == "BOOKED"
