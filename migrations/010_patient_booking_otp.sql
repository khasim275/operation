CREATE TABLE IF NOT EXISTS public_booking_otp_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  phone_e164 text NOT NULL CHECK (left(phone_e164,3) = '+91' AND length(phone_e164) = 13
    AND substring(phone_e164,4,1) BETWEEN '6' AND '9' AND substring(phone_e164,4) ~ '^[0-9]{10}$'),
  email text NOT NULL,
  delivery_channel text NOT NULL CHECK (delivery_channel IN ('SMS','EMAIL','WHATSAPP')),
  otp_reason text NOT NULL CHECK (otp_reason = 'Patient Booking & OP Confirmation'),
  request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
  code_hash bytea NOT NULL,
  attempts smallint NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
  expires_at timestamptz NOT NULL,
  code_consumed_at timestamptz,
  access_token_hash bytea,
  access_expires_at timestamptz,
  appointment_card_id uuid UNIQUE REFERENCES appointment_cards(id) ON DELETE SET NULL,
  idempotency_key text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS public_booking_otp_active_idx ON public_booking_otp_challenges(phone_e164, email, expires_at DESC) WHERE code_consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS public_booking_otp_token_idx ON public_booking_otp_challenges(access_token_hash) WHERE access_token_hash IS NOT NULL;
