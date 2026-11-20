import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";

type OpsRecord = {
  op_id: string; appointment_date: string; time_slot: string; status: string;
  medicines_given: Array<{ name: string; dose?: string; frequency?: string; duration?: string }>;
  medical_files: Array<{ id: string; name: string; type: string }>;
  is_operation: boolean; operation_files: Array<{ id: string; name: string; type: string }>;
  operation_summary: string; amount_inr: string; payment_status: "UNPAID" | "PARTIAL" | "PAID" | "WAIVED";
};

function OpsCard({ item, patientId, canClinical, canBilling, notify }: {
  item: OpsRecord; patientId: string; canClinical: boolean; canBilling: boolean; notify: (message: string, error?: boolean) => void;
}) {
  const [medicines, setMedicines] = useState(item.medicines_given.map((m) => [m.name, m.dose, m.frequency, m.duration].filter(Boolean).join(" | ")).join("\n"));
  const [operation, setOperation] = useState(item.is_operation);
  const [summary, setSummary] = useState(item.operation_summary);
  const [amount, setAmount] = useState(item.amount_inr);
  const [payment, setPayment] = useState(item.payment_status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const payload: Record<string, unknown> = {};
      if (canClinical) {
        payload.medicines_given = medicines.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
          const [name, dose = "", frequency = "", duration = ""] = line.split("|").map((part) => part.trim());
          return { name, dose, frequency, duration };
        });
        payload.is_operation = operation;
        payload.operation_summary = summary;
      }
      if (canBilling) {
        payload.amount_inr = Number(amount || 0);
        payload.payment_status = payment;
      }
      await api("/api/clinical-ops/" + encodeURIComponent(item.op_id), { method: "PATCH", body: JSON.stringify(payload) });
      notify("Clinical OP record saved.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to save the OP record."); }
    finally { setBusy(false); }
  }

  async function upload(event: FormEvent<HTMLFormElement>, type: string) {
    event.preventDefault(); const form = event.currentTarget; const file = form.querySelector<HTMLInputElement>("input[type=file]")?.files?.[0];
    if (!file) return;
    setBusy(true); setError("");
    try {
      const body = new FormData(); body.set("file", file);
      await api("/api/patients/" + encodeURIComponent(patientId) + "/documents?document_type=" + type + "&op_id=" + encodeURIComponent(item.op_id), { method: "POST", body });
      form.reset(); notify("File uploaded to the private patient record.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Upload failed."); }
    finally { setBusy(false); }
  }

  const fileList = [...item.medical_files, ...item.operation_files];
  return (
    <article className="card clinical-op-card">
      <div className="card-header"><div><p className="eyebrow">OP APPOINTMENT</p><h3>{item.op_id}</h3><p>{item.appointment_date} · {item.time_slot} · {item.status}</p></div><span className="badge">{item.payment_status}</span></div>
      <form className="op-record-form" onSubmit={save}>
        {canClinical && <>
          <label>Medicines given <small>One per line: name | dose | frequency | duration</small><textarea rows={3} value={medicines} onChange={(event) => setMedicines(event.target.value)} placeholder="Medicine name | 500 mg | Twice daily | 5 days" /></label>
          <label className="op-check"><input type="checkbox" checked={operation} onChange={(event) => setOperation(event.target.checked)} /> Operation / procedure performed</label>
          {operation && <label>Operation summary<textarea rows={3} value={summary} onChange={(event) => setSummary(event.target.value)} /></label>}
        </>}
        {canBilling && <div className="op-billing"><label>Amount (INR)<input type="number" min="0" step="0.01" value={amount} onChange={(event) => setAmount(event.target.value)} /></label><label>Payment status<select value={payment} onChange={(event) => setPayment(event.target.value as OpsRecord["payment_status"])}><option>UNPAID</option><option>PARTIAL</option><option>PAID</option><option>WAIVED</option></select></label></div>}
        {(canClinical || canBilling) && <button className="button primary" disabled={busy} type="submit">{busy ? "Saving…" : "Save OP record"}</button>}
      </form>
      {canClinical && <div className="op-uploads">
        <form onSubmit={(event) => void upload(event, "report")}><label>Medical report / X-ray<input type="file" accept="application/pdf,image/jpeg,image/png" capture="environment" required /></label><button className="button secondary small" disabled={busy}>Upload file</button></form>
        {operation && <form onSubmit={(event) => void upload(event, "operation")}><label>Operation file<input type="file" accept="application/pdf,image/jpeg,image/png" capture="environment" required /></label><button className="button secondary small" disabled={busy}>Upload operation file</button></form>}
        {operation && <form onSubmit={(event) => void upload(event, "operation_consent")}><label>Operation consent<input type="file" accept="application/pdf,image/jpeg,image/png" capture="environment" required /></label><button className="button secondary small" disabled={busy}>Upload consent</button></form>}
      </div>}
      {fileList.length > 0 && <div className="list-links op-file-list">{fileList.map((file) => <a className="list-link" key={file.id} href={"/api/documents/" + encodeURIComponent(file.id)} target="_blank" rel="noreferrer">{file.name} · {file.type}</a>)}</div>}
      {error && <p className="form-error" role="alert">{error}</p>}
    </article>
  );
}

export default function ClinicalOpsPanel({ patientId, canClinical, canBilling, notify }: {
  patientId: string; canClinical: boolean; canBilling: boolean; notify: (message: string, error?: boolean) => void;
}) {
  const [items, setItems] = useState<OpsRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void api<OpsRecord[]>("/api/clinical-ops?patient_id=" + encodeURIComponent(patientId))
      .then((value) => { if (active) setItems(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Could not load OP records."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [patientId]);
  return <section className="clinical-op-panel"><div className="card-header"><div><h2>Appointment and clinical OP records</h2><p>Patient history linked through the OP ID.</p></div></div>
    {loading ? <div className="empty-state">Loading OP records…</div> : error ? <p className="form-error" role="alert">{error}</p> : items.length ? items.map((item) => <OpsCard key={item.op_id} item={item} patientId={patientId} canClinical={canClinical} canBilling={canBilling} notify={notify} />) : <div className="empty-state">No website OP cards have been issued for this patient.</div>}
  </section>;
}
