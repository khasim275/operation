import hashlib
import hmac

import pytest
from pydantic import ValidationError

from api import _detect_upload
from config import get_settings
from messaging import hash_otp, normalize_phone, verify_meta_signature, verify_otp_hash
from models import CallOutcomeInput


@pytest.fixture
def configured_environment(monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "a" * 48)
    monkeypatch.setenv("OTP_PEPPER", "b" * 48)
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("META_APP_SECRET", "test-meta-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_normalize_phone_formats_country_code(configured_environment):
    assert normalize_phone("+91 98765-00001") == "+919876500001"
    assert normalize_phone("09876500001", "91") == "+919876500001"
    assert normalize_phone("0014155552671", "91") == "+14155552671"


def test_normalize_phone_rejects_incomplete_numbers(configured_environment):
    with pytest.raises(ValueError):
        normalize_phone("12345")


def test_otp_hash_is_bound_to_challenge_and_phone(configured_environment):
    digest = hash_otp("challenge-1", "+919876500001", "012345")
    assert len(digest) == hashlib.sha256().digest_size
    assert verify_otp_hash("challenge-1", "+919876500001", "012345", digest)
    assert not verify_otp_hash("challenge-2", "+919876500001", "012345", digest)
    assert not verify_otp_hash("challenge-1", "+919876500002", "012345", digest)


def test_meta_signature_verification(configured_environment):
    body = b'{"object":"whatsapp_business_account"}'
    signature = hmac.new(b"test-meta-secret", body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, f"sha256={signature}")
    assert not verify_meta_signature(body, "sha256=incorrect")


def test_confirmed_call_requires_timezone_and_slot():
    with pytest.raises(ValidationError):
        CallOutcomeInput(outcome="confirmed")
    with pytest.raises(ValidationError):
        CallOutcomeInput(
            outcome="confirmed",
            start_at="2026-04-20T10:00:00",
            end_at="2026-04-20T10:30:00",
            dentist_id="00000000-0000-0000-0000-000000000001",
        )


def test_reschedule_call_requires_a_preferred_date_and_time():
    with pytest.raises(ValidationError):
        CallOutcomeInput(outcome="reschedule")
    request = CallOutcomeInput(
        outcome="reschedule",
        preferred_date="2026-04-20",
        preferred_time_of_day="afternoon",
    )
    assert request.preferred_time_of_day == "afternoon"


def test_upload_requires_matching_file_signature():
    assert _detect_upload(b"%PDF-1.7\n", "application/pdf") == ("application/pdf", "pdf")
    with pytest.raises(Exception):
        _detect_upload(b"%PDF-1.7\n", "image/png")
    with pytest.raises(Exception):
        _detect_upload(b"not a document", "application/pdf")
