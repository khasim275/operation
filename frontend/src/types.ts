export type User = {
  id: string;
  full_name: string;
  email: string;
  organisation_id: string;
  branch_id: string | null;
  roles: string[];
};

export type TicketSummary = {
  id: string;
  ticket_number: string;
  op_id?: string;
  patient_id?: string;
  patient_name?: string;
  phone: string | null;
  reason: string;
  preferred_date: string | null;
  preferred_time_of_day: string;
  source: string;
  status: string;
  created_at: string;
  start_at?: string | null;
  dentist?: string | null;
};

export type Dashboard = {
  today: string;
  metrics: {
    appointments_today: number;
    requests_awaiting_call: number;
    callbacks_today: number;
  };
  appointments: Array<{
    id: string;
    ticket_number: string;
    op_id?: string;
    start_at: string;
    reason: string;
    status: string;
    patient_name: string;
    phone: string | null;
    dentist: string | null;
  }>;
  requests: Array<{
    id: string;
    ticket_number: string;
    op_id?: string;
    created_at: string;
    reason: string;
    preferred_date: string | null;
    preferred_time_of_day: string;
    source: string;
    patient_name: string;
    phone: string | null;
    status: string;
  }>;
  followups: Array<{
    appointment_id: string;
    patient_name: string;
    ticket_number: string;
    attempt_number: number;
    scheduled_at: string;
    reason: string;
    phone: string | null;
  }>;
};

export type PatientSummary = {
  id: string;
  patient_number: string;
  full_name: string;
  phone?: string | null;
  date_of_birth?: string | null;
};

export type PatientDetail = {
  patient: PatientSummary & {
    email: string | null;
    gender: string | null;
    preferred_language: string | null;
    occupation: string | null;
    status: string;
    created_at: string;
  };
  phones: Array<{ phone: string; label: string; is_primary: boolean; verified_at: string | null }>;
  allergies: Array<{ allergen: string; reaction: string | null; severity: string; status: string; recorded_at: string }>;
  appointments: Array<{
    id: string;
    ticket_number: string;
    reason: string;
    preferred_date: string | null;
    preferred_time_of_day: string;
    start_at: string | null;
    end_at: string | null;
    status: string;
    source: string;
    created_at: string;
  }>;
  visits: Array<{
    id: string;
    complaint: string;
    treatment_plan: string;
    examination_findings: string;
    recorded_at: string;
  }>;
  calls: Array<{
    appointment_id: string;
    started_at: string;
    outcome: string;
    notes: string;
    caller: string;
    next_follow_up_at?: string | null;
  }>;
  documents: Array<{
    id: string;
    original_name: string;
    mime_type: string;
    size_bytes: number;
    document_type: string;
    uploaded_at: string;
  }>;
  invoices: Array<{
    id: string;
    status: string;
    currency: string;
    created_at: string;
    total_minor: number;
    paid_minor: number;
    outstanding_minor: number;
  }>;
  medical_notes: Array<{ note: string; recorded_at: string; author: string }>;
  medicines: Array<{
    medicine_name: string;
    dose: string | null;
    frequency: string | null;
    start_date: string | null;
    end_date: string | null;
    status: string;
    recorded_at: string;
  }>;
  prescriptions: Array<{
    id: string;
    created_at: string;
    status: string;
    dentist: string;
    items: Array<{ medicine_name: string; dose: string; frequency: string; duration: string }>;
  }>;
};

export type TicketDetail = {
  ticket: TicketSummary & {
    branch_id: string;
    status_note: string;
    end_at: string | null;
    patient_name: string;
  };
  allergies: Array<{ allergen: string; reaction: string | null; severity: string }>;
  calls: Array<{
    started_at: string;
    duration_seconds: number | null;
    outcome: string;
    notes: string;
    next_follow_up_at: string | null;
    caller: string;
  }>;
  visits: PatientDetail["visits"];
  events: Array<{
    event_type: string;
    occurred_at: string;
    old_status: string | null;
    new_status: string | null;
  }>;
  messages: Array<{
    direction: string;
    message: string;
    occurred_at: string;
    delivery_status: string;
  }>;
};

export type Dentist = { id: string; full_name: string };
export type Room = { id: string; name: string };

export type Invoice = {
  id: string;
  patient_name: string;
  created_at: string;
  status: string;
  currency: string;
  total_minor: number;
  paid_minor: number;
  outstanding_minor: number;
};

