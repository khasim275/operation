ALTER TABLE notification_delivery_log ADD COLUMN IF NOT EXISTS recipient_email text;
ALTER TABLE notification_delivery_log ALTER COLUMN recipient_mobile_10_digit DROP NOT NULL;
ALTER TABLE notification_delivery_log DROP CONSTRAINT IF EXISTS notification_delivery_log_channel_check;
ALTER TABLE notification_delivery_log ADD CONSTRAINT notification_delivery_log_channel_check
  CHECK (channel IN ('WHATSAPP','SMS','EMAIL'));
ALTER TABLE notification_delivery_log ADD CONSTRAINT notification_delivery_recipient_check
  CHECK (recipient_mobile_10_digit IS NOT NULL OR recipient_email IS NOT NULL);
ALTER TABLE staff_otp_challenges ADD COLUMN IF NOT EXISTS delivery_channel text NOT NULL DEFAULT 'SMS'
  CHECK (delivery_channel IN ('SMS','EMAIL','WHATSAPP'));
ALTER TABLE staff_otp_challenges DROP CONSTRAINT IF EXISTS staff_otp_challenges_delivery_channel_check;
ALTER TABLE staff_otp_challenges ADD CONSTRAINT staff_otp_challenges_delivery_channel_check
  CHECK (delivery_channel IN ('SMS','EMAIL','WHATSAPP'));
ALTER TABLE staff_otp_challenges ADD COLUMN IF NOT EXISTS otp_reason text NOT NULL DEFAULT 'Staff 15-Minute OTP Portal Login & Password Reset';
ALTER TABLE patient_history_challenges ADD COLUMN IF NOT EXISTS otp_reason text NOT NULL DEFAULT 'Patient Visit History Verification';
