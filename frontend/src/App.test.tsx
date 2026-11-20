import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Dashboard, PatientDetail, TicketDetail, User } from "./types";

const staffUser: User = {
  id: "staff-1",
  full_name: "Alex Morgan",
  email: "alex@example.test",
  organisation_id: "org-1",
  branch_id: "branch-1",
  roles: ["admin"],
};

const board: Dashboard = {
  today: "2026-11-18",
  metrics: { appointments_today: 1, requests_awaiting_call: 1, callbacks_today: 1 },
  appointments: [{
    id: "ticket-1",
    ticket_number: "TK-0001",
    start_at: "2026-11-18T10:00:00+05:30",
    reason: "Check-up",
    status: "confirmed",
    patient_name: "Jamie Demo",
    phone: "+919876500001",
    dentist: "Dr. Alex",
  }],
  requests: [{
    id: "ticket-1",
    ticket_number: "TK-0001",
    created_at: "2026-11-18T09:00:00+05:30",
    reason: "Tooth pain",
    preferred_date: "2026-11-20",
    preferred_time_of_day: "morning",
    source: "whatsapp",
    patient_name: "Jamie Demo",
    phone: "+919876500001",
    status: "requested",
  }],
  followups: [{
    appointment_id: "ticket-1",
    patient_name: "Jamie Demo",
    ticket_number: "TK-0001",
    attempt_number: 1,
    scheduled_at: "2026-11-18T11:00:00+05:30",
    reason: "Tooth pain",
    phone: "+919876500001",
  }],
};

const patientDetail: PatientDetail = {
  patient: {
    id: "patient-1",
    patient_number: "PT-000001",
    full_name: "Jamie Demo",
    email: "jamie@example.test",
    date_of_birth: "1992-06-15",
    gender: "Prefer not to say",
    occupation: "Designer",
    preferred_language: "en",
    status: "active",
    created_at: "2025-01-01T10:00:00Z",
  },
  phones: [{ phone: "+919876500001", label: "mobile", is_primary: true, verified_at: null }],
  allergies: [{ allergen: "Penicillin", reaction: "Rash", severity: "moderate", status: "active", recorded_at: "2026-01-01T10:00:00Z" }],
  appointments: [{
    id: "ticket-1",
    ticket_number: "TK-0001",
    reason: "Tooth pain",
    preferred_date: "2026-11-20",
    preferred_time_of_day: "morning",
    start_at: null,
    end_at: null,
    status: "requested",
    source: "whatsapp",
    created_at: "2026-11-18T09:00:00+05:30",
  }],
  visits: [],
  calls: [],
  documents: [{ id: "document-1", original_name: "xray.pdf", mime_type: "application/pdf", size_bytes: 4096, document_type: "xray", uploaded_at: "2026-01-01T10:00:00Z" }],
  invoices: [],
  medical_notes: [],
  medicines: [],
  prescriptions: [],
};

const opPatientFile = {
  patient: {
    id: "patient-1",
    op_id: "OP-000123-0",
    patient_number: "PT-000001",
    full_name: "Jamie Demo",
    email: "jamie@example.test",
    date_of_birth: "1992-06-15",
    gender: "Prefer not to say",
    occupation: "Designer",
    preferred_language: "en",
    status: "active",
    created_at: "2025-01-01T10:00:00Z",
    merged_into: null,
    phone: "+919876500001",
    address: { line1: "", line2: "", city: "", state: "", postal_code: "" },
  },
  medical_profile: {},
  phones: [],
  allergies: [],
  conditions: [],
  medicines: [],
  surgeries: [],
  habits: [],
  family_history: [],
  medical_notes: [],
  calls: [],
  relationships: [],
  consents: [],
  messages: [],
  tooth_chart: [],
  visits: [],
  appointments: [],
  followups: [],
  reminders: [],
  prescriptions: [],
  documents: [],
  payments: [],
  invoices: [],
  payment_details: [],
  payments_permitted: true,
  whatsapp_card_consent: false,
  cards: [],
  notice: null,
};

const ticketDetail: TicketDetail = {
  ticket: {
    id: "ticket-1",
    ticket_number: "TK-0001",
    patient_id: "patient-1",
    branch_id: "branch-1",
    reason: "Tooth pain",
    preferred_date: "2026-11-20",
    preferred_time_of_day: "morning",
    start_at: null,
    end_at: null,
    status: "requested",
    status_note: "",
    source: "whatsapp",
    created_at: "2026-11-18T09:00:00+05:30",
    patient_name: "Jamie Demo",
    phone: "+919876500001",
  },
  allergies: [{ allergen: "Penicillin", reaction: "Rash", severity: "moderate" }],
  calls: [],
  events: [],
  visits: [],
  messages: [{ direction: "inbound", message: "Hi", occurred_at: "2026-11-18T09:00:00Z", delivery_status: "received" }],
};

const invoices = [{
  id: "invoice-1",
  patient_name: "Jamie Demo",
  created_at: "2026-11-18T09:00:00Z",
  status: "issued",
  currency: "INR",
  total_minor: 250000,
  paid_minor: 50000,
  outstanding_minor: 200000,
}];

type RecordedRequest = { path: string; method: string; body: BodyInit | null | undefined };
let requests: RecordedRequest[];
let authenticated: boolean;
let failDashboard: boolean;
let failLogin: boolean;
let emptyDashboard: boolean;

function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installFetch() {
  requests = [];
  authenticated = false;
  failDashboard = false;
  failLogin = false;
  emptyDashboard = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    requests.push({ path, method, body: init?.body });
    const url = new URL(path, "http://localhost");
    if (url.pathname === "/api/auth/me") return authenticated ? response(staffUser) : response({ detail: "Sign in required." }, 401);
    if (url.pathname === "/api/auth/login" && method === "POST") {
      if (failLogin) return response({ detail: "Email or password is incorrect." }, 401);
      authenticated = true;
      return response(staffUser);
    }
    if (url.pathname === "/api/auth/logout" && method === "POST") {
      authenticated = false;
      return response({ ok: true });
    }
    if (url.pathname === "/api/public/settings" && method === "GET") {
      return response({ hospital_name: "Gangayya Dental Hospital", card_finish: "porcelain", time_slots: ["09:00"] });
    }
    if (url.pathname === "/api/public/settings" && method === "PATCH") return response(JSON.parse(String(init?.body ?? "{}")));
    if (url.pathname === "/api/dashboard") {
      if (failDashboard) return response({ detail: "Database unavailable." }, 503);
      if (emptyDashboard) return response({ ...board, appointments: [], requests: [], followups: [], metrics: { appointments_today: 0, requests_awaiting_call: 0, callbacks_today: 0 } });
      return response(board);
    }
    if (url.pathname === "/api/patients/lookup") return response([{
      id: "patient-1",
      op_id: "OP-000123-0",
      patient_number: "PT-000001",
      full_name: "Jamie Demo",
      phone: "+919876500001",
      date_of_birth: "1992-06-15",
    }]);
    if (url.pathname === "/api/patients/by-op/OP-000123-0") return response(opPatientFile);
    if (url.pathname === "/api/patients/patient-1" && method === "GET") return response(patientDetail);
    if (url.pathname === "/api/patients/patient-1") return response({ ok: true });
    if (url.pathname === "/api/appointments/ticket-1" && method === "GET") return response(ticketDetail);
    if (url.pathname === "/api/appointments/ticket-1") return response({ ok: true });
    if (url.pathname === "/api/dentists") return response([{ id: "dentist-1", full_name: "Dr. Alex" }]);
    if (url.pathname === "/api/rooms") return response([{ id: "room-1", name: "Chair 1" }]);
    if (url.pathname === "/api/appointments") return response(board.appointments);
    if (url.pathname === "/api/invoices" && method === "GET") return response(invoices);
    if (url.pathname === "/api/invoices" && method === "POST") return response({ id: "invoice-2" }, 201);
    if (url.pathname === "/api/invoices/invoice-1/payments") return response({ id: "payment-1" }, 201);
    if (url.pathname === "/api/simulator/reset") return response({ ok: true });
    if (url.pathname === "/api/simulator/messages") return response({ replies: ["Welcome. Reply Hi to begin."], debug_code: "123456" });
    if (url.pathname.endsWith("/documents") && method === "POST") return response({ id: "document-2" }, 201);
    if (url.pathname.endsWith("/medical-notes") || url.pathname.endsWith("/medicines") || url.pathname.endsWith("/allergies")) return response({ id: "record-1" }, 201);
    if (url.pathname.endsWith("/messages")) return response({ queued: true }, 202);
    return response({ detail: `Unexpected test request: ${method} ${url.pathname}` }, 404);
  }));
}

async function signIn(user = userEvent.setup()) {
  render(<App />);
  const email = await screen.findByLabelText("Work email");
  await user.type(email, "alex@example.test");
  await user.type(screen.getByLabelText("Password"), "correct-horse");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByRole("heading", { name: "Today" });
  return user;
}

beforeEach(() => {
  window.localStorage.clear();
  installFetch();
});

describe("staff CRM actions", () => {
  it("opens and saves persisted public site settings", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(await screen.findByRole("heading", { name: "Appointment pass appearance" })).toBeInTheDocument();
    const name = screen.getByLabelText("Hospital name");
    await user.clear(name);
    await user.type(name, "Gangayya Dental Hospital, Marlapur");
    await user.selectOptions(screen.getByLabelText("Default card finish"), "sage");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Public appointment card settings saved.")).toBeInTheDocument();
    const update = requests.find((request) => request.path === "/api/public/settings" && request.method === "PATCH");
    expect(JSON.parse(String(update?.body))).toEqual({ hospital_name: "Gangayya Dental Hospital, Marlapur", card_finish: "sage" });
  });

  it("authenticates, navigates, searches, changes theme, and signs out", async () => {
    const user = await signIn();
    expect(await screen.findByText("Appointments today")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Requests" }));
    expect(await screen.findByRole("heading", { name: "Awaiting a call" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Call Jamie Demo" })).toHaveAttribute("href", "tel:+919876500001");

    await user.type(screen.getByRole("searchbox", { name: /Search by OP ID/ }), "Jamie{Enter}");
    await user.click(await screen.findByRole("button", { name: /Jamie Demo/ }));
    expect(await screen.findByRole("heading", { name: "Jamie Demo" })).toBeInTheDocument();
    expect(screen.getByText("OP-000123-0")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Calendar" }));
    expect(await screen.findByRole("heading", { name: "Appointments by day" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show appointments for today" }));
    expect(requests.some((request) => request.path.startsWith("/api/appointments?day="))).toBe(true);

    await user.click(screen.getByRole("button", { name: "Billing" }));
    expect(await screen.findByRole("heading", { name: "Invoices" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Bot simulator" }));
    expect(await screen.findByRole("heading", { name: "Test the appointment bot" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Switch to dark mode" }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem("dental-crm-theme")).toBe("dark");
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
    expect(requests.some((request) => request.path === "/api/auth/logout" && request.method === "POST")).toBe(true);
  });

  it("supports patient history, profile edits, clinical records, document upload, and ticket actions", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "Requests" }));
    await user.click(await screen.findByRole("button", { name: /TK-0001/ }));
    await user.click(await screen.findByRole("button", { name: "Open patient history" }));
    expect(await screen.findByLabelText("Allergy warning")).toHaveTextContent("Penicillin");
    expect(screen.getByRole("link", { name: /xray\.pdf/ })).toHaveAttribute("href", "/api/documents/document-1");

    await user.click(screen.getByRole("button", { name: "Edit profile" }));
    const editDialog = screen.getByRole("dialog");
    await user.clear(within(editDialog).getByLabelText("Full name"));
    await user.type(within(editDialog).getByLabelText("Full name"), "Jamie Demo Updated");
    await user.click(within(editDialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requests.some((request) => request.path === "/api/patients/patient-1" && request.method === "PATCH")).toBe(true));

    await user.click(screen.getByRole("button", { name: "Add note" }));
    await user.type(within(screen.getByRole("dialog")).getByLabelText("Note"), "Clinical note");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requests.some((request) => request.path.endsWith("/medical-notes"))).toBe(true));

    await user.click(screen.getByRole("button", { name: "Add medicine" }));
    const medicineDialog = screen.getByRole("dialog");
    await user.type(within(medicineDialog).getByLabelText("Medicine"), "Medicine A");
    await user.click(within(medicineDialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requests.some((request) => request.path.endsWith("/medicines"))).toBe(true));

    await user.click(screen.getByRole("button", { name: "Add allergy" }));
    const allergyDialog = screen.getByRole("dialog");
    await user.type(within(allergyDialog).getByLabelText("Allergen"), "Latex");
    await user.selectOptions(within(allergyDialog).getByLabelText("Severity"), "severe");
    await user.click(within(allergyDialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requests.some((request) => request.path.endsWith("/allergies"))).toBe(true));

    await user.click(screen.getByRole("button", { name: "Upload file" }));
    const uploadDialog = screen.getByRole("dialog");
    const upload = new window.File(["%PDF-1.7"], "report.pdf", { type: "application/pdf" });
    const fileInput = within(uploadDialog).getByLabelText(/Select a PDF/);
    await user.upload(fileInput, upload);
    expect((fileInput as HTMLInputElement).files?.[0]?.name).toBe("report.pdf");
    expect((fileInput as HTMLInputElement).files?.[0]?.size).toBeGreaterThan(0);
    fireEvent.submit(uploadDialog.querySelector("form") as HTMLFormElement);
    await waitFor(() => expect(
      requests.filter((request) => request.method === "POST").map((request) => request.path),
    ).toContainEqual(expect.stringContaining("/documents")));

    await user.click(screen.getByRole("button", { name: /TK-0001/ }));
    expect(await screen.findByRole("heading", { name: /Request & patient/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Call \+919876500001/ })).toHaveAttribute("href", "tel:+919876500001");
    await user.selectOptions(screen.getByLabelText("Outcome"), "reschedule");
    await user.type(screen.getByLabelText("Preferred date"), "2026-11-25");
    await user.selectOptions(screen.getByLabelText("Preferred time of day"), "afternoon");
    await user.click(screen.getByRole("button", { name: "Save outcome" }));
    await waitFor(() => expect(requests.some((request) => request.path.endsWith("/call-outcome") && request.method === "POST")).toBe(true));

    await user.selectOptions(screen.getByLabelText("Ticket status"), "cancelled");
    await user.click(screen.getByRole("button", { name: "Update status" }));
    await waitFor(() => expect(requests.some((request) => request.path === "/api/appointments/ticket-1" && request.method === "PATCH")).toBe(true));
    await user.type(screen.getByLabelText("Reply to patient"), "We will call soon.");
    await user.click(screen.getByRole("button", { name: "Queue WhatsApp reply" }));
    await waitFor(() => expect(requests.some((request) => request.path.endsWith("/messages") && request.method === "POST")).toBe(true));
    await user.click(screen.getByRole("button", { name: "Open patient history" }));
    expect(await screen.findByRole("heading", { name: "Patient details" })).toBeInTheDocument();
  });

  it("confirms a call with a dentist and records status errors accessibly", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "Requests" }));
    await user.click(await screen.findByRole("button", { name: /TK-0001/ }));
    await screen.findByRole("heading", { name: /Request & patient/ });
    await user.selectOptions(screen.getByLabelText("Outcome"), "confirmed");
    fireEvent.change(screen.getByLabelText("Appointment start"), { target: { value: "2026-11-25T10:00" } });
    fireEvent.change(screen.getByLabelText("Appointment end"), { target: { value: "2026-11-25T10:30" } });
    await user.selectOptions(screen.getByLabelText("Dentist"), "dentist-1");
    await user.click(screen.getByRole("button", { name: "Save outcome" }));
    await waitFor(() => expect(requests.some((request) => request.path.endsWith("/call-outcome"))).toBe(true));
  });

  it("creates invoices, records payments, and tests bot messages and reset", async () => {
    const user = await signIn();
    await user.click(screen.getByRole("button", { name: "Billing" }));
    await screen.findByRole("heading", { name: "Invoices" });
    await user.click(screen.getByRole("button", { name: "Create invoice" }));
    const invoiceDialog = screen.getByRole("dialog");
    await user.type(within(invoiceDialog).getByLabelText("Patient ID"), "patient-1");
    await user.type(within(invoiceDialog).getByLabelText("Description"), "Consultation");
    await user.type(within(invoiceDialog).getByLabelText("Amount (INR)"), "500");
    await user.click(within(invoiceDialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requests.some((request) => request.path === "/api/invoices" && request.method === "POST")).toBe(true));

    await user.click(screen.getByRole("button", { name: "Add payment" }));
    const paymentDialog = screen.getByRole("dialog");
    await user.type(within(paymentDialog).getByLabelText(/Amount \(INR\)/), "100");
    await user.selectOptions(within(paymentDialog).getByLabelText("Payment method"), "upi");
    await user.click(within(paymentDialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requests.some((request) => request.path.endsWith("/payments") && request.method === "POST")).toBe(true));

    await user.click(screen.getByRole("button", { name: "Bot simulator" }));
    await user.type(screen.getByLabelText("Simulator mobile number"), "+919876500001");
    await user.type(screen.getByLabelText("Type a message"), "Hi");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Welcome. Reply Hi to begin.")).toBeInTheDocument();
    expect(await screen.findByText("123456")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reset conversation" }));
    await waitFor(() => expect(requests.some((request) => request.path === "/api/simulator/reset" && request.method === "POST")).toBe(true));
    expect(screen.getByText("No messages yet")).toBeInTheDocument();
  });

  it("shows loading, empty, retry, and login error states", async () => {
    failDashboard = true;
    const user = await signIn();
    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable.");
    failDashboard = false;
    emptyDashboard = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No appointments scheduled today")).toBeInTheDocument();
  });

  it("reports invalid login attempts and supports the mobile navigation drawer", async () => {
    failLogin = true;
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByLabelText("Work email"), "alex@example.test");
    await user.type(screen.getByLabelText("Password"), "incorrect-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");

    failLogin = false;
    await user.type(screen.getByLabelText("Work email"), "alex@example.test");
    await user.type(screen.getByLabelText("Password"), "correct-horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Today" });
    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("button", { name: "Close navigation", expanded: true })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Requests" }));
    expect(await screen.findByRole("heading", { name: "Awaiting a call" })).toBeInTheDocument();
  });
});
