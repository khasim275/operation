from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from config import get_settings
from patient_cards import (
    MedicalProfileInput,
    RegistrationInput,
    _render_card_files,
    is_valid_op_id,
    luhn_check_digit,
)


@pytest.fixture
def configured_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_SECRET_KEY", "a" * 48)
    monkeypatch.setenv("OTP_PEPPER", "b" * 48)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_NAME", "Harbour Dental")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://clinic.example")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_op_id_uses_luhn_digit_and_rejects_mistypes():
    assert luhn_check_digit("000123") == 0
    assert is_valid_op_id("OP-000123-0")
    assert not is_valid_op_id("OP-000123-7")
    assert not is_valid_op_id("OP-00012X-0")


def test_registration_requires_data_processing_consent(configured_environment):
    with pytest.raises(ValidationError, match="Data-processing consent"):
        RegistrationInput(full_name="Sample Person", phone="+919876500001", data_processing_consent=False)


def test_medical_profile_validates_and_normalizes_emergency_phone():
    profile = MedicalProfileInput.model_validate({
        "chronic_conditions": "  asthma  ",
        "emergency_contact_phone": "+91 98765 00001",
    })
    assert profile.chronic_conditions == "asthma"
    assert profile.emergency_contact_phone == "+919876500001"
    with pytest.raises(ValidationError, match="valid emergency contact number"):
        MedicalProfileInput.model_validate({"emergency_contact_phone": "not a phone"})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        MedicalProfileInput.model_validate({"unsupported": "value"})


def test_card_renders_deterministically_and_excludes_allergy_details(configured_environment):
    issued_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    patient = {
        "op_id": "OP-000123-0",
        "full_name": "Aarav Sample",
        "date_of_birth": "1990-05-01",
        "gender": "Not recorded",
        "phone": "+919876500001",
        "blood_group": "O+",
        "has_allergies": True,
        "allergies": ["Penicillin"],
    }
    qr_payload = "https://clinic.example/p/OP-000123-0"
    first_pdf, first_png = _render_card_files("card-1", patient, issued_at, qr_payload)
    second_pdf, second_png = _render_card_files("card-1", patient, issued_at, qr_payload)
    patient["allergies"] = ["Latex"]
    allergy_changed_pdf, allergy_changed_png = _render_card_files("card-1", patient, issued_at, qr_payload)
    assert first_pdf == second_pdf
    assert first_png == second_png
    assert first_pdf == allergy_changed_pdf
    assert first_png == allergy_changed_png
    assert first_pdf.startswith(b"%PDF")
    assert first_png.startswith(b"\x89PNG\r\n\x1a\n")
