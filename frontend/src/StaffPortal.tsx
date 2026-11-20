import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import "./staff-portal.css";

type PortalUser = { id: string; full_name: string; email: string; roles: string[]; session_expires_at: string; password_expires_at: string };
type Reference = { found: boolean; query: string; compounds: string[]; uses: string[]; safety_notes: string[]; matches: Array<{ name: string; type: string }>; sources: string[]; disclaimer: string };

function csrf() {
  return document.cookie.split("; ").find((part) => part.startsWith("dental_crm_csrf="))?.split("=").slice(1).join("=") ?? "";
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  const token = csrf();
  if (token) headers.set("X-CSRF-Token", decodeURIComponent(token));
  const response = await fetch(path, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "The secure portal could not complete that request.");
  return result as T;
}

export default function StaffPortal() {
  const [user, setUser] = useState<PortalUser | null>(null);
  const [email, setEmail] = useState("");
  const [purpose, setPurpose] = useState<"PORTAL_LOGIN" | "PASSWORD_RESET">("PORTAL_LOGIN");
  const [channel, setChannel] = useState<"SMS" | "EMAIL" | "WHATSAPP">("WHATSAPP");
  const otpReason = purpose === "PORTAL_LOGIN" ? "Staff 15-Minute OTP Portal Login" : "Staff Credential Password Reset (30-day renewal)";
  const [code, setCode] = useState("");
  const [requested, setRequested] = useState(false);
  const [demoCode, setDemoCode] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [query, setQuery] = useState("");
  const [reference, setReference] = useState<Reference | null>(null);
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api<PortalUser>("/api/secure-portal/me").then((current) => {
      setUser(current);
      setSeconds(Math.max(0, Math.floor((new Date(current.session_expires_at).getTime() - Date.now()) / 1000)));
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!user) return;
    const timer = window.setInterval(() => {
      setSeconds((current) => {
        if (current <= 1) {
          window.clearInterval(timer);
          setUser(null);
          setReference(null);
          setError("Your 15-minute session ended. Request a fresh OTP to continue.");
          void api("/api/secure-portal/logout", { method: "POST" }).catch(() => undefined);
          return 0;
        }
        return current - 1;
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [user]);

  async function requestCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setMessage(""); setDemoCode("");
    try {
      const result = await api<{ message: string; demo_code?: string }>("/api/secure-portal/otp/request", {
        method: "POST", body: JSON.stringify({ email, purpose, channel, otp_reason: otpReason }),
      });
      setRequested(true);
      setDemoCode(result.demo_code ?? "");
      setMessage(result.message);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not request a code."); }
    finally { setBusy(false); }
  }

  async function verifyCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api<PortalUser & { session_expires_in: number }>("/api/secure-portal/otp/verify", {
        method: "POST", body: JSON.stringify({ email, purpose, channel, otp_reason: otpReason, code }),
      });
      const expires = new Date(Date.now() + result.session_expires_in * 1000).toISOString();
      setUser({ ...result, session_expires_at: expires, password_expires_at: "" });
      setSeconds(result.session_expires_in);
      setRequested(false); setCode(""); setDemoCode("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The code could not be verified."); }
    finally { setBusy(false); }
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      await api("/api/secure-portal/password", { method: "POST", body: JSON.stringify({ password }) });
      setPassword(""); setMessage("Password updated. It is valid for 30 days. Sign in to the staff workspace with the new password.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update password."); }
    finally { setBusy(false); }
  }

  async function searchMedicine(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setReference(null);
    try {
      setReference(await api<Reference>("/api/medicine-reference?query=" + encodeURIComponent(query)));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Medicine reference lookup failed."); }
    finally { setBusy(false); }
  }

  async function logout() {
    await api("/api/secure-portal/logout", { method: "POST" }).catch(() => undefined);
    setUser(null); setReference(null); setSeconds(0); setMessage("Signed out securely.");
  }

  return (
    <main className="secure-portal">
      <header className="portal-header"><a className="portal-mark" href="/">✳ <span>Gangayya Dental Hospital<small>SECURE STAFF PORTAL</small></span></a><a href="/staff">Staff workspace ↗</a></header>
      {!user ? (
        <section className="portal-card">
          <p className="portal-kicker">STAFF VERIFICATION</p><h1>Private access,<br /><em>kept simple.</em></h1>
          <p className="portal-copy">A fresh one-time code is sent for: <strong>{otpReason}</strong>.</p>
          {!requested ? (
            <form onSubmit={requestCode}>
              <label htmlFor="portal-email">Staff email</label><input id="portal-email" autoComplete="username" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
              <label htmlFor="portal-purpose">I need to</label><select id="portal-purpose" value={purpose} onChange={(event) => setPurpose(event.target.value as typeof purpose)}><option value="PORTAL_LOGIN">Open staff portal</option><option value="PASSWORD_RESET">Reset an expired password</option></select>
              <label htmlFor="portal-channel">Send code to</label><select id="portal-channel" value={channel} onChange={(event) => setChannel(event.target.value as typeof channel)}><option value="WHATSAPP">Registered mobile by WhatsApp, SMS fallback</option><option value="SMS">Registered mobile by SMS</option><option value="EMAIL">Staff email</option></select>
              <button className="portal-primary" disabled={busy} type="submit">{busy ? "Sending code…" : "Send one-time code"}</button>
            </form>
          ) : (
            <form onSubmit={verifyCode}>
              <p className="portal-copy"><strong>Reason:</strong> {otpReason} · <strong>Delivery:</strong> {channel}</p>
              <label htmlFor="portal-otp">Six-digit code</label><input id="portal-otp" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} />
              {demoCode && <p className="portal-demo-code">Local demo code: <strong>{demoCode}</strong></p>}
              <button className="portal-primary" disabled={busy || code.length !== 6} type="submit">{busy ? "Verifying…" : "Verify and continue"}</button>
              <button className="portal-secondary" disabled={busy} onClick={() => setRequested(false)} type="button">Use a different email or purpose</button>
            </form>
          )}
          {message && <p className="portal-message" role="status">{message}</p>}
          {error && <p className="portal-error" role="alert">{error}</p>}
          <p className="portal-footnote">Codes expire after 15 minutes. Never share a code with anyone.</p>
        </section>
      ) : (
        <div className="portal-workspace">
          <section className="portal-welcome"><div><p className="portal-kicker">VERIFIED STAFF</p><h1>Hello, {user.full_name.split(" ")[0]}.</h1><p>Secure session ends in <strong>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, "0")}</strong></p></div><button className="portal-secondary" onClick={() => void logout()} type="button">Sign out</button></section>
          <div className="portal-grid">
            <section className="portal-panel"><p className="portal-kicker">ACCOUNT SECURITY</p><h2>Update password</h2><p className="portal-copy">Staff workspace credentials expire every 30 days.</p><form onSubmit={changePassword}><label htmlFor="new-password">New password</label><input id="new-password" autoComplete="new-password" minLength={12} maxLength={256} type="password" required value={password} onChange={(event) => setPassword(event.target.value)} /><button className="portal-primary" disabled={busy} type="submit">{busy ? "Saving…" : "Save new password"}</button></form></section>
            <section className="portal-panel"><p className="portal-kicker">REFERENCE DESK</p><h2>Medicine reference</h2><p className="portal-copy">Search ingredient, brand, label uses, and safety notes.</p><form onSubmit={searchMedicine}><label htmlFor="medicine-query">Medicine or brand</label><input id="medicine-query" minLength={2} maxLength={100} required value={query} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. amoxicillin" /><button className="portal-primary" disabled={busy} type="submit">{busy ? "Searching…" : "Search reference"}</button></form></section>
          </div>
          {reference && <section className="portal-panel reference-result"><h2>{reference.found ? "Reference: " + reference.query : "No match for " + reference.query}</h2>{reference.compounds.length > 0 && <><h3>Listed ingredients</h3><p>{reference.compounds.join(", ")}</p></>}{reference.matches.length > 0 && <><h3>Terminology matches</h3><ul>{reference.matches.map((item, index) => <li key={item.name + "-" + index}>{item.name} <small>{item.type}</small></li>)}</ul></>}{reference.uses.length > 0 && <><h3>Label indications / purpose</h3>{reference.uses.map((item, index) => <p key={index}>{item}</p>)}</>}{reference.safety_notes.length > 0 && <><h3>Contraindications and warnings</h3>{reference.safety_notes.map((item, index) => <p key={index}>{item}</p>)}</>}<aside>{reference.disclaimer}</aside><small>Sources: {reference.sources.map((source, index) => <span key={source}>{index > 0 ? " · " : ""}<a href={source} target="_blank" rel="noreferrer">{index === 0 ? "NLM RxNorm / RxNav" : "US FDA drug labels"}</a></span>)}</small></section>}
          {message && <p className="portal-message" role="status">{message}</p>}{error && <p className="portal-error" role="alert">{error}</p>}
        </div>
      )}
    </main>
  );
}
