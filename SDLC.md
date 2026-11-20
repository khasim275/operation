# Dental Hospital SDLC & go-live plan

## Current architecture

- `/` is the public Gangayya Dental Hospital landing page. Its appointment CTAs lead to `/book`.
- `/book` is a responsive React booking flow. A successful API response reveals the generated OP card; the page verifies restored cards against PostgreSQL and supports lookup at `/book/OP-YYMMDD-0001`.
- `/staff` is the protected staff CRM. `/api/public/appointments/book` calls the database transaction; `/api/public/appointments/{opid}` reopens the persisted pass.
- `/secure` and `/staff-portal` are the isolated 15-minute OTP workspace for password renewal and staff medicine reference. `/api/health` checks PostgreSQL reachability.
- Portal 1 visit history requires a one-time code to the registered mobile. Its API projection contains only the public patient ID, OP ID, IST appointment time, booking status, and safe arrival instructions. It never calls or returns the staff history JSON.
- Portal 2 history is authenticated and organisation/branch scoped. Dentist/admin roles can view and update clinical history and files; billing changes require accountant/admin roles. Patient and public endpoints never expose those records.
- Unhandled backend exceptions are logged with a request ID, route, and exception class in `system_error_logs`; patient content and credentials are excluded. Administrators can inspect sanitized entries through `/api/admin/system-errors`.
- PostgreSQL remains authoritative. `book_appointment(...)` atomically links/creates a patient, creates a staff queue appointment, allocates the daily OPID and slot token, assigns a room, and creates the pass record. An idempotency key prevents retries from creating another booking.

The existing CRM already has a rich `patients` table and an operational `appointments` table with its own statuses, dentist/room constraints, and history. Replacing those tables with the smaller proposed shapes would break existing patient and staff workflows. Public appointment passes therefore live in `appointment_cards` and link to a regular `appointments` row; patient identity and phone remain in the CRM's `patients` and `patient_phones` tables. `opid_counters` and the required date/slot/token uniqueness remain database-enforced.

## Repository selection by SDLC stage

These repositories are references and optional tools, not runtime dependencies. None is installed by the application or pulled into its production bundle.

| SDLC stage | Repository | Use here |
|---|---|---|
| Requirements & planning | [hallmark](https://github.com/nutlope/hallmark) | Visual quality and anti-template critique for the landing page and booking/card hierarchy. The existing clinic landing page is the design source. |
| Coding & UI audit | [astryx](https://github.com/meta/astryx) | Optional component and design-system pattern reference. The app uses its own React/CSS tokens; no package is required. |
| Coding / notifications | [evolution-api](https://github.com/evolution-foundation/evolution-api) | Optional messaging gateway only if the clinic explicitly chooses it. The current booking path does not send WhatsApp messages or expose new webhook credentials. |
| Running / deployment prep | [floci](https://github.com/floci-io/floci) | AWS emulator only if AWS-backed queues, object storage, or other AWS adapters are introduced. Current runtime uses PostgreSQL and private local storage, so Floci is not started. |
| Testing & scenario planning | [MiroFish](https://github.com/666ghj/MiroFish) | Optional agent-based patient journey scenarios. Deterministic correctness and concurrency are measured by the checked-in load script, not inferred from an agent simulation. |
| Testing / debugging / monitoring | [Zeek](https://github.com/zeek/zeek) | Optional packet/protocol investigation on a controlled staging capture. It is not an application dependency and should only inspect traffic the operator is authorised to capture. |
| Pre-go-live SEO audit | [jev-seo](https://github.com/agricidaniel/jev-seo) | Run against the final HTTPS domain after launch configuration and metadata are verified. No domain was supplied, so a public crawl cannot run yet. |
| Launch artifact | [brag](https://github.com/latent-spaces/brag) | Optional concise launch showcase after the public deployment is independently verified. |
| Isolated experiments | [demon-old](https://github.com/Aerocatia/demon-old) | Keep separate from production; no code from it is needed for this booking flow. |
| Duplicate reference | [MiroFish](https://github.com/666ghj/MiroFish) | Same optional scenario role above; duplicate links do not imply duplicate installs. |

The supplied repository list is not a dependency manifest. The current runtime is the existing FastAPI + psycopg + PostgreSQL backend and React/Vite frontend. In particular, do not add AWS, messaging, packet-analysis, or agent frameworks until an accepted feature requires them.

## Configuration and data flow

The local `.env` is ignored by Git. It contains `DATABASE_URL`, `APP_SECRET_KEY`, `OTP_PEPPER`, `CLINIC_NAME`, `CLINIC_CITY`, `CLINIC_TIMEZONE`, `DB_POOL_MAX`, `STORAGE_DIR`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD`. WhatsApp/SMS settings remain blank until the clinic configures a provider. `APP_SECRET_KEY` and `OTP_PEPPER` must be unique random values; the database administrator secret is not used by the application role.

1. A patient follows a landing-page CTA to `/book`, enters name, mobile, date, and slot, and submits. The browser sends the unchanged payload and an idempotency key; it never calculates an OPID or token.
2. FastAPI validates the payload and queues concurrent booking work behind a bounded async gate, then calls the PostgreSQL function inside one transaction.
3. PostgreSQL serializes the phone link and time-slot token, atomically increments the day counter, writes the patient/phone link, CRM appointment, appointment card, and idempotency record, then returns the committed record.
4. Only after a successful response does the browser store the pass in local storage and show the 3D card. A retry with the same key returns the same saved record. Lookup and refresh fetch the backend record by OPID before replacing local state.
5. Staff users see the linked booking in the CRM requests queue. Admins can update the hospital name and default card finish in Staff → Settings; sound and per-device finish preferences are stored on the device.

## Run locally

```powershell
.\run.ps1
```

Open `http://127.0.0.1:8000/` for the public clinic site, `http://127.0.0.1:8000/book` for booking, or `http://127.0.0.1:8000/staff` for staff sign-in. Local PostgreSQL must be running and `.env` must contain the app role's connection URL. Before public launch, verify the clinic name, hours, address, contact links, consent/privacy copy, administrator account, and HTTPS domain. Clinic phone, WhatsApp, map, and public base URL were not supplied; the public booking feature does not fabricate them.

## Test and load-check

Backend and frontend checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run test
npm.cmd --prefix frontend run build
```

PostgreSQL integration tests require `CRM_TEST_DATABASE_URL` pointing to a disposable database whose name contains `test`. They verify migrations, registration/card persistence, idempotent booking retry, OPID lookup, admin settings persistence, and 100 simultaneous bookings for one slot. Do not point tests or the stress script at clinic production data. The local database has migrations through `008_error_feedback_patient_access` applied; the integration suite still needs a separate disposable database to execute its concurrency cases.

For HTTP load checks, start a second app process against an isolated PostgreSQL test database, then run each scenario:

```powershell
.\.venv\Scripts\python.exe scripts\load_public_appointments.py --base-url http://127.0.0.1:8001 --users 100 --concurrency 100
.\.venv\Scripts\python.exe scripts\load_public_appointments.py --base-url http://127.0.0.1:8001 --users 1000 --concurrency 1000
```

The script creates synthetic records and checks response success, unique OPIDs, unique tokens, and sample OPID lookups. A local PostgreSQL 18 / FastAPI run completed 100/100 bookings with 100 unique OPIDs and tokens in 1.04 seconds (p95 0.912 s), and 1,000/1,000 with 1,000 unique OPIDs and tokens in 7.87 seconds (p95 7.089 s). These are local development measurements, not a production capacity guarantee; repeat on the target database, app-server worker count, and network before making an SLA claim.

## Debugging checklist

- **Book button does nothing:** confirm `/book` loaded, browser console has no exception, required fields are valid, and Network shows `POST /api/public/appointments/book`.
- **Retry/duplicate:** reuse the same idempotency key for an unchanged attempt. A mismatched payload intentionally returns 409; editing values starts a new attempt key.
- **Backend error:** use `/api/health`, inspect the request ID in server logs and sanitized admin error log, verify `DATABASE_URL`, `schema_migrations` includes `008_error_feedback_patient_access`, and check the app role's database grants.
- **Card lookup:** use the returned exact OPID at `/book/OPID`; `GET /api/public/appointments/OPID` must return the saved card. Browser storage is a cache, not the source of truth.
- **Settings:** public Card & sound choices save locally. Hospital name and default card finish require an admin session and save through `PATCH /api/public/settings`.
- **3D/audio:** test mouse, keyboard, touch, and `prefers-reduced-motion`; audio starts only after a user gesture and respects the stored mute setting.
- **Traffic:** use the load script only on disposable data. Check API response codes, Postgres pool wait time, lock waits, request latency, and logs before changing pool size.

## Go-live sequence

1. Confirm clinic-approved hours and slots, room mapping, address, phone/WhatsApp, privacy notice, and a named admin credential with the clinic.
2. Apply immutable numbered migrations using the application role; review checksums and a backup/restore before production traffic.
3. Deploy FastAPI behind HTTPS with a production process manager, non-public PostgreSQL access, private uploads, secure cookies, unique secrets, and an explicit connection-pool budget. Set production `APP_ENV`, `COOKIE_SECURE=true`, and `DEMO_MODE=false`.
4. Run API, UI, accessibility/reduced-motion, backup restore, SEO, and 100/1,000 concurrency checks against staging. Validate staff queue and public OPID lookup with synthetic records; clear them before launch.
5. Link the verified domain, monitor 4xx/5xx rates, database connections/locks, backup status, and audit logs. Run Jev SEO and prepare a Brag showcase only after the site is reachable and verified.

The current work is running locally, not deployed to a public internet domain. A verified domain, clinic contacts, named administrator credentials, and deployment target are still required for a genuine public go-live.
