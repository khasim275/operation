import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useState,
} from "react";
import { api, ApiError } from "./api";
import ClinicalOpsPanel from "./ClinicalOpsPanel";
import {
  PatientCardScanner,
  PatientOpFilePage,
  PatientRegistrationPage,
  isValidOpId,
} from "./PatientCardPages";
import type {
  Dashboard,
  Dentist,
  Invoice,
  PatientDetail,
  PatientSummary,
  Room,
  TicketDetail,
  User,
} from "./types";

type Page = "today" | "requests" | "patients" | "patient" | "op-file" | "register" | "ticket" | "calendar" | "billing" | "simulator" | "settings";
type Notice = { message: string; error: boolean } | null;
type Resource<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
};

const pageTitles: Record<Page, [string, string]> = {
  today: ["YOUR CLINIC", "Today"],
  requests: ["PATIENT SUPPORT", "Requests queue"],
  patients: ["PATIENT RECORDS", "Patient search"],
  patient: ["PATIENT RECORDS", "Patient profile"],
  "op-file": ["PATIENT RECORDS", "Instant patient file"],
  register: ["PATIENT RECORDS", "Patient registration"],
  ticket: ["REQUEST DETAILS", "Ticket detail"],
  calendar: ["SCHEDULE", "Appointments"],
  billing: ["FINANCE", "Billing"],
  simulator: ["TEST ENVIRONMENT", "WhatsApp bot simulator"],
  settings: ["CLINIC CONFIGURATION", "Public site settings"],
};

function useResource<T>(load: () => Promise<T>): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((current) => current + 1), []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    load()
      .then((result) => {
        if (active) setData(result);
      })
      .catch((reason: unknown) => {
        if (active) setError(errorMessage(reason));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [load, version]);

  return { data, loading, error, reload };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

function todayInputValue(): string {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function formatDate(value: string | null | undefined, options: Intl.DateTimeFormatOptions = { dateStyle: "medium" }): string {
  if (!value) return "Not set";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, options).format(date);
}

function formatMoney(minor: number, currency = "INR"): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency || "INR",
    maximumFractionDigits: 2,
  }).format(Number(minor || 0) / 100);
}

function initials(name: string | null | undefined): string {
  return (name ?? "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0] ?? "")
    .join("")
    .toUpperCase();
}

function roleLabel(roles: string[]): string {
  return roles.map((role) => `${role[0]?.toUpperCase() ?? ""}${role.slice(1)}`).join(", ") || "Staff";
}

function StatusBadge({ status }: { status: string }) {
  const label = status.replaceAll("_", " ");
  return <span className={`badge ${status.toLowerCase()}`}>{label}</span>;
}

function LoadingState({ label = "Loading", detail = "Just a moment…" }: { label?: string; detail?: string }) {
  return (
    <div className="empty-state" role="status" aria-live="polite">
      <strong>{label}</strong>
      {detail}
    </div>
  );
}

function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="empty-state" role="alert">
      <strong>We couldn’t load this page</strong>
      {message}
      <p className="section-gap">
        <button className="button secondary" onClick={retry} type="button">
          Try again
        </button>
      </p>
    </div>
  );
}

function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {children}
    </div>
  );
}

function Dialog({
  title,
  children,
  onClose,
  onSubmit,
  submitLabel = "Save",
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  onSubmit: (data: FormData, file?: File) => Promise<void>;
  submitLabel?: string;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusTarget = document.querySelector<HTMLElement>("[data-dialog-focus]");
    focusTarget?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
      if (event.key === "Tab") {
        const dialog = document.querySelector<HTMLElement>("[role=dialog]");
        const focusable = dialog?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]",
        );
        if (!focusable?.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [onClose, saving]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const form = event.currentTarget;
      const file = form.querySelector<HTMLInputElement>('input[type="file"][name="file"]')?.files?.[0];
      await onSubmit(new FormData(form), file);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !saving) onClose();
      }}
      role="presentation"
    >
      <section aria-labelledby="dialog-title" aria-modal="true" className="dialog" role="dialog">
        <div className="dialog-header">
          <div>
            <p className="eyebrow">PATIENT RECORDS</p>
            <h2 id="dialog-title">{title}</h2>
          </div>
          <button
            aria-label="Close dialog"
            className="icon-button"
            disabled={saving}
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </div>
        <form onSubmit={submit}>
          <fieldset className="border-0 p-0" disabled={saving}>
            {children}
          </fieldset>
          {error && <p className="form-error" role="alert">{error}</p>}
          {saving && <p role="status">Saving…</p>}
          <div className="dialog-actions">
            <button className="button secondary" disabled={saving} onClick={onClose} type="button">
              Cancel
            </button>
            <button className="button primary" disabled={saving} type="submit">
              {saving ? "Saving…" : submitLabel}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function App() {
  const initialOpMatch = /^\/p\/([^/]+)$/.exec(window.location.pathname);
  const initialOpId = initialOpMatch ? decodeURIComponent(initialOpMatch[1]) : null;
  const [user, setUser] = useState<User | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [page, setPage] = useState<Page>(initialOpId ? "op-file" : "today");
  const [currentOpId, setCurrentOpId] = useState<string | null>(initialOpId);
  const [opFileSource, setOpFileSource] = useState<"view" | "scan">(
    new URLSearchParams(window.location.search).get("source") === "scan" || initialOpId ? "scan" : "view",
  );
  const [currentPatientId, setCurrentPatientId] = useState<string | null>(null);
  const [currentTicketId, setCurrentTicketId] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [liveVersion, setLiveVersion] = useState(0);
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = window.localStorage.getItem("dental-crm-theme");
    if (saved === "dark" || saved === "light") return saved;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  const notify = useCallback((message: string, error = false) => {
    setNotice({ message, error });
    window.setTimeout(() => setNotice(null), 4200);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("dental-crm-theme", theme);
  }, [theme]);

  useEffect(() => {
    let active = true;
    api<User>("/api/auth/me")
      .then((currentUser) => {
        if (active) {
          setUser(currentUser);
          setAuthError(null);
        }
      })
      .catch((reason: unknown) => {
        if (active && (!(reason instanceof ApiError) || reason.status !== 401)) {
          setAuthError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (active) setAuthLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!user || !("EventSource" in window)) return;
    const events = new EventSource("/api/events");
    events.onmessage = (event) => {
      try {
        const payload: { organisation_id?: string } = JSON.parse(event.data);
        if (payload.organisation_id === user.organisation_id) setLiveVersion((version) => version + 1);
      } catch {
        notify("A live update could not be read. The next update will refresh the screen.", true);
      }
    };
    events.onerror = () => {
      const liveStatus = document.querySelector<HTMLElement>(".live-status");
      if (liveStatus) liveStatus.title = "Connection interrupted; reconnecting automatically.";
    };
    events.onopen = () => {
      const liveStatus = document.querySelector<HTMLElement>(".live-status");
      if (liveStatus) liveStatus.title = "Connected to live updates.";
    };
    return () => events.close();
  }, [notify, user]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAuthLoading(true);
    setAuthError(null);
    const form = new FormData(event.currentTarget);
    try {
      const loggedIn = await api<User>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      setUser(loggedIn);
      if (!window.location.pathname.startsWith("/p/")) {
        setPage(loggedIn.roles.includes("accountant") && !loggedIn.roles.includes("admin") ? "billing" : "today");
      }
      notify("Signed in successfully.");
    } catch (reason) {
      setAuthError(errorMessage(reason));
    } finally {
      setAuthLoading(false);
    }
  }

  async function logout() {
    setAuthLoading(true);
    try {
      await api<{ ok: boolean }>("/api/auth/logout", { method: "POST", body: "{}" });
      setUser(null);
      setAuthError(null);
      notify("You have signed out.");
    } catch (reason) {
      notify(errorMessage(reason), true);
    } finally {
      setAuthLoading(false);
    }
  }

  function navigate(nextPage: Page) {
    setPage(nextPage);
    setMenuOpen(false);
    if (nextPage !== "op-file" && window.location.pathname.startsWith("/p/")) {
      window.history.pushState(null, "", "/staff");
    }
  }

  function openTicket(id: string) {
    setCurrentTicketId(id);
    navigate("ticket");
  }

  function openPatient(id: string) {
    setCurrentPatientId(id);
    navigate("patient");
  }

  function openOpPatient(opId: string, source: "view" | "scan" = "view") {
    setCurrentOpId(opId.toUpperCase());
    setOpFileSource(source);
    setPage("op-file");
    setMenuOpen(false);
    const suffix = source === "scan" ? "?source=scan" : "";
    window.history.pushState(null, "", `/p/${encodeURIComponent(opId.toUpperCase())}${suffix}`);
  }

  function handleGlobalSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const term = String(form.get("search") ?? "").trim();
    if (term.length < 2) {
      notify("Enter at least 2 characters to search.", true);
      return;
    }
    if (term.toUpperCase().startsWith("OP-")) {
      if (!isValidOpId(term)) {
        notify("This ID is not valid, please check the last digit", true);
        return;
      }
      openOpPatient(term.toUpperCase());
      return;
    }
    setSearchTerm(term);
    navigate("patients");
  }

  if (authLoading && !user) {
    return <LoadingState label="Connecting to the clinic" detail="Checking your staff session…" />;
  }

  if (!user) {
    return (
      <div className="login-view">
        <form className="login-card" onSubmit={login}>
          <a aria-label="Clinic CRM" className="brand" href="/staff">
            <span className="brand-mark">+</span>
            <span>Dental care</span>
          </a>
          <p className="eyebrow">STAFF WORKSPACE</p>
          <h1>Welcome back</h1>
          <p className="muted">Sign in to manage patient requests and appointments.</p>
          {authError && <p className="form-error" role="alert">{authError}</p>}
          <label htmlFor="login-email">Work email</label>
          <input autoComplete="username" id="login-email" maxLength={254} name="email" required type="email" />
          <label htmlFor="login-password">Password</label>
          <input
            autoComplete="current-password"
            id="login-password"
            maxLength={256}
            minLength={1}
            name="password"
            required
            type="password"
          />
          <button className="button primary full" disabled={authLoading} type="submit">
            {authLoading ? "Signing in…" : "Sign in"}
          </button>
          <p className="login-foot muted">For authorised clinic staff only.</p>
        </form>
      </div>
    );
  }

  const isAccountant = user.roles.includes("accountant") && !user.roles.includes("admin");
  const canBill = user.roles.some((role) => ["admin", "accountant"].includes(role));
  const canUseSimulator = user.roles.some((role) => ["admin", "telecaller", "receptionist"].includes(role));
  const canRegisterPatients = user.roles.some((role) => ["admin", "receptionist"].includes(role));
  const canEditSettings = user.roles.includes("admin");
  const title = pageTitles[page];

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? "open" : ""}`} id="sidebar">
        <a aria-label="CRM home" className="brand" href="/staff" onClick={(event) => { event.preventDefault(); navigate(isAccountant ? "billing" : "today"); }}>
          <span className="brand-mark">+</span>
          <span>Dental care</span>
        </a>
        <div className="workspace-label">WORKSPACE</div>
        <nav aria-label="Main navigation" className="side-nav">
          {!isAccountant && (
            <>
              <NavButton active={page === "today"} icon="▦" label="Today" onClick={() => navigate("today")} />
              <NavButton active={page === "requests"} icon="◷" label="Requests" onClick={() => navigate("requests")} />
              <NavButton active={page === "patients" || page === "patient" || page === "op-file" || page === "register"} icon="♙" label="Patients" onClick={() => navigate("patients")} />
              {canRegisterPatients && <NavButton active={page === "register"} icon="＋" label="Register patient" onClick={() => navigate("register")} />}
              <NavButton active={page === "calendar"} icon="▣" label="Calendar" onClick={() => navigate("calendar")} />
            </>
          )}
          {canBill && <NavButton active={page === "billing"} icon="▤" label="Billing" onClick={() => navigate("billing")} />}
          {canUseSimulator && (
            <NavButton active={page === "simulator"} icon="◉" label="Bot simulator" onClick={() => navigate("simulator")} />
          )}
          {canEditSettings && <NavButton active={page === "settings"} icon="⚙" label="Settings" onClick={() => navigate("settings")} />}
        </nav>
        <div className="sidebar-bottom">
          <div aria-hidden="true" className="user-avatar">{initials(user.full_name)}</div>
          <div className="user-label"><b>{user.full_name}</b><small>{roleLabel(user.roles)}</small></div>
          <button aria-label="Sign out" className="icon-button" disabled={authLoading} onClick={logout} title="Sign out" type="button">↗</button>
        </div>
      </aside>
      {menuOpen && (
        <button
          aria-label="Close navigation"
          className="mobile-nav-backdrop"
          onClick={() => setMenuOpen(false)}
          type="button"
        />
      )}
      <main className="main">
        <header className="topbar">
          <button
            aria-expanded={menuOpen}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            className="icon-button mobile-nav"
            onClick={() => setMenuOpen((open) => !open)}
            type="button"
          >
            {menuOpen ? "×" : "☰"}
          </button>
          <div className="heading-copy"><p className="eyebrow">{title[0]}</p><h1>{title[1]}</h1></div>
          {!isAccountant && (
            <form className="global-search" onSubmit={handleGlobalSearch} role="search">
              <label className="sr-only" htmlFor="global-search">Search by OP ID, patient name or mobile number</label>
              <span aria-hidden="true">⌕</span>
            <input autoComplete="off" defaultValue={page === "patients" ? searchTerm : ""} id="global-search" maxLength={120} minLength={2} name="search" placeholder="OP ID, name or mobile number" type="search" />
              <button type="submit">Search</button>
            </form>
          )}
          {!isAccountant && <button className="button secondary small scan-card-button" onClick={() => setScannerOpen(true)} type="button">Scan card</button>}
          <div className="live-status" title="Connected to live updates."><span aria-hidden="true" className="live-dot" /><span>Live</span></div>
          <button
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className="icon-button"
            onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            type="button"
          >
            ◐
          </button>
        </header>
        {notice && <div className={`notice ${notice.error ? "error" : ""}`} role={notice.error ? "alert" : "status"}>{notice.message}</div>}
        <section className="page-content" id="page-content" tabIndex={-1}>
          {page === "today" && <DashboardPage refresh={liveVersion} openTicket={openTicket} navigate={navigate} />}
          {page === "requests" && <RequestsPage refresh={liveVersion} openTicket={openTicket} />}
          {page === "patients" && <PatientSearchPage initialTerm={searchTerm} openPatient={openOpPatient} />}
          {page === "register" && <PatientRegistrationPage notify={notify} openFile={(opId) => openOpPatient(opId)} />}
          {page === "op-file" && currentOpId && (
            <PatientOpFilePage
              key={`${currentOpId}-${opFileSource}`}
              opId={currentOpId}
              source={opFileSource}
              roles={user.roles}
              notify={notify}
            />
          )}
          {page === "patient" && currentPatientId && (
            <PatientProfilePage
              key={currentPatientId}
              canBilling={user.roles.some((role) => ["admin", "accountant"].includes(role))}
              canEditClinical={user.roles.some((role) => ["admin", "dentist"].includes(role))}
              patientId={currentPatientId}
              openTicket={openTicket}
              notify={notify}
            />
          )}
          {page === "ticket" && currentTicketId && (
            <TicketPage
              canCall={user.roles.some((role) => ["admin", "telecaller", "receptionist"].includes(role))}
              key={currentTicketId}
              ticketId={currentTicketId}
              openPatient={openPatient}
              notify={notify}
            />
          )}
          {page === "calendar" && <CalendarPage refresh={liveVersion} openTicket={openTicket} />}
          {page === "billing" && canBill && <BillingPage notify={notify} />}
          {page === "simulator" && canUseSimulator && <SimulatorPage notify={notify} />}
          {page === "settings" && canEditSettings && <PublicSiteSettingsPage notify={notify} />}
        </section>
      </main>
      {scannerOpen && <PatientCardScanner onClose={() => setScannerOpen(false)} onScan={(opId) => { setScannerOpen(false); openOpPatient(opId, "scan"); }} />}
    </div>
  );
}

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: string; label: string; onClick: () => void }) {
  return (
    <button aria-current={active ? "page" : undefined} className={`nav-item ${active ? "active" : ""}`} onClick={onClick} type="button">
      <span aria-hidden="true">{icon}</span>{label}
    </button>
  );
}

function DashboardPage({
  refresh,
  openTicket,
  navigate,
}: {
  refresh: number;
  openTicket: (id: string) => void;
  navigate: (page: Page) => void;
}) {
  const load = useCallback(() => api<Dashboard>("/api/dashboard"), []);
  const resource = useResource(load);
  const reload = resource.reload;
  useEffect(() => {
    if (refresh) reload();
  }, [refresh, reload]);
  if (resource.loading && !resource.data) return <LoadingState />;
  if (resource.error) return <ErrorState message={resource.error} retry={resource.reload} />;
  const data = resource.data;
  if (!data) return <EmptyState title="Dashboard data is unavailable" />;

  return (
    <>
      <div className="metric-grid">
        <Metric label="Appointments today" value={data.metrics.appointments_today} note="Confirmed and checked-in visits" />
        <Metric label="Requests awaiting a call" value={data.metrics.requests_awaiting_call} note="Patient requests in the queue" />
        <Metric label="Callbacks today" value={data.metrics.callbacks_today} note="Scheduled retry reminders" />
      </div>
      <div className="dashboard-grid">
        <section className="card">
          <div className="card-header">
            <div><h2>Today’s appointments</h2><p>{formatDate(data.today)} · Sorted by appointment time</p></div>
            <button className="button secondary small" onClick={() => navigate("calendar")} type="button">Open calendar</button>
          </div>
          <div className="appointment-list">
            {data.appointments.length ? data.appointments.map((item) => (
              <article className="appointment-item" key={item.id}>
                <div>
                  <button className="link-button queue-name" onClick={() => openTicket(item.id)} type="button">{item.patient_name}</button>
                  <span className="queue-meta">{item.reason} · {item.ticket_number}</span>
                  <span className="queue-phone">{item.dentist ? `Dr. ${item.dentist}` : "Dentist not assigned"}</span>
                </div>
                <div><StatusBadge status={item.status} /><span className="queue-meta">{formatDate(item.start_at, { timeStyle: "short" })}</span></div>
              </article>
            )) : <EmptyState title="No appointments scheduled today">Confirmed appointments will appear here.</EmptyState>}
          </div>
        </section>
        <section className="card">
          <div className="card-header">
            <div><h2>Requests to call</h2><p>New requests, shown in arrival order</p></div>
            <button className="button secondary small" onClick={() => navigate("requests")} type="button">View queue</button>
          </div>
          <div className="queue-list">
            {data.requests.slice(0, 5).map((item) => (
              <QueueItem item={item} key={item.id} openTicket={openTicket} />
            ))}
            {!data.requests.length && <EmptyState title="You’re all caught up">New requests will appear here.</EmptyState>}
          </div>
        </section>
      </div>
      <section className="card">
        <div className="card-header">
          <div><h2>Recent requests</h2><p>New appointment requests from available channels</p></div>
          <span className="live-status"><span aria-hidden="true" className="live-dot" />Updating live</span>
        </div>
        <RequestTable items={data.requests.slice(0, 20)} openTicket={openTicket} />
      </section>
      {!!data.followups.length && (
        <section className="card section-gap">
          <div className="card-header"><div><h2>Callbacks due today</h2><p>Automatic reminders for unanswered calls</p></div><span className="badge">{data.metrics.callbacks_today} due</span></div>
          <div className="queue-list">
            {data.followups.map((item) => (
              <article className="queue-item" key={item.appointment_id}>
                <div>
                  <button className="link-button queue-name" onClick={() => openTicket(item.appointment_id)} type="button">{item.patient_name}</button>
                  <span className="queue-meta">{item.ticket_number} · retry {item.attempt_number} · {formatDate(item.scheduled_at, { timeStyle: "short" })}</span>
                  <span className="queue-phone">{item.reason} · {item.phone ?? "No phone on record"}</span>
                </div>
                {item.phone && <a aria-label={`Call ${item.patient_name}`} className="button primary small" href={`tel:${item.phone}`}>Call</a>}
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function Metric({ label, value, note }: { label: string; value: number; note: string }) {
  return <article className="metric"><div className="metric-label">{label}</div><div className="metric-value">{value}</div><div className="metric-note">{note}</div></article>;
}

type QueueEntry = {
  id: string;
  ticket_number: string;
  op_id?: string;
  patient_name: string;
  phone: string | null;
  reason: string;
  preferred_date: string | null;
  preferred_time_of_day: string;
  source: string;
  status: string;
};

function QueueItem({ item, openTicket }: { item: QueueEntry; openTicket: (id: string) => void }) {
  const preferred = item.preferred_date ? formatDate(item.preferred_date) : item.preferred_time_of_day || "Any time";
  return (
    <article className="queue-item">
      <div>
        <button className="link-button queue-name" onClick={() => openTicket(item.id)} type="button">{item.patient_name || "Patient"}</button>
        <span className="queue-meta">{item.op_id || item.ticket_number} · {item.reason} · {item.source || "phone"}</span>
        <span className="queue-phone">{item.phone || "No phone on record"} · Preferred {preferred}</span>
      </div>
      <div className="queue-actions">
        <StatusBadge status={item.status} />
        {item.phone && <a aria-label={`Call ${item.patient_name}`} className="button primary small" href={`tel:${item.phone}`}>Call</a>}
      </div>
    </article>
  );
}

function RequestTable({ items, openTicket }: { items: QueueEntry[]; openTicket: (id: string) => void }) {
  if (!items.length) return <EmptyState title="No requests yet">Requests will appear here as soon as they are submitted.</EmptyState>;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>OP / Ticket</th><th>Patient</th><th>Phone</th><th>Request</th><th>Source</th><th>Status</th></tr></thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td><button className="link-button" onClick={() => openTicket(item.id)} type="button">{item.op_id || item.ticket_number}</button></td>
              <td>{item.patient_name}</td><td>{item.phone || "—"}</td><td>{item.reason}</td>
              <td><span className="source-tag">{item.source}</span></td><td><StatusBadge status={item.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RequestsPage({ refresh, openTicket }: { refresh: number; openTicket: (id: string) => void }) {
  const load = useCallback(() => api<Dashboard>("/api/dashboard"), []);
  const resource = useResource(load);
  const reload = resource.reload;
  useEffect(() => {
    if (refresh) reload();
  }, [refresh, reload]);
  if (resource.loading && !resource.data) return <LoadingState />;
  if (resource.error) return <ErrorState message={resource.error} retry={resource.reload} />;
  const items = resource.data?.requests ?? [];
  return (
    <>
      <section className="card">
        <div className="card-header"><div><h2>Awaiting a call</h2><p>Open each request to call the patient and record an outcome.</p></div><span className="badge">{items.length} open</span></div>
        <div className="queue-list">
          {items.map((item) => <QueueItem item={item} key={item.id} openTicket={openTicket} />)}
          {!items.length && <EmptyState title="No open requests">New requests will appear here.</EmptyState>}
        </div>
      </section>
      <section className="card section-gap">
        <div className="card-header"><div><h2>All active requests</h2><p>Use ticket details to record call outcomes.</p></div></div>
        <RequestTable items={items} openTicket={openTicket} />
      </section>
    </>
  );
}

function PatientSearchPage({ initialTerm, openPatient }: { initialTerm: string; openPatient: (opId: string) => void }) {
  const [term, setTerm] = useState(initialTerm);
  const [query, setQuery] = useState(initialTerm.length >= 2 ? initialTerm : "");
  const load = useCallback(
    () => query ? api<Array<PatientSummary & { op_id: string }>>(`/api/patients/lookup?q=${encodeURIComponent(query)}`) : Promise.resolve([]),
    [query],
  );
  const resource = useResource(load);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const clean = term.trim();
    if (clean.length < 2) return;
    setQuery(clean);
  }

  return (
    <section className="card">
      <div className="card-header"><div><h2>Find a patient</h2><p>Search by full or partial name, email, or mobile number.</p></div></div>
      <form className="toolbar" onSubmit={submit}>
        <label className="sr-only" htmlFor="patient-search-input">Search patients</label>
        <input
          id="patient-search-input"
          maxLength={120}
          minLength={2}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="e.g. +91 98765 00001 or a patient name"
          required
          type="search"
          value={term}
        />
        <button className="button primary" disabled={resource.loading} type="submit">{resource.loading ? "Searching…" : "Search"}</button>
      </form>
      {query && resource.loading && <LoadingState label="Searching patients" />}
      {resource.error && <ErrorState message={resource.error} retry={resource.reload} />}
      {query && !resource.loading && !resource.error && resource.data && (
        resource.data.length ? (
          <div aria-label="Patient search results" className="result-list">
            {resource.data.map((patient) => (
              <button className="result-card" key={patient.id} onClick={() => openPatient(patient.op_id)} type="button">
                <span aria-hidden="true" className="result-avatar">{initials(patient.full_name)}</span>
                <span>
                  <strong>{patient.full_name}</strong>
                  <small className="queue-meta">
                    {patient.op_id} · {patient.patient_number} · {patient.phone || "No phone"} · {patient.date_of_birth ? formatDate(patient.date_of_birth) : "Date of birth not recorded"}
                  </small>
                </span>
                <span className="source-tag">View history</span>
              </button>
            ))}
          </div>
        ) : <EmptyState title="No matching patients">Check the spelling or enter a longer mobile number.</EmptyState>
      )}
      {!query && <EmptyState title="Search by name or mobile number">Enter at least 2 characters to find a patient.</EmptyState>}
    </section>
  );
}

type PatientDialog = "edit" | "note" | "medicine" | "allergy" | "upload" | null;

function PatientProfilePage({
  patientId,
  canEditClinical,
  canBilling,
  openTicket,
  notify,
}: {
  patientId: string;
  canEditClinical: boolean;
  canBilling: boolean;
  openTicket: (id: string) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const [dialog, setDialog] = useState<PatientDialog>(null);
  const load = useCallback(() => api<PatientDetail>(`/api/patients/${encodeURIComponent(patientId)}`), [patientId]);
  const resource = useResource(load);
  const reload = resource.reload;
  const [refreshVersion, setRefreshVersion] = useState(0);
  const refresh = useCallback(() => setRefreshVersion((value) => value + 1), []);

  useEffect(() => {
    if (!refreshVersion) return;
    reload();
  }, [refreshVersion, reload]);

  if (resource.loading && !resource.data) return <LoadingState label="Loading patient history" />;
  if (resource.error) return <ErrorState message={resource.error} retry={resource.reload} />;
  const data = resource.data;
  if (!data) return <EmptyState title="Patient record is unavailable" />;
  const patient = data.patient;
  const allergies = data.allergies.filter((item) => item.status === "active");
  const timeline = [
    ...data.appointments.map((item) => ({
      date: item.start_at || item.created_at,
      type: "Appointment",
      title: `${item.ticket_number} · ${item.reason}`,
      detail: `Status: ${item.status}`,
    })),
    ...data.calls.map((item) => ({
      date: item.started_at,
      type: "Call",
      title: `Call ${item.outcome.replaceAll("_", " ")}`,
      detail: item.notes || "No note recorded",
    })),
    ...data.visits.map((item) => ({
      date: item.recorded_at,
      type: "Visit",
      title: item.complaint,
      detail: item.treatment_plan || item.examination_findings || "Clinical details recorded",
    })),
    ...data.invoices.map((item) => ({
      date: item.created_at,
      type: "Billing",
      title: `Invoice · ${formatMoney(item.total_minor, item.currency)}`,
      detail: `Outstanding ${formatMoney(item.outstanding_minor, item.currency)}`,
    })),
  ].sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());

  async function mutate(url: string, method: "PATCH" | "POST", body: BodyInit) {
    await api(url, { method, body });
    setDialog(null);
    notify("Patient record updated.");
    refresh();
  }

  return (
    <>
      <div className="patient-heading">
        <span aria-hidden="true" className="result-avatar">{initials(patient.full_name)}</span>
        <div><p className="eyebrow">{patient.patient_number}</p><h2>{patient.full_name}</h2><span className="patient-number">{data.phones.map((item) => item.phone).join(" · ") || "No phone on record"}</span></div>
        <button className="button secondary small" onClick={() => setDialog("edit")} type="button">Edit profile</button>
        {canEditClinical && (
          <>
            <button className="button primary small" onClick={() => setDialog("note")} type="button">Add note</button>
            <button className="button secondary small" onClick={() => setDialog("medicine")} type="button">Add medicine</button>
            <button className="button secondary small" onClick={() => setDialog("allergy")} type="button">Add allergy</button>
          </>
        )}
      </div>
      {allergies.length ? (
        <section aria-label="Allergy warning" className="allergy-warning">
          <strong>⚠ Active allergies</strong>
          <ul>{allergies.map((item) => <li key={`${item.allergen}-${item.recorded_at}`}>{item.allergen}{item.severity ? ` · ${item.severity}` : ""}{item.reaction ? ` — ${item.reaction}` : ""}</li>)}</ul>
        </section>
      ) : (
        <section className="allergy-warning"><strong>Allergies</strong><div>No active allergies recorded. Confirm this with the patient before treatment.</div></section>
      )}
      <div className="profile-grid">
        <section className="card">
          <div className="card-header"><div><h2>Patient details</h2><p>Permanent record · {patient.status}</p></div></div>
          <div className="patient-details">
            <Detail label="Date of birth" value={patient.date_of_birth ? formatDate(patient.date_of_birth) : "Not recorded"} />
            <Detail label="Gender" value={patient.gender || "Not recorded"} />
            <Detail label="Email" value={patient.email || "Not recorded"} />
            <Detail label="Language" value={patient.preferred_language || "Not recorded"} />
            <Detail label="Occupation" value={patient.occupation || "Not recorded"} />
            <Detail label="Patient since" value={formatDate(patient.created_at)} />
          </div>
          <h3 className="section-gap">Current medicines</h3>
          <div className="list-links">
            {data.medicines.filter((item) => item.status === "current").map((item) => (
              <div className="list-link" key={`${item.medicine_name}-${item.recorded_at}`}>
                <span>{item.medicine_name}{item.dose ? ` · ${item.dose}` : ""}<small className="queue-meta">{item.frequency || ""}{item.end_date ? ` · through ${formatDate(item.end_date)}` : ""}</small></span><span className="source-tag">Current</span>
              </div>
            ))}
            {!data.medicines.some((item) => item.status === "current") && <EmptyState title="No current medicines recorded" />}
          </div>
          <h3 className="section-gap">Appointments</h3>
          <div className="list-links">
            {data.appointments.map((item) => (
              <button className="list-link" key={item.id} onClick={() => openTicket(item.id)} type="button">
                <span>{item.ticket_number} · {item.reason}<small className="queue-meta">{formatDate(item.start_at || item.created_at)}</small></span><StatusBadge status={item.status} />
              </button>
            ))}
            {!data.appointments.length && <EmptyState title="No appointment history yet" />}
          </div>
        </section>
        <section className="card">
          <div className="card-header"><div><h2>Full history</h2><p>Appointments, calls, visits and billing in date order.</p></div></div>
          {data.medical_notes.length > 0 && (
            <>
              <h3>Clinical notes</h3>
              <div className="call-log section-gap">
                {data.medical_notes.map((item) => <article className="call-log-entry" key={`${item.recorded_at}-${item.author}`}><small>{formatDate(item.recorded_at, { dateStyle: "medium", timeStyle: "short" })} · {item.author}</small><p>{item.note}</p></article>)}
              </div>
            </>
          )}
          {data.prescriptions.length > 0 && (
            <>
              <h3 className="section-gap">Prescriptions</h3>
              <div className="list-links">
                {data.prescriptions.map((prescription) => <article className="list-link" key={prescription.id}><span><strong>{formatDate(prescription.created_at)} · {prescription.dentist}</strong><small className="queue-meta">{prescription.items.map((item) => `${item.medicine_name} · ${item.dose} · ${item.frequency}`).join("; ")}</small></span><StatusBadge status={prescription.status} /></article>)}
              </div>
            </>
          )}
          <div className="timeline">
            {timeline.length ? timeline.map((item, index) => <article className="timeline-item" key={`${item.type}-${item.date}-${index}`}><time>{formatDate(item.date, { dateStyle: "medium", timeStyle: "short" })} · {item.type}</time><strong>{item.title}</strong><small>{item.detail}</small></article>) : <EmptyState title="No history recorded">Patient activity will appear here.</EmptyState>}
          </div>
        </section>
      </div>
      <section className="card section-gap">
        <div className="card-header"><div><h2>Documents</h2><p>X-rays, reports, photos and consent forms for this patient.</p></div><button className="button secondary small" onClick={() => setDialog("upload")} type="button">Upload file</button></div>
        <div className="list-links">
          {data.documents.map((doc) => (
            <a className="list-link" href={`/api/documents/${encodeURIComponent(doc.id)}`} key={doc.id} rel="noreferrer" target="_blank">
              <span>{doc.original_name}<small className="queue-meta">{doc.document_type} · {formatDate(doc.uploaded_at)}</small></span><span>{Math.ceil(doc.size_bytes / 1024)} KB</span>
            </a>
          ))}
          {!data.documents.length && <EmptyState title="No documents uploaded" />}
        </div>
      </section>
      <ClinicalOpsPanel
        patientId={patientId}
        canClinical={canEditClinical}
        canBilling={canBilling}
        notify={notify}
      />
      {dialog && (
        <PatientRecordDialog
          dialog={dialog}
          patient={patient}
          onClose={() => setDialog(null)}
          onSubmit={async (form, uploadFile) => {
            if (dialog === "edit") {
              const payload = Object.fromEntries([...form.entries()].filter(([, value]) => value !== ""));
              await mutate(`/api/patients/${encodeURIComponent(patientId)}`, "PATCH", JSON.stringify(payload));
            } else if (dialog === "note") {
              await mutate(`/api/patients/${encodeURIComponent(patientId)}/medical-notes`, "POST", JSON.stringify({ note_text: form.get("note_text") }));
            } else if (dialog === "medicine") {
              const payload = Object.fromEntries([...form.entries()].filter(([, value]) => value !== ""));
              await mutate(`/api/patients/${encodeURIComponent(patientId)}/medicines`, "POST", JSON.stringify(payload));
            } else if (dialog === "allergy") {
              await mutate(`/api/patients/${encodeURIComponent(patientId)}/allergies`, "POST", JSON.stringify({
                allergen_text: form.get("allergen_text"),
                reaction: form.get("reaction") || null,
                severity: form.get("severity"),
              }));
            } else {
              if (!uploadFile || uploadFile.size === 0) throw new Error("Choose a non-empty PDF, JPEG, or PNG file.");
              const upload = new FormData();
              upload.set("file", uploadFile);
              const type = String(form.get("document_type"));
              await api(`/api/patients/${encodeURIComponent(patientId)}/documents?document_type=${encodeURIComponent(type)}`, {
                method: "POST",
                body: upload,
              });
              setDialog(null);
              notify("Document uploaded.");
              refresh();
            }
          }}
        />
      )}
    </>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="detail-pair"><span>{label}</span><b>{value || "Not recorded"}</b></div>;
}

function PatientRecordDialog({
  dialog,
  patient,
  onClose,
  onSubmit,
}: {
  dialog: Exclude<PatientDialog, null>;
  patient: PatientDetail["patient"];
  onClose: () => void;
  onSubmit: (form: FormData, file?: File) => Promise<void>;
}) {
  if (dialog === "edit") {
    return (
      <Dialog onClose={onClose} onSubmit={onSubmit} title="Edit patient profile">
        <div className="form-grid">
          <label className="span-2">Full name<input data-dialog-focus maxLength={200} name="full_name" required defaultValue={patient.full_name} /></label>
          <label>Email<input name="email" type="email" defaultValue={patient.email ?? ""} /></label>
          <label>Date of birth<input name="date_of_birth" type="date" defaultValue={patient.date_of_birth ?? ""} /></label>
          <label>Gender<input name="gender" maxLength={40} defaultValue={patient.gender ?? ""} /></label>
          <label>Preferred language<input name="preferred_language" maxLength={12} minLength={2} defaultValue={patient.preferred_language ?? ""} /></label>
          <label className="span-2">Occupation<input name="occupation" maxLength={120} defaultValue={patient.occupation ?? ""} /></label>
        </div>
      </Dialog>
    );
  }
  if (dialog === "note") {
    return <Dialog onClose={onClose} onSubmit={onSubmit} title="Add clinical note"><label className="field-stack">Note<textarea data-dialog-focus maxLength={10000} name="note_text" placeholder="Add a dated clinical note" required /></label></Dialog>;
  }
  if (dialog === "medicine") {
    return (
      <Dialog onClose={onClose} onSubmit={onSubmit} title="Add current medicine">
        <div className="field-stack">
          <label>Medicine<input data-dialog-focus maxLength={200} name="medicine_name" required /></label>
          <label>Dose<input maxLength={200} name="dose" /></label>
          <label>Frequency<input maxLength={200} name="frequency" /></label>
          <label>Start date<input name="start_date" type="date" /></label>
          <label>End date<input name="end_date" type="date" /></label>
        </div>
      </Dialog>
    );
  }
  if (dialog === "allergy") {
    return (
      <Dialog onClose={onClose} onSubmit={onSubmit} title="Add allergy">
        <div className="field-stack">
          <label>Allergen<input data-dialog-focus maxLength={300} name="allergen_text" required /></label>
          <label>Reaction<input maxLength={1000} name="reaction" /></label>
          <label>Severity<select defaultValue="unknown" name="severity"><option value="unknown">Unknown</option><option value="mild">Mild</option><option value="moderate">Moderate</option><option value="severe">Severe</option></select></label>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog onClose={onClose} onSubmit={onSubmit} title="Upload patient document">
      <div className="field-stack">
        <label>Document type<select data-dialog-focus name="document_type" required><option value="xray">X-ray</option><option value="photo">Photo</option><option value="report">Report</option><option value="consent">Consent form</option><option value="other">Other</option></select></label>
        <label>Select a PDF, JPEG or PNG file<input accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" name="file" required type="file" /></label>
        <small className="muted">Maximum upload size is configured by the clinic.</small>
      </div>
    </Dialog>
  );
}

function TicketPage({
  ticketId,
  canCall,
  openPatient,
  notify,
}: {
  ticketId: string;
  canCall: boolean;
  openPatient: (id: string) => void;
  notify: (message: string, error?: boolean) => void;
}) {
  const load = useCallback(async () => {
    const [ticket, dentists, rooms] = await Promise.all([
      api<TicketDetail>(`/api/appointments/${encodeURIComponent(ticketId)}`),
      api<Dentist[]>("/api/dentists"),
      api<Room[]>("/api/rooms"),
    ]);
    return { ticket, dentists, rooms };
  }, [ticketId]);
  const resource = useResource(load);
  const [outcome, setOutcome] = useState("");
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [statusNote, setStatusNote] = useState("");
  const [reply, setReply] = useState("");
  const [replyBusy, setReplyBusy] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const data = resource.data;

  useEffect(() => {
    if (data) {
      setStatus(data.ticket.ticket.status);
      setStatusNote(data.ticket.ticket.status_note);
    }
  }, [data]);

  if (resource.loading && !data) return <LoadingState label="Loading ticket and patient history" />;
  if (resource.error) return <ErrorState message={resource.error} retry={resource.reload} />;
  if (!data) return <EmptyState title="Ticket details are unavailable" />;
  const { ticket, dentists, rooms } = data;
  const current = ticket.ticket;

  async function saveOutcome(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSaving(true);
    setActionError(null);
    const body: Record<string, unknown> = { outcome, notes: String(form.get("notes") ?? "") };
    if (outcome === "confirmed") {
      const start = String(form.get("start_at") ?? "");
      const end = String(form.get("end_at") ?? "");
      if (!start || !end) {
        setActionError("Enter both appointment start and end times.");
        setSaving(false);
        return;
      }
      body.start_at = new Date(start).toISOString();
      body.end_at = new Date(end).toISOString();
      body.dentist_id = form.get("dentist_id");
      body.room_id = form.get("room_id") || null;
    }
    if (outcome === "reschedule") {
      body.preferred_date = form.get("preferred_date");
      body.preferred_time_of_day = form.get("preferred_time_of_day");
    }
    try {
      await api(`/api/appointments/${encodeURIComponent(ticketId)}/call-outcome`, { method: "POST", body: JSON.stringify(body) });
      notify("Call outcome saved.");
      setOutcome("");
      resource.reload();
    } catch (reason) {
      setActionError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  async function updateStatus() {
    setSaving(true);
    setActionError(null);
    try {
      await api(`/api/appointments/${encodeURIComponent(ticketId)}`, {
        method: "PATCH",
        body: JSON.stringify({ status, status_note: statusNote }),
      });
      notify("Ticket status updated.");
      resource.reload();
    } catch (reason) {
      setActionError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  async function sendReply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setReplyBusy(true);
    setReplyError(null);
    try {
      await api(`/api/patients/${encodeURIComponent(current.patient_id ?? "")}/messages`, {
        method: "POST",
        body: JSON.stringify({ message: reply.trim() }),
      });
      setReply("");
      notify("WhatsApp reply queued.");
      resource.reload();
    } catch (reason) {
      setReplyError(errorMessage(reason));
    } finally {
      setReplyBusy(false);
    }
  }

  const allergies = ticket.allergies;
  return (
    <>
      <div className="card-header">
        <div><p className="eyebrow">{current.source} REQUEST</p><h2>{current.op_id ? current.op_id + " · " : ""}{current.ticket_number} · {current.reason}</h2><p>Received {formatDate(current.created_at, { dateStyle: "medium", timeStyle: "short" })}</p></div>
        <button className="button secondary small" onClick={() => current.patient_id && openPatient(current.patient_id)} type="button">Open patient history</button>
      </div>
      <div className="ticket-grid">
        <section className="card">
          <div className="card-header"><div><h2>Request & patient</h2><p>{current.patient_name}</p></div><StatusBadge status={current.status} /></div>
          <div className="patient-details">
            <Detail label="Mobile" value={current.phone || "No phone on record"} />
            <Detail label="Preferred day" value={current.preferred_date ? formatDate(current.preferred_date) : "Not specified"} />
            <Detail label="Preferred time" value={current.preferred_time_of_day} />
            <Detail label="Source" value={current.source} />
          </div>
          {allergies.length ? (
            <div className="allergy-warning"><strong>⚠ Allergy warning</strong><ul>{allergies.map((item) => <li key={item.allergen}>{item.allergen}{item.severity ? ` · ${item.severity}` : ""}{item.reaction ? ` — ${item.reaction}` : ""}</li>)}</ul></div>
          ) : <div className="allergy-warning"><strong>Allergies</strong><div>None recorded. Confirm with patient.</div></div>}
          {canCall && current.phone ? <a className="button whatsapp ticket-call" href={`tel:${current.phone}`}>☎ Call {current.phone}</a> : canCall ? <p className="form-error">No phone number is available.</p> : null}
          {canCall && <form className="call-form" onSubmit={saveOutcome}>
            <h3>Record call outcome</h3>
            <label>Outcome
              <select onChange={(event) => setOutcome(event.target.value)} required value={outcome}>
                <option value="">Choose an outcome</option><option value="confirmed">Confirmed</option><option value="not_reachable">Not reachable</option><option value="reschedule">Reschedule</option><option value="cancelled">Cancelled</option>
              </select>
            </label>
            {outcome === "confirmed" && (
              <div className="form-grid">
                <label>Appointment start<input name="start_at" required type="datetime-local" /></label>
                <label>Appointment end<input name="end_at" required type="datetime-local" /></label>
                <label>Dentist<select name="dentist_id" required><option value="">Choose dentist</option>{dentists.map((dentist) => <option key={dentist.id} value={dentist.id}>{dentist.full_name}</option>)}</select></label>
                <label>Room / chair<select name="room_id"><option value="">Not assigned</option>{rooms.map((room) => <option key={room.id} value={room.id}>{room.name}</option>)}</select></label>
              </div>
            )}
            {outcome === "reschedule" && (
              <div className="form-grid">
                <label>Preferred date<input name="preferred_date" required type="date" min={todayInputValue()} /></label>
                <label>Preferred time of day<select name="preferred_time_of_day" required defaultValue=""><option value="">Choose a time</option><option value="morning">Morning</option><option value="afternoon">Afternoon</option><option value="evening">Evening</option><option value="any">Any time</option></select></label>
              </div>
            )}
            <label>Call note<textarea maxLength={4000} name="notes" placeholder="Briefly record the agreed next step" /></label>
            {actionError && <p className="form-error" role="alert">{actionError}</p>}
            <button className="button primary full" disabled={saving} type="submit">{saving ? "Saving…" : "Save outcome"}</button>
          </form>}
        </section>
        <section className="card">
          <div className="card-header"><div><h2>Call timeline</h2><p>Prior calls and appointment updates</p></div></div>
          <div className="call-log">
            {ticket.calls.map((call, index) => <article className="call-log-entry" key={`${call.started_at}-${index}`}><strong>{call.outcome.replaceAll("_", " ")}</strong><small>{formatDate(call.started_at, { dateStyle: "medium", timeStyle: "short" })} · {call.caller}</small><p>{call.notes || "No note recorded"}</p>{call.next_follow_up_at && <small>Retry due {formatDate(call.next_follow_up_at, { dateStyle: "medium", timeStyle: "short" })}</small>}</article>)}
            {!ticket.calls.length && <EmptyState title="No call history">First-time patient request. Record the first call outcome here.</EmptyState>}
          </div>
          <h3 className="section-gap">Patient visits</h3>
          <div className="timeline">
            {ticket.visits.map((visit) => <article className="timeline-item" key={visit.id}><time>{formatDate(visit.recorded_at)}</time><strong>{visit.complaint}</strong><small>{visit.treatment_plan || visit.examination_findings || "No additional notes"}</small></article>)}
            {!ticket.visits.length && <EmptyState title="No previous visit records" />}
          </div>
        </section>
        <section className="card">
          <div className="card-header"><div><h2>Status & activity</h2><p>Request state and audit timeline</p></div></div>
          {canCall ? (
            <>
              <label className="field-stack">Current status<select aria-label="Ticket status" onChange={(event) => setStatus(event.target.value)} value={status}>{["requested", "confirmed", "rescheduled", "checked_in", "completed", "cancelled", "no_show"].map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>
              <label className="field-stack section-gap">Status note<textarea maxLength={1000} onChange={(event) => setStatusNote(event.target.value)} placeholder="Optional context" value={statusNote} /></label>
              <button className="button secondary full section-gap" disabled={saving} onClick={updateStatus} type="button">{saving ? "Saving…" : "Update status"}</button>
            </>
          ) : <p className="muted">Status and call outcomes are managed by the clinic support team.</p>}
          <h3 className="section-gap">Timeline</h3>
          <div className="timeline">
            {ticket.events.map((event, index) => <article className="timeline-item" key={`${event.occurred_at}-${index}`}><time>{formatDate(event.occurred_at, { dateStyle: "medium", timeStyle: "short" })}</time><strong>{event.event_type.replaceAll("_", " ")}</strong><small>{event.old_status ? `${event.old_status} → ${event.new_status}` : event.new_status || ""}</small></article>)}
            {!ticket.events.length && <EmptyState title="No activity yet" />}
          </div>
          <h3 className="section-gap">WhatsApp conversation</h3>
          <div aria-live="polite" className="conversation-panel">
            {ticket.messages.map((message, index) => <div className={`chat-bubble ${message.direction === "outbound" ? "outgoing" : ""}`} key={`${message.occurred_at}-${index}`}>{message.message}<small>{formatDate(message.occurred_at, { timeStyle: "short" })} · {message.delivery_status}</small></div>)}
            {!ticket.messages.length && <EmptyState title="No WhatsApp messages recorded" />}
          </div>
          {canCall && <form className="field-stack section-gap" onSubmit={sendReply}>
            <label htmlFor="ticket-reply">Reply to patient</label>
            <textarea id="ticket-reply" maxLength={4000} onChange={(event) => setReply(event.target.value)} placeholder="Free-form replies are available for 24 hours after the patient’s last message." required value={reply} />
            {replyError && <p className="form-error" role="alert">{replyError}</p>}
            <button className="button whatsapp" disabled={replyBusy} type="submit">{replyBusy ? "Queueing…" : "Queue WhatsApp reply"}</button>
          </form>}
        </section>
      </div>
    </>
  );
}

function CalendarPage({ refresh, openTicket }: { refresh: number; openTicket: (id: string) => void }) {
  const [day, setDay] = useState(todayInputValue);
  const load = useCallback(() => api<Dashboard["appointments"]>(`/api/appointments?day=${encodeURIComponent(day)}`), [day]);
  const resource = useResource(load);
  const reload = resource.reload;
  useEffect(() => {
    if (refresh) reload();
  }, [refresh, reload]);
  return (
    <>
      <div className="calendar-toolbar">
        <label className="field-stack">Day<input aria-label="Appointment day" onChange={(event) => setDay(event.target.value)} type="date" value={day} /></label>
        <button aria-label="Show appointments for today" className="button secondary" onClick={() => setDay(todayInputValue())} type="button">Today</button>
        <span className="muted">{resource.data?.length ?? 0} appointments or requests</span>
      </div>
      <section className="card">
        <div className="card-header"><div><h2>Appointments by day</h2><p>Schedule is grouped by selected clinic date.</p></div></div>
        {resource.loading && !resource.data ? <LoadingState label="Loading appointments" /> : resource.error ? <ErrorState message={resource.error} retry={resource.reload} /> : (
          <div className="calendar-list">
            {resource.data?.map((item) => (
              <article className="calendar-row" key={item.id}>
                <div className="calendar-time">{item.start_at ? formatDate(item.start_at, { timeStyle: "short" }) : "Request"}</div>
                <div>
                  <button className="link-button queue-name" onClick={() => openTicket(item.id)} type="button">{item.patient_name}</button>
                  <span className="queue-meta">{item.reason} · {item.ticket_number}</span>
                  <span className="queue-phone">{item.dentist ? `Dentist: ${item.dentist}` : "Dentist to be confirmed"}</span>
                </div>
                <StatusBadge status={item.status} />
              </article>
            ))}
            {!resource.data?.length && <EmptyState title="No appointments for this day">Try another date or check the requests queue.</EmptyState>}
          </div>
        )}
      </section>
    </>
  );
}

type BillingDialog = "invoice" | { paymentId: string; outstanding: number; currency: string } | null;

function BillingPage({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const [dialog, setDialog] = useState<BillingDialog>(null);
  const load = useCallback(() => api<Invoice[]>("/api/invoices"), []);
  const resource = useResource(load);
  if (resource.loading && !resource.data) return <LoadingState label="Loading invoices" />;
  if (resource.error) return <ErrorState message={resource.error} retry={resource.reload} />;
  const invoices = resource.data ?? [];
  return (
    <>
      <section className="card">
        <div className="card-header">
          <div><h2>Invoices</h2><p>Outstanding balances and payments. Amounts are shown in the invoice currency.</p></div>
          <button className="button primary small" onClick={() => setDialog("invoice")} type="button">Create invoice</button>
        </div>
        {invoices.length ? (
          <div className="table-wrap">
            <table><thead><tr><th>Patient</th><th>Created</th><th>Status</th><th>Total</th><th>Paid</th><th>Outstanding</th><th>Actions</th></tr></thead>
              <tbody>{invoices.map((invoice) => <tr key={invoice.id}>
                <td><strong>{invoice.patient_name}</strong></td><td>{formatDate(invoice.created_at)}</td><td><StatusBadge status={invoice.status} /></td>
                <td>{formatMoney(invoice.total_minor, invoice.currency)}</td><td>{formatMoney(invoice.paid_minor, invoice.currency)}</td><td>{formatMoney(invoice.outstanding_minor, invoice.currency)}</td>
                <td><button className="button secondary small" disabled={invoice.outstanding_minor <= 0} onClick={() => setDialog({ paymentId: invoice.id, outstanding: invoice.outstanding_minor, currency: invoice.currency })} type="button">Add payment</button></td>
              </tr>)}</tbody>
            </table>
          </div>
        ) : <EmptyState title="No invoices yet">Invoices created for patients will appear here.</EmptyState>}
      </section>
      {dialog === "invoice" && (
        <Dialog onClose={() => setDialog(null)} onSubmit={async (form) => {
          const amount = Number(form.get("amount"));
          if (!Number.isFinite(amount) || amount <= 0) throw new Error("Enter an amount greater than zero.");
          await api("/api/invoices", {
            method: "POST",
            body: JSON.stringify({
              patient_id: form.get("patient_id"),
              items: [{ description: form.get("description"), quantity: 1, unit_amount_minor: Math.round(amount * 100), discount_minor: 0 }],
            }),
          });
          setDialog(null);
          notify("Invoice created.");
          resource.reload();
        }} title="Create invoice">
          <div className="field-stack">
            <label>Patient ID<input data-dialog-focus name="patient_id" required /></label>
            <label>Description<input maxLength={300} name="description" required /></label>
            <label>Amount (INR)<input min="0.01" name="amount" required step="0.01" type="number" /></label>
          </div>
        </Dialog>
      )}
      {typeof dialog === "object" && dialog !== null && (
        <Dialog onClose={() => setDialog(null)} onSubmit={async (form) => {
          const amount = Number(form.get("amount"));
          if (!Number.isFinite(amount) || amount <= 0 || Math.round(amount * 100) > dialog.outstanding) throw new Error("Enter a payment within the outstanding balance.");
          await api(`/api/invoices/${encodeURIComponent(dialog.paymentId)}/payments`, {
            method: "POST",
            body: JSON.stringify({
              amount_minor: Math.round(amount * 100),
              method: form.get("method"),
              reference: form.get("reference") || null,
            }),
          });
          setDialog(null);
          notify("Payment recorded.");
          resource.reload();
        }} title="Record payment">
          <div className="field-stack">
            <p>Outstanding balance: <strong>{formatMoney(dialog.outstanding, dialog.currency)}</strong></p>
            <label>Amount ({dialog.currency})<input data-dialog-focus max={(dialog.outstanding / 100).toFixed(2)} min="0.01" name="amount" required step="0.01" type="number" /></label>
            <label>Payment method<select name="method"><option value="upi">UPI</option><option value="card">Card</option><option value="cash">Cash</option><option value="insurance">Insurance</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label>
            <label>Reference (optional)<input maxLength={200} name="reference" /></label>
          </div>
        </Dialog>
      )}
    </>
  );
}

function SimulatorPage({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const [phone, setPhone] = useState("");
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState<Array<{ from: "patient" | "bot"; text: string }>>([]);
  const [debugCode, setDebugCode] = useState<string | null>(null);
  const [cardUrl, setCardUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reset() {
    if (!phone.trim()) {
      setError("Enter the simulator mobile number before resetting the conversation.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/api/simulator/reset", { method: "POST", body: JSON.stringify({ phone: phone.trim() }) });
      setMessages([]);
      setDebugCode(null);
      setCardUrl(null);
      notify("Simulator conversation reset.");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!phone.trim() || !message.trim()) {
      setError("Enter a mobile number and a message.");
      return;
    }
    setBusy(true);
    setError(null);
    const sentMessage = message.trim();
    try {
      const response = await api<{ replies: string[]; debug_code: string | null; card_url: string | null }>("/api/simulator/messages", {
        method: "POST",
        body: JSON.stringify({ phone: phone.trim(), message: sentMessage }),
      });
      setMessages((items) => [...items, { from: "patient", text: sentMessage }, ...response.replies.map((text) => ({ from: "bot" as const, text }))]);
      setDebugCode(response.debug_code);
      setCardUrl(response.card_url);
      setMessage("");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="simulator-layout">
      <section className="card">
        <div className="card-header"><div><h2>Test the appointment bot</h2><p>Simulator messages are recorded by the local demo backend.</p></div></div>
        <label className="field-stack">Simulator mobile number<input autoComplete="tel" maxLength={30} onChange={(event) => setPhone(event.target.value)} required type="tel" value={phone} /></label>
        <p className="muted section-gap">Try “Hi”, then select a service, day, time and mobile number. OTPs are shown only in demo mode.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button secondary" disabled={busy} onClick={reset} type="button">{busy ? "Working…" : "Reset conversation"}</button>
        {debugCode && <div className="demo-code" role="status"><strong>Demo verification code</strong><p><code>{debugCode}</code></p><small>Displayed only by the local simulator.</small></div>}
      </section>
      <section aria-label="Simulated WhatsApp chat" className="phone-frame">
        <header className="phone-head"><span aria-hidden="true" className="result-avatar">+</span><span><b>Clinic bot</b><small>Local simulator</small></span></header>
        <div aria-live="polite" className="chat-window">
          {messages.map((item, index) => <div className={`chat-bubble ${item.from === "patient" ? "outgoing" : ""}`} key={`${index}-${item.text}`}>{item.text}</div>)}
          {cardUrl && <a className="simulator-card-link" href={cardUrl} rel="noreferrer" target="_blank"><img alt="Patient card preview sent by the bot" src={cardUrl} /><span>Open the consented patient card image</span></a>}
          {!messages.length && <EmptyState title="No messages yet">Send a message to begin the conversation.</EmptyState>}
        </div>
        <form className="chat-composer" onSubmit={send}>
          <label className="sr-only" htmlFor="sim-message">Type a message</label>
          <input id="sim-message" maxLength={4000} onChange={(event) => setMessage(event.target.value)} placeholder="Type a message…" required value={message} />
          <button className="button whatsapp small" disabled={busy} type="submit">{busy ? "Sending…" : "Send"}</button>
        </form>
      </section>
    </div>
  );
}

function PublicSiteSettingsPage({ notify }: { notify: (message: string, error?: boolean) => void }) {
  const load = useCallback(() => api<{ hospital_name: string; card_finish: "porcelain" | "midnight" | "sage" }>("/api/public/settings"), []);
  const resource = useResource(load);
  const [hospitalName, setHospitalName] = useState("");
  const [cardFinish, setCardFinish] = useState<"porcelain" | "midnight" | "sage">("porcelain");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!resource.data) return;
    setHospitalName(resource.data.hospital_name);
    setCardFinish(resource.data.card_finish);
  }, [resource.data]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/public/settings", {
        method: "PATCH",
        body: JSON.stringify({ hospital_name: hospitalName.trim(), card_finish: cardFinish }),
      });
      notify("Public appointment card settings saved.");
      resource.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  if (resource.loading && !resource.data) return <LoadingState label="Loading public site settings" />;
  if (resource.error && !resource.data) return <ErrorState message={resource.error} retry={resource.reload} />;

  return (
    <section className="card settings-page">
      <div className="card-header"><div><h2>Appointment pass appearance</h2><p>These settings update the public booking page and newly issued cards.</p></div></div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <form className="field-stack" onSubmit={save}>
        <label>Hospital name<input autoComplete="organization" maxLength={120} minLength={2} onChange={(event) => setHospitalName(event.target.value)} required value={hospitalName} /></label>
        <label>Default card finish<select onChange={(event) => setCardFinish(event.target.value as typeof cardFinish)} value={cardFinish}><option value="porcelain">Porcelain</option><option value="midnight">Midnight</option><option value="sage">Sage</option></select></label>
        <div className="settings-preview"><span className={`settings-swatch ${cardFinish}`} aria-hidden="true" /><span><b>Live preview</b><small>{hospitalName || "Hospital name"} · {cardFinish}</small></span></div>
        <button className="button primary" disabled={busy} type="submit">{busy ? "Saving…" : "Save settings"}</button>
      </form>
    </section>
  );
}

export default App;
