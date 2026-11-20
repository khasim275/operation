import os
import uuid
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from config import get_settings
from security import hash_password


@pytest.fixture(scope="module")
def authenticated_crm(tmp_path_factory):
    database_url = os.environ.get("CRM_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set CRM_TEST_DATABASE_URL to a disposable PostgreSQL database to run integration tests.")
    database_name = conninfo_to_dict(database_url).get("dbname", "")
    if "test" not in database_name.lower():
        pytest.fail("CRM_TEST_DATABASE_URL must target a database whose name contains 'test'.")

    run_id = uuid.uuid4().hex
    clinic_name = f"OP Card Integration Clinic {run_id}"
    admin_email = f"op-card-admin+{run_id}@clinic.example.com"
    env_values = {
        "DATABASE_URL": database_url,
        "APP_SECRET_KEY": "integration-test-secret-value-not-for-production",
        "OTP_PEPPER": "integration-test-otp-pepper-not-for-production",
        "ADMIN_EMAIL": admin_email,
        "ADMIN_PASSWORD": "Local-Integration-Test-Password-948!",
        "ADMIN_MOBILE_10_DIGIT": "9876543210",
        "CLINIC_NAME": clinic_name,
        "PUBLIC_BASE_URL": "https://clinic.example.test",
        "STORAGE_DIR": str(tmp_path_factory.mktemp("patient-cards")),
        "DEMO_MODE": "true",
    }
    original = {key: os.environ.get(key) for key in env_values}
    os.environ.update(env_values)
    get_settings.cache_clear()
    try:
        from bootstrap import initialize_database

        initialize_database(seed_demo=False)
        initialize_database(seed_demo=False)
        with psycopg.connect(database_url) as connection:
            migration_versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            assert [row[0] for row in migration_versions] == [
                "000_schema",
                "001_patient_cards",
                "002_public_appointment_cards",
                "003_clinic_booking_hours",
                "004_public_card_audit",
                "005_secure_patient_portals",
                "006_atomic_public_booking",
                "007_secure_operation_documents",
                "008_error_feedback_patient_access",
                "009_email_otp_reasons",
            ]
            organisation_id = connection.execute(
                "SELECT id FROM organisations WHERE name = %s",
                (clinic_name,),
            ).fetchone()[0]
            user_row = connection.execute(
                """
                SELECT id, branch_id FROM users
                WHERE organisation_id = %s AND lower(email) = lower(%s)
                """,
                (organisation_id, env_values["ADMIN_EMAIL"]),
            ).fetchone()
            connection.execute(
                "UPDATE users SET password_hash = %s, is_active = true WHERE id = %s",
                (hash_password(env_values["ADMIN_PASSWORD"]), user_row[0]),
            )
            connection.execute(
                """
                INSERT INTO user_roles (user_id, role_id)
                SELECT %s, id FROM roles WHERE name = 'admin'
                ON CONFLICT DO NOTHING
                """,
                (user_row[0],),
            )
        from app import app

        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"email": env_values["ADMIN_EMAIL"], "password": env_values["ADMIN_PASSWORD"]},
            )
            assert login.status_code == 200, login.text
            csrf_token = client.cookies.get("dental_crm_csrf")
            assert csrf_token
            yield client, database_url, organisation_id, {"X-CSRF-Token": csrf_token}
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def _registration(name: str, phone: str, birth_date: str | None = None) -> dict:
    return {
        "full_name": name,
        "phone": phone,
        "date_of_birth": birth_date,
        "preferred_language": "en",
        "data_processing_consent": True,
    }


def test_postgres_migration_registration_cards_and_indexed_lookups(authenticated_crm):
    client, database_url, organisation_id, headers = authenticated_crm
    idempotency_key = str(uuid.uuid4())
    first_payload = _registration("Aarav Integration", "+919900123001", "1990-05-01")
    first = client.post(
        "/api/patients/register",
        json=first_payload,
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert first.status_code == 201, first.text
    first_result = first.json()

    retry = client.post(
        "/api/patients/register",
        json=first_payload,
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["patient"]["op_id"] == first_result["patient"]["op_id"]
    assert retry.json()["card"]["id"] == first_result["card"]["id"]

    duplicate = client.post(
        "/api/patients/register",
        json=first_payload,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "possible_duplicate"
    assert duplicate.json()["detail"]["matches"][0]["date_of_birth"] == "1990-05-01"

    race_payload = _registration("Race Condition Patient", "+919900123099", "1988-04-02")
    race_barrier = Barrier(2)

    def race_registration(index: int):
        race_barrier.wait()
        return client.post(
            "/api/patients/register",
            json=race_payload,
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        race_results = list(executor.map(race_registration, range(2)))
    assert sorted(response.status_code for response in race_results) == [201, 409]

    family_member = client.post(
        "/api/patients/register",
        json=_registration("Mira Integration", "+919900123001", "1992-06-15"),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert family_member.status_code == 201, family_member.text
    family_result = family_member.json()
    assert family_result["patient"]["op_id"] != first_result["patient"]["op_id"]

    reissue = client.post(
        f"/api/patients/{first_result['patient']['id']}/card/reissue",
        json={"reason": "Damaged card"},
        headers=headers,
    )
    assert reissue.status_code == 200, reissue.text
    cards = client.get(
        f"/api/patients/{first_result['patient']['id']}/card-history",
        headers=headers,
    )
    assert [card["status"] for card in cards.json()[:2]] == ["active", "superseded"]

    merge = client.post(
        f"/api/patients/{family_result['patient']['id']}/merge",
        json={"surviving_patient_id": first_result["patient"]["id"]},
        headers=headers,
    )
    assert merge.status_code == 200, merge.text
    old_id_file = client.get(
        f"/api/patients/by-op/{family_result['patient']['op_id']}?source=scan",
    )
    assert old_id_file.status_code == 200, old_id_file.text
    assert "merged" in old_id_file.json()["notice"].lower()

    parallel_count = 200

    def register_parallel(index: int):
        response = client.post(
            "/api/patients/register",
            json=_registration(f"Parallel Patient {index}", f"+919900{index:06d}"),
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=20) as executor:
        parallel_results = list(executor.map(register_parallel, range(parallel_count)))
    assert all(status == 201 for status, _ in parallel_results)
    parallel_ids = [result["patient"]["op_id"] for _, result in parallel_results]
    assert len(set(parallel_ids)) == parallel_count

    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.Error, match="OP IDs are permanent"):
            with connection.transaction():
                connection.execute(
                    "UPDATE patients SET op_id = 'OP-999999-4' WHERE id = %s",
                    (first_result["patient"]["id"],),
                )
        access_log = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM patient_access_log
                WHERE patient_id = %s AND action = 'scan'
            )
            """,
            (family_result["patient"]["id"],),
        ).fetchone()[0]
        assert access_log

        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
            ).fetchall()
        }
        assert "patients_op_id_lookup_idx" in indexes
        assert "patient_phone_lookup_idx" in indexes


def test_public_appointment_booking_idempotency_lookup_settings_and_100_way_race(authenticated_crm):
    client, database_url, _, headers = authenticated_crm
    booking_date = (date.today() + timedelta(days=14 + uuid.uuid4().int % 300)).isoformat()
    key = f"public-booking-{uuid.uuid4()}"
    payload = {
        "name": "Public Booking Patient",
        "phone": "9876543211",
        "email": "public-booking@example.test",
        "problem_summary": "Tooth pain",
        "appointment_date": booking_date,
        "time_slot": "10:00",
    }
    created = client.post("/api/public/appointments/book", json=payload, headers={**headers, "Idempotency-Key": key})
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["opid"].startswith(f"OP-{date.fromisoformat(booking_date).strftime('%y%m%d')}-")
    assert card["patient_name"] == payload["name"]
    assert card["appointment_date"] == booking_date
    assert card["time_slot"] == "10:00"
    assert card["token_no"] == 1
    assert card["room"]
    assert card["status"] == "CONFIRMED"

    retry = client.post("/api/public/appointments/book", json=payload, headers={**headers, "Idempotency-Key": key})
    assert retry.status_code == 201
    assert retry.json()["id"] == card["id"]
    mismatch = client.post(
        "/api/public/appointments/book",
        json={**payload, "name": "Changed Name"},
        headers={**headers, "Idempotency-Key": key},
    )
    assert mismatch.status_code == 409

    lookup = client.get(f"/api/public/appointments/{card['opid']}")
    assert lookup.status_code == 200
    assert lookup.json()["id"] == card["id"]

    saved_settings = client.patch(
        "/api/public/settings",
        json={"hospital_name": "Updated Integration Clinic", "card_finish": "sage"},
        headers=headers,
    )
    assert saved_settings.status_code == 200, saved_settings.text
    assert client.get("/api/public/settings").json()["hospital_name"] == "Updated Integration Clinic"

    race_date = (date.today() + timedelta(days=14 + uuid.uuid4().int % 300)).isoformat()
    def concurrent_booking(index: int):
        phone = f"{9000000000 + index:010d}"
        request_payload = {
            "name": f"Concurrent Patient {index}",
            "phone": phone,
            "email": f"concurrent-{index}@example.test",
            "problem_summary": "Check-up",
            "appointment_date": race_date,
            "time_slot": "11:00",
        }
        response = client.post(
            "/api/public/appointments/book",
            json=request_payload,
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(concurrent_booking, range(100)))
    assert all(status == 201 for status, _ in results), [result for result in results if result[0] != 201]
    records = [result for _, result in results]
    assert len({item["opid"] for item in records}) == 100
    assert len({item["token_no"] for item in records}) == 100
    assert sorted(item["token_no"] for item in records) == list(range(1, 101))

