import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Html5Qrcode as Html5QrcodeInstance } from "html5-qrcode";
import { api } from "./api";
import "./patient-cards.css";

type LookupPatient = {
  id: string;
  op_id: string;
  full_name: string;
  date_of_birth: string | null;
  phone: string | null;
};

type CardState = {
  id: string;
  version: number;
  status: string;
  pdf_url: string;
  png_url: string;
};

type RegistrationResponse = {
  patient: LookupPatient & { patient_number: string };
  card: CardState;
};

type PatientFile = {
  patient: {
    id: string;
    op_id: string;
    patient_number: string;
    full_name: string;
    email: string | null;
    date_of_birth: string | null;
    gender: string | null;
    occupation: string | null;
    preferred_language: string;
    status: string;
    created_at: string;
    merged_into: string | null;
    phone: string | null;
    address: { line1: string | null; line2: string | null; city: string | null; state: string | null; postal_code: string | null };
  };
  medical_profile: Record<string, string>;
  phones: Array<{ phone: string; label: string; is_primary: boolean; verified_at: string | null }>;
  allergies: Array<{ allergen: string; reaction: string | null; severity: string; status: string; recorded_at: string }>;
  conditions: Array<{ name: string; status: string; onset_date: string | null; notes: string; recorded_at: string }>;
  medicines: Array<{ name: string; dose: string | null; frequency: string | null; start_date: string | null; end_date: string | null; status: string; recorded_at: string }>;
  surgeries: Array<{ name: string; date: string | null; notes: string; recorded_at: string }>;
  habits: Array<{ type: string; details: string; status: string; recorded_at: string }>;
  family_history: Array<{ relative: string; condition: string; recorded_at: string }>;
  medical_notes: Array<{ note: string; recorded_at: string; author: string }>;
  relationships: Array<{ type: string; name: string; phone: string | null; notes: string }>;
  consents: Array<{ type: string; policy_version: string; granted_at: string; withdrawn_at: string | null; source: string }>;
  messages: Array<{ channel: string; direction: string; message: string; delivery_status: string; occurred_at: string }>;
  calls: Array<{ started_at: string; outcome: string; notes: string; next_follow_up_at: string | null; caller: string; ticket_number: string }>;
  tooth_chart: Array<{ tooth: number; condition: string; details: string; recorded_at: string }>;
  dental_procedures: Array<{ name: string; tooth: number | null; performed_on: string | null; details: string; recorded_at: string }>;
  gum_assessments: Array<{ assessment: Record<string, unknown>; recorded_at: string; dentist: string }>;
  orthodontic_status: Array<{ status: string; details: string; recorded_at: string; dentist: string | null }>;
  visits: Array<{
    id: string; complaint: string; examination_findings: string; treatment_plan: string; recorded_at: string;
    dentist: string; diagnoses: Array<{ description: string }>;
    treatments: Array<{ procedure_name: string; fdi_tooth: number | null; surface: string | null; notes: string }>;
  }>;
  appointments: Array<{
    id: string; ticket_number: string; reason: string; start_at: string | null; status: string;
    preferred_date: string | null; preferred_time_of_day: string; dentist: string | null;
  }>;
  appointment_events: Array<{ ticket_number: string; event: string; old_status: string | null; new_status: string | null; details: Record<string, unknown>; occurred_at: string }>;
  followups: Array<{ scheduled_at: string; attempt_number: number; status: string; ticket_number: string }>;
  reminders: Array<{ type: string; due_at: string; status: string; ticket_number: string }>;
  prescriptions: Array<{
    id: string; created_at: string; status: string; dentist: string;
    items: Array<{ medicine_name: string; dose: string; frequency: string; duration: string; instructions: string }>;
  }>;
  documents: Array<{ id: string; original_name: string; mime_type: string; size_bytes: number; document_type: string; uploaded_at: string; url: string }>;
  payments: Array<{ currency: string; total_minor: number; paid_minor: number; outstanding_minor: number }> | null;
  invoices: Array<{ id: string; status: string; currency: string; created_at: string; total_minor: number; paid_minor: number; outstanding_minor: number }> | null;
  payment_details: Array<{ id: string; invoice_id: string; amount_minor: number; currency: string; method: string; status: string; received_at: string }> | null;
  payments_permitted: boolean;
  whatsapp_card_consent: boolean;
  cards: Array<{ id: string; version: number; status: string; issued_at: string; reason: string; print_count: number; sha256: string | null }>;
  notice: string | null;
};

type FileTab = "Details" | "Health" | "Visits and treatments" | "Prescriptions" | "Follow-ups and reminders" | "Files" | "Payments" | "Card";
const tabs: FileTab[] = ["Details", "Health", "Visits and treatments", "Prescriptions", "Follow-ups and reminders", "Files", "Payments", "Card"];

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

function normalizeName(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/g, "");
}

export function isValidOpId(value: string): boolean {
  const match = /^OP-(\d{6})-(\d)$/i.exec(value.trim());
  if (!match) return false;
  let total = 0;
  let doubleDigit = true;
  for (const character of match[1].split("").reverse()) {
    let digit = Number(character);
    if (doubleDigit) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    total += digit;
    doubleDigit = !doubleDigit;
  }
  return Number(match[2]) === (10 - total % 10) % 10;
}

function CardActions({
  opId,
  patientId,
  card,
  canManage,
  cardConsent,
  onConsent,
  onRefresh,
}: {
  opId: string;
  patientId: string;
  card: CardState | null;
  canManage: boolean;
  cardConsent: boolean;
  onConsent: () => void;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const [consentConfirmed, setConsentConfirmed] = useState(false);

  async function sendCard() {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      if (!cardConsent) {
        if (!consentConfirmed) {
          setError("Confirm that the patient agreed to receive the card on WhatsApp.");
          return;
        }
        await api(`/api/patients/${encodeURIComponent(patientId)}/card/consent`, {
          method: "POST",
          body: JSON.stringify({ granted: true }),
        });
        onConsent();
        setConsentConfirmed(false);
      }
      await api(`/api/patients/${encodeURIComponent(patientId)}/card/send-whatsapp`, {
        method: "POST",
        body: "{}",
      });
      setStatus("The card was sent using the approved WhatsApp template.");
    } catch (reasonValue) {
      setError(messageOf(reasonValue));
    } finally {
      setBusy(false);
    }
  }

  async function reissue() {
    if (reason.trim().length < 3) {
      setError("Enter a reason for reissuing this card.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/api/patients/${encodeURIComponent(patientId)}/card/reissue`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      });
      setReason("");
      setStatus("A new card version was issued. The previous version is superseded.");
      onRefresh();
    } catch (reasonValue) {
      setError(messageOf(reasonValue));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card-actions">
      {card && card.status === "active" && (
        <>
          <a className="button secondary" href={card.pdf_url} download={`${opId}.pdf`}>Download PDF</a>
          <button className="button secondary" onClick={() => window.open(card.pdf_url, "_blank", "noopener")} type="button">Print</button>
        </>
      )}
      {card && card.status === "stale" && <p className="patient-file-notice">This card is stale. Reissue it before printing or sharing.</p>}
      {canManage && (
        <div className="card-whatsapp-actions">
          {!cardConsent && <label className="consent-check"><input checked={consentConfirmed} onChange={(event) => setConsentConfirmed(event.target.checked)} type="checkbox" /> The patient has agreed to receive this card on WhatsApp.</label>}
          <button className="button whatsapp" disabled={busy || !card || card.status !== "active" || (!cardConsent && !consentConfirmed)} onClick={sendCard} type="button">
            {busy ? "Sending…" : "Send on WhatsApp"}
          </button>
        </div>
      )}
      {canManage && (
        <div className="card-reissue">
          <label htmlFor={`reissue-${patientId}`}>Reason for reissue</label>
          <input id={`reissue-${patientId}`} maxLength={500} onChange={(event) => setReason(event.target.value)} placeholder="Lost, damaged, or corrected details" value={reason} />
          <button className="button secondary" disabled={busy} onClick={reissue} type="button">{busy ? "Working…" : "Reissue card"}</button>
        </div>
      )}
      {status && <p className="form-success" role="status">{status}</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  );
}

function possibleDuplicateMatches(reason: unknown): LookupPatient[] | null {
  if (typeof reason !== "object" || reason === null || !("data" in reason)) return null;
  const data = reason.data;
  if (typeof data !== "object" || data === null || !("detail" in data)) return null;
  const detail = data.detail;
  if (typeof detail !== "object" || detail === null || !("matches" in detail) || !Array.isArray(detail.matches)) return null;
  return detail.matches.filter((item): item is LookupPatient =>
    typeof item === "object" && item !== null &&
    "id" in item && typeof item.id === "string" &&
    "op_id" in item && typeof item.op_id === "string" &&
    "full_name" in item && typeof item.full_name === "string",
  );
}

export function PatientRegistrationPage({
  openFile,
  notify,
}: {
  openFile: (opId: string) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [matches, setMatches] = useState<LookupPatient[]>([]);
  const [card, setCard] = useState<RegistrationResponse | null>(null);
  const [hasCardConsent, setHasCardConsent] = useState(false);
  const [key, setKey] = useState(() => crypto.randomUUID());

  useEffect(() => {
    if (!card || card.card.status !== "pending") return;
    const timer = window.setTimeout(async () => {
      try {
        const file = await api<PatientFile>(`/api/patients/by-op/${encodeURIComponent(card.patient.op_id)}`);
        const latest = file.cards[0];
        if (latest) {
          setCard((current) => current ? { ...current, card: { ...current.card, status: latest.status } } : current);
        }
      } catch (reason) {
        setError(messageOf(reason));
      }
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [card]);

  function formPayload(form: HTMLFormElement, confirmDifferent: boolean) {
    const values = new FormData(form);
    return {
      full_name: String(values.get("full_name") ?? "").trim(),
      phone: String(values.get("phone") ?? "").trim(),
      email: String(values.get("email") ?? "").trim() || null,
      date_of_birth: String(values.get("date_of_birth") ?? "") || null,
      gender: String(values.get("gender") ?? "").trim(),
      preferred_language: String(values.get("preferred_language") ?? "en"),
      address_line1: String(values.get("address_line1") ?? "").trim(),
      address_line2: String(values.get("address_line2") ?? "").trim(),
      city: String(values.get("city") ?? "").trim(),
      state: String(values.get("state") ?? "").trim(),
      postal_code: String(values.get("postal_code") ?? "").trim(),
      blood_group: String(values.get("blood_group") ?? "").trim(),
      allergies: String(values.get("allergies") ?? "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
      chronic_conditions: String(values.get("chronic_conditions") ?? "").trim(),
      current_medications: String(values.get("current_medications") ?? "").trim(),
      past_surgeries: String(values.get("past_surgeries") ?? "").trim(),
      pregnancy_status: String(values.get("pregnancy_status") ?? "not_recorded"),
      habits: String(values.get("habits") ?? "").trim(),
      family_history: String(values.get("family_history") ?? "").trim(),
      medical_notes: String(values.get("medical_notes") ?? "").trim(),
      emergency_contact_name: String(values.get("emergency_contact_name") ?? "").trim(),
      emergency_contact_phone: String(values.get("emergency_contact_phone") ?? "").trim(),
      emergency_contact_relationship: String(values.get("emergency_contact_relationship") ?? "").trim(),
      data_processing_consent: values.get("data_processing_consent") === "on",
      whatsapp_card_consent: values.get("whatsapp_card_consent") === "on",
      confirm_different_person: confirmDifferent,
    };
  }

  async function save(confirmDifferent: boolean) {
    const form = formRef.current;
    if (!form) return;
    setBusy(true);
    setError("");
    try {
      const payload = formPayload(form, confirmDifferent);
      const result = await api<RegistrationResponse>("/api/patients/register", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: JSON.stringify(payload),
      });
      setCard(result);
      setHasCardConsent(payload.whatsapp_card_consent);
      setMatches([]);
      setKey(crypto.randomUUID());
      notify(`Patient registered with OP ID ${result.patient.op_id}.`);
    } catch (reason) {
      const found = possibleDuplicateMatches(reason);
      if (found) {
        setMatches(found);
        setError("Possible matching patients found. Open an existing record, or confirm that this is a different person.");
        return;
      }
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setError("");
    setMatches([]);
    setBusy(true);
    try {
      const payload = formPayload(form, false);
      if (payload.date_of_birth) {
        const candidates = await api<LookupPatient[]>(`/api/patients/lookup?q=${encodeURIComponent(payload.phone)}`);
        const possible = candidates.filter((candidate) =>
          candidate.date_of_birth === payload.date_of_birth &&
          normalizeName(candidate.full_name) === normalizeName(payload.full_name),
        );
        if (possible.length) {
          setMatches(possible);
          setError("Possible matching patients found. Open an existing record, or confirm that this is a different person.");
          return;
        }
      }
      await save(false);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }

  async function sendFromSuccess() {
    if (!card) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/patients/${encodeURIComponent(card.patient.id)}/card/send-whatsapp`, {
        method: "POST",
        body: "{}",
      });
      notify("Patient card sent using the approved WhatsApp template.");
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }

  if (card) {
    return (
      <section className="card patient-card-success" aria-labelledby="registration-success">
        <p className="eyebrow">REGISTRATION COMPLETE</p>
        <h2 id="registration-success">Patient card ready</h2>
        <p className="muted">The patient file is saved. The permanent OP ID is <strong>{card.patient.op_id}</strong>.</p>
        {card.card.status === "pending" ? (
          <div className="empty-state" role="status">Preparing the card PDF and preview…</div>
        ) : (
          <>
            <img className="patient-card-preview" src={card.card.png_url} alt={`Front and back of the patient card for ${card.patient.full_name}`} />
            <div className="card-actions">
              <a className="button secondary" download={`${card.patient.op_id}.pdf`} href={card.card.pdf_url}>Download PDF</a>
              <button className="button secondary" onClick={() => window.open(card.card.pdf_url, "_blank", "noopener")} type="button">Print</button>
              <button className="button whatsapp" disabled={busy || !hasCardConsent} onClick={sendFromSuccess} type="button">
                {busy ? "Sending…" : "Send on WhatsApp"}
              </button>
              {!hasCardConsent && <small className="muted">WhatsApp sharing is disabled until the patient gives separate consent.</small>}
              <button className="button primary" onClick={() => openFile(card.patient.op_id)} type="button">Open patient file</button>
            </div>
          </>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button secondary section-gap" onClick={() => { setCard(null); setHasCardConsent(false); formRef.current?.reset(); }} type="button">Register another patient</button>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="card-header">
        <div><h2>Register a patient</h2><p>Registration creates a permanent OP ID and patient card.</p></div>
        <span className="source-tag">New walk-in</span>
      </div>
      <form className="patient-registration-form" onSubmit={submit} ref={formRef}>
        <fieldset disabled={busy}>
          <legend>Personal details</legend>
          <div className="patient-card-form-grid">
            <label>Full name<input autoComplete="name" maxLength={200} minLength={2} name="full_name" required /></label>
            <label>Mobile number<input autoComplete="tel" maxLength={30} name="phone" required type="tel" /></label>
            <label>Email (optional)<input autoComplete="email" maxLength={254} name="email" type="email" /></label>
            <label>Date of birth<input name="date_of_birth" type="date" /></label>
            <label>Gender<input maxLength={40} name="gender" /></label>
            <label>Preferred language<input defaultValue="en" maxLength={12} minLength={2} name="preferred_language" required /></label>
            <label className="span-2">Address<input autoComplete="street-address" maxLength={250} name="address_line1" /></label>
            <label>Address line 2<input maxLength={250} name="address_line2" /></label>
            <label>City<input autoComplete="address-level2" maxLength={120} name="city" /></label>
            <label>State<input autoComplete="address-level1" maxLength={120} name="state" /></label>
            <label>Postal code<input autoComplete="postal-code" maxLength={20} name="postal_code" /></label>
          </div>
          <legend>Health and emergency details</legend>
          <div className="patient-card-form-grid">
            <label>Blood group<input maxLength={8} name="blood_group" placeholder="Optional" /></label>
            <label>Pregnancy status<select defaultValue="not_recorded" name="pregnancy_status"><option value="not_recorded">Not recorded</option><option value="not_applicable">Not applicable</option><option value="no">No</option><option value="yes">Yes</option><option value="prefer_not_to_say">Prefer not to say</option></select></label>
            <label className="span-2">Allergies<textarea maxLength={3000} name="allergies" placeholder="One allergy per line" rows={2} /></label>
            <label>Chronic conditions<textarea maxLength={4000} name="chronic_conditions" rows={2} /></label>
            <label>Current medicines<textarea maxLength={4000} name="current_medications" rows={2} /></label>
            <label>Past surgeries<textarea maxLength={4000} name="past_surgeries" rows={2} /></label>
            <label>Habits<textarea maxLength={2000} name="habits" rows={2} /></label>
            <label>Family history<textarea maxLength={4000} name="family_history" rows={2} /></label>
            <label>Medical notes<textarea maxLength={8000} name="medical_notes" rows={2} /></label>
            <label>Emergency contact name<input maxLength={200} name="emergency_contact_name" /></label>
            <label>Emergency contact phone<input maxLength={30} name="emergency_contact_phone" type="tel" /></label>
            <label>Relationship<input maxLength={80} name="emergency_contact_relationship" /></label>
          </div>
          <div className="consent-list">
            <label className="consent-check"><input name="data_processing_consent" required type="checkbox" /> Patient consent for processing their registration and health details has been recorded.</label>
            <label className="consent-check"><input name="whatsapp_card_consent" type="checkbox" /> Patient separately consents to receive their card image on WhatsApp.</label>
          </div>
        </fieldset>
        {error && <p className="form-error" role="alert">{error}</p>}
        {busy && <p className="muted" role="status">Checking records and saving registration…</p>}
        <button className="button primary" disabled={busy} type="submit">{busy ? "Saving…" : "Save registration"}</button>
      </form>
      {matches.length > 0 && (
        <div aria-label="Possible duplicate patients" className="duplicate-prompt" role="alert">
          <h3>Check before continuing</h3>
          <p>These patients share the mobile number, name and date of birth entered.</p>
          {matches.map((patient) => (
            <article className="duplicate-match" key={patient.id}>
              <span><strong>{patient.full_name}</strong><small>{patient.op_id} · {patient.date_of_birth || "Date of birth not recorded"}</small></span>
              <button className="button secondary small" onClick={() => openFile(patient.op_id)} type="button">Open existing patient</button>
            </article>
          ))}
          <button className="button primary" disabled={busy} onClick={() => void save(true)} type="button">This is a different person</button>
        </div>
      )}
    </section>
  );
}

export function PatientOpFilePage({
  opId,
  source = "view",
  roles,
  notify,
}: {
  opId: string;
  source?: "view" | "scan";
  roles: string[];
  notify: (message: string, error?: boolean) => void;
}) {
  const [data, setData] = useState<PatientFile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<FileTab>("Details");
  const [cardConsent, setCardConsent] = useState(false);
  const [savingDetails, setSavingDetails] = useState(false);
  const [editError, setEditError] = useState("");
  const [savingHealth, setSavingHealth] = useState(false);
  const [healthError, setHealthError] = useState("");
  const canManageCard = roles.some((role) => ["admin", "receptionist"].includes(role));
  const canEditHealth = roles.some((role) => ["admin", "receptionist", "dentist"].includes(role));

  async function load(): Promise<boolean> {
    setLoading(true);
    setError("");
    try {
      if (!isValidOpId(opId)) throw new Error("This ID is not valid, please check the last digit");
      const result = await api<PatientFile>(`/api/patients/by-op/${encodeURIComponent(opId)}?source=${source}`);
      setData(result);
      setCardConsent(result.whatsapp_card_consent);
      if (result.patient.merged_into) notify("This record was merged and opens the surviving patient file.");
      return true;
    } catch (reason) {
      setError(messageOf(reason));
      return false;
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // Load once per OP ID and access method.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opId, source]);

  if (loading) return <div className="empty-state" role="status">Opening the patient file…</div>;
  if (error) return <div className="empty-state" role="alert"><strong>Patient file unavailable</strong><p>{error}</p><button className="button secondary" onClick={() => void load()} type="button">Try again</button></div>;
  if (!data) return <div className="empty-state">No patient file is available.</div>;
  const patient = data.patient;
  const latestCard = data.cards[0];
  const card = latestCard ? {
    id: latestCard.id,
    version: latestCard.version,
    status: latestCard.status,
    pdf_url: `/api/cards/${encodeURIComponent(patient.op_id)}.pdf`,
    png_url: `/api/cards/${encodeURIComponent(patient.op_id)}.png`,
  } : null;
  const availableTabs = tabs.filter((name) => name !== "Payments" || data.payments_permitted);
  const activeAllergies = data.allergies.filter((item) => item.status === "active");

  async function saveDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingDetails(true);
    setEditError("");
    try {
      const form = new FormData(event.currentTarget);
      const values = Object.fromEntries([...form.entries()].filter(([, value]) => value !== ""));
      await api(`/api/patients/${encodeURIComponent(patient.id)}`, { method: "PATCH", body: JSON.stringify(values) });
      if (await load()) notify("Patient details updated. The card may need to be reissued.");
    } catch (reason) {
      setEditError(messageOf(reason));
    } finally {
      setSavingDetails(false);
    }
  }

  async function saveHealth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingHealth(true);
    setHealthError("");
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget).entries());
      await api(`/api/patients/${encodeURIComponent(patient.id)}/medical-profile`, {
        method: "PATCH",
        body: JSON.stringify(values),
      });
      if (await load()) notify("Health details updated.");
    } catch (reason) {
      setHealthError(messageOf(reason));
    } finally {
      setSavingHealth(false);
    }
  }

  return (
    <div className="patient-file">
      {data.notice && <div className="patient-file-notice" role="status">{data.notice}</div>}
      <header className="patient-file-header">
        <div><p className="eyebrow">{patient.patient_number} · PERMANENT RECORD</p><h2>{patient.full_name}</h2><strong className="op-id-large">{patient.op_id}</strong></div>
        <div className="patient-file-header-actions">
          <span className="source-tag">{patient.status}</span>
          {card?.status === "active" && <a className="button secondary small" href={card.pdf_url} download={`${patient.op_id}.pdf`}>Patient card PDF</a>}
        </div>
      </header>
      <section aria-label="Allergy and medical alert" className={`allergy-warning ${activeAllergies.length ? "has-allergies" : ""}`}>
        <strong>{activeAllergies.length ? "⚠ Medical alert — active allergies" : "Medical alert"}</strong>
        {activeAllergies.length ? (
          <ul>{activeAllergies.map((item) => <li key={`${item.allergen}-${item.recorded_at}`}>{item.allergen} · {item.severity}{item.reaction ? ` — ${item.reaction}` : ""}</li>)}</ul>
        ) : <p>No active allergies recorded. Confirm this with the patient before treatment.</p>}
      </section>
      <nav aria-label="Patient file sections" className="patient-file-tabs">
        {availableTabs.map((name) => (
          <button aria-current={tab === name ? "page" : undefined} className={tab === name ? "active" : ""} key={name} onClick={() => setTab(name)} type="button">{name}</button>
        ))}
      </nav>
      {tab === "Details" && (
        <section className="card patient-file-panel">
          <div className="card-header"><div><h3>Personal details</h3><p>OP ID stays the same when details are corrected.</p></div></div>
          <form className="patient-card-form-grid" onSubmit={saveDetails}>
            <label>Full name<input defaultValue={patient.full_name} maxLength={200} minLength={1} name="full_name" required /></label>
            <label>Date of birth<input defaultValue={patient.date_of_birth || ""} name="date_of_birth" type="date" /></label>
            <label>Gender<input defaultValue={patient.gender || ""} maxLength={40} name="gender" /></label>
            <label>Email<input defaultValue={patient.email || ""} name="email" type="email" /></label>
            <label>Mobile number<input defaultValue={patient.phone || ""} disabled type="tel" /></label>
            <label>Preferred language<input defaultValue={patient.preferred_language} maxLength={12} minLength={2} name="preferred_language" /></label>
            <label className="span-2">Address<input defaultValue={patient.address.line1 || ""} maxLength={250} name="address_line1" /></label>
            <label>Address line 2<input defaultValue={patient.address.line2 || ""} maxLength={250} name="address_line2" /></label>
            <label>City<input defaultValue={patient.address.city || ""} maxLength={120} name="city" /></label>
            <label>State<input defaultValue={patient.address.state || ""} maxLength={120} name="state" /></label>
            <label>Postal code<input defaultValue={patient.address.postal_code || ""} maxLength={20} name="postal_code" /></label>
            {editError && <p className="form-error span-2" role="alert">{editError}</p>}
            <button className="button primary" disabled={savingDetails} type="submit">{savingDetails ? "Saving…" : "Save details"}</button>
          </form>
          <h3 className="section-gap">Contact numbers</h3>
          {data.phones.length ? data.phones.map((item, index) => <article className="list-link" key={`${item.phone}-${index}`}><span>{item.phone}<small>{item.label || "Phone"}{item.is_primary ? " · primary" : ""}</small></span><span>{item.verified_at ? "Verified" : "Not verified"}</span></article>) : <div className="empty-state">No phone numbers recorded.</div>}
          <h3 className="section-gap">Emergency and guardian contacts</h3>
          {data.relationships.length ? data.relationships.map((item, index) => <article className="list-link" key={`${item.type}-${index}`}><span>{item.name}<small>{item.type} · {item.phone || "No phone recorded"}</small></span><span>{item.notes}</span></article>) : <div className="empty-state">No additional contacts recorded.</div>}
          <h3 className="section-gap">Consent history</h3>
          {data.consents.length ? data.consents.map((item, index) => <article className="list-link" key={`${item.type}-${index}`}><span>{item.type}<small>{item.policy_version} · {item.source}</small></span><span>{item.withdrawn_at ? "Withdrawn" : "Granted"} · {new Date(item.granted_at).toLocaleDateString()}</span></article>) : <div className="empty-state">No consent records found.</div>}
        </section>
      )}
      {tab === "Health" && (
        <section className="card patient-file-panel">
          <h3>Health history</h3>
          {canEditHealth && (
            <form className="patient-card-form-grid" onSubmit={saveHealth}>
              <label>Blood group<input defaultValue={data.medical_profile.blood_group || ""} maxLength={8} name="blood_group" /></label>
              <label>Pregnancy status<select defaultValue={data.medical_profile.pregnancy_status || "not_recorded"} name="pregnancy_status"><option value="not_recorded">Not recorded</option><option value="not_applicable">Not applicable</option><option value="no">No</option><option value="yes">Yes</option><option value="prefer_not_to_say">Prefer not to say</option></select></label>
              <label className="span-2">Chronic conditions<textarea defaultValue={data.medical_profile.chronic_conditions || ""} maxLength={4000} name="chronic_conditions" rows={2} /></label>
              <label>Current medicines<textarea defaultValue={data.medical_profile.current_medications || ""} maxLength={4000} name="current_medications" rows={2} /></label>
              <label>Past surgeries<textarea defaultValue={data.medical_profile.past_surgeries || ""} maxLength={4000} name="past_surgeries" rows={2} /></label>
              <label>Habits<textarea defaultValue={data.medical_profile.habits || ""} maxLength={2000} name="habits" rows={2} /></label>
              <label>Family history<textarea defaultValue={data.medical_profile.family_history || ""} maxLength={4000} name="family_history" rows={2} /></label>
              <label className="span-2">Medical notes<textarea defaultValue={data.medical_profile.notes || ""} maxLength={8000} name="notes" rows={3} /></label>
              <label>Emergency contact name<input defaultValue={data.medical_profile.emergency_contact_name || ""} maxLength={200} name="emergency_contact_name" /></label>
              <label>Emergency contact phone<input defaultValue={data.medical_profile.emergency_contact_phone || ""} maxLength={30} name="emergency_contact_phone" type="tel" /></label>
              <label>Relationship<input defaultValue={data.medical_profile.emergency_contact_relationship || ""} maxLength={80} name="emergency_contact_relationship" /></label>
              {healthError && <p className="form-error span-2" role="alert">{healthError}</p>}
              <button className="button primary" disabled={savingHealth} type="submit">{savingHealth ? "Saving…" : "Save health details"}</button>
            </form>
          )}
          <div className="patient-card-form-grid">
            {Object.entries(data.medical_profile).map(([label, value]) => <div className="detail-pair" key={label}><span>{label.replaceAll("_", " ")}</span><b>{value || "Not recorded"}</b></div>)}
          </div>
          <h3 className="section-gap">Conditions</h3>
          {data.conditions.length ? data.conditions.map((condition, index) => <article className="timeline-item" key={`${condition.name}-${index}`}><strong>{condition.name}</strong><small>{condition.status} · {condition.notes || "No note recorded"}</small></article>) : <div className="empty-state">No conditions recorded.</div>}
          <h3 className="section-gap">Medicines</h3>
          {data.medicines.length ? data.medicines.map((item, index) => <article className="list-link" key={`${item.name}-${index}`}><span>{item.name}<small>{item.dose || "Dose not recorded"} · {item.frequency || "Frequency not recorded"}</small></span><span>{item.status}{item.start_date ? ` · from ${item.start_date}` : ""}{item.end_date ? ` to ${item.end_date}` : ""}</span></article>) : <div className="empty-state">No medicines recorded.</div>}
          <h3 className="section-gap">Past surgeries</h3>
          {data.surgeries.length ? data.surgeries.map((item, index) => <article className="timeline-item" key={`${item.name}-${index}`}><strong>{item.name}</strong><small>{item.date || "Date not recorded"} · {item.notes || "No note recorded"}</small></article>) : <div className="empty-state">No surgeries recorded.</div>}
          <h3 className="section-gap">Habits and family history</h3>
          {[...data.habits.map((item, index) => ({ key: `habit-${index}`, title: item.type, detail: `${item.status} · ${item.details}` })), ...data.family_history.map((item, index) => ({ key: `family-${index}`, title: item.relative, detail: item.condition }))].length ? (
            [...data.habits.map((item, index) => ({ key: `habit-${index}`, title: item.type, detail: `${item.status} · ${item.details}` })), ...data.family_history.map((item, index) => ({ key: `family-${index}`, title: item.relative, detail: item.condition }))].map((item) => <article className="list-link" key={item.key}><strong>{item.title}</strong><span>{item.detail}</span></article>)
          ) : <div className="empty-state">No habits or family history recorded.</div>}
          <h3 className="section-gap">Tooth chart (FDI)</h3>
          {data.tooth_chart.length ? data.tooth_chart.map((tooth) => <article className="list-link" key={tooth.tooth}><strong>{tooth.tooth} · {tooth.condition}</strong><span>{tooth.details}</span></article>) : <div className="empty-state">No tooth chart entries.</div>}
          <h3 className="section-gap">Previous dental procedures</h3>
          {data.dental_procedures.length ? data.dental_procedures.map((item, index) => <article className="timeline-item" key={`${item.name}-${index}`}><strong>{item.name}{item.tooth ? ` · tooth ${item.tooth}` : ""}</strong><small>{item.performed_on || "Date not recorded"} · {item.details || "No details recorded."}</small></article>) : <div className="empty-state">No previous dental procedures recorded.</div>}
          <h3 className="section-gap">Gum health</h3>
          {data.gum_assessments.length ? data.gum_assessments.map((item, index) => <article className="timeline-item" key={`${item.recorded_at}-${index}`}><time>{new Date(item.recorded_at).toLocaleDateString()} · {item.dentist}</time><pre>{JSON.stringify(item.assessment, null, 2)}</pre></article>) : <div className="empty-state">No gum assessments recorded.</div>}
          <h3 className="section-gap">Orthodontic status</h3>
          {data.orthodontic_status.length ? data.orthodontic_status.map((item, index) => <article className="timeline-item" key={`${item.recorded_at}-${index}`}><time>{new Date(item.recorded_at).toLocaleDateString()} · {item.dentist || "Staff record"}</time><strong>{item.status}</strong><small>{item.details || "No details recorded."}</small></article>) : <div className="empty-state">No orthodontic status recorded.</div>}
          <h3 className="section-gap">Medical notes</h3>
          {data.medical_notes.length ? data.medical_notes.map((item, index) => <article className="timeline-item" key={`${item.recorded_at}-${index}`}><time>{new Date(item.recorded_at).toLocaleString()} · {item.author}</time><p>{item.note}</p></article>) : <div className="empty-state">No medical notes recorded.</div>}
        </section>
      )}
      {tab === "Visits and treatments" && (
        <section className="card patient-file-panel">
          <h3>Visits and treatments</h3>
          {data.visits.length ? data.visits.map((visit) => (
            <article className="timeline-item" key={visit.id}>
              <time>{new Date(visit.recorded_at).toLocaleString()} · {visit.dentist}</time>
              <strong>{visit.complaint}</strong>
              <small>{visit.examination_findings || "No examination findings recorded."}</small>
              <small>{visit.treatment_plan || "No treatment plan recorded."}</small>
              {visit.diagnoses.map((item, index) => <small key={index}>Diagnosis: {item.description}</small>)}
              {visit.treatments.map((item, index) => <small key={index}>Treatment: {item.procedure_name}{item.fdi_tooth ? ` · tooth ${item.fdi_tooth}` : ""}{item.surface ? ` · ${item.surface}` : ""}</small>)}
            </article>
          )) : <div className="empty-state">No visits or treatments recorded.</div>}
          <h3 className="section-gap">Appointments</h3>
          {data.appointments.length ? data.appointments.map((item) => <article className="list-link" key={item.id}><span>{item.ticket_number} · {item.reason}<small>{item.start_at || item.preferred_date || "Date to be confirmed"} · {item.dentist || "Dentist not assigned"}</small></span><span className="source-tag">{item.status}</span></article>) : <div className="empty-state">No appointments recorded.</div>}
          <h3 className="section-gap">Call history</h3>
          {data.calls.length ? data.calls.map((item, index) => <article className="timeline-item" key={`${item.started_at}-${index}`}><time>{new Date(item.started_at).toLocaleString()} · {item.caller}</time><strong>{item.outcome} · {item.ticket_number}</strong><small>{item.notes || "No call note recorded."}{item.next_follow_up_at ? ` · Follow-up ${new Date(item.next_follow_up_at).toLocaleString()}` : ""}</small></article>) : <div className="empty-state">No calls recorded.</div>}
          <h3 className="section-gap">Appointment history</h3>
          {data.appointment_events.length ? data.appointment_events.map((item, index) => <article className="timeline-item" key={`${item.occurred_at}-${index}`}><time>{new Date(item.occurred_at).toLocaleString()} · {item.ticket_number}</time><strong>{item.event.replaceAll("_", " ")}</strong><small>{item.old_status || "—"} → {item.new_status || "—"}</small></article>) : <div className="empty-state">No appointment changes recorded.</div>}
          <h3 className="section-gap">Messages</h3>
          {data.messages.length ? data.messages.map((item, index) => <article className="timeline-item" key={`${item.occurred_at}-${index}`}><time>{new Date(item.occurred_at).toLocaleString()} · {item.channel} · {item.direction}</time><strong>{item.delivery_status}</strong><p>{item.message}</p></article>) : <div className="empty-state">No messages recorded.</div>}
        </section>
      )}
      {tab === "Prescriptions" && (
        <section className="card patient-file-panel">
          <h3>Prescriptions</h3>
          {data.prescriptions.length ? data.prescriptions.map((prescription) => (
            <article className="timeline-item" key={prescription.id}>
              <time>{new Date(prescription.created_at).toLocaleDateString()} · {prescription.dentist}</time>
              <strong>{prescription.status}</strong>
              {prescription.items.map((item, index) => <small key={index}>{item.medicine_name} · {item.dose} · {item.frequency} · {item.duration}{item.instructions ? ` · ${item.instructions}` : ""}</small>)}
            </article>
          )) : <div className="empty-state">No prescriptions recorded.</div>}
        </section>
      )}
      {tab === "Follow-ups and reminders" && (
        <section className="card patient-file-panel">
          <h3>Follow-ups</h3>
          {data.followups.length ? data.followups.map((item, index) => <article className="list-link" key={`${item.ticket_number}-${index}`}><span>{item.ticket_number} · attempt {item.attempt_number}<small>{new Date(item.scheduled_at).toLocaleString()}</small></span><span className="source-tag">{item.status}</span></article>) : <div className="empty-state">No follow-ups scheduled.</div>}
          <h3 className="section-gap">Appointment reminders</h3>
          {data.reminders.length ? data.reminders.map((item, index) => <article className="list-link" key={`${item.ticket_number}-${index}`}><span>{item.ticket_number} · {item.type}<small>{new Date(item.due_at).toLocaleString()}</small></span><span className="source-tag">{item.status}</span></article>) : <div className="empty-state">No reminders recorded.</div>}
        </section>
      )}
      {tab === "Files" && (
        <section className="card patient-file-panel">
          <h3>Patient files</h3>
          {data.documents.length ? data.documents.map((item) => <a className="list-link" href={item.url} key={item.id} rel="noreferrer" target="_blank"><span>{item.original_name}<small>{item.document_type} · {new Date(item.uploaded_at).toLocaleDateString()}</small></span><span>{Math.ceil(item.size_bytes / 1024)} KB</span></a>) : <div className="empty-state">No files uploaded.</div>}
        </section>
      )}
      {tab === "Payments" && data.payments_permitted && (
        <section className="card patient-file-panel">
          <h3>Payment summary</h3>
          {data.payments?.length ? data.payments.map((payment) => (
            <div className="patient-card-form-grid" key={payment.currency}>
              <div className="detail-pair"><span>Total invoiced ({payment.currency})</span><b>{(payment.total_minor / 100).toFixed(2)}</b></div>
              <div className="detail-pair"><span>Paid</span><b>{(payment.paid_minor / 100).toFixed(2)}</b></div>
              <div className="detail-pair"><span>Outstanding</span><b>{(payment.outstanding_minor / 100).toFixed(2)}</b></div>
            </div>
          )) : <div className="empty-state">No payment history recorded.</div>}
          <h3 className="section-gap">Invoices</h3>
          {data.invoices?.length ? data.invoices.map((invoice) => <article className="list-link" key={invoice.id}><span>{invoice.id}<small>{invoice.currency} · {(invoice.total_minor / 100).toFixed(2)} · Paid {(invoice.paid_minor / 100).toFixed(2)}</small></span><span className="source-tag">{invoice.status}</span></article>) : <div className="empty-state">No invoices recorded.</div>}
          <h3 className="section-gap">Payments</h3>
          {data.payment_details?.length ? data.payment_details.map((payment) => <article className="list-link" key={payment.id}><span>{(payment.amount_minor / 100).toFixed(2)} {payment.currency}<small>{payment.method} · {new Date(payment.received_at).toLocaleString()}</small></span><span className="source-tag">{payment.status}</span></article>) : <div className="empty-state">No individual payments recorded.</div>}
        </section>
      )}
      {tab === "Card" && (
        <section className="card patient-file-panel">
          <div className="card-header"><div><h3>Patient card</h3><p>The QR code contains only this OP ID link.</p></div><span className="source-tag">{card?.status || "not issued"}</span></div>
          {card?.status === "active" ? <img className="patient-card-preview" src={card.png_url} alt={`Front and back of card for ${patient.full_name}`} /> : <div className="empty-state">{card?.status === "pending" ? "The patient card is being prepared." : card?.status === "stale" ? "The patient card is stale. Reissue it to print current details." : "No active patient card is available."}</div>}
          {data.cards.length > 0 && <div className="list-links">{data.cards.map((item) => <article className="list-link" key={item.id}><span>Version {item.version} · {new Date(item.issued_at).toLocaleDateString()}<small>{item.reason} · {item.print_count} prints</small></span><span className="source-tag">{item.status}</span></article>)}</div>}
          <CardActions
            canManage={canManageCard}
            card={card}
            cardConsent={cardConsent}
            onConsent={() => setCardConsent(true)}
            onRefresh={() => void load()}
            opId={patient.op_id}
            patientId={patient.id}
          />
        </section>
      )}
    </div>
  );
}

type BarcodeDetectorResult = { rawValue: string };
type BarcodeDetectorLike = { detect: (video: HTMLVideoElement) => Promise<BarcodeDetectorResult[]> };
type BarcodeDetectorConstructor = new (options?: { formats: string[] }) => BarcodeDetectorLike;
type WindowWithBarcodeDetector = Window & { BarcodeDetector?: BarcodeDetectorConstructor };

export function PatientCardScanner({ onScan, onClose }: { onScan: (opId: string) => void; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const elementId = useRef(`patient-card-scan-${crypto.randomUUID()}`);
  const scanRef = useRef(onScan);
  const [error, setError] = useState("");
  const [usingFallback, setUsingFallback] = useState(false);

  useEffect(() => {
    scanRef.current = onScan;
  }, [onScan]);

  useEffect(() => {
    let active = true;
    let stream: MediaStream | null = null;
    let scanner: Html5QrcodeInstance | null = null;
    let frame = 0;

    function accept(value: string) {
      const match = /(?:\/p\/)?(OP-\d{6}-\d)/i.exec(value.trim());
      if (!match || !isValidOpId(match[1])) {
        setError("This card ID is not valid. Check the last digit and scan again.");
        return;
      }
      scanRef.current(match[1].toUpperCase());
    }

    async function start() {
      try {
        const Detector = (window as WindowWithBarcodeDetector).BarcodeDetector;
        if (Detector && videoRef.current && navigator.mediaDevices?.getUserMedia) {
          setUsingFallback(false);
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
          if (!active || !videoRef.current) return;
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
          const detector = new Detector({ formats: ["qr_code"] });
          const detect = async () => {
            if (!active || !videoRef.current) return;
            try {
              const results = await detector.detect(videoRef.current);
              if (results[0]?.rawValue) {
                accept(results[0].rawValue);
                return;
              }
            } catch {
              setError("The camera could not read this card. Try moving closer or use the ID field.");
            }
            frame = window.requestAnimationFrame(() => { void detect(); });
          };
          frame = window.requestAnimationFrame(() => { void detect(); });
          return;
        }
        setUsingFallback(true);
        const { Html5Qrcode } = await import("html5-qrcode");
        scanner = new Html5Qrcode(elementId.current);
        await scanner.start(
          { facingMode: "environment" },
          { fps: 10, qrbox: { width: 240, height: 240 } },
          accept,
          () => undefined,
        );
      } catch {
        setError("Camera access is unavailable. Allow camera permission or type the OP ID in the search bar.");
      }
    }

    void start();
    return () => {
      active = false;
      window.cancelAnimationFrame(frame);
      stream?.getTracks().forEach((track) => track.stop());
      if (scanner) void scanner.stop().catch(() => undefined);
    };
  }, []);

  return (
    <div className="dialog-backdrop" role="presentation">
      <section aria-labelledby="card-scanner-title" aria-modal="true" className="dialog patient-scanner-dialog" role="dialog">
        <div className="dialog-header">
          <div><p className="eyebrow">PATIENT RECORDS</p><h2 id="card-scanner-title">Scan patient card</h2></div>
          <button aria-label="Close scanner" className="icon-button" onClick={onClose} type="button">×</button>
        </div>
        <video aria-label="Camera preview" className="patient-scanner-video" hidden={usingFallback} muted playsInline ref={videoRef} />
        <div aria-label="QR camera fallback" className="patient-scanner-fallback" hidden={!usingFallback} id={elementId.current} />
        {error && <p className="form-error" role="alert">{error}</p>}
        <p className="muted">Hold the QR code inside the frame. USB barcode scanners can type the OP ID into global search and press Enter.</p>
        <button className="button secondary" onClick={onClose} type="button">Close</button>
      </section>
    </div>
  );
}

