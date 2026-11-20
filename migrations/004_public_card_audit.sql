-- Keep the customer pass within the CRM's change history and live tenant feed.
DROP TRIGGER IF EXISTS audit_change ON appointment_cards;
CREATE TRIGGER audit_change
    AFTER INSERT OR UPDATE OR DELETE ON appointment_cards
    FOR EACH ROW EXECUTE FUNCTION audit_row_change();

DROP TRIGGER IF EXISTS no_hard_delete ON appointment_cards;
CREATE TRIGGER no_hard_delete
    BEFORE DELETE ON appointment_cards
    FOR EACH ROW EXECUTE FUNCTION reject_hard_delete();

DROP TRIGGER IF EXISTS audit_change ON public_site_settings;
CREATE TRIGGER audit_change
    AFTER INSERT OR UPDATE OR DELETE ON public_site_settings
    FOR EACH ROW EXECUTE FUNCTION audit_row_change();
