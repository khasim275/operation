# operation — Dental Hospital CRM

A local-first dental clinic CRM built with FastAPI, PostgreSQL 16+, psycopg 3, React 18, Vite, TypeScript, and Tailwind CSS. It includes a staff workspace, appointment-request bot, OTP verification, call handling, patient history, billing, file uploads, PostgreSQL live updates, and a local bot simulator.

This project is a dental hospital website and staff CRM with a PostgreSQL source of truth. It is not a certified medical-record system or legal opinion. Have a qualified security and India privacy/compliance professional review it before storing real patient data.

Deployment configuration and GitHub-to-Render instructions are in [DEPLOYMENT.md](./DEPLOYMENT.md). The included Render free service is for preview/testing only because uploaded patient documents are stored on ephemeral local disk.

## What is included

- Staff sign-in with Argon2 password hashes, HTTP-only sessions, CSRF protection, role checks, and login throttling.
- WhatsApp Business Cloud API webhook verification, HMAC signature checks, message idempotency, approved-template OTP delivery, and a provider-neutral SMS fallback adapter.
- A bot simulator that runs the same appointment flow without a real WhatsApp number. Its random OTP is shown only in demo mode; OTP codes are stored as keyed hashes and redacted from message logs.
- Patient lookup by name, email, and phone; patient details, prominent allergies, visit and call history, prescriptions, files, consent records, and privacy-request tracking.
- Appointment requests, call outcomes, database-enforced dentist/chair overlap prevention, callback reminders, WhatsApp confirmation templates, and 24-hour/2-hour reminder jobs.
- Public booking at `/book`, with server-generated daily OPIDs, serialized per-slot tokens, room assignment, retry idempotency, OPID lookup, a 3D flip card, and a staff queue record.
- Clinical notes, current medicines, allergies, visits, prescriptions, document upload/download, invoices, payments, and a live CRM event stream.
- Monthly PostgreSQL partitions for audit and communication logs, immutable audit/history triggers, soft-delete fields, synthetic sample patients, and backup/restore-test scripts.
- Interactive API documentation at `/docs` and OpenAPI JSON at `/openapi.json`.

## Architecture

```text
Patient WhatsApp ── Meta Cloud API ── /webhook/whatsapp
       │                                  │
       └── OTP template / SMS adapter     ├── conversation + OTP state
                                          └── appointment request
                                                     │
Public site ── `/book` React ── FastAPI ── psycopg pool ── PostgreSQL 16+
    │              │                          ├── audit triggers
    │              ├── private file storage  └── LISTEN/NOTIFY ── SSE
    └── plain HTML/CSS/JavaScript
```

Database tables are grouped around:

- **Organisation:** `organisations`, `branches`, `rooms`, `users`, `roles`, `user_roles`, `working_hours`, `holidays`.
- **Patient/medical/dental:** `patients`, `patient_phones`, `patient_addresses`, `patient_relationships`, `conditions`, `patient_conditions`, `allergies`, `patient_allergies`, `medicines`, `patient_medicines`, `patient_surgeries`, `patient_habits`, `family_history`, `medical_notes`, `tooth_conditions`, `dental_procedures`, `gum_assessments`, `orthodontic_status`.
- **Appointments/calls/visits:** `appointments`, `appointment_events`, `call_attempts`, `call_followups`, `visits`, `diagnoses`, `performed_procedures`, `prescriptions`, `prescription_items`.
- **Documents/finance:** `documents`, `treatment_catalog`, `price_lists`, `price_list_items`, `estimates`, `estimate_items`, `invoices`, `invoice_items`, `payments`, `insurance_policies`, `insurance_claims`, `invoice_balances`.
- **Communications/privacy/audit:** `whatsapp_templates`, `webhook_inbox`, `conversations`, `otp_challenges`, `otp_events`, `reminder_jobs`, `message_outbox`, partitioned `communication_log`, `consents`, `patient_privacy_requests`, partitioned `audit_log`.

## Permanent OP IDs and patient cards

The immutable `migrations/001_patient_cards.sql` migration adds database-generated `OP-NNNNNN-C` identifiers, where `C` is the Luhn check digit, plus patient-card versions, registration idempotency keys, medical-profile records, and a patient-access log. `bootstrap.py` applies the baseline and numbered migrations transactionally, records SHA-256 checksums in `schema_migrations`, and rejects edits to an already-applied migration; add a new numbered migration for later schema changes. The prompt's illustrative `OP-000123-7` does not pass the standard Luhn algorithm; this implementation correctly accepts `OP-000123-0` for sequence value `000123`.

Reception staff can register walk-in patients from **Register patient**. Registration, patient medical profile, consent records, card record, and OP ID are committed together; card PDF/PNG rendering follows and can be retried if rendering is interrupted. The patient file opens from the global search or the camera/USB scanner. Search checks OP ID validity before database access, ranks an exact OP ID first, and supports family members sharing one mobile number. Card sharing requires a recorded, unwithdrawn WhatsApp-card consent. Payments are returned only to admins and accountants; prescription creation is restricted to dentists.

Set `PUBLIC_BASE_URL` to the clinic's HTTPS domain, and set `CLINIC_ADDRESS`, `CLINIC_PHONE`, and `CLINIC_WHATSAPP` before issuing real cards. The QR encodes only `https://<PUBLIC_BASE_URL>/p/<OP-ID>`. Store generated files under the private `STORAGE_DIR`; do not serve that directory as static content. The simulator can optionally show a consented card URL. On startup, `bootstrap.py` applies only pending migrations.

## Public appointment pass

The public website is served at `/`; its appointment buttons lead to `/book`. Booking sends the name, mobile, date, and time to `POST /api/public/appointments/book` with an idempotency key. PostgreSQL owns patient linking, the `OP-YYMMDD-0001` counter, per-slot token allocation, room assignment, the staff queue entry, and the persisted card. `GET /api/public/appointments/{opid}` reopens the saved pass. The existing CRM already owns its `patients` and `appointments` tables, so public passes live in `appointment_cards` and link to the CRM records instead of replacing the existing clinical schema.

The card is displayed only after the API confirms the committed record. Refresh uses local storage as a cache and verifies the OPID against PostgreSQL. Card finish and sound preferences save locally; admins can update the hospital name and default card finish from Staff → Settings, persisted in PostgreSQL.

## Windows setup

### 1. Install PostgreSQL and Python

Install PostgreSQL 16 or newer, Python 3.11 or newer, and Node.js 20 or newer (which includes npm). During PostgreSQL installation, keep the service running and remember the database administrator password. Add PostgreSQL's `bin` folder to `PATH` so `psql`, `pg_dump`, and `pg_restore` are available.

Confirm both tools in a new PowerShell window:

```powershell
python --version
psql --version
```

If Windows opens the Microsoft Store when you type `python`, turn off the Python app execution aliases in **Settings → Apps → Advanced app settings → App execution aliases**, or use the full Python executable path.

### 2. Create the database

Open SQL Shell (`psql`) or pgAdmin as a PostgreSQL administrator and run:

```sql
CREATE USER dentalcrm WITH PASSWORD 'replace-with-a-unique-strong-password';
CREATE DATABASE dental_crm OWNER dentalcrm;
```

If your PostgreSQL policy requires a superuser to install extensions, connect to `dental_crm` as the administrator and run:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

Use a unique local password; do not reuse a real clinic or personal password.

### 3. Configure the app

From the project directory:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set `DATABASE_URL`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD`. Generate two separate random values of at least 32 characters for `APP_SECRET_KEY` and `OTP_PEPPER`. Do not commit `.env` or send it to anyone. Leave WhatsApp credentials blank and `DEMO_MODE=true` while testing locally.

Example connection-string shape:

```text
DATABASE_URL=postgresql://dentalcrm:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:5432/dental_crm
```

URL-encode special characters in the database password. Set `CLINIC_NAME`, `CLINIC_CITY`, and `BRANCH_NAME` to the clinic's real display values only when ready.

### 4. Start the server

Run one command in PowerShell:

```powershell
.\run.ps1
```

If script execution is blocked, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

The script creates a project-local `.venv`, installs the version-bounded dependencies, applies pending migrations, and starts the public site at <http://127.0.0.1:8000>. Public booking is at `/book`; staff sign-in is at `/staff`. It does not create sample patients by default. For a local demo database only, run `.\.venv\Scripts\python.exe bootstrap.py --seed-demo` after configuration. `APP_HOST` and `APP_PORT` in `.env` control the bind address and port; for a cloud deployment, bind behind a TLS-terminating reverse proxy and use `APP_ENV=production`, `COOKIE_SECURE=true`, and `DEMO_MODE=false`. Stop it with **Ctrl+C**. Do not expose the development server directly to the public internet.

Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` before the first database initialization. The bootstrap creates that administrator only when the email is not already in use; it does not overwrite an existing account or reset its password.

## Test the booking flow

1. Sign in with the administrator account.
2. Open **Bot simulator**.
3. Send `Hello`, then `Hi`.
4. Reply `1` for an appointment, choose a reason, enter a future date as `YYYY-MM-DD`, choose a time of day, and enter a mobile number with its country code.
5. Enter the random code shown in the simulator's **Demo verification code** panel. This value is not persisted in the database.
6. For a first-time phone number, enter a full name. The bot returns a `TK-...` ticket reference.
7. Open **Today** or **Requests**. The ticket appears in the queue; database changes also stream to open CRM screens without refreshing.
8. Use **Call**, choose an outcome, and save it. A confirmation requires an active dentist plus a start and end time. The database refuses overlapping bookings for the same dentist or chair.
9. In **Patient search**, search the test number. Dentist/admin users can add notes, medicines, and allergies; allergies display above the patient history.

The simulator stores synthetic messages and requests so you can test the database end to end without contacting Meta or a patient. The sample records use reserved-looking fake numbers and must never be treated as real patients.

## API outline

| Area | Routes |
|---|---|
| Auth/users | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`, `GET/POST /api/users`, `PATCH /api/users/{id}` |
| Dashboard/live | `GET /api/dashboard`, `GET /api/events` (SSE), `GET /health` |
| Patients | `GET /api/patients/search?q=...`, `GET/PATCH /api/patients/{id}`, `POST /api/patients/{id}/medical-notes`, `/medicines`, `/allergies`, `/consents`, `/privacy-requests`, `/messages`, `/documents` |
| OP IDs/patient cards | `POST /api/patients/register` (requires `Idempotency-Key`), `GET /api/patients/lookup?q=...`, `GET /api/patients/by-op/{op_id}`, `PATCH /api/patients/{id}/medical-profile`, `GET /api/cards/{op_id}.pdf`, `GET /api/cards/{op_id}.png`, `POST /api/patients/{id}/card/reissue`, `/card/consent`, `/card/send-whatsapp`, `/merge` |
| Public appointment cards | `POST /api/public/appointments/book` (requires `Idempotency-Key`), `GET /api/public/appointments/{opid}`, `GET /api/public/settings`, admin-only `PATCH /api/public/settings` |
| Appointments/calls | `GET/POST /api/appointments`, `GET/PATCH /api/appointments/{id}`, `POST /api/appointments/{id}/call-outcome`, `GET /api/dentists`, `GET /api/rooms` |
| Clinical | `POST /api/visits`, `POST /api/prescriptions` |
| Documents | `POST /api/patients/{id}/documents`, `GET /api/documents/{id}` |
| Billing | `GET/POST /api/invoices`, `GET/POST /api/invoices/{id}/payments` |
| WhatsApp | `GET/POST /webhook/whatsapp`; development simulator at `POST /api/simulator/messages` and `/api/simulator/reset` |
| Privacy | `GET /api/privacy-requests`, `PATCH /api/privacy-requests/{id}` for admins; consent withdrawal endpoint under the patient |

FastAPI validates request bodies and documents the current endpoint contracts at `/docs`. The database is the source of truth; triggers write audit changes and publish notifications. Audit records store changed field names, not old/new PHI values. Read access is recorded explicitly for patient and ticket details because database triggers cannot observe `SELECT` queries.

## WhatsApp Business Cloud API

The simulator is local-only. To connect a real number:

1. Set up a Meta WhatsApp Business Cloud API app and an HTTPS-accessible deployment. Never publish the local development server as a webhook.
2. Configure `META_VERIFY_TOKEN`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, and `META_PHONE_NUMBER_ID`.
3. Create and get approval for authentication OTP, appointment confirmation, 24-hour reminder, and 2-hour reminder templates. Set the corresponding `META_TEMPLATE_*` names and language in `.env`; configure each template's parameters to match the code.
4. Set Meta's callback URL to `https://your-domain/webhook/whatsapp`, subscribe to message/status webhook fields, and use the exact verify token.
5. For SMS fallback, configure `SMS_PROVIDER`, `SMS_API_URL`, and `SMS_API_TOKEN`. The adapter sends a JSON body with `to` and `message` and a bearer token; adapt `send_sms_otp` in `messaging.py` to the selected gateway's contract before enabling it.
6. Set `DEMO_MODE=false`, `COOKIE_SECURE=true`, and `APP_ENV=production` behind HTTPS before processing real traffic.

Bot replies use free-form messages only within the WhatsApp 24-hour customer-service window. Outbox processing expires free-form replies outside that window. OTP, confirmations, and reminders use configured approved templates. Provider delivery failures are logged and retried up to three times for queued messages. OTP is delivered immediately through the template adapter with SMS fallback.

## Backups

Install PostgreSQL command-line tools and keep `pg_dump` and `pg_restore` on `PATH`.

Create a timestamped backup directory containing a custom-format database dump and a ZIP of uploaded files:

```powershell
.\backup.ps1
```

Test a backup by restoring it to a newly named temporary database and checking that the schema and patient table can be read:

```powershell
.\restore-test.ps1 -BackupPath .\backups\dental-crm-YYYYMMDDTHHMMSSZ
```

The restore-test database is dropped after validation. Store backups encrypted with restricted access and test restore procedures regularly; backups contain sensitive health and financial information.

## Security and operational notes

- Replace all example secrets. Use a secrets manager and TLS on a cloud deployment.
- Configure a least-privilege database account. PostgreSQL extensions may need an administrator to install once.
- Clinical history and audit/communication rows are append-only; corrections are added rather than overwritten. Patients are soft-deleted. A privacy erasure request is queued for admin review and soft-deletes the patient; it does not automatically erase clinical/financial records that may have retention obligations.
- Uploaded files are restricted by configured size and PDF/JPEG/PNG signatures, stored outside the static web root, and referenced by SHA-256 hash in PostgreSQL.
- Restrict `private_uploads`, `.env`, backups, PostgreSQL data, and server logs to authorised operators. Use encrypted disks/backups and a documented retention policy.
- Configure HTTPS, monitoring, log rotation, backup retention, database access control, and incident-response procedures before production.
- Database triggers audit inserts/updates/deletes. They do not capture reads; authenticated patient/ticket view endpoints call the audit function.
- `working_hours` and `holidays` are stored for future schedule-management workflows; the current booking guard enforces dentist/chair overlap but does not yet reject appointments outside configured working hours or holidays.

## Validation

Run the backend and staff CRM checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
```

The backend tests cover phone normalization, OTP hashing, Meta signature validation, appointment input validation, uploaded-file signatures, OP ID/Luhn validation, consent validation, medical-profile validation, deterministic card rendering, database migrations, idempotent appointment retries, settings persistence, and a 100-booking race. React tests exercise staff actions and OP ID registration/file validation using test-only API responses. Database-backed checks require a disposable PostgreSQL 16+ instance; do not run test or load-test bookings against clinic production data. The 100/1,000-user HTTP load runner is `scripts/load_public_appointments.py`; repository selection and go-live steps are documented in [SDLC.md](./SDLC.md).
# GitHub and Render deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the single-service Docker deployment, environment-variable setup, Render Blueprint, and free-tier limitations.

