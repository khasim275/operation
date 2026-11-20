import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PublicBooking from "./PublicBooking";

const appointment = {
  id: "card-1",
  opid: "OP-261204-0001",
  patient_id: "patient-1",
  appointment_id: "appointment-1",
  patient_name: "Asha Rao",
  patient_mobile: "9876543210",
  patient_email: "asha@example.com",
  appointment_date: "2026-12-04",
  time_slot: "10:00",
  token_no: 1,
  room: "Room 1",
  status: "CONFIRMED",
  created_at: "2026-11-20T10:00:00+05:30",
};

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  }));
}

describe("public booking experience", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, "", "/book");
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "unique-booking-key-123456") });
    vi.stubGlobal("fetch", vi.fn().mockImplementation((path: string) => {
      if (String(path).includes("/settings")) {
        return response({ hospital_name: "Gangayya Dental Hospital", card_finish: "porcelain", time_slots: ["10:00"] });
      }
      return response(appointment);
    }));
  });

  it("books through the API, stores the persisted card, and flips on tap", async () => {
    const user = userEvent.setup();
    render(<PublicBooking />);
    await screen.findByRole("link", { name: "Gangayya Dental Hospital booking home" });
    await user.type(screen.getByLabelText("Your name"), "Asha Rao");
    await user.type(screen.getByLabelText("Mobile number"), "9876543210");
    await user.type(screen.getByLabelText("Email address"), "asha@example.com");
    await user.type(screen.getByLabelText("What would you like help with?"), "Check-up");
    await user.type(screen.getByLabelText("Date"), "2026-12-04");
    await user.selectOptions(screen.getByLabelText("Time"), "10:00");
    await user.click(screen.getByRole("button", { name: /book appointment/i }));

    expect(await screen.findByText("OP-261204-0001")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem("dental-hospital.latest-appointment.v1") ?? "{}").opid).toBe(appointment.opid);
    const post = vi.mocked(fetch).mock.calls.find(([path, init]) =>
      String(path) === "/api/public/appointments/book" && init?.method === "POST",
    );
    expect(post).toBeDefined();
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ name: "Asha Rao", phone: "9876543210", email: "asha@example.com", problem_summary: "Check-up", time_slot: "10:00" });
    expect(new Headers(post?.[1]?.headers).get("Idempotency-Key")).toBe("unique-booking-key-123456");

    const card = screen.getByRole("button", { name: /appointment card front/i });
    await user.click(card);
    expect(await screen.findByText("QUEUE TOKEN")).toBeInTheDocument();
    expect(screen.getByText("Room 1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /appointment card back/i }));
    expect(screen.getByText("YOUR OP ID")).toBeInTheDocument();
  });

  it("keeps form values on server failure and retries with the same idempotency key", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation((path: RequestInfo | URL, init?: RequestInit) => {
      if (String(path).includes("/settings")) {
        return response({ hospital_name: "Gangayya Dental Hospital", card_finish: "porcelain", time_slots: ["10:00"] });
      }
      if (init?.method === "POST") {
        return fetchMock.mock.calls.filter(([calledPath, calledInit]) =>
          String(calledPath) === "/api/public/appointments/book" && calledInit?.method === "POST",
        ).length === 1
          ? response({ detail: "Temporary database issue. Retry." }, 503)
          : response(appointment, 201);
      }
      return response(appointment);
    });
    render(<PublicBooking />);
    await user.type(await screen.findByLabelText("Your name"), "Asha Rao");
    await user.type(screen.getByLabelText("Mobile number"), "9876543210");
    await user.type(screen.getByLabelText("Email address"), "asha@example.com");
    await user.type(screen.getByLabelText("What would you like help with?"), "Check-up");
    await user.type(screen.getByLabelText("Date"), "2026-12-04");
    await user.selectOptions(screen.getByLabelText("Time"), "10:00");
    await user.click(screen.getByRole("button", { name: /book appointment/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Temporary database issue");
    expect(screen.getByLabelText("Your name")).toHaveValue("Asha Rao");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(appointment.opid)).toBeInTheDocument();
    const bookingCalls = fetchMock.mock.calls.filter(([path, init]) => String(path) === "/api/public/appointments/book" && init?.method === "POST");
    expect(bookingCalls).toHaveLength(2);
    expect(new Headers(bookingCalls[0][1]?.headers).get("Idempotency-Key")).toBe(new Headers(bookingCalls[1][1]?.headers).get("Idempotency-Key"));
  });

  it("persists sound/card preferences and looks up a card by OPID", async () => {
    const user = userEvent.setup();
    render(<PublicBooking />);
    await screen.findByRole("link", { name: "Gangayya Dental Hospital booking home" });
    await user.click(screen.getByRole("button", { name: "Open settings" }));
    await user.selectOptions(screen.getByLabelText("Card finish"), "sage");
    await user.click(screen.getByRole("button", { name: "Mute sound effects" }));
    expect(JSON.parse(localStorage.getItem("dental-hospital.public-preferences.v1") ?? "{}")).toMatchObject({ cardFinish: "sage", muted: true });
    await user.click(screen.getByRole("button", { name: /done/i }));

    await user.click(screen.getByRole("button", { name: "Find my card" }));
    await user.type(screen.getByLabelText("OP ID"), appointment.opid);
    await user.click(screen.getByRole("button", { name: /open my card/i }));
    await waitFor(() => expect(screen.getByText(appointment.opid)).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(`/api/public/appointments/${appointment.opid}`, expect.any(Object));
    expect(localStorage.getItem("dental-hospital.latest-appointment.v1")).toContain(appointment.opid);
  });
});
