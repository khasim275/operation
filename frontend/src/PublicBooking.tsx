import { type FormEvent, useEffect, useRef, useState } from "react";
import "./public-booking.css";

type AppointmentCard = {
  id: string;
  opid: string;
  patient_name: string;
  patient_mobile?: string;
  patient_email?: string;
  patient_id?: string;
  patient_public_id?: string;
  appointment_date: string;
  time_slot: string;
  token_no: number;
  room: string;
  status: string;
};
type SiteSettings = { hospital_name: string; card_finish: string; time_slots: string[] };
type Preferences = { cardFinish: "porcelain" | "midnight" | "sage"; muted: boolean };
type PatientHistory = { patient_id: string; visits: { op_id: string; appointment_date_ist: string; appointment_time_ist: string; status: string; instructions: string }[] };
type BookingDetails = { name: string; phone: string; email: string; appointment_date: string; time_slot: string; problem_summary: string };
const CARD_STORAGE = "dental-hospital.latest-appointment.v1";
const PREF_STORAGE = "dental-hospital.public-preferences.v1";
const defaultPreferences: Preferences = { cardFinish: "porcelain", muted: false };

function readPreferences(): Preferences {
  try {
    return { ...defaultPreferences, ...JSON.parse(localStorage.getItem(PREF_STORAGE) ?? "{}") };
  } catch {
    return defaultPreferences;
  }
}

function csrfToken(): string {
  return document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith("dental_crm_csrf="))?.split("=").slice(1).join("=") ?? "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  const csrf = csrfToken();
  if (csrf) headers.set("X-CSRF-Token", decodeURIComponent(csrf));
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : "The server could not complete this request.";
    throw new Error(detail);
  }
  return data as T;
}

function todayValue(): string {
  const today = new Date();
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
}

function prettyDate(date: string): string {
  const parsed = new Date(`${date}T12:00:00`);
  return Number.isNaN(parsed.getTime()) ? date : new Intl.DateTimeFormat(undefined, { dateStyle: "long" }).format(parsed);
}

function useSound(muted: boolean) {
  const contextRef = useRef<AudioContext | null>(null);
  const mutedRef = useRef(muted);
  mutedRef.current = muted;

  function context(): AudioContext | null {
    if (mutedRef.current) return null;
    if (!contextRef.current) {
      const BrowserAudio = window.AudioContext;
      if (!BrowserAudio) return null;
      contextRef.current = new BrowserAudio();
    }
    if (contextRef.current.state === "suspended") void contextRef.current.resume();
    return contextRef.current;
  }

  function tick() {
    const audio = context();
    if (!audio) return;
    const oscillator = audio.createOscillator();
    const gain = audio.createGain();
    oscillator.type = "triangle";
    oscillator.frequency.value = 720;
    gain.gain.setValueAtTime(0.0001, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.035, audio.currentTime + 0.002);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.018);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start();
    oscillator.stop(audio.currentTime + 0.02);
  }

  function chime() {
    const audio = context();
    if (!audio) return;
    [523.25, 783.99].forEach((frequency, index) => {
      const start = audio.currentTime + index * 0.12;
      const oscillator = audio.createOscillator();
      const gain = audio.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(index ? 0.09 : 0.065, start + 0.025);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.48);
      oscillator.connect(gain).connect(audio.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.5);
    });
  }

  function swoosh() {
    const audio = context();
    if (!audio) return;
    const length = Math.max(1, Math.floor(audio.sampleRate * 0.14));
    const buffer = audio.createBuffer(1, length, audio.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < length; index += 1) channel[index] = (Math.random() * 2 - 1) * (1 - index / length);
    const source = audio.createBufferSource();
    const filter = audio.createBiquadFilter();
    const gain = audio.createGain();
    source.buffer = buffer;
    filter.type = "bandpass";
    filter.frequency.setValueAtTime(650, audio.currentTime);
    filter.frequency.exponentialRampToValueAtTime(2_600, audio.currentTime + 0.14);
    gain.gain.setValueAtTime(0.0001, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.07, audio.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.14);
    source.connect(filter).connect(gain).connect(audio.destination);
    source.start();
    source.stop(audio.currentTime + 0.145);
  }

  useEffect(() => () => { void contextRef.current?.close(); }, []);
  return { tick, chime, swoosh };
}

export default function PublicBooking() {
  const [settings, setSettings] = useState<SiteSettings>({
    hospital_name: "Dental Hospital",
    card_finish: "porcelain",
    time_slots: Array.from({ length: 11 }, (_, index) => `${String(index + 9).padStart(2, "0")}:00`),
  });
  const [preferences, setPreferences] = useState(readPreferences);
  const [card, setCard] = useState<AppointmentCard | null>(null);
  const [cardVerified, setCardVerified] = useState(false);
  const [flipped, setFlipped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [bookingOtpRequested, setBookingOtpRequested] = useState(false);
  const [bookingOtpCode, setBookingOtpCode] = useState("");
  const [bookingOtpChannel, setBookingOtpChannel] = useState<"WHATSAPP" | "SMS" | "EMAIL">("WHATSAPP");
  const [bookingOtpDemoCode, setBookingOtpDemoCode] = useState("");
  const [pendingBooking, setPendingBooking] = useState<BookingDetails | null>(null);
  const [bookingOtpToken, setBookingOtpToken] = useState("");
  const [error, setError] = useState("");
  const [lookup, setLookup] = useState("");
  const [lookupOpen, setLookupOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyMobile, setHistoryMobile] = useState("");
  const [historyCode, setHistoryCode] = useState("");
  const [historyDemoCode, setHistoryDemoCode] = useState("");
  const [historyData, setHistoryData] = useState<PatientHistory | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [saved, setSaved] = useState(false);
  const idempotency = useRef(crypto.randomUUID());
  const lastPayload = useRef("");
  const sound = useSound(preferences.muted);

  useEffect(() => {
    request<SiteSettings>("/api/public/settings").then((value) => {
      setSettings(value);
      if (!localStorage.getItem(PREF_STORAGE)) {
        setPreferences((current) => ({ ...current, cardFinish: value.card_finish as Preferences["cardFinish"] }));
      }
    }).catch(() => undefined);
    const pathOpid = decodeURIComponent(window.location.pathname.split("/").filter(Boolean)[1] ?? "");
    if (pathOpid.startsWith("OP-")) {
      setLookup(pathOpid);
      void loadCard(pathOpid);
      setLookupOpen(true);
      return;
    }
    const cached = localStorage.getItem(CARD_STORAGE);
    if (!cached) return;
    try {
      const savedCard = JSON.parse(cached) as AppointmentCard;
      setCard(savedCard);
      setCardVerified(false);
      void request<AppointmentCard>(`/api/public/appointments/${encodeURIComponent(savedCard.opid)}`)
        .then((latest) => {
          setCard(latest);
          setCardVerified(true);
          localStorage.setItem(CARD_STORAGE, JSON.stringify(latest));
        })
        .catch(() => setCardVerified(false));
    } catch {
      localStorage.removeItem(CARD_STORAGE);
    }
  }, []);

  useEffect(() => {
    if (!lookupOpen && !settingsOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setLookupOpen(false);
        setSettingsOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [lookupOpen, settingsOpen]);

  function savePreferences(next: Preferences) {
    setPreferences(next);
    localStorage.setItem(PREF_STORAGE, JSON.stringify(next));
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  }

  async function loadCard(opid: string) {
    setBusy(true);
    setError("");
    try {
      const result = await request<AppointmentCard>(`/api/public/appointments/${encodeURIComponent(opid.trim())}`);
      setCard(result);
      setCardVerified(true);
      setFlipped(false);
      localStorage.setItem(CARD_STORAGE, JSON.stringify(result));
      window.history.replaceState(null, "", `/book/${encodeURIComponent(result.opid)}`);
      setLookupOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Couldn’t find that appointment.");
    } finally {
      setBusy(false);
    }
  }

  async function requestHistoryCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setHistoryBusy(true); setHistoryError("");
    try {
      const result = await request<{ demo_code?: string; otp_reason: string }>("/api/public/history/otp/request", {
        method: "POST", body: JSON.stringify({ mobile_10_digit: historyMobile, otp_reason: "Patient Visit History Verification" }),
      });
      setHistoryDemoCode(result.demo_code ?? "");
      setHistoryCode("");
    } catch (reason) { setHistoryError(reason instanceof Error ? reason.message : "Could not request a code."); }
    finally { setHistoryBusy(false); }
  }

  async function verifyHistoryCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setHistoryBusy(true); setHistoryError("");
    try {
      const result = await request<{ access_token: string }>("/api/public/history/otp/verify", {
        method: "POST", body: JSON.stringify({ mobile_10_digit: historyMobile, code: historyCode, otp_reason: "Patient Visit History Verification" }),
      });
      const history = await request<PatientHistory>("/api/public/history", { headers: { "X-Patient-History-Token": result.access_token } });
      setHistoryData(history);
      setHistoryDemoCode("");
    } catch (reason) { setHistoryError(reason instanceof Error ? reason.message : "Could not verify this code."); }
    finally { setHistoryBusy(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = new FormData(event.currentTarget);
    const payload: BookingDetails = {
      name: String(form.get("name") ?? "").trim(),
      phone: String(form.get("phone") ?? "").trim(),
      email: String(form.get("email") ?? "").trim(),
      appointment_date: String(form.get("appointment_date") ?? ""),
      time_slot: String(form.get("time_slot") ?? ""),
      problem_summary: String(form.get("problem_summary") ?? "").trim(),
    };
    const signature = JSON.stringify(payload);
    if (signature !== lastPayload.current) {
      idempotency.current = crypto.randomUUID();
      lastPayload.current = signature;
      setBookingOtpToken("");
    }
    setPendingBooking(payload);
    setBusy(true);
    setError("");
    try {
      const result = await request<{ demo_code?: string; message: string }>("/api/public/appointments/otp/request", {
        method: "POST",
        body: JSON.stringify({ ...payload, channel: bookingOtpChannel, otp_reason: "Patient Booking & OP Confirmation" }),
      });
      setBookingOtpDemoCode(result.demo_code ?? "");
      setBookingOtpCode("");
      setBookingOtpRequested(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We couldn’t complete the booking. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function confirmBooking(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!pendingBooking || busy) return;
    setBusy(true); setError("");
    try {
      let token = bookingOtpToken;
      if (!token) {
        const verified = await request<{ booking_token: string }>("/api/public/appointments/otp/verify", {
          method: "POST", body: JSON.stringify({ ...pendingBooking, channel: bookingOtpChannel,
            otp_reason: "Patient Booking & OP Confirmation", code: bookingOtpCode }),
        });
        token = verified.booking_token;
        setBookingOtpToken(token);
      }
      const result = await request<AppointmentCard>("/api/public/appointments/book", {
        method: "POST", headers: { "Idempotency-Key": idempotency.current, "X-Booking-OTP-Token": token },
        body: JSON.stringify(pendingBooking),
      });
      setCard(result); setCardVerified(true); setFlipped(false);
      localStorage.setItem(CARD_STORAGE, JSON.stringify(result));
      window.history.pushState(null, "", `/book/${encodeURIComponent(result.opid)}`);
      idempotency.current = crypto.randomUUID(); lastPayload.current = "";
      setBookingOtpRequested(false); setBookingOtpCode(""); setBookingOtpToken(""); setPendingBooking(null);
      sound.chime();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We couldn’t complete the booking. Please try again.");
    } finally { setBusy(false); }
  }

  function startAnother() {
    setCard(null);
    setFlipped(false);
    setError("");
    setBookingOtpRequested(false); setBookingOtpCode(""); setBookingOtpToken(""); setPendingBooking(null); setBookingOtpDemoCode("");
    window.history.pushState(null, "", "/book");
  }

  function flipCard() {
    setFlipped((current) => !current);
    sound.swoosh();
  }

  return (
    <main className="public-booking" onClickCapture={(event) => {
      if ((event.target as HTMLElement).closest("button")) sound.tick();
    }}>
      <div className="booking-grain" aria-hidden="true" />
      <header className="public-header">
        <a className="public-brand" href="/book" aria-label={`${settings.hospital_name} booking home`}>
          <span className="brand-symbol" aria-hidden="true">✳</span>
          <span>{settings.hospital_name}<small>CARE, MADE CLEAR</small></span>
        </a>
        <div className="public-actions">
          <button className="quiet-button" onClick={() => { setLookupOpen(true); setError(""); }} type="button">Find my card</button>
          <button className="quiet-button" onClick={() => { setHistoryOpen(true); setHistoryData(null); setHistoryError(""); }} type="button">Visit history</button>
          <button className="round-control" onClick={() => setSettingsOpen(true)} aria-label="Open settings" title="Settings" type="button">⚙</button>
          <a className="staff-link" href="/staff">Staff sign in <span aria-hidden="true">↗</span></a>
        </div>
      </header>

      <section className={`booking-stage ${card ? "has-card" : ""}`}>
        <div className="booking-copy">
          <p className="kicker"><span className="kicker-dot" /> APPOINTMENTS, AT YOUR PACE</p>
          <h1>A good visit<br />starts <em>here.</em></h1>
          <p className="stage-note">Choose a time. We’ll prepare your appointment card instantly.</p>
          {!card ? (
            bookingOtpRequested && pendingBooking ? (
            <form className="booking-form" onSubmit={confirmBooking}>
              <p className="kicker">PATIENT BOOKING VERIFICATION</p>
              <p className="stage-note">Reason: <strong>Patient Booking &amp; OP Confirmation</strong>. Enter the one-time code sent by {bookingOtpChannel.toLowerCase()}.</p>
              <label className="field-label" htmlFor="booking-otp">Six-digit verification code</label>
              <input autoComplete="one-time-code" id="booking-otp" inputMode="numeric" maxLength={6} minLength={6} pattern="[0-9]{6}" required value={bookingOtpCode} onChange={(event) => setBookingOtpCode(event.target.value.replace(/\D/g, "").slice(0, 6))} />
              {bookingOtpDemoCode && <p className="saved-note">Local development code: <strong>{bookingOtpDemoCode}</strong></p>}
              <button className="book-submit" disabled={busy || bookingOtpCode.length !== 6} type="submit"><span>{busy ? "Verifying and booking…" : "Verify & create OP card"}</span><span>↗</span></button>
              {bookingOtpToken && <button className="quiet-button" disabled={busy} onClick={() => void confirmBooking()} type="button">Retry booking</button>}
              {error && <div className="booking-error" role="alert">{error}</div>}
              <button className="quiet-button" disabled={busy} onClick={() => { setBookingOtpRequested(false); setBookingOtpToken(""); setBookingOtpCode(""); }} type="button">Change appointment details</button>
            </form>
            ) : (
            <form className="booking-form" onSubmit={submit}>
              <label className="field-label" htmlFor="booking-name">Your name</label>
              <input autoComplete="name" id="booking-name" defaultValue={pendingBooking?.name ?? ""} maxLength={120} minLength={2} name="name" placeholder="e.g. Asha Rao" required />
              <label className="field-label" htmlFor="booking-phone">Mobile number</label>
              <input autoComplete="tel-national" id="booking-phone" defaultValue={pendingBooking?.phone ?? ""} inputMode="numeric" pattern="[6-9][0-9]{9}" maxLength={10} minLength={10} name="phone" placeholder="9876543210" required />
              <label className="field-label" htmlFor="booking-email">Email address</label>
              <input autoComplete="email" id="booking-email" defaultValue={pendingBooking?.email ?? ""} maxLength={254} name="email" placeholder="you@example.com" required type="email" />
              <label className="field-label" htmlFor="booking-problem">What would you like help with?</label>
              <input id="booking-problem" defaultValue={pendingBooking?.problem_summary ?? ""} maxLength={500} minLength={2} name="problem_summary" placeholder="Tooth pain, check-up, cleaning..." required />
              <div className="field-pair">
                <div><label className="field-label" htmlFor="booking-date">Date</label><input id="booking-date" defaultValue={pendingBooking?.appointment_date ?? ""} min={todayValue()} name="appointment_date" required type="date" /></div>
                <div><label className="field-label" htmlFor="booking-slot">Time</label><select id="booking-slot" defaultValue={pendingBooking?.time_slot ?? ""} name="time_slot" required><option disabled value="">Choose a time</option>{settings.time_slots.map((slot) => <option key={slot} value={slot}>{slot}</option>)}</select></div>
              </div>
              <label className="field-label" htmlFor="booking-otp-channel">Verification delivery</label>
              <select id="booking-otp-channel" value={bookingOtpChannel} onChange={(event) => setBookingOtpChannel(event.target.value as typeof bookingOtpChannel)}><option value="WHATSAPP">WhatsApp (SMS fallback)</option><option value="SMS">SMS</option><option value="EMAIL">Email</option></select>
              <button className="book-submit" disabled={busy} type="submit">
                <span>{busy ? "Preparing your card…" : "Book appointment"}</span><span aria-hidden="true">{busy ? "···" : "↗"}</span>
              </button>
              {error && <div className="booking-error" role="alert"><span>{error}</span><button disabled={busy} onClick={(event) => { const formElement = event.currentTarget.closest("form"); formElement?.requestSubmit(); }} type="button">Retry</button></div>}
              <p className="privacy-line"><span aria-hidden="true">⌑</span> Your information is kept private and secure.</p>
            </form>
            )
          ) : (
            <div className="booking-success" role="status">
              <span className="success-orbit" aria-hidden="true">✓</span>
              <div><p className="kicker">YOUR CARD IS READY</p><h2>See you soon, {card.patient_name.split(" ")[0]}.</h2><p>Appointment details are saved securely. Tap your card to turn it over.</p></div>
              <button className="quiet-button" onClick={startAnother} type="button">Book another time ↗</button>
              {!cardVerified && <small className="offline-note">Showing your saved card. Reconnect to refresh its details.</small>}
            </div>
          )}
          <div className="trust-row"><span><b>01</b> Choose a time</span><i /><span><b>02</b> Get your OP card</span><i /><span><b>03</b> Arrive with ease</span></div>
        </div>

        <div className="card-scene" aria-live="polite">
          {card ? (
            <button aria-label={flipped ? "Appointment card back. Tap to see front." : "Appointment card front. Tap to see token and room."} aria-pressed={flipped} className={`appointment-card card-${preferences.cardFinish} ${flipped ? "is-flipped" : ""}`} onClick={flipCard} type="button">
              <span className="card-face card-front">
                <span className="card-head"><span className="card-emblem">✳</span><span>{settings.hospital_name}<small>APPOINTMENT PASS</small></span><span className="card-chip" aria-hidden="true"><i /><i /><i /></span></span>
                <span className="card-label">YOUR OP ID</span>
                <strong className="card-opid">{card.opid}</strong>
                <span className="card-person">{card.patient_name}{card.patient_mobile ? " · " + card.patient_mobile : ""}</span>
                <span className="card-foot"><span>{prettyDate(card.appointment_date)}<small>DATE</small></span><span>{card.time_slot}<small>TIME</small></span><span className="tap-cue">TAP TO FLIP <b>↻</b></span></span>
                <span className="sheen" aria-hidden="true" />
              </span>
              <span className="card-face card-back">
                <span className="magstripe" />
                <span className="back-detail"><span><small>QUEUE TOKEN</small><b>{String(card.token_no).padStart(2, "0")}</b></span><span><small>ROOM</small><b>{card.room}</b></span></span>
                <span className="status-line"><i /> {card.status}</span>
                <span className="visit-note">Please arrive 10 minutes before your appointment. Keep this pass handy at reception.</span>
                <span className="back-bottom">{settings.hospital_name}<span>TAP TO FLIP <b>↻</b></span></span>
              </span>
            </button>
          ) : (
            <div className="card-placeholder" aria-hidden="true">
              <div className="placeholder-card"><span className="placeholder-brand">✳ <b>{settings.hospital_name}</b></span><span className="placeholder-wave" /><span className="placeholder-id">OP · · · · · ·</span><span className="placeholder-name">YOUR APPOINTMENT PASS</span><span className="placeholder-orb" /></div>
              <span className="orbit orbit-one" /><span className="orbit orbit-two" /><span className="stage-caption">A little more care.<br /><b>A little less waiting.</b></span>
            </div>
          )}
        </div>
      </section>

      <footer className="public-footer"><span>© {new Date().getFullYear()} {settings.hospital_name}</span><span>CARE THAT FEELS HUMAN <i>✳</i></span><button className="footer-settings" onClick={() => setSettingsOpen(true)} type="button">Preferences</button></footer>

      {lookupOpen && <div className="modal-scrim" onMouseDown={(event) => { if (event.currentTarget === event.target) setLookupOpen(false); }}>
        <section aria-labelledby="lookup-title" aria-modal="true" className="site-modal" role="dialog">
          <button aria-label="Close" className="modal-close" onClick={() => setLookupOpen(false)} type="button">×</button>
          <p className="kicker">PICK UP WHERE YOU LEFT OFF</p><h2 id="lookup-title">Find your appointment card</h2><form onSubmit={(event) => { event.preventDefault(); void loadCard(lookup); }}>
            <label className="field-label" htmlFor="lookup-opid">OP ID</label><input autoComplete="off" id="lookup-opid" maxLength={24} onChange={(event) => setLookup(event.target.value.toUpperCase())} placeholder="OP-261120-0001" value={lookup} />
            {error && <p className="booking-error" role="alert">{error}</p>}<button className="book-submit" disabled={busy || !lookup.trim()} type="submit"><span>{busy ? "Looking it up…" : "Open my card"}</span><span>↗</span></button>
          </form>
        </section>
      </div>}

      {historyOpen && <div className="modal-scrim" onMouseDown={(event) => { if (event.currentTarget === event.target) setHistoryOpen(false); }}>
        <section aria-labelledby="history-title" aria-modal="true" className="site-modal" role="dialog">
          <button aria-label="Close history" className="modal-close" onClick={() => setHistoryOpen(false)} type="button">×</button>
          <p className="kicker">YOUR VISITS, KEPT SIMPLE</p><h2 id="history-title">Visit history</h2>
          {!historyData ? <>
          <p className="modal-intro">Verification reason: <strong>Patient Visit History Verification</strong>. We’ll send a one-time code to the mobile number on your record.</p>
            {!historyDemoCode ? <form onSubmit={requestHistoryCode}>
              <label className="field-label" htmlFor="history-mobile">Registered mobile number</label>
              <input autoComplete="tel-national" id="history-mobile" inputMode="numeric" maxLength={10} minLength={10} pattern="[6-9][0-9]{9}" required value={historyMobile} onChange={(event) => setHistoryMobile(event.target.value.replace(/\D/g, "").slice(0, 10))} />
              <button className="book-submit" disabled={historyBusy} type="submit"><span>{historyBusy ? "Sending code…" : "Send verification code"}</span><span>↗</span></button>
            </form> : <form onSubmit={verifyHistoryCode}>
              <label className="field-label" htmlFor="history-otp">Six-digit code</label>
              <input autoComplete="one-time-code" id="history-otp" inputMode="numeric" maxLength={6} minLength={6} pattern="[0-9]{6}" required value={historyCode} onChange={(event) => setHistoryCode(event.target.value.replace(/\D/g, "").slice(0, 6))} />
              {historyDemoCode && <p className="saved-note">Local demo code: {historyDemoCode}</p>}
              <button className="book-submit" disabled={historyBusy} type="submit"><span>{historyBusy ? "Checking…" : "View my visits"}</span><span>↗</span></button>
            </form>}
            {historyError && <p className="booking-error" role="alert">{historyError}</p>}
          </> : <div className="patient-history-summary">
            <p><small>PATIENT ID</small><strong>{historyData.patient_id}</strong></p>
            {historyData.visits.map((visit) => <article key={visit.op_id}>
              <div><b>{visit.op_id}</b><span>{visit.status.replaceAll("_", " ")}</span></div>
              <p>{prettyDate(visit.appointment_date_ist)} · {visit.appointment_time_ist} IST</p>
              <small>{visit.instructions}</small>
            </article>)}
            {!historyData.visits.length && <p>No visits are linked to this record yet.</p>}
            <button className="quiet-button" onClick={() => { setHistoryData(null); setHistoryDemoCode(""); }} type="button">Close verified view</button>
          </div>}
        </section>
      </div>}

      {settingsOpen && <div className="modal-scrim" onMouseDown={(event) => { if (event.currentTarget === event.target) setSettingsOpen(false); }}>
        <section aria-labelledby="settings-title" aria-modal="true" className="site-modal settings-modal" role="dialog">
          <button aria-label="Close settings" className="modal-close" onClick={() => setSettingsOpen(false)} type="button">×</button>
          <p className="kicker">MAKE IT YOURS</p><h2 id="settings-title">Card & sound</h2><p className="modal-intro">Your preferences stay on this device.</p>
          <label className="field-label" htmlFor="card-finish">Card finish</label><select id="card-finish" onChange={(event) => savePreferences({ ...preferences, cardFinish: event.target.value as Preferences["cardFinish"] })} value={preferences.cardFinish}><option value="porcelain">Porcelain</option><option value="midnight">Midnight</option><option value="sage">Sage</option></select>
          <div className="sound-setting"><span><b>Sound effects</b><small>Soft clicks and card chimes</small></span><button aria-label={preferences.muted ? "Unmute sound effects" : "Mute sound effects"} aria-pressed={!preferences.muted} className={`sound-switch ${preferences.muted ? "muted" : ""}`} onClick={() => { const muted = !preferences.muted; savePreferences({ ...preferences, muted }); if (!muted) sound.tick(); }} type="button"><i />{preferences.muted ? "Muted" : "On"}</button></div>
          {saved && <p className="saved-note" role="status">Preferences saved</p>}<button className="book-submit modal-done" onClick={() => setSettingsOpen(false)} type="button"><span>Done</span><span>✓</span></button>
        </section>
      </div>}
    </main>
  );
}
