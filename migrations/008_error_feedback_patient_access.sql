CREATE TABLE IF NOT EXISTS system_error_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), request_id text NOT NULL,
  source text NOT NULL, error_type text NOT NULL, path text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), acknowledged_at timestamptz
);
CREATE INDEX IF NOT EXISTS system_error_logs_recent_idx ON system_error_logs(created_at DESC) WHERE acknowledged_at IS NULL;

CREATE TABLE IF NOT EXISTS patient_feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  appointment_card_id uuid NOT NULL REFERENCES appointment_cards(id) ON DELETE CASCADE,
  rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comments text NOT NULL DEFAULT '' CHECK (length(comments) <= 3000),
  follow_up_status text NOT NULL DEFAULT 'NONE' CHECK (follow_up_status IN ('NONE','OPEN','IN_PROGRESS','CLOSED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (appointment_card_id)
);
CREATE INDEX IF NOT EXISTS patient_feedback_followup_idx ON patient_feedback(follow_up_status, created_at DESC);
CREATE TABLE IF NOT EXISTS patient_feedback_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), appointment_card_id uuid NOT NULL REFERENCES appointment_cards(id) ON DELETE CASCADE,
  token_hash bytea NOT NULL UNIQUE, expires_at timestamptz NOT NULL, consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS patient_feedback_delivery_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), appointment_card_id uuid NOT NULL UNIQUE REFERENCES appointment_cards(id) ON DELETE CASCADE,
  token_hash bytea NOT NULL, status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PROCESSING','COMPLETE')),
  attempts smallint NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient_history_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), patient_id uuid NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
  code_hash bytea NOT NULL, expires_at timestamptz NOT NULL, attempts smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
  consumed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS patient_history_challenge_active_idx ON patient_history_challenges(patient_id, expires_at DESC) WHERE consumed_at IS NULL;
