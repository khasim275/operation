CREATE SEQUENCE IF NOT EXISTS op_seq;

CREATE OR REPLACE FUNCTION luhn_check_digit(value text)
RETURNS integer
LANGUAGE plpgsql
IMMUTABLE
STRICT
AS $$
DECLARE
    position integer;
    digit integer;
    total integer := 0;
    double_digit boolean := true;
BEGIN
    IF value = '' OR value !~ '^[0-9]+$' THEN
        RAISE EXCEPTION 'Luhn input must contain digits';
    END IF;
    FOR position IN REVERSE length(value)..1 LOOP
        digit := substr(value, position, 1)::integer;
        IF double_digit THEN
            digit := digit * 2;
            IF digit > 9 THEN
                digit := digit - 9;
            END IF;
        END IF;
        total := total + digit;
        double_digit := NOT double_digit;
    END LOOP;
    RETURN (10 - total % 10) % 10;
END;
$$;

CREATE OR REPLACE FUNCTION generate_op_id()
RETURNS text
LANGUAGE plpgsql
VOLATILE
AS $$
DECLARE
    sequence_value bigint;
    body text;
BEGIN
    sequence_value := nextval('op_seq');
    IF sequence_value > 999999 THEN
        RAISE EXCEPTION 'OP ID sequence exceeds the supported six-digit range';
    END IF;
    body := lpad(sequence_value::text, 6, '0');
    RETURN 'OP-' || body || '-' || luhn_check_digit(body)::text;
END;
$$;

ALTER TABLE patients ADD COLUMN IF NOT EXISTS op_id text;
ALTER TABLE patients ALTER COLUMN op_id SET DEFAULT generate_op_id();
UPDATE patients SET op_id = generate_op_id() WHERE op_id IS NULL;
ALTER TABLE patients ALTER COLUMN op_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS patients_op_id_unique_idx ON patients(op_id);
CREATE INDEX IF NOT EXISTS patients_op_id_lookup_idx ON patients(organisation_id, op_id);
ALTER TABLE patients ADD COLUMN IF NOT EXISTS merged_into uuid REFERENCES patients(id);
CREATE INDEX IF NOT EXISTS patients_merged_into_idx ON patients(merged_into) WHERE merged_into IS NOT NULL;

CREATE OR REPLACE FUNCTION reject_op_id_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.op_id IS DISTINCT FROM OLD.op_id THEN
        RAISE EXCEPTION 'OP IDs are permanent and cannot be changed';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS patients_op_id_immutable ON patients;
CREATE TRIGGER patients_op_id_immutable
    BEFORE UPDATE OF op_id ON patients
    FOR EACH ROW EXECUTE FUNCTION reject_op_id_change();

CREATE TABLE IF NOT EXISTS patient_medical_profile (
    patient_id uuid PRIMARY KEY REFERENCES patients(id),
    blood_group text NOT NULL DEFAULT '',
    chronic_conditions text NOT NULL DEFAULT '',
    current_medications text NOT NULL DEFAULT '',
    past_surgeries text NOT NULL DEFAULT '',
    pregnancy_status text NOT NULL DEFAULT 'not_recorded',
    habits text NOT NULL DEFAULT '',
    family_history text NOT NULL DEFAULT '',
    notes text NOT NULL DEFAULT '',
    emergency_contact_name text NOT NULL DEFAULT '',
    emergency_contact_phone text NOT NULL DEFAULT '',
    emergency_contact_relationship text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient_cards (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id uuid NOT NULL REFERENCES patients(id),
    op_id text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('active','stale','superseded','pending')),
    qr_payload text NOT NULL,
    pdf_key text,
    png_key text,
    sha256 char(64),
    issued_at timestamptz NOT NULL DEFAULT now(),
    issued_by uuid REFERENCES users(id),
    issue_reason text NOT NULL DEFAULT 'Initial issue',
    print_count integer NOT NULL DEFAULT 0 CHECK (print_count >= 0),
    render_attempts integer NOT NULL DEFAULT 0 CHECK (render_attempts >= 0),
    last_render_attempt_at timestamptz,
    UNIQUE (patient_id, version)
);
ALTER TABLE patient_cards ADD COLUMN IF NOT EXISTS render_attempts integer NOT NULL DEFAULT 0;
ALTER TABLE patient_cards ADD COLUMN IF NOT EXISTS last_render_attempt_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS patient_cards_one_active_idx
    ON patient_cards(patient_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS patient_cards_patient_history_idx
    ON patient_cards(patient_id, version DESC);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key text PRIMARY KEY,
    request_hash char(64) NOT NULL,
    response jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idempotency_keys_expiry_idx ON idempotency_keys(created_at);

CREATE TABLE IF NOT EXISTS patient_access_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    patient_id uuid NOT NULL REFERENCES patients(id),
    action text NOT NULL CHECK (action IN ('view','print','send','scan','search')),
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS patient_access_log_patient_time_idx
    ON patient_access_log(patient_id, at DESC);
CREATE INDEX IF NOT EXISTS patient_access_log_user_time_idx
    ON patient_access_log(user_id, at DESC);
DROP TRIGGER IF EXISTS patient_access_log_immutable ON patient_access_log;
CREATE TRIGGER patient_access_log_immutable
    BEFORE UPDATE OR DELETE ON patient_access_log
    FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();

ALTER TABLE patient_phones DROP CONSTRAINT IF EXISTS patient_phones_phone_e164_key;
CREATE INDEX IF NOT EXISTS patient_phone_lookup_idx ON patient_phones(phone_e164);
CREATE INDEX IF NOT EXISTS patient_phone_trgm_idx
    ON patient_phones USING gin(phone_e164 gin_trgm_ops);

ALTER TABLE consents DROP CONSTRAINT IF EXISTS consents_consent_type_check;
ALTER TABLE consents ADD CONSTRAINT consents_consent_type_check
    CHECK (consent_type IN ('data_processing','treatment','photographs','marketing','whatsapp_card'));

CREATE OR REPLACE FUNCTION mark_patient_card_stale()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE patient_cards SET status = 'stale'
    WHERE patient_id = NEW.id AND status = 'active';
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS patients_card_stale ON patients;
CREATE TRIGGER patients_card_stale
    AFTER UPDATE OF full_name, date_of_birth, gender ON patients
    FOR EACH ROW
    WHEN (
        OLD.full_name IS DISTINCT FROM NEW.full_name
        OR OLD.date_of_birth IS DISTINCT FROM NEW.date_of_birth
        OR OLD.gender IS DISTINCT FROM NEW.gender
    )
    EXECUTE FUNCTION mark_patient_card_stale();

CREATE OR REPLACE FUNCTION mark_card_stale_on_blood_group_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.blood_group IS DISTINCT FROM NEW.blood_group THEN
        UPDATE patient_cards SET status = 'stale'
        WHERE patient_id = NEW.patient_id AND status = 'active';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS patient_profile_card_stale ON patient_medical_profile;
CREATE TRIGGER patient_profile_card_stale
    AFTER UPDATE OF blood_group ON patient_medical_profile
    FOR EACH ROW EXECUTE FUNCTION mark_card_stale_on_blood_group_change();

CREATE OR REPLACE FUNCTION mark_card_stale_on_address_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.is_current THEN
        UPDATE patient_cards SET status = 'stale'
        WHERE patient_id = NEW.patient_id AND status = 'active';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS patient_address_card_stale ON patient_addresses;
CREATE TRIGGER patient_address_card_stale
    AFTER INSERT OR UPDATE OF line1, line2, city, state, postal_code ON patient_addresses
    FOR EACH ROW EXECUTE FUNCTION mark_card_stale_on_address_change();

DROP TRIGGER IF EXISTS patient_cards_audit_change ON patient_cards;
CREATE TRIGGER patient_cards_audit_change
    AFTER INSERT OR UPDATE OR DELETE ON patient_cards
    FOR EACH ROW EXECUTE FUNCTION audit_row_change();
DROP TRIGGER IF EXISTS patient_medical_profile_audit_change ON patient_medical_profile;
CREATE TRIGGER patient_medical_profile_audit_change
    AFTER INSERT OR UPDATE OR DELETE ON patient_medical_profile
    FOR EACH ROW EXECUTE FUNCTION audit_row_change();
DROP TRIGGER IF EXISTS patient_cards_no_hard_delete ON patient_cards;
CREATE TRIGGER patient_cards_no_hard_delete
    BEFORE DELETE ON patient_cards
    FOR EACH ROW EXECUTE FUNCTION reject_hard_delete();
DROP TRIGGER IF EXISTS patient_medical_profile_no_hard_delete ON patient_medical_profile;
CREATE TRIGGER patient_medical_profile_no_hard_delete
    BEFORE DELETE ON patient_medical_profile
    FOR EACH ROW EXECUTE FUNCTION reject_hard_delete();

CREATE INDEX IF NOT EXISTS patients_full_name_lower_idx ON patients(organisation_id, lower(full_name));
CREATE INDEX IF NOT EXISTS patient_phones_patient_idx ON patient_phones(patient_id, created_at);
CREATE INDEX IF NOT EXISTS patient_conditions_patient_date_idx ON patient_conditions(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS patient_medicines_patient_date_idx ON patient_medicines(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS patient_surgeries_patient_date_idx ON patient_surgeries(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS patient_habits_patient_date_idx ON patient_habits(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS family_history_patient_date_idx ON family_history(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS medical_notes_patient_date_idx ON medical_notes(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS dental_procedures_patient_date_idx ON dental_procedures(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS gum_assessments_patient_date_idx ON gum_assessments(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS orthodontic_status_patient_date_idx ON orthodontic_status(patient_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS appointment_events_appointment_time_idx ON appointment_events(appointment_id, occurred_at DESC);
