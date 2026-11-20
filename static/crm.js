(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const pageContent = $("#page-content");
  const dialogRoot = $("#dialog-root");
  const notice = $("#notice");
  const state = {
    user: null,
    page: "today",
    currentPatientId: null,
    currentTicketId: null,
    searchTerm: "",
    simulatorPhone: "+919876500099",
    simulatorMessages: [],
    calendarDay: new Date().toISOString().slice(0, 10),
    timer: null
  };
  let noticeTimer;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  function csrfToken() {
    const item = document.cookie.split("; ").find(part => part.startsWith("dental_crm_csrf="));
    return item ? decodeURIComponent(item.slice("dental_crm_csrf=".length)) : "";
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
    if (!["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
      const token = csrfToken();
      if (token) headers.set("X-CSRF-Token", token);
    }
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const detail = body?.detail;
      throw new Error(Array.isArray(detail) ? detail.map(item => item.msg).join("; ") : (detail || `Request failed (${response.status}).`));
    }
    return body;
  }

  function toast(message, error = false) {
    notice.textContent = message;
    notice.classList.toggle("error", error);
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 4200);
  }

  function formatDate(value, options = { dateStyle: "medium" }) {
    if (!value) return "Not set";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }

  function initials(name) {
    return String(name || "?").trim().split(/\s+/).slice(0, 2).map(part => part[0] || "").join("").toUpperCase();
  }

  function badge(status) {
    const label = String(status || "unknown").replaceAll("_", " ");
    return `<span class="badge ${escapeHtml(String(status || "").toLowerCase())}">${escapeHtml(label)}</span>`;
  }

  function roleLabel(roles = []) {
    return roles.map(role => role[0].toUpperCase() + role.slice(1)).join(", ") || "Staff";
  }

  function showLogin() {
    $("#login-view").hidden = false;
    $("#app-view").hidden = true;
    $("#login-email").focus();
  }

  async function initialize() {
    try {
      state.user = await api("/api/auth/me");
      $("#login-view").hidden = true;
      $("#app-view").hidden = false;
      $("#user-name").textContent = state.user.full_name;
      $("#user-role").textContent = roleLabel(state.user.roles);
      $("#user-avatar").textContent = initials(state.user.full_name);
      $("#clinic-name").textContent = document.title.replace(" CRM", "");
      const isAccountant = state.user.roles.includes("accountant") && !state.user.roles.some(role => ["admin", "telecaller", "receptionist", "dentist"].includes(role));
      if (!state.user.roles.includes("admin") && !state.user.roles.includes("accountant")) {
        $('.nav-item[data-page="billing"]').hidden = true;
      }
      if (isAccountant) {
        $$('.nav-item').forEach(button => {
          if (!["billing"].includes(button.dataset.page)) button.hidden = true;
        });
        state.page = "billing";
      }
      const savedTheme = localStorage.getItem("dental-crm-theme");
      if (savedTheme === "dark") document.documentElement.dataset.theme = "dark";
      updateThemeButton();
      await showPage(state.page);
      connectLiveUpdates();
    } catch (error) {
      showLogin();
    }
  }

  function updateThemeButton() {
    const dark = document.documentElement.dataset.theme === "dark";
    $("#theme-toggle").setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    $("#theme-toggle").title = dark ? "Switch to light mode" : "Switch to dark mode";
  }

  async function showPage(page) {
    state.page = page;
    $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.page === page));
    const titles = {
      today: ["YOUR CLINIC", "Today"],
      requests: ["PATIENT SUPPORT", "Requests queue"],
      patients: ["PATIENT RECORDS", "Patient search"],
      patient: ["PATIENT RECORDS", "Patient profile"],
      ticket: ["REQUEST DETAILS", "Ticket detail"],
      calendar: ["SCHEDULE", "Appointments"],
      billing: ["FINANCE", "Billing"],
      simulator: ["TEST ENVIRONMENT", "WhatsApp bot simulator"]
    };
    $("#page-eyebrow").textContent = titles[page]?.[0] || "YOUR CLINIC";
    $("#page-title").textContent = titles[page]?.[1] || "Today";
    $("#sidebar").classList.remove("open");
    $("#mobile-nav").setAttribute("aria-expanded", "false");
    pageContent.innerHTML = `<div class="empty-state"><strong>Loading</strong>Just a moment…</div>`;
    try {
      if (page === "today") await renderToday();
      else if (page === "requests") await renderRequests();
      else if (page === "patients") await renderSearchResults(state.searchTerm);
      else if (page === "patient") await renderPatient(state.currentPatientId);
      else if (page === "ticket") await renderTicket(state.currentTicketId);
      else if (page === "calendar") await renderCalendar();
      else if (page === "billing") await renderBilling();
      else if (page === "simulator") renderSimulator();
      pageContent.focus({ preventScroll: true });
    } catch (error) {
      pageContent.innerHTML = `<div class="empty-state"><strong>We couldn’t load this page</strong>${escapeHtml(error.message)}<p><button class="button secondary" data-action="retry">Try again</button></p></div>`;
    }
  }

  function renderQueueItem(item, includeCall = true) {
    return `<article class="queue-item">
      <div>
        <button class="link-button queue-name" data-action="open-ticket" data-id="${escapeHtml(item.id)}">${escapeHtml(item.patient_name || "Patient")}</button>
        <span class="queue-meta">${escapeHtml(item.ticket_number)} · ${escapeHtml(item.reason)} · ${escapeHtml(item.source || "phone")}</span>
        <span class="queue-phone">${escapeHtml(item.phone || "No phone on record")} · Preferred ${escapeHtml(item.preferred_date ? formatDate(item.preferred_date) : item.preferred_time_of_day || "Any time")}</span>
      </div>
      <div class="queue-actions">
        ${badge(item.status)}
        ${includeCall && item.phone ? `<a class="button primary small" href="tel:${escapeHtml(item.phone)}" aria-label="Call ${escapeHtml(item.patient_name)}">Call</a>` : ""}
      </div>
    </article>`;
  }

  function renderAppointmentItem(item) {
    return `<article class="appointment-item">
      <div><span class="queue-name">${escapeHtml(item.patient_name)}</span>
        <span class="queue-meta">${escapeHtml(item.reason)} · ${escapeHtml(item.ticket_number)}</span>
        <span class="queue-phone">${escapeHtml(item.dentist ? `Dr. ${item.dentist}` : "Dentist not assigned")}</span>
      </div>
      <div>${badge(item.status)}<span class="queue-meta">${escapeHtml(formatDate(item.start_at, { timeStyle: "short" }))}</span></div>
    </article>`;
  }

  async function renderToday() {
    const data = await api("/api/dashboard");
    $("#request-count").textContent = data.metrics.requests_awaiting_call;
    pageContent.innerHTML = `
      <div class="metric-grid">
        <article class="metric"><div class="metric-label">Appointments today</div><div class="metric-value">${data.metrics.appointments_today}</div><div class="metric-note">Confirmed and checked-in visits</div></article>
        <article class="metric"><div class="metric-label">Requests awaiting a call</div><div class="metric-value">${data.metrics.requests_awaiting_call}</div><div class="metric-note">Patient requests in the queue</div></article>
        <article class="metric"><div class="metric-label">Callbacks today</div><div class="metric-value">${data.metrics.callbacks_today}</div><div class="metric-note">Scheduled retry reminders</div></article>
      </div>
      <div class="dashboard-grid">
        <section class="card">
          <div class="card-header"><div><h2>Today’s appointments</h2><p>${escapeHtml(formatDate(data.today))} · Sorted by appointment time</p></div><button class="button secondary small" data-page="calendar">Open calendar</button></div>
          <div class="appointment-list">${data.appointments.length ? data.appointments.map(renderAppointmentItem).join("") : `<div class="empty-state"><strong>No appointments scheduled today</strong>Confirmed appointments will appear here.</div>`}</div>
        </section>
        <section class="card">
          <div class="card-header"><div><h2>Requests to call</h2><p>New requests, shown in arrival order</p></div><button class="button secondary small" data-page="requests">View queue</button></div>
          <div class="queue-list">${data.requests.slice(0, 5).map(item => renderQueueItem(item)).join("") || `<div class="empty-state"><strong>You’re all caught up</strong>New appointment requests appear here.</div>`}</div>
        </section>
      </div>
      <section class="card">
        <div class="card-header"><div><h2>Recent requests</h2><p>New appointment requests from all available channels</p></div><span class="live-status"><span class="live-dot"></span> Updating live</span></div>
        ${renderRequestTable(data.requests.slice(0, 20))}
      </section>`;
    if (data.followups.length) {
      pageContent.insertAdjacentHTML("beforeend", `<section class="card section-gap"><div class="card-header"><div><h2>Callbacks due today</h2><p>Automatic reminders for unanswered calls</p></div><span class="badge">${data.metrics.callbacks_today} due</span></div>
        <div class="queue-list">${data.followups.map(item => `<article class="queue-item"><div>
          <button class="link-button queue-name" data-action="open-ticket" data-id="${escapeHtml(item.appointment_id)}">${escapeHtml(item.patient_name)}</button>
          <span class="queue-meta">${escapeHtml(item.ticket_number)} · retry ${item.attempt_number} · ${escapeHtml(formatDate(item.scheduled_at, { timeStyle: "short" }))}</span>
          <span class="queue-phone">${escapeHtml(item.reason)} · ${escapeHtml(item.phone || "No phone on record")}</span>
        </div>${item.phone ? `<a class="button primary small" href="tel:${escapeHtml(item.phone)}">Call</a>` : ""}</article>`).join("")}</div></section>`);
    }
  }

  function renderRequestTable(items) {
    if (!items.length) return `<div class="empty-state"><strong>No requests yet</strong>Requests will appear here as soon as they are submitted.</div>`;
    return `<div class="table-wrap"><table><thead><tr><th>Ticket</th><th>Patient</th><th>Phone</th><th>Request</th><th>Source</th><th>Status</th></tr></thead><tbody>
      ${items.map(item => `<tr data-action="open-ticket" data-id="${escapeHtml(item.id)}" tabindex="0">
        <td><strong>${escapeHtml(item.ticket_number)}</strong></td><td>${escapeHtml(item.patient_name)}</td>
        <td>${escapeHtml(item.phone || "—")}</td><td>${escapeHtml(item.reason)}</td>
        <td><span class="source-tag">${escapeHtml(item.source)}</span></td><td>${badge(item.status)}</td>
      </tr>`).join("")}</tbody></table></div>`;
  }

  async function renderRequests() {
    const data = await api("/api/dashboard");
    $("#request-count").textContent = data.metrics.requests_awaiting_call;
    pageContent.innerHTML = `<section class="card">
      <div class="card-header"><div><h2>Awaiting a call</h2><p>Open each request to call the patient and record an outcome.</p></div><span class="badge">${data.requests.length} open</span></div>
      <div class="queue-list">${data.requests.map(item => renderQueueItem(item)).join("") || `<div class="empty-state"><strong>No open requests</strong>New requests will appear here.</div>`}</div>
    </section>
    <section class="card section-gap"><div class="card-header"><div><h2>All active requests</h2><p>Use the ticket detail to record call outcomes.</p></div></div>${renderRequestTable(data.requests)}</section>`;
  }

  async function renderSearchResults(term) {
    pageContent.innerHTML = `<section class="card">
      <div class="card-header"><div><h2>Find a patient</h2><p>Search by full or partial name, email, or mobile number.</p></div></div>
      <form id="patient-search-form" class="toolbar"><input id="patient-search-input" type="search" minlength="2" maxlength="120" placeholder="e.g. +91 98765 00001 or Aarav" value="${escapeHtml(term)}" aria-label="Search patients"><button class="button primary" type="submit">Search</button></form>
      <div id="patient-results"></div>
    </section>`;
    if (term.length >= 2) await loadPatientResults(term);
  }

  async function loadPatientResults(term) {
    state.searchTerm = term;
    const results = await api(`/api/patients/search?q=${encodeURIComponent(term)}`);
    const target = $("#patient-results");
    if (!target) return;
    target.innerHTML = results.length ? `<div class="result-list">${results.map(patient => `
      <button class="result-card" data-action="open-patient" data-id="${escapeHtml(patient.id)}">
        <span class="result-avatar">${escapeHtml(initials(patient.full_name))}</span>
        <span><strong>${escapeHtml(patient.full_name)}</strong><small class="queue-meta">${escapeHtml(patient.patient_number)} · ${escapeHtml(patient.phone || "No phone")} · ${escapeHtml(patient.date_of_birth ? formatDate(patient.date_of_birth) : "Date of birth not recorded")}</small></span>
        <span class="source-tag">View history</span>
      </button>`).join("")}</div>` : `<div class="empty-state"><strong>No matching patients</strong>Check the spelling or enter a longer mobile number.</div>`;
  }

  async function renderPatient(patientId) {
    if (!patientId) {
      await showPage("patients");
      return;
    }
    const data = await api(`/api/patients/${encodeURIComponent(patientId)}`);
    const patient = data.patient;
    const activeAllergies = data.allergies.filter(item => item.status === "active");
    const events = [
      ...data.appointments.map(item => ({ date: item.start_at || item.created_at, type: "Appointment", title: `${item.ticket_number} · ${item.reason}`, detail: `Status: ${item.status}` })),
      ...data.calls.map(item => ({ date: item.started_at, type: "Call", title: `Call ${item.outcome.replaceAll("_", " ")}`, detail: item.notes || "No note recorded" })),
      ...data.visits.map(item => ({ date: item.recorded_at, type: "Visit", title: item.complaint, detail: item.treatment_plan || item.examination_findings || "Clinical details recorded" })),
      ...data.invoices.map(item => ({ date: item.created_at, type: "Billing", title: `Invoice · ${formatMoney(item.total_minor, item.currency)}`, detail: `Outstanding ${formatMoney(item.outstanding_minor, item.currency)}` }))
    ].sort((a, b) => new Date(b.date) - new Date(a.date));
    pageContent.innerHTML = `
      <div class="patient-heading">
        <span class="result-avatar">${escapeHtml(initials(patient.full_name))}</span>
        <div><p class="eyebrow">${escapeHtml(patient.patient_number)}</p><h2>${escapeHtml(patient.full_name)}</h2><span class="patient-number">${escapeHtml(data.phones.map(item => item.phone).join(" · ") || "No phone on record")}</span></div>
        <button class="button secondary small" data-action="edit-patient" data-id="${escapeHtml(patient.id)}">Edit profile</button>
        ${state.user.roles.some(role => ["admin", "dentist"].includes(role)) ? `<button class="button primary small" data-action="add-note" data-id="${escapeHtml(patient.id)}">Add note</button><button class="button secondary small" data-action="add-medicine" data-id="${escapeHtml(patient.id)}">Add medicine</button><button class="button secondary small" data-action="add-allergy" data-id="${escapeHtml(patient.id)}">Add allergy</button>` : ""}
      </div>
      ${activeAllergies.length ? `<section class="allergy-warning" aria-label="Allergy warning"><strong>⚠ Active allergies</strong><ul>${activeAllergies.map(item => `<li>${escapeHtml(item.allergen)}${item.severity ? ` · ${escapeHtml(item.severity)}` : ""}${item.reaction ? ` — ${escapeHtml(item.reaction)}` : ""}</li>`).join("")}</ul></section>` : `<section class="allergy-warning"><strong>Allergies</strong><div>No active allergies recorded. Confirm this with the patient before treatment.</div></section>`}
      <div class="profile-grid">
        <section class="card"><div class="card-header"><div><h2>Patient details</h2><p>Permanent record · ${escapeHtml(patient.status)}</p></div></div>
          <div class="patient-details">
            ${detail("Date of birth", patient.date_of_birth ? formatDate(patient.date_of_birth) : "Not recorded")}
            ${detail("Gender", patient.gender || "Not recorded")}
            ${detail("Email", patient.email || "Not recorded")}
            ${detail("Language", patient.preferred_language || "Not recorded")}
            ${detail("Occupation", patient.occupation || "Not recorded")}
            ${detail("Patient since", formatDate(patient.created_at))}
          </div>
          <h3 class="section-gap">Current medicines</h3>
          <div class="list-links">${data.medicines.filter(item => item.status === "current").map(item => `<div class="list-link"><span>${escapeHtml(item.medicine_name)}${item.dose ? ` · ${escapeHtml(item.dose)}` : ""}<small class="queue-meta">${escapeHtml(item.frequency || "")}${item.end_date ? ` · through ${escapeHtml(formatDate(item.end_date))}` : ""}</small></span><span class="source-tag">Current</span></div>`).join("") || `<div class="empty-state">No current medicines recorded.</div>`}</div>
          <h3 class="section-gap">Appointments</h3>
          <div class="list-links">${data.appointments.map(item => `<button class="list-link" data-action="open-ticket" data-id="${escapeHtml(item.id)}"><span>${escapeHtml(item.ticket_number)} · ${escapeHtml(item.reason)}<small class="queue-meta">${escapeHtml(formatDate(item.start_at || item.created_at))}</small></span>${badge(item.status)}</button>`).join("") || `<div class="empty-state">No appointment history yet.</div>`}</div>
        </section>
        <section class="card"><div class="card-header"><div><h2>Full history</h2><p>Appointments, calls, visits and billing in date order.</p></div></div>
          ${data.medical_notes.length ? `<h3>Clinical notes</h3><div class="call-log section-gap">${data.medical_notes.map(item => `<article class="call-log-entry"><small>${escapeHtml(formatDate(item.recorded_at, { dateStyle: "medium", timeStyle: "short" }))} · ${escapeHtml(item.author)}</small><p>${escapeHtml(item.note)}</p></article>`).join("")}</div>` : ""}
          ${data.prescriptions.length ? `<h3 class="section-gap">Prescriptions</h3><div class="list-links">${data.prescriptions.map(prescription => `<article class="list-link"><span><strong>${escapeHtml(formatDate(prescription.created_at))} · ${escapeHtml(prescription.dentist)}</strong><small class="queue-meta">${prescription.items.map(item => `${escapeHtml(item.medicine_name)} · ${escapeHtml(item.dose)} · ${escapeHtml(item.frequency)}`).join("; ")}</small></span>${badge(prescription.status)}</article>`).join("")}</div>` : ""}
          <div class="timeline">${events.length ? events.map(item => `<article class="timeline-item"><time>${escapeHtml(formatDate(item.date, { dateStyle: "medium", timeStyle: "short" }))} · ${escapeHtml(item.type)}</time><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.detail)}</small></article>`).join("") : `<div class="empty-state"><strong>No history recorded</strong>Patient activity will appear here.</div>`}</div>
        </section>
      </div>
      <section class="card section-gap"><div class="card-header"><div><h2>Documents</h2><p>X-rays, reports, photos and consent forms for this patient.</p></div><button class="button secondary small" data-action="upload-document" data-id="${escapeHtml(patient.id)}">Upload file</button></div>
        <div class="list-links">${data.documents.map(doc => `<a class="list-link" href="/api/documents/${encodeURIComponent(doc.id)}" target="_blank" rel="noopener"><span>${escapeHtml(doc.original_name)}<small class="queue-meta">${escapeHtml(doc.document_type)} · ${escapeHtml(formatDate(doc.uploaded_at))}</small></span><span>${Math.ceil(doc.size_bytes / 1024)} KB</span></a>`).join("") || `<div class="empty-state">No documents uploaded.</div>`}</div>
      </section>`;
  }

  function detail(label, value) {
    return `<div class="detail-pair"><span>${escapeHtml(label)}</span><b>${escapeHtml(value || "Not recorded")}</b></div>`;
  }

  function formatMoney(minor, currency = "INR") {
    const value = Number(minor || 0) / 100;
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "INR", maximumFractionDigits: 2 }).format(value);
  }

  async function renderTicket(ticketId) {
    if (!ticketId) {
      await showPage("requests");
      return;
    }
    const data = await api(`/api/appointments/${encodeURIComponent(ticketId)}`);
    const ticket = data.ticket;
    const dentistList = await api("/api/dentists");
    const roomList = await api("/api/rooms");
    const allergies = data.allergies.length
      ? `<div class="allergy-warning"><strong>⚠ Allergy warning</strong><ul>${data.allergies.map(item => `<li>${escapeHtml(item.allergen)}${item.severity ? ` · ${escapeHtml(item.severity)}` : ""}${item.reaction ? ` — ${escapeHtml(item.reaction)}` : ""}</li>`).join("")}</ul></div>`
      : `<div class="allergy-warning"><strong>Allergies</strong><div>None recorded. Confirm with patient.</div></div>`;
    pageContent.innerHTML = `
      <div class="card-header"><div><p class="eyebrow">${escapeHtml(ticket.source)} REQUEST</p><h2>${escapeHtml(ticket.ticket_number)} · ${escapeHtml(ticket.reason)}</h2><p>Received ${escapeHtml(formatDate(ticket.created_at, { dateStyle: "medium", timeStyle: "short" }))}</p></div><button class="button secondary small" data-action="open-patient" data-id="${escapeHtml(ticket.patient_id)}">Open patient history</button></div>
      <div class="ticket-grid">
        <section class="card">
          <div class="card-header"><div><h2>Request & patient</h2><p>${escapeHtml(ticket.patient_name)}</p></div>${badge(ticket.status)}</div>
          <div class="patient-details">${detail("Mobile", ticket.phone)}${detail("Preferred day", ticket.preferred_date ? formatDate(ticket.preferred_date) : "Not specified")}${detail("Preferred time", ticket.preferred_time_of_day)}${detail("Source", ticket.source)}</div>
          ${allergies}
          ${ticket.phone ? `<a class="button whatsapp ticket-call" href="tel:${escapeHtml(ticket.phone)}">☎ Call ${escapeHtml(ticket.phone)}</a>` : `<p class="form-error">No phone number is available.</p>`}
          <form id="call-form" class="call-form">
            <h3>Record call outcome</h3>
            <label>Outcome
              <select name="outcome" id="call-outcome" required>
                <option value="">Choose an outcome</option><option value="confirmed">Confirmed</option><option value="not_reachable">Not reachable</option><option value="reschedule">Reschedule</option><option value="cancelled">Cancelled</option>
              </select>
            </label>
            <div id="confirmation-fields" class="form-grid" hidden>
              <label>Appointment start<input name="start_at" type="datetime-local"></label>
              <label>Appointment end<input name="end_at" type="datetime-local"></label>
              <label>Dentist<select name="dentist_id"><option value="">Choose dentist</option>${dentistList.map(d => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.full_name)}</option>`).join("")}</select></label>
              <label>Room / chair<select name="room_id"><option value="">Not assigned</option>${roomList.map(r => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.name)}</option>`).join("")}</select></label>
            </div>
            <div id="reschedule-fields" class="form-grid" hidden>
              <label>Preferred date<input name="preferred_date" type="date"></label>
              <label>Preferred time of day<select name="preferred_time_of_day"><option value="">Choose a time</option><option value="morning">Morning</option><option value="afternoon">Afternoon</option><option value="evening">Evening</option><option value="any">Any time</option></select></label>
            </div>
            <label>Call note<textarea name="notes" maxlength="4000" placeholder="Briefly record the agreed next step"></textarea></label>
            <button class="button primary full" type="submit">Save outcome</button>
          </form>
        </section>
        <section class="card"><div class="card-header"><div><h2>Call timeline</h2><p>Prior calls and appointment updates</p></div></div>
          <div class="call-log">${data.calls.map(call => `<article class="call-log-entry"><strong>${escapeHtml(call.outcome.replaceAll("_", " "))}</strong><small>${escapeHtml(formatDate(call.started_at, { dateStyle: "medium", timeStyle: "short" }))} · ${escapeHtml(call.caller)}</small><p>${escapeHtml(call.notes || "No note recorded")}</p>${call.next_follow_up_at ? `<small>Retry due ${escapeHtml(formatDate(call.next_follow_up_at, { dateStyle: "medium", timeStyle: "short" }))}</small>` : ""}</article>`).join("") || `<div class="empty-state"><strong>No call history</strong>First-time patient request. Record the first call outcome here.</div>`}</div>
          <h3 class="section-gap">Patient visits</h3><div class="timeline">${data.visits.map(visit => `<article class="timeline-item"><time>${escapeHtml(formatDate(visit.recorded_at))}</time><strong>${escapeHtml(visit.complaint)}</strong><small>${escapeHtml(visit.treatment_plan || visit.examination_findings || "No additional notes")}</small></article>`).join("") || `<div class="empty-state">No previous visit records.</div>`}</div>
        </section>
        <section class="card"><div class="card-header"><div><h2>Status & activity</h2><p>Request state and audit timeline</p></div></div>
          <label class="field-stack">Current status<select id="ticket-status" aria-label="Ticket status">${["requested", "confirmed", "rescheduled", "checked_in", "completed", "cancelled", "no_show"].map(s => `<option value="${s}" ${s === ticket.status ? "selected" : ""}>${s.replaceAll("_", " ")}</option>`).join("")}</select></label>
          <label class="field-stack section-gap">Status note<textarea id="status-note" maxlength="1000" placeholder="Optional context">${escapeHtml(ticket.status_note || "")}</textarea></label>
          <button class="button secondary full section-gap" data-action="save-status" data-id="${escapeHtml(ticket.id)}">Update status</button>
          <h3 class="section-gap">Timeline</h3><div class="timeline">${data.events.map(event => `<article class="timeline-item"><time>${escapeHtml(formatDate(event.occurred_at, { dateStyle: "medium", timeStyle: "short" }))}</time><strong>${escapeHtml(event.event_type.replaceAll("_", " "))}</strong><small>${escapeHtml(event.old_status ? `${event.old_status} → ${event.new_status}` : event.new_status || "")}</small></article>`).join("") || `<div class="empty-state">No activity yet.</div>`}</div>
          <h3 class="section-gap">WhatsApp conversation</h3>
          <div class="conversation-panel" aria-live="polite">${data.messages.map(message => `<div class="chat-bubble ${message.direction === "outbound" ? "outgoing" : ""}">${escapeHtml(message.message)}<small>${escapeHtml(formatDate(message.occurred_at, { timeStyle: "short" }))} · ${escapeHtml(message.delivery_status)}</small></div>`).join("") || `<div class="empty-state">No WhatsApp messages recorded.</div>`}</div>
          ${state.user.roles.some(role => ["admin", "telecaller", "receptionist"].includes(role)) ? `<form id="ticket-reply-form" class="field-stack section-gap" data-patient-id="${escapeHtml(ticket.patient_id)}"><label for="ticket-reply">Reply to patient</label><textarea id="ticket-reply" name="message" maxlength="4000" required placeholder="Free-form replies are available for 24 hours after the patient’s last message."></textarea><button class="button whatsapp" type="submit">Queue WhatsApp reply</button></form>` : ""}
        </section>
      </div>`;
  }

  async function renderCalendar() {
    const rows = await api(`/api/appointments?day=${encodeURIComponent(state.calendarDay)}`);
    pageContent.innerHTML = `<div class="calendar-toolbar"><label class="field-stack">Day<input id="calendar-day" type="date" value="${escapeHtml(state.calendarDay)}"></label><button class="button secondary" data-action="calendar-today">Today</button><span class="muted">${rows.length} appointments or requests</span></div>
      <section class="card"><div class="card-header"><div><h2>Appointments by day</h2><p>Schedule is grouped by selected clinic date.</p></div></div>
        <div class="calendar-list">${rows.map(item => `<article class="calendar-row">
          <div class="calendar-time">${escapeHtml(item.start_at ? formatDate(item.start_at, { timeStyle: "short" }) : "Request")}</div>
          <div><button class="link-button queue-name" data-action="open-ticket" data-id="${escapeHtml(item.id)}">${escapeHtml(item.patient_name)}</button><span class="queue-meta">${escapeHtml(item.reason)} · ${escapeHtml(item.ticket_number)}</span><span class="queue-phone">${escapeHtml(item.dentist ? `Dentist: ${item.dentist}` : "Dentist to be confirmed")}</span></div>
          ${badge(item.status)}
        </article>`).join("") || `<div class="empty-state"><strong>No appointments for this day</strong>Try another date or check the requests queue.</div>`}</div>
      </section>`;
  }

  async function renderBilling() {
    const invoices = await api("/api/invoices");
    pageContent.innerHTML = `<section class="card"><div class="card-header"><div><h2>Invoices</h2><p>Outstanding balances and payments. Amounts are shown in the invoice currency.</p></div><button class="button primary small" data-action="new-invoice">Create invoice</button></div>
      <div class="table-wrap"><table><thead><tr><th>Patient</th><th>Created</th><th>Status</th><th>Total</th><th>Paid</th><th>Outstanding</th><th></th></tr></thead><tbody>
        ${invoices.map(item => `<tr><td><strong>${escapeHtml(item.patient_name)}</strong></td><td>${escapeHtml(formatDate(item.created_at))}</td><td>${badge(item.status)}</td><td>${escapeHtml(formatMoney(item.total_minor, item.currency))}</td><td>${escapeHtml(formatMoney(item.paid_minor, item.currency))}</td><td>${escapeHtml(formatMoney(item.outstanding_minor, item.currency))}</td><td><button class="button secondary small" data-action="record-payment" data-id="${escapeHtml(item.id)}" data-outstanding="${escapeHtml(item.outstanding_minor)}" data-currency="${escapeHtml(item.currency)}">Add payment</button></td></tr>`).join("") || ""}
      </tbody></table></div>
      ${invoices.length ? "" : `<div class="empty-state"><strong>No invoices yet</strong>Invoices created for patients will appear here.</div>`}
    </section>`;
  }

  function renderSimulator() {
    pageContent.innerHTML = `<div class="simulator-layout">
      <section class="card"><div class="card-header"><div><h2>Test the appointment bot</h2><p>Messages stay local to the demo and use synthetic contact details.</p></div></div>
        <label class="field-stack">Simulator mobile number<input id="sim-phone" type="tel" value="${escapeHtml(state.simulatorPhone)}" autocomplete="tel"></label>
        <p class="muted section-gap">Try “Hi”, then select a service, day, time and mobile number. OTPs are random and shown only in this demo.</p>
        <button class="button secondary" data-action="sim-reset">Reset conversation</button>
        <div id="sim-code" class="demo-code" hidden><strong>Demo verification code</strong><p><code></code></p><small>Displayed only by the local simulator; the database stores a hash.</small></div>
      </section>
      <section class="phone-frame" aria-label="Simulated WhatsApp chat">
        <header class="phone-head"><span class="result-avatar">+</span><span><b>${escapeHtml(document.title.replace(" CRM", ""))}</b><small>Clinic bot · local simulator</small></span></header>
        <div id="sim-chat" class="chat-window" aria-live="polite"></div>
        <form id="sim-form" class="chat-composer"><label class="sr-only" for="sim-message">Type a message</label><input id="sim-message" placeholder="Type a message…" maxlength="4000" required><button class="button whatsapp small" type="submit">Send</button></form>
      </section>
    </div>`;
    renderSimulatorMessages();
  }

  function renderSimulatorMessages() {
    const target = $("#sim-chat");
    if (!target) return;
    target.innerHTML = state.simulatorMessages.map(item => `<div class="chat-bubble ${item.from === "patient" ? "outgoing" : ""}">${escapeHtml(item.text)}</div>`).join("") || `<div class="empty-state">Send “Hi” to begin the conversation.</div>`;
    target.scrollTop = target.scrollHeight;
  }

  function showDialog(title, bodyHtml, onSubmit) {
    dialogRoot.innerHTML = `<div class="dialog-backdrop" role="presentation"><section class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
      <div class="dialog-header"><div><p class="eyebrow">PATIENT RECORDS</p><h2 id="dialog-title">${escapeHtml(title)}</h2></div><button class="icon-button" data-action="close-dialog" aria-label="Close dialog">×</button></div>
      <form id="dialog-form">${bodyHtml}<div class="dialog-actions"><button type="button" class="button secondary" data-action="close-dialog">Cancel</button><button type="submit" class="button primary">Save</button></div></form>
    </section></div>`;
    $("#dialog-form").addEventListener("submit", async event => {
      event.preventDefault();
      try {
        await onSubmit(new FormData(event.currentTarget));
      } catch (error) {
        toast(error.message, true);
      }
    });
    $("[data-action='close-dialog']", dialogRoot).focus();
  }

  async function editPatient(patientId) {
    const data = await api(`/api/patients/${encodeURIComponent(patientId)}`);
    const patient = data.patient;
    showDialog("Edit patient profile", `
      <div class="form-grid">
        <label class="span-2">Full name<input name="full_name" value="${escapeHtml(patient.full_name)}" maxlength="200" required></label>
        <label>Email<input name="email" type="email" value="${escapeHtml(patient.email || "")}"></label>
        <label>Date of birth<input name="date_of_birth" type="date" value="${escapeHtml(patient.date_of_birth || "")}"></label>
        <label>Gender<input name="gender" value="${escapeHtml(patient.gender || "")}" maxlength="40"></label>
        <label>Preferred language<input name="preferred_language" value="${escapeHtml(patient.preferred_language || "")}" maxlength="12"></label>
        <label class="span-2">Occupation<input name="occupation" value="${escapeHtml(patient.occupation || "")}" maxlength="120"></label>
      </div>`, async form => {
      const payload = Object.fromEntries([...form.entries()].filter(([, value]) => value !== ""));
      await api(`/api/patients/${encodeURIComponent(patientId)}`, { method: "PATCH", body: JSON.stringify(payload) });
      dialogRoot.innerHTML = "";
      toast("Patient profile updated.");
      await renderPatient(patientId);
    });
  }

  function addClinicalRecord(patientId, type) {
    if (type === "note") {
      showDialog("Add clinical note", `<label class="field-stack">Note<textarea name="note_text" maxlength="10000" required placeholder="Add a dated clinical note"></textarea></label>`, async form => {
        await api(`/api/patients/${encodeURIComponent(patientId)}/medical-notes`, { method: "POST", body: JSON.stringify({ note_text: form.get("note_text") }) });
        dialogRoot.innerHTML = "";
        toast("Clinical note added.");
        await renderPatient(patientId);
      });
    } else if (type === "medicine") {
      showDialog("Add current medicine", `<div class="field-stack">
        <label>Medicine<input name="medicine_name" maxlength="200" required></label>
        <label>Dose<input name="dose" maxlength="200"></label>
        <label>Frequency<input name="frequency" maxlength="200"></label>
        <label>Start date<input name="start_date" type="date"></label>
        <label>End date<input name="end_date" type="date"></label>
      </div>`, async form => {
        const payload = Object.fromEntries([...form.entries()].filter(([, value]) => value !== ""));
        await api(`/api/patients/${encodeURIComponent(patientId)}/medicines`, { method: "POST", body: JSON.stringify(payload) });
        dialogRoot.innerHTML = "";
        toast("Medicine added to the patient record.");
        await renderPatient(patientId);
      });
    } else {
      showDialog("Add allergy", `<div class="field-stack">
        <label>Allergen<input name="allergen_text" maxlength="300" required></label>
        <label>Reaction<input name="reaction" maxlength="1000"></label>
        <label>Severity<select name="severity"><option value="unknown">Unknown</option><option value="mild">Mild</option><option value="moderate">Moderate</option><option value="severe">Severe</option></select></label>
      </div>`, async form => {
        await api(`/api/patients/${encodeURIComponent(patientId)}/allergies`, {
          method: "POST",
          body: JSON.stringify({ allergen_text: form.get("allergen_text"), reaction: form.get("reaction") || null, severity: form.get("severity") })
        });
        dialogRoot.innerHTML = "";
        toast("Allergy added to the prominent patient warning.");
        await renderPatient(patientId);
      });
    }
  }

  function openUploadDialog(patientId) {
    showDialog("Upload patient document", `
      <div class="field-stack">
        <label>Document type<select name="document_type" required><option value="xray">X-ray</option><option value="photo">Photo</option><option value="report">Report</option><option value="consent">Consent form</option><option value="other">Other</option></select></label>
        <label>Select a PDF, JPEG or PNG file<input name="file" type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" required></label>
        <small class="muted">Maximum upload size is configured by the clinic.</small>
      </div>`, async form => {
      const formData = new FormData();
      formData.set("file", form.get("file"));
      formData.set("document_type", form.get("document_type"));
      await api(`/api/patients/${encodeURIComponent(patientId)}/documents?document_type=${encodeURIComponent(form.get("document_type"))}`, { method: "POST", body: formData });
      dialogRoot.innerHTML = "";
      toast("Document uploaded.");
      await renderPatient(patientId);
    });
  }

  function openInvoiceDialog() {
    showDialog("Create invoice", `
      <div class="field-stack">
        <label>Patient ID<input name="patient_id" placeholder="Paste the patient record ID" required></label>
        <label>Description<input name="description" maxlength="300" required></label>
        <label>Amount (INR)<input name="amount" type="number" min="0.01" step="0.01" required></label>
      </div>`, async form => {
      const amountMinor = Math.round(Number(form.get("amount")) * 100);
      await api("/api/invoices", {
        method: "POST",
        body: JSON.stringify({
          patient_id: form.get("patient_id"),
          items: [{ description: form.get("description"), quantity: 1, unit_amount_minor: amountMinor, discount_minor: 0 }]
        })
      });
      dialogRoot.innerHTML = "";
      toast("Invoice created.");
      await renderBilling();
    });
  }

  function openPaymentDialog(invoiceId, outstanding, currency) {
    showDialog("Record payment", `
      <div class="field-stack">
        <p>Outstanding balance: <strong>${escapeHtml(formatMoney(outstanding, currency))}</strong></p>
        <label>Amount (${escapeHtml(currency)})<input name="amount" type="number" min="0.01" max="${Number(outstanding) / 100}" step="0.01" required></label>
        <label>Payment method<select name="method"><option value="upi">UPI</option><option value="card">Card</option><option value="cash">Cash</option><option value="insurance">Insurance</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label>
        <label>Reference (optional)<input name="reference" maxlength="200"></label>
      </div>`, async form => {
      await api(`/api/invoices/${encodeURIComponent(invoiceId)}/payments`, {
        method: "POST",
        body: JSON.stringify({ amount_minor: Math.round(Number(form.get("amount")) * 100), method: form.get("method"), reference: form.get("reference") || null })
      });
      dialogRoot.innerHTML = "";
      toast("Payment recorded.");
      await renderBilling();
    });
  }

  async function submitCallOutcome(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const formData = new FormData(form);
    const outcome = formData.get("outcome");
    const body = { outcome, notes: formData.get("notes") || "" };
    if (outcome === "confirmed") {
      const start = formData.get("start_at");
      const end = formData.get("end_at");
      body.start_at = start ? new Date(start).toISOString() : "";
      body.end_at = end ? new Date(end).toISOString() : "";
      body.dentist_id = formData.get("dentist_id") || null;
      body.room_id = formData.get("room_id") || null;
    }
    if (outcome === "reschedule") {
      body.preferred_date = formData.get("preferred_date") || null;
      body.preferred_time_of_day = formData.get("preferred_time_of_day") || null;
    }
    await api(`/api/appointments/${encodeURIComponent(state.currentTicketId)}/call-outcome`, { method: "POST", body: JSON.stringify(body) });
    toast("Call outcome saved.");
    await renderTicket(state.currentTicketId);
  }

  async function handleClick(event) {
    const pageButton = event.target.closest("[data-page]");
    if (pageButton) {
      event.preventDefault();
      await showPage(pageButton.dataset.page);
      return;
    }
    const action = event.target.closest("[data-action]");
    if (!action) return;
    const id = action.dataset.id;
    try {
      switch (action.dataset.action) {
        case "open-ticket":
          state.currentTicketId = id;
          await showPage("ticket");
          break;
        case "open-patient":
          state.currentPatientId = id;
          await showPage("patient");
          break;
        case "retry":
          await showPage(state.page);
          break;
        case "edit-patient":
          await editPatient(id);
          break;
        case "add-note":
          addClinicalRecord(id, "note");
          break;
        case "add-medicine":
          addClinicalRecord(id, "medicine");
          break;
        case "add-allergy":
          addClinicalRecord(id, "allergy");
          break;
        case "upload-document":
          openUploadDialog(id);
          break;
        case "save-status":
          await api(`/api/appointments/${encodeURIComponent(id)}`, {
            method: "PATCH",
            body: JSON.stringify({ status: $("#ticket-status").value, status_note: $("#status-note").value })
          });
          toast("Ticket status updated.");
          await renderTicket(id);
          break;
        case "calendar-today":
          state.calendarDay = new Date().toISOString().slice(0, 10);
          await renderCalendar();
          break;
        case "new-invoice":
          openInvoiceDialog();
          break;
        case "record-payment":
          openPaymentDialog(id, Number(action.dataset.outstanding), action.dataset.currency);
          break;
        case "close-dialog":
          dialogRoot.innerHTML = "";
          break;
        case "sim-reset":
          state.simulatorPhone = $("#sim-phone").value;
          await api("/api/simulator/reset", { method: "POST", body: JSON.stringify({ phone: state.simulatorPhone }) });
          state.simulatorMessages = [];
          $("#sim-code").hidden = true;
          renderSimulatorMessages();
          toast("Simulator conversation reset.");
          break;
        default:
          break;
      }
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function handleSubmit(event) {
    if (event.target.id === "login-form") {
      event.preventDefault();
      const errorBox = $("#login-error");
      errorBox.hidden = true;
      try {
        const payload = { email: $("#login-email").value, password: $("#login-password").value };
        await api("/api/auth/login", { method: "POST", body: JSON.stringify(payload) });
        $("#login-password").value = "";
        await initialize();
      } catch (error) {
        errorBox.textContent = error.message;
        errorBox.hidden = false;
      }
      return;
    }
    if (event.target.id === "global-search-form" || event.target.id === "patient-search-form") {
      event.preventDefault();
      const input = event.target.id === "global-search-form" ? $("#global-search") : $("#patient-search-input");
      const term = input.value.trim();
      if (term.length < 2) {
        toast("Enter at least 2 characters to search.", true);
        return;
      }
      state.searchTerm = term;
      if (event.target.id === "global-search-form") await showPage("patients");
      else await loadPatientResults(term);
      return;
    }
    if (event.target.id === "call-form") {
      await submitCallOutcome(event);
      return;
    }
    if (event.target.id === "sim-form") {
      event.preventDefault();
      const input = $("#sim-message");
      const message = input.value.trim();
      if (!message) return;
      state.simulatorPhone = $("#sim-phone").value.trim();
      state.simulatorMessages.push({ from: "patient", text: message });
      input.value = "";
      renderSimulatorMessages();
      try {
        const response = await api("/api/simulator/messages", { method: "POST", body: JSON.stringify({ phone: state.simulatorPhone, message }) });
        response.replies.forEach(text => state.simulatorMessages.push({ from: "bot", text }));
        const code = $("#sim-code");
        if (response.debug_code) {
          code.hidden = false;
          $("code", code).textContent = response.debug_code;
        }
        renderSimulatorMessages();
      } catch (error) {
        state.simulatorMessages.push({ from: "bot", text: `Could not send: ${error.message}` });
        renderSimulatorMessages();
      }
      return;
    }
    if (event.target.id === "ticket-reply-form") {
      event.preventDefault();
      const patientId = event.target.dataset.patientId;
      const message = $("#ticket-reply").value.trim();
      try {
        await api(`/api/patients/${encodeURIComponent(patientId)}/messages`, {
          method: "POST",
          body: JSON.stringify({ message })
        });
        $("#ticket-reply").value = "";
        toast("WhatsApp reply queued.");
        await renderTicket(state.currentTicketId);
      } catch (error) {
        toast(error.message, true);
      }
    }
  }

  function handleChange(event) {
    if (event.target.id === "call-outcome") {
      $("#confirmation-fields").hidden = event.target.value !== "confirmed";
      $("#reschedule-fields").hidden = event.target.value !== "reschedule";
      const required = event.target.value === "confirmed";
      $("#confirmation-fields").querySelectorAll("input, select").forEach(input => {
        input.required = required && ["start_at", "end_at", "dentist_id"].includes(input.name);
      });
      const needsReschedule = event.target.value === "reschedule";
      $("#reschedule-fields").querySelectorAll("input, select").forEach(input => {
        input.required = needsReschedule;
      });
    }
    if (event.target.id === "calendar-day") {
      state.calendarDay = event.target.value;
      renderCalendar().catch(error => toast(error.message, true));
    }
  }

  function connectLiveUpdates() {
    if (!window.EventSource) return;
    const events = new EventSource("/api/events");
    events.onmessage = event => {
      try {
        const data = JSON.parse(event.data);
        if (data.organisation_id !== state.user?.organisation_id) return;
        if (["today", "requests", "calendar"].includes(state.page)) {
          showPage(state.page).catch(error => toast(error.message, true));
        }
      } catch {
        // Ignore malformed live events; the next database notification will refresh the view.
      }
    };
    events.onerror = () => {
      $(".live-status").title = "Connection interrupted; reconnecting automatically.";
    };
    events.onopen = () => {
      $(".live-status").title = "Connected to live updates.";
    };
  }

  $("#logout-button").addEventListener("click", async () => {
    try {
      await api("/api/auth/logout", { method: "POST", body: "{}" });
    } finally {
      window.location.reload();
    }
  });
  $("#theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark";
    if (dark) delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = "dark";
    localStorage.setItem("dental-crm-theme", dark ? "light" : "dark");
    updateThemeButton();
  });
  $("#mobile-nav").addEventListener("click", () => {
    const isOpen = $("#sidebar").classList.toggle("open");
    $("#mobile-nav").setAttribute("aria-expanded", String(isOpen));
  });
  document.addEventListener("click", handleClick);
  document.addEventListener("submit", handleSubmit);
  document.addEventListener("change", handleChange);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      dialogRoot.innerHTML = "";
      $("#sidebar").classList.remove("open");
      $("#mobile-nav").setAttribute("aria-expanded", "false");
    }
    if (event.key === "Enter" && event.target.matches("tr[data-action='open-ticket']")) {
      event.target.click();
    }
  });

  initialize();
})();
