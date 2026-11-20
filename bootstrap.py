from pathlib import Path
import argparse
import hashlib

import psycopg

from config import get_settings
from patient_cards import _create_card, _render_card_with_connection
from security import hash_password


def apply_schema_migrations(database_dsn: str) -> None:
    """Apply immutable SQL migrations once, recording each checksum atomically."""
    project_dir = Path(__file__).parent
    migrations = [("000_schema", (project_dir / "schema.sql").read_text(encoding="utf-8"))]
    migrations.extend(
        (path.stem, path.read_text(encoding="utf-8"))
        for path in sorted((project_dir / "migrations").glob("*.sql"))
    )

    with psycopg.connect(database_dsn, autocommit=True) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                checksum text NOT NULL CHECK (checksum ~ '^[a-f0-9]{64}$'),
                applied_at timestamptz NOT NULL DEFAULT now(),
                applied_by name NOT NULL DEFAULT current_user
            )
            """
        )
        for version, sql in migrations:
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("dental-hospital-crm:schema-migrations",),
                )
                recorded = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = %s",
                    (version,),
                ).fetchone()
                if recorded:
                    if recorded[0] != checksum:
                        raise RuntimeError(
                            f"Applied migration {version} has changed. Add a new migration instead of editing it."
                        )
                    continue
                connection.execute(sql, prepare=False)
                connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )


def initialize_database(seed_demo: bool = False) -> None:
    settings = get_settings()
    settings.require_secrets()
    apply_schema_migrations(settings.database_dsn)

    with psycopg.connect(settings.database_dsn) as connection:
        cards_to_render = []
        with connection.transaction():
            organisation_id = connection.execute(
                "SELECT id FROM organisations WHERE name = %s ORDER BY created_at LIMIT 1",
                (settings.clinic_name,),
            ).fetchone()
            if organisation_id:
                organisation_id = organisation_id[0]
            else:
                organisation_id = connection.execute(
                    """
                    INSERT INTO organisations (name, country_code, timezone)
                    VALUES (%s, 'IN', %s) RETURNING id
                    """,
                    (settings.clinic_name, settings.clinic_timezone),
                ).fetchone()[0]

            branch_id = connection.execute(
                "SELECT id FROM branches WHERE organisation_id = %s AND name = %s",
                (organisation_id, settings.branch_name),
            ).fetchone()
            if branch_id:
                branch_id = branch_id[0]
            else:
                branch_id = connection.execute(
                    """
                    INSERT INTO branches (organisation_id, name, city, country_code, timezone)
                    VALUES (%s, %s, %s, 'IN', %s) RETURNING id
                    """,
                    (organisation_id, settings.branch_name, settings.clinic_city, settings.clinic_timezone),
                ).fetchone()[0]

            connection.execute(
                """INSERT INTO public_site_settings (id, hospital_name)
                   VALUES (true, %s) ON CONFLICT (id) DO NOTHING""",
                (settings.clinic_name,),
            )

            if settings.admin_email and settings.admin_password.get_secret_value():
                admin_id = connection.execute(
                    """
                    INSERT INTO users (organisation_id, branch_id, full_name, email, mobile_10_digit,
                                       password_hash, password_updated_at, password_expires_at)
                    VALUES (%s, %s, 'Clinic Administrator', %s, %s, %s, now(), now() + interval '30 days')
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (
                        organisation_id,
                        branch_id,
                        settings.admin_email.strip().lower(),
                        settings.admin_mobile_10_digit or None,
                        hash_password(settings.admin_password.get_secret_value()),
                    ),
                ).fetchone()
                if admin_id:
                    connection.execute(
                        "INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE name = 'admin'",
                        (admin_id[0],),
                    )

            if seed_demo:
                cards_to_render = seed_fake_data(connection, organisation_id, branch_id)
            legacy_patients = connection.execute(
                """
                SELECT p.id, p.op_id
                FROM patients p
                WHERE p.organisation_id = %s
                  AND p.soft_deleted_at IS NULL
                  AND p.merged_into IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM patient_cards c WHERE c.patient_id = p.id
                  )
                """,
                (organisation_id,),
            ).fetchall()
            for patient_id, op_id in legacy_patients:
                card = _create_card(connection, patient_id, op_id, None, "Initial card after OP ID migration")
                cards_to_render.append((patient_id, card[0]))
        for patient_id, card_id in cards_to_render:
            _render_card_with_connection(connection, str(card_id), str(patient_id))


def seed_fake_data(connection, organisation_id, branch_id):
    if connection.execute(
        "SELECT EXISTS (SELECT 1 FROM patients WHERE organisation_id = %s)",
        (organisation_id,),
    ).fetchone()[0]:
        return []

    fake_patient = connection.execute(
        """
        INSERT INTO patients (organisation_id, full_name, preferred_language, referral_source)
        VALUES (%s, 'Aarav Sample', 'en', 'website') RETURNING id
        """,
        (organisation_id,),
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO patient_phones (patient_id, phone_e164, is_primary, verified_at)
        VALUES (%s, '+919876500001', true, now())
        """,
        (fake_patient,),
    )
    connection.execute(
        """
        INSERT INTO appointments (patient_id, branch_id, source, reason, preferred_time_of_day, status)
        VALUES (%s, %s, 'website', 'Check-up', 'morning', 'requested')
        """,
        (fake_patient, branch_id),
    )

    fake_patient_2 = connection.execute(
        """
        INSERT INTO patients (organisation_id, full_name, preferred_language, referral_source)
        VALUES (%s, 'Mira Demo', 'en', 'walk_in') RETURNING id
        """,
        (organisation_id,),
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO patient_phones (patient_id, phone_e164, is_primary)
        VALUES (%s, '+919876500002', true)
        """,
        (fake_patient_2,),
    )
    connection.execute(
        """
        INSERT INTO appointments (patient_id, branch_id, source, reason, preferred_time_of_day, status)
        VALUES (%s, %s, 'whatsapp', 'Tooth pain', 'afternoon', 'requested')
        """,
        (fake_patient_2, branch_id),
    )
    seeded = []
    for patient_id in (fake_patient, fake_patient_2):
        connection.execute(
            "INSERT INTO patient_medical_profile (patient_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (patient_id,),
        )
        op_id = connection.execute(
            "SELECT op_id FROM patients WHERE id = %s", (patient_id,)
        ).fetchone()[0]
        card = _create_card(connection, patient_id, op_id, None, "Development seed")
        seeded.append((patient_id, card[0]))
    return seeded


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create or update the Dental Hospital CRM database.")
    parser.add_argument("--seed-demo", action="store_true", help="Insert synthetic example patients for a local demo.")
    args = parser.parse_args()
    initialize_database(seed_demo=args.seed_demo)
    print("Database schema is ready.")
