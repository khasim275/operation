-- Public booking records are separated from the staff scheduling model so older
-- CRM appointment requests keep their existing status and conflict semantics.
CREATE TABLE IF NOT EXISTS opid_counters (
    day date PRIMARY KEY,
    last_seq integer NOT NULL CHECK (last_seq BETWEEN 1 AND 9999)
);

CREATE TABLE IF NOT EXISTS appointment_cards (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    opid text NOT NULL UNIQUE,
    patient_id uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    appointment_id uuid NOT NULL UNIQUE REFERENCES appointments(id) ON DELETE CASCADE,
    patient_name text NOT NULL,
    appointment_date date NOT NULL,
    time_slot text NOT NULL,
    token_no integer NOT NULL CHECK (token_no > 0),
    room text NOT NULL,
    status text NOT NULL DEFAULT 'CONFIRMED'
        CHECK (status IN ('CONFIRMED','CANCELLED','COMPLETED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (appointment_date, time_slot, token_no)
);
CREATE INDEX IF NOT EXISTS appointment_cards_date_slot_idx
    ON appointment_cards(appointment_date, time_slot);
CREATE INDEX IF NOT EXISTS appointment_cards_patient_idx
    ON appointment_cards(patient_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public_booking_idempotency (
    idempotency_key text PRIMARY KEY CHECK (length(idempotency_key) BETWEEN 16 AND 128),
    request_hash char(64) NOT NULL,
    appointment_card_id uuid NOT NULL REFERENCES appointment_cards(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_site_settings (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    hospital_name text NOT NULL DEFAULT 'Dental Hospital',
    card_finish text NOT NULL DEFAULT 'porcelain'
        CHECK (card_finish IN ('porcelain','midnight','sage')),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION book_appointment(
    p_name text,
    p_phone text,
    p_date date,
    p_slot text
)
RETURNS appointment_cards
LANGUAGE plpgsql
AS $$
DECLARE
    clinic_org uuid;
    clinic_branch uuid;
    patient_uuid uuid;
    crm_appointment uuid;
    result appointment_cards;
    daily_sequence integer;
    token integer;
    assigned_room text;
    daypart text;
    phone_lock bigint;
BEGIN
    IF length(btrim(p_name)) NOT BETWEEN 2 AND 120 THEN
        RAISE EXCEPTION 'Enter a name between 2 and 120 characters' USING ERRCODE = '22023';
    END IF;
    IF p_phone !~ '^\+[1-9][0-9]{7,14}$' THEN
        RAISE EXCEPTION 'Enter a valid international phone number' USING ERRCODE = '22023';
    END IF;
    IF p_date IS NULL OR p_date < current_date OR p_date > current_date + 365 THEN
        RAISE EXCEPTION 'Choose a date from today through the next 365 days' USING ERRCODE = '22023';
    END IF;
    IF p_slot !~ '^(0[8-9]|1[0-7]):00$' THEN
        RAISE EXCEPTION 'Choose an available hourly slot between 08:00 and 17:00' USING ERRCODE = '22023';
    END IF;

    SELECT id INTO clinic_org FROM organisations ORDER BY created_at LIMIT 1;
    IF clinic_org IS NULL THEN
        RAISE EXCEPTION 'Clinic is not configured yet' USING ERRCODE = '55000';
    END IF;
    SELECT id INTO clinic_branch FROM branches
    WHERE organisation_id = clinic_org AND is_active
    ORDER BY created_at LIMIT 1;
    IF clinic_branch IS NULL THEN
        RAISE EXCEPTION 'Clinic has no active branch' USING ERRCODE = '55000';
    END IF;

    -- Avoid duplicate patient rows when the same phone books concurrently.
    phone_lock := hashtextextended(clinic_org::text || ':' || p_phone, 0);
    PERFORM pg_advisory_xact_lock(phone_lock);
    SELECT pp.patient_id INTO patient_uuid
    FROM patient_phones pp
    JOIN patients p ON p.id = pp.patient_id
    WHERE p.organisation_id = clinic_org AND pp.phone_e164 = p_phone
      AND p.soft_deleted_at IS NULL
    ORDER BY pp.is_primary DESC, p.created_at
    LIMIT 1;
    IF patient_uuid IS NULL THEN
        INSERT INTO patients (organisation_id, full_name, preferred_language, referral_source)
        VALUES (clinic_org, btrim(p_name), 'en', 'website')
        RETURNING id INTO patient_uuid;
        INSERT INTO patient_phones (patient_id, phone_e164, is_primary)
        VALUES (patient_uuid, p_phone, true);
    END IF;

    -- One daily OPID sequence and one serialized token sequence per slot.
    INSERT INTO opid_counters(day, last_seq) VALUES (p_date, 1)
    ON CONFLICT (day) DO UPDATE SET last_seq = opid_counters.last_seq + 1
    RETURNING last_seq INTO daily_sequence;
    IF daily_sequence > 9999 THEN
        RAISE EXCEPTION 'No more OP IDs are available for this date' USING ERRCODE = '54000';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_date::text || ':' || p_slot, 1));
    SELECT coalesce(max(token_no), 0) + 1 INTO token
    FROM appointment_cards
    WHERE appointment_date = p_date AND time_slot = p_slot;

    SELECT name INTO assigned_room FROM rooms
    WHERE branch_id = clinic_branch AND is_active
    ORDER BY name
    OFFSET ((token - 1) % nullif((SELECT count(*) FROM rooms WHERE branch_id = clinic_branch AND is_active), 0))
    LIMIT 1;
    assigned_room := coalesce(assigned_room, 'Room ' || (((token - 1) % 6) + 1)::text);

    daypart := CASE
        WHEN split_part(p_slot, ':', 1)::integer < 12 THEN 'morning'
        WHEN split_part(p_slot, ':', 1)::integer < 16 THEN 'afternoon'
        ELSE 'evening'
    END;
    INSERT INTO appointments (
        patient_id, branch_id, source, reason, preferred_date,
        preferred_time_of_day, status
    ) VALUES (
        patient_uuid, clinic_branch, 'website', 'Online appointment booking',
        p_date, daypart, 'requested'
    ) RETURNING id INTO crm_appointment;

    INSERT INTO appointment_cards (
        opid, patient_id, appointment_id, patient_name,
        appointment_date, time_slot, token_no, room, status
    ) VALUES (
        'OP-' || to_char(p_date, 'YYMMDD') || '-' || lpad(daily_sequence::text, 4, '0'),
        patient_uuid, crm_appointment, btrim(p_name), p_date, p_slot,
        token, assigned_room, 'CONFIRMED'
    ) RETURNING * INTO result;
    RETURN result;
END;
$$;

CREATE OR REPLACE FUNCTION get_appointment(p_opid text)
RETURNS appointment_cards
LANGUAGE sql
STABLE
AS $$
    SELECT * FROM appointment_cards WHERE opid = upper(btrim(p_opid))
$$;

