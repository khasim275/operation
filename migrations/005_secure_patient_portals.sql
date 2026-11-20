-- Shared patient identity, appointment clinical record, staff password lifecycle,
-- OTP portal sessions, and notification delivery audit.
CREATE SEQUENCE IF NOT EXISTS patient_public_id_seq;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS patient_public_id text;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS mobile_10_digit text;
ALTER TABLE patients ADD COLUMN IF NOT EXISTS email text;
UPDATE patients SET patient_public_id = 'PAT-' || to_char(created_at AT TIME ZONE 'Asia/Kolkata', 'YYYY') || '-' || lpad(nextval('patient_public_id_seq')::text, 6, '0') WHERE patient_public_id IS NULL;
UPDATE patients p SET mobile_10_digit = right(pp.phone_e164, 10)
FROM patient_phones pp WHERE pp.patient_id = p.id AND pp.is_primary AND p.mobile_10_digit IS NULL
  AND left(pp.phone_e164, 3) = '+91' AND length(pp.phone_e164) = 13
  AND substring(pp.phone_e164, 4, 1) BETWEEN '6' AND '9'
  AND substring(pp.phone_e164, 4) ~ '^[0-9]{10}$';
CREATE UNIQUE INDEX IF NOT EXISTS patients_public_id_uidx ON patients(patient_public_id);
CREATE INDEX IF NOT EXISTS patients_mobile_10_idx ON patients(mobile_10_digit) WHERE mobile_10_digit IS NOT NULL;
ALTER TABLE patients ADD CONSTRAINT patients_mobile_10_check
  CHECK (mobile_10_digit IS NULL OR mobile_10_digit ~ '^[6-9][0-9]{9}$');
ALTER TABLE patients ALTER COLUMN patient_public_id SET NOT NULL;

ALTER TABLE users ADD COLUMN IF NOT EXISTS mobile_10_digit text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_expires_at timestamptz;
UPDATE users SET password_expires_at = password_updated_at + interval '30 days' WHERE password_expires_at IS NULL;
ALTER TABLE users ALTER COLUMN password_expires_at SET NOT NULL;
ALTER TABLE users ALTER COLUMN password_expires_at SET DEFAULT (now() + interval '30 days');
ALTER TABLE users ADD CONSTRAINT users_mobile_10_check
  CHECK (mobile_10_digit IS NULL OR mobile_10_digit ~ '^[6-9][0-9]{9}$');
CREATE UNIQUE INDEX IF NOT EXISTS users_mobile_10_uidx ON users(mobile_10_digit) WHERE mobile_10_digit IS NOT NULL;

ALTER TABLE appointment_cards ADD COLUMN IF NOT EXISTS delivery_channel text NOT NULL DEFAULT 'PENDING'
  CHECK (delivery_channel IN ('WHATSAPP','SMS','FAILED','PENDING'));
ALTER TABLE appointment_cards ADD COLUMN IF NOT EXISTS delivery_status text NOT NULL DEFAULT 'QUEUED'
  CHECK (delivery_status IN ('QUEUED','SENT','DELIVERED','FAILED'));
ALTER TABLE appointment_cards ADD COLUMN IF NOT EXISTS problem_summary text NOT NULL DEFAULT '';
ALTER TABLE appointment_cards ADD CONSTRAINT appointment_cards_op_patient_uidx UNIQUE (opid, patient_id);

CREATE TABLE IF NOT EXISTS clinical_records_billing (
  record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  op_id text NOT NULL,
  patient_id uuid NOT NULL REFERENCES patients(id) ON DELETE RESTRICT,
  medicines_given jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(medicines_given) = 'array'),
  medical_files jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(medical_files) = 'array'),
  is_operation boolean NOT NULL DEFAULT false,
  operation_files jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(operation_files) = 'array'),
  operation_summary text NOT NULL DEFAULT '',
  amount_inr numeric(12,2) NOT NULL DEFAULT 0 CHECK (amount_inr >= 0),
  payment_status text NOT NULL DEFAULT 'UNPAID' CHECK (payment_status IN ('UNPAID','PARTIAL','PAID','WAIVED')),
  created_at_ist timestamptz NOT NULL DEFAULT now(),
  updated_at_ist timestamptz NOT NULL DEFAULT now(),
  UNIQUE (op_id),
  FOREIGN KEY (op_id, patient_id) REFERENCES appointment_cards(opid, patient_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS clinical_records_patient_history_idx ON clinical_records_billing(patient_id, created_at_ist DESC);

-- Staff's email remains users.email; the mobile is a required second-factor destination
-- before enabling OTP login for an individual staff account.
CREATE TABLE IF NOT EXISTS staff_otp_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash bytea NOT NULL,
  purpose text NOT NULL CHECK (purpose IN ('PORTAL_LOGIN','PASSWORD_RESET')),
  expires_at timestamptz NOT NULL,
  attempts smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS staff_otp_active_idx ON staff_otp_challenges(user_id, purpose, expires_at DESC) WHERE consumed_at IS NULL;
CREATE TABLE IF NOT EXISTS staff_portal_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash bytea NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE staff_portal_sessions ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'PORTAL_LOGIN'
  CHECK (purpose IN ('PORTAL_LOGIN','PASSWORD_RESET'));
CREATE INDEX IF NOT EXISTS staff_portal_sessions_expiry_idx ON staff_portal_sessions(expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS notification_delivery_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  appointment_card_id uuid REFERENCES appointment_cards(id) ON DELETE SET NULL,
  recipient_mobile_10_digit text NOT NULL CHECK (recipient_mobile_10_digit ~ '^[6-9][0-9]{9}$'),
  message_type text NOT NULL CHECK (message_type IN ('APPOINTMENT_CARD','OTP')),
  channel text NOT NULL CHECK (channel IN ('WHATSAPP','SMS')),
  provider_status text NOT NULL CHECK (provider_status IN ('SENT','DELIVERED','FAILED')),
  fallback_reason text,
  provider_reference text,
  error_summary text,
  timestamp_ist timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notification_delivery_card_idx ON notification_delivery_log(appointment_card_id, timestamp_ist DESC);
CREATE INDEX IF NOT EXISTS notification_delivery_phone_idx ON notification_delivery_log(recipient_mobile_10_digit, timestamp_ist DESC);

CREATE TABLE IF NOT EXISTS public_appointment_delivery_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  appointment_card_id uuid NOT NULL UNIQUE REFERENCES appointment_cards(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','COMPLETE')),
  attempts smallint NOT NULL DEFAULT 0,
  available_at timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS public_appointment_delivery_due_idx ON public_appointment_delivery_outbox(available_at) WHERE status = 'PENDING';

CREATE OR REPLACE VIEW appointments_op AS
SELECT c.opid AS op_id, c.patient_id, p.patient_public_id, c.problem_summary,
       c.appointment_date AS appointment_date_ist, c.time_slot AS appointment_time,
       c.status, c.delivery_channel, c.created_at AS created_at_ist,
       c.patient_name, p.mobile_10_digit, p.email, c.token_no, c.room
FROM appointment_cards c JOIN patients p ON p.id = c.patient_id;
