ALTER TABLE patients ALTER COLUMN patient_public_id SET DEFAULT
  ('PAT-' || to_char((now() AT TIME ZONE 'Asia/Kolkata')::date, 'YYYY') || '-' || lpad(nextval('patient_public_id_seq')::text, 6, '0'));
DROP FUNCTION IF EXISTS book_appointment(text, text, date, text);
CREATE FUNCTION book_appointment(
    p_name text, p_phone text, p_date date, p_slot text,
    p_email text, p_problem text DEFAULT 'Online appointment booking'
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
    local_now timestamp;
BEGIN
    IF length(btrim(p_name)) NOT BETWEEN 2 AND 120 THEN
        RAISE EXCEPTION 'Enter a name between 2 and 120 characters' USING ERRCODE = '22023';
    END IF;
    IF left(p_phone, 3) <> '+91' OR length(p_phone) <> 13
       OR substring(p_phone, 4, 1) NOT BETWEEN '6' AND '9'
       OR substring(p_phone, 4) !~ '^[0-9]{10}$' THEN
        RAISE EXCEPTION 'Enter exactly 10 valid Indian mobile digits' USING ERRCODE = '22023';
    END IF;
    IF p_email IS NULL OR p_email !~* '^[A-Z0-9._%+-]+@[A-Z0-9.-]*[.][A-Z]{2,}$' THEN
        RAISE EXCEPTION 'Enter a valid email address' USING ERRCODE = '22023';
    END IF;
    IF p_date IS NULL OR p_date < (now() AT TIME ZONE 'Asia/Kolkata')::date
       OR p_date > (now() AT TIME ZONE 'Asia/Kolkata')::date + 365 THEN
        RAISE EXCEPTION 'Choose a date from today through the next 365 days' USING ERRCODE = '22023';
    END IF;
    IF p_slot !~ '^((09)|(1[0-9])):00$' THEN
        RAISE EXCEPTION 'Choose an available hourly slot between 09:00 and 19:00' USING ERRCODE = '22023';
    END IF;
    local_now := now() AT TIME ZONE 'Asia/Kolkata';
    IF (p_date + p_slot::time) <= local_now THEN
        RAISE EXCEPTION 'Choose a future appointment time in India Standard Time' USING ERRCODE = '22023';
    END IF;
    IF p_problem IS NULL OR length(btrim(p_problem)) NOT BETWEEN 2 AND 500 THEN
        RAISE EXCEPTION 'Enter a short reason for the visit' USING ERRCODE = '22023';
    END IF;

    SELECT id INTO clinic_org FROM organisations ORDER BY created_at LIMIT 1;
    IF clinic_org IS NULL THEN RAISE EXCEPTION 'Clinic is not configured yet' USING ERRCODE = '55000'; END IF;
    SELECT id INTO clinic_branch FROM branches
      WHERE organisation_id = clinic_org AND is_active ORDER BY created_at LIMIT 1;
    IF clinic_branch IS NULL THEN RAISE EXCEPTION 'Clinic has no active branch' USING ERRCODE = '55000'; END IF;

    phone_lock := hashtextextended(clinic_org::text || ':' || p_phone, 0);
    PERFORM pg_advisory_xact_lock(phone_lock);
    SELECT pp.patient_id INTO patient_uuid FROM patient_phones pp
      JOIN patients p ON p.id = pp.patient_id
      WHERE p.organisation_id = clinic_org AND pp.phone_e164 = p_phone AND p.soft_deleted_at IS NULL
      ORDER BY pp.is_primary DESC, p.created_at LIMIT 1;
    IF patient_uuid IS NULL THEN
      INSERT INTO patients (organisation_id, full_name, email, mobile_10_digit, preferred_language, referral_source)
      VALUES (clinic_org, btrim(p_name), lower(btrim(p_email)), right(p_phone, 10), 'en', 'website')
      RETURNING id INTO patient_uuid;
      INSERT INTO patient_phones (patient_id, phone_e164, is_primary) VALUES (patient_uuid, p_phone, true);
    ELSE
      UPDATE patients SET email = coalesce(email, lower(btrim(p_email))), mobile_10_digit = right(p_phone, 10)
        WHERE id = patient_uuid;
    END IF;

    INSERT INTO opid_counters(day, last_seq) VALUES (p_date, 1)
      ON CONFLICT (day) DO UPDATE SET last_seq = opid_counters.last_seq + 1
      RETURNING last_seq INTO daily_sequence;
    IF daily_sequence > 9999 THEN RAISE EXCEPTION 'No more OP IDs are available for this date' USING ERRCODE = '54000'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(p_date::text || ':' || p_slot, 1));
    SELECT coalesce(max(token_no), 0) + 1 INTO token FROM appointment_cards
      WHERE appointment_date = p_date AND time_slot = p_slot;
    SELECT name INTO assigned_room FROM rooms WHERE branch_id = clinic_branch AND is_active
      ORDER BY name OFFSET ((token - 1) % nullif((SELECT count(*) FROM rooms WHERE branch_id = clinic_branch AND is_active), 0)) LIMIT 1;
    assigned_room := coalesce(assigned_room, 'Room ' || (((token - 1) % 6) + 1)::text);
    daypart := CASE WHEN split_part(p_slot, ':', 1)::integer < 12 THEN 'morning'
                    WHEN split_part(p_slot, ':', 1)::integer < 16 THEN 'afternoon' ELSE 'evening' END;
    INSERT INTO appointments (patient_id, branch_id, source, reason, preferred_date, preferred_time_of_day, status)
      VALUES (patient_uuid, clinic_branch, 'website', btrim(p_problem), p_date, daypart, 'requested')
      RETURNING id INTO crm_appointment;
    INSERT INTO appointment_cards (opid, patient_id, appointment_id, patient_name, appointment_date,
        time_slot, token_no, room, status, problem_summary)
      VALUES ('OP-' || to_char(p_date, 'YYMMDD') || '-' || lpad(daily_sequence::text, 4, '0'),
        patient_uuid, crm_appointment, btrim(p_name), p_date, p_slot, token, assigned_room, 'CONFIRMED', btrim(p_problem))
      RETURNING * INTO result;
    INSERT INTO clinical_records_billing(op_id, patient_id) VALUES (result.opid, patient_uuid);
    RETURN result;
END;
$$;

CREATE OR REPLACE FUNCTION get_patient_history(p_patient_id uuid DEFAULT NULL, p_mobile_10_digit text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE matched_patient uuid; result jsonb;
BEGIN
  IF (p_patient_id IS NULL) = (p_mobile_10_digit IS NULL) THEN
    RAISE EXCEPTION 'Supply exactly one patient id or mobile number' USING ERRCODE = '22023';
  END IF;
  IF p_mobile_10_digit IS NOT NULL AND p_mobile_10_digit !~ '^[6-9][0-9]{9}$' THEN
    RAISE EXCEPTION 'Mobile number must be exactly 10 Indian digits' USING ERRCODE = '22023';
  END IF;
  SELECT id INTO matched_patient FROM patients
    WHERE (p_patient_id IS NOT NULL AND id = p_patient_id)
       OR (p_mobile_10_digit IS NOT NULL AND mobile_10_digit = p_mobile_10_digit)
    LIMIT 1;
  IF matched_patient IS NULL THEN RETURN NULL; END IF;
  SELECT jsonb_build_object(
    'patient', (SELECT to_jsonb(p) - 'id_proof_encrypted' - 'photo_document_id' FROM patients p WHERE p.id = matched_patient),
    'appointments', coalesce((SELECT jsonb_agg(jsonb_build_object(
      'op_id', op.op_id, 'appointment_date_ist', op.appointment_date_ist, 'appointment_time', op.appointment_time,
      'problem_summary', op.problem_summary, 'status', op.status, 'delivery_channel', op.delivery_channel,
      'token_no', op.token_no, 'room', op.room, 'clinical', jsonb_build_object(
        'medicines_given', cr.medicines_given, 'medical_files', cr.medical_files,
        'is_operation', cr.is_operation, 'operation_files', cr.operation_files,
        'operation_summary', cr.operation_summary, 'amount_inr', cr.amount_inr, 'payment_status', cr.payment_status))
      ORDER BY op.appointment_date_ist DESC, op.appointment_time DESC)
      FROM appointments_op op JOIN clinical_records_billing cr ON cr.op_id = op.op_id
      WHERE op.patient_id = matched_patient), '[]'::jsonb),
    'documents', coalesce((SELECT jsonb_agg(jsonb_build_object('id',d.id,'name',d.original_name,'type',d.document_type,
      'uploaded_at',d.uploaded_at) ORDER BY d.uploaded_at DESC) FROM documents d WHERE d.patient_id = matched_patient AND d.status='active'),'[]'::jsonb),
    'prescriptions', coalesce((SELECT jsonb_agg(jsonb_build_object('id',pr.id,'created_at',pr.created_at,
      'status',pr.status) ORDER BY pr.created_at DESC) FROM prescriptions pr WHERE pr.patient_id=matched_patient),'[]'::jsonb)
  ) INTO result;
  RETURN result;
END;
$$;
