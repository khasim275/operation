import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PatientOpFilePage, PatientRegistrationPage } from "./PatientCardPages";

const notify = vi.fn();

function response(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

function registrationResult() {
  return {
    patient: {
      id: "patient-1",
      op_id: "OP-000123-0",
      full_name: "Aarav Sample",
      date_of_birth: null,
      gender: null,
      phone: "+919876500001",
    },
    card: {
      id: "card-1",
      version: 1,
      status: "active",
      issued_at: "2026-01-02T00:00:00Z",
      pdf_url: "/api/cards/OP-000123-0.pdf",
      png_url: "/api/cards/OP-000123-0.png",
    },
  };
}

function patientFileResult() {
  const empty: never[] = [];
  return {
    patient: {
      id: "patient-1", op_id: "OP-000123-0", patient_number: "PT-000123",
      full_name: "Aarav Sample", email: null, date_of_birth: null, gender: null,
      occupation: null, preferred_language: "en", status: "active", created_at: "2026-01-02T00:00:00Z",
      merged_into: null, phone: "+919876500001",
      address: { line1: null, line2: null, city: null, state: null, postal_code: null },
    },
    medical_profile: {
      blood_group: "", chronic_conditions: "", current_medications: "", past_surgeries: "",
      pregnancy_status: "not_recorded", habits: "", family_history: "", notes: "",
      emergency_contact_name: "", emergency_contact_phone: "", emergency_contact_relationship: "",
    },
    phones: empty, allergies: empty, conditions: empty, medicines: empty, surgeries: empty,
    habits: empty, family_history: empty, medical_notes: empty, relationships: empty, consents: empty,
    messages: empty, calls: empty, tooth_chart: empty, dental_procedures: empty, gum_assessments: empty,
    orthodontic_status: empty, visits: empty, appointments: empty, appointment_events: empty,
    followups: empty, reminders: empty, prescriptions: empty, documents: empty,
    payments: null, invoices: null, payment_details: null, payments_permitted: false,
    whatsapp_card_consent: false, cards: empty, notice: null,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  notify.mockClear();
});

describe("patient card registration", () => {
  it("registers once with an idempotency key and shows the card preview and actions", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(registrationResult(), 201))
      .mockResolvedValueOnce(response({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PatientRegistrationPage openFile={vi.fn()} notify={notify} />);

    await user.type(screen.getByLabelText("Full name"), "Aarav Sample");
    await user.type(screen.getByLabelText("Mobile number"), "+919876500001");
    await user.click(screen.getByLabelText(/Patient consent for processing/));
    await user.click(screen.getByLabelText(/Patient separately consents/));
    await user.click(screen.getByRole("button", { name: "Save registration" }));

    expect(await screen.findByRole("heading", { name: "Patient card ready" })).toBeInTheDocument();
    expect(screen.getByText("OP-000123-0")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Front and back of the patient card/ })).toHaveAttribute("src", "/api/cards/OP-000123-0.png");
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute("href", "/api/cards/OP-000123-0.pdf");
    const registration = fetchMock.mock.calls[0];
    expect(new Headers(registration[1]?.headers).get("Idempotency-Key")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Send on WhatsApp" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/patients/patient-1/card/send-whatsapp");
  });

  it("requires staff to resolve a possible duplicate before registration", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response({
        detail: {
          code: "possible_duplicate",
          message: "Possible duplicate.",
          matches: [{ id: "existing-1", op_id: "OP-000010-8", patient_number: "PT-000010", full_name: "Aarav Sample", date_of_birth: "1990-05-01", phone: "+919876500001" }],
        },
      }, 409))
      .mockResolvedValueOnce(response(registrationResult(), 201));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PatientRegistrationPage openFile={vi.fn()} notify={notify} />);

    await user.type(screen.getByLabelText("Full name"), "Aarav Sample");
    await user.type(screen.getByLabelText("Mobile number"), "+919876500001");
    fireEvent.change(screen.getByLabelText("Date of birth"), { target: { value: "1990-05-01" } });
    await user.click(screen.getByLabelText(/Patient consent for processing/));
    await user.click(screen.getByRole("button", { name: "Save registration" }));

    expect(await screen.findByRole("heading", { name: "Check before continuing" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "This is a different person" }));
    expect(await screen.findByRole("heading", { name: "Patient card ready" })).toBeInTheDocument();
    const first = JSON.parse(String(fetchMock.mock.calls[1][1]?.body)) as { confirm_different_person: boolean };
    const second = JSON.parse(String(fetchMock.mock.calls[2][1]?.body)) as { confirm_different_person: boolean };
    expect(first.confirm_different_person).toBe(false);
    expect(second.confirm_different_person).toBe(true);
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get("Idempotency-Key"))
      .toBe(new Headers(fetchMock.mock.calls[2][1]?.headers).get("Idempotency-Key"));
  });
});

describe("patient OP file", () => {
  it("rejects a mistyped check digit without making a patient-data request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<PatientOpFilePage opId="OP-000123-7" roles={["telecaller"]} notify={notify} />);
    expect(await screen.findByText("This ID is not valid, please check the last digit")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("allows dentists to save validated medical profile details", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(patientFileResult()))
      .mockResolvedValueOnce(response({ ok: true, message: "Medical profile updated." }))
      .mockResolvedValueOnce(response(patientFileResult()));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<PatientOpFilePage opId="OP-000123-0" roles={["dentist"]} notify={notify} />);

    await user.click(await screen.findByRole("button", { name: "Health" }));
    await user.type(screen.getByLabelText("Chronic conditions"), "Asthma");
    await user.click(screen.getByRole("button", { name: "Save health details" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/patients/patient-1/medical-profile");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({ chronic_conditions: "Asthma" });
    expect(notify).toHaveBeenCalledWith("Health details updated.");
  });
});
