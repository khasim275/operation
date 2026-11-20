# GitHub and Render deployment

## What this repository deploys

The repository is a single-origin application: FastAPI serves the public patient portal, the built Vite/React staff and OTP portals, and the `/api/*` endpoints. Deploy it as one Docker web service on Render. The current frontend uses same-origin relative API requests, so a separate Vercel deployment and `vercel.json` rewrite are not needed. A Vercel split would require an externally configured API rewrite and credentialed CORS; keep the current single service until there is a concrete need to split it.

## Hosting limits

The included `render.yaml` uses Render's free plan for preview/testing, an external Neon or Supabase PostgreSQL database, and temporary `/tmp` storage. Do not put real patient records or uploaded X-rays/documents on this free deployment: its local files are ephemeral. Render also documents that free web services can restart, cannot attach persistent disks, and free Render Postgres expires after 30 days. Free services cannot connect to SMTP ports 25, 465, or 587. For live patient use, move the web service to a paid plan with persistent storage or implement a private object-storage adapter, use a backed-up paid database, and use an email provider with a supported HTTPS API or a paid service that permits the selected transport. See Render's [free service limits](https://render.com/docs/free) and [persistent disk docs](https://render.com/docs/disks).

The code currently stores private uploads on local disk. A Render disk is single-instance storage and only available on paid services; it is not multi-instance object storage. Do not scale this app horizontally with local uploads until a shared private object storage adapter is implemented.

## Deploy from GitHub

1. Create a PostgreSQL database on Neon or Supabase. Copy its pooled or direct connection string as supported by the provider; URL-encode special characters in the password and require TLS (`sslmode=require`). Use a database role with permission to create the schema, extensions (`pg_trgm`, `btree_gist`, `pgcrypto`), tables, triggers, and functions. Test migrations against a disposable database first.
2. Review the repository for secrets and local data. `.env`, `.venv`, `private_uploads`, and the unrelated `floci` checkout are excluded by `.gitignore`. Never add production credentials or patient uploads to Git.
3. From PowerShell at the project root, create and push a GitHub repository (replace the URL with the empty repository you created):

   ```powershell
   git init
   git add .
   git status --short
   # Confirm .env, private_uploads, .venv, and floci are absent from this list.
   git commit -m "Prepare Dental CRM portals for deployment"
   git branch -M main
   git remote add origin https://github.com/YOUR_GITHUB_ACCOUNT/YOUR_REPOSITORY.git
   git push -u origin main
   ```

4. In Render, choose **New > Blueprint**, connect that GitHub repository, and select `render.yaml`. Keep the service name `dental-crm-portals` if using the included URL values. Render prompts for variables marked `sync: false`.
5. Set the actual clinic values and credentials in Render. Required before production startup: `DATABASE_URL`, `CLINIC_NAME`, `PUBLIC_BASE_URL` matching the actual HTTPS service/custom domain, `ADMIN_EMAIL`, `ADMIN_MOBILE_10_DIGIT`, `ADMIN_PASSWORD` (12+ characters), Meta WhatsApp credentials and approved confirmation template, and a working SMS fallback (`SMS_PROVIDER=twilio` with Twilio values or `SMS_PROVIDER=api` with URL/token). Keep `DEMO_MODE=false`, `ALLOW_TEST_OTP=false`, and `COOKIE_SECURE=true`. Do not use test OTP display in production.
6. If the Render-generated URL differs from `https://dental-crm-portals.onrender.com`, update `PUBLIC_BASE_URL`, `PATIENT_PORTAL_URL`, `STAFF_PORTAL_URL`, and `OTP_PORTAL_URL` to the actual HTTPS URLs. If you add a separate frontend origin later, set `CORS_ALLOWED_ORIGINS` in Render to a JSON array such as `["https://portal.example.org"]`; do not use `*` with cookies.
7. Set required email OTP delivery. `EMAIL_PROVIDER=disabled` means email OTP is unavailable. `EMAIL_PROVIDER=smtp` uses `SMTP_*`; verify that the host permits the configured port. Render free web services block SMTP ports 25/465/587, so SMTP on the free plan will not work.
8. Wait for the first deployment. The container runs `python bootstrap.py` before Uvicorn; it applies the migration files in order and initializes the clinic/admin bootstrap data. Confirm `https://YOUR_RENDER_HOST/api/health` returns `{"status":"ok"}`, then open `/book`, `/staff`, and `/secure`. Keep a separate encrypted database backup and verify restore procedures before entering real patient data.
9. Render watches the connected branch and deploys future pushes automatically. Confirm the Render service's deploy branch is `main` and its health check is `/api/health`.

## Portal URLs

All portals share one deployed HTTPS host:

- Patient: `https://dental-crm-portals.onrender.com/book`
- Staff: `https://dental-crm-portals.onrender.com/staff`
- OTP and medicine: `https://dental-crm-portals.onrender.com/secure`
- Health: `https://dental-crm-portals.onrender.com/api/health`

The hostname above becomes live only after Render creates the service with that exact name. Replace it in both the service environment and this document if Render assigns a different URL or a custom domain is attached.

## Environment template

Use [.env.example](./.env.example) as the inventory of application variables. For local development, set a reachable PostgreSQL `DATABASE_URL` and replace both secret values. For Render, set variables through the dashboard/Blueprint prompts; do not upload a `.env` file. Secrets are generated or prompted by `render.yaml`; provider IDs, sender templates, and clinic identity are entered from the relevant service accounts.
