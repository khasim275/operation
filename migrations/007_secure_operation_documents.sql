ALTER TABLE documents ADD COLUMN IF NOT EXISTS appointment_card_id uuid REFERENCES appointment_cards(id) ON DELETE SET NULL;
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_document_type_check;
ALTER TABLE documents ADD CONSTRAINT documents_document_type_check
  CHECK (document_type IN ('xray','photo','report','consent','operation','operation_consent','operation_summary','other'));
CREATE INDEX IF NOT EXISTS documents_appointment_idx ON documents(appointment_card_id, uploaded_at DESC)
  WHERE appointment_card_id IS NOT NULL;
