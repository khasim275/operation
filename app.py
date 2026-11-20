from __future__ import annotations

import asyncio
import hmac
import json
import logging
import uuid
import queue
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from api import router as api_router
from auth_api import router as auth_router
from bot_api import router as bot_router
from config import get_settings
from db import close_pool, get_pool, open_pool
from patient_cards import router as patient_cards_router
from public_appointments import router as public_appointments_router
from staff_portal_api import router as staff_portal_router
from secure_portal_api import router as secure_portal_router
from realtime import encode_event, start_listener, stop_listener, subscribe, unsubscribe
from security import UserInfo, current_user
from workers import run_worker_tick

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).with_name("frontend") / "dist"
PUBLIC_SITE = Path(__file__).with_name("dental-hospital-website")


async def _worker_loop():
    while True:
        try:
            await asyncio.to_thread(run_worker_tick)
        except Exception:
            logger.exception("Background message/reminder worker tick failed.")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.require_secrets()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    open_pool()
    start_listener()
    port = settings.app_port
    public_root = (
        settings.public_base_url.rstrip("/")
        if settings.app_env.lower() == "production"
        else f"http://127.0.0.1:{port}"
    )
    logger.info("Patient Portal: %s", settings.patient_portal_url or f"{public_root}/book")
    logger.info("Staff Workspace: %s", settings.staff_portal_url or f"{public_root}/staff")
    logger.info("OTP & Medicine Portal: %s", settings.otp_portal_url or f"{public_root}/secure")
    if settings.app_host in {"0.0.0.0", "::"}:
        try:
            addresses = sorted({
                item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                if not item[4][0].startswith("127.") and not item[4][0].startswith("169.254.")
            })
            for address in addresses:
                logger.info("LAN Portal URLs (available to devices on this network): http://%s:%s/book | http://%s:%s/staff | http://%s:%s/secure", address, port, address, port, address, port)
        except OSError:
            logger.info("LAN URLs unavailable; check the network adapter and firewall.")
    worker = asyncio.create_task(_worker_loop(), name="crm-outbox-reminders")
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        stop_listener()
        close_pool()


app = FastAPI(
    title="Dental Hospital CRM",
    version="1.0.0",
    description="Patient requests, appointments, clinical records, billing, and WhatsApp integration.",
    lifespan=lifespan,
)
settings = get_settings()
if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Booking-OTP-Token", "Idempotency-Key"],
    )
app.include_router(auth_router)
app.include_router(patient_cards_router)
app.include_router(api_router)
app.include_router(bot_router)
app.include_router(public_appointments_router)
app.include_router(staff_portal_router)
app.include_router(secure_portal_router)
app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="frontend-assets")
app.mount("/posters", StaticFiles(directory=PUBLIC_SITE / "posters", check_dir=False), name="public-site-posters")


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    path = request.url.path
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith("/api/"):
        preauth_otp_endpoints = {
            "/api/secure-portal/otp/request",
            "/api/secure-portal/otp/verify",
            "/api/public/appointments/otp/request",
            "/api/public/appointments/otp/verify",
            "/api/public/history/otp/request",
            "/api/public/history/otp/verify",
        }
        exempt = path in ({"/api/auth/login"} | preauth_otp_endpoints) or path.startswith("/webhook/")
        if not exempt and (request.cookies.get("dental_crm_session") or request.cookies.get("staff_portal_session")):
            cookie_token = request.cookies.get("dental_crm_csrf", "")
            header_token = request.headers.get("X-CSRF-Token", "")
            if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
                return JSONResponse(
                    {"detail": "CSRF token is missing or invalid."},
                    status_code=403,
                )
    request.state.request_id = str(uuid.uuid4())
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled request failure request_id=%s path=%s", request.state.request_id, path)
        try:
            from db import get_pool
            with get_pool().connection() as connection:
                connection.execute(
                    "INSERT INTO system_error_logs(request_id, source, error_type, path) VALUES (%s, 'api', %s, %s)",
                    (request.state.request_id, type(exc).__name__[:100], path[:300]),
                )
        except Exception:
            logger.exception("Could not persist system error log request_id=%s", request.state.request_id)
        return JSONResponse({"detail": "The request could not be completed.", "request_id": request.state.request_id}, status_code=500)
    if path.startswith("/api/") or path in {"/secure", "/staff-portal", "/staff"}:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/", include_in_schema=False)
def home():
    index = PUBLIC_SITE / "index.html"
    if not index.is_file():
        raise HTTPException(503, "The public site is missing.")
    return FileResponse(index)


@app.get("/staff", include_in_schema=False)
def staff_workspace():
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(503, "The CRM frontend has not been built. Run npm --prefix frontend run build.")
    return FileResponse(index)


@app.get("/secure", include_in_schema=False)
@app.get("/staff-portal", include_in_schema=False)
def secure_staff_portal():
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(503, "The secure portal frontend has not been built.")
    return FileResponse(index)


@app.get("/p/{op_id}", include_in_schema=False)
def patient_file_page(op_id: str):
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(503, "The CRM frontend has not been built. Run npm --prefix frontend run build.")
    return FileResponse(index)


@app.get("/book", include_in_schema=False)
@app.get("/book/{op_id}", include_in_schema=False)
def public_booking_page(op_id: str | None = None):
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(503, "The public booking frontend has not been built.")
    return FileResponse(index)


@app.get("/health")
@app.get("/api/health")
def health():
    with get_pool().connection() as connection:
        connection.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/events")
async def events(request: Request, user: UserInfo = Depends(current_user)):
    subscriber = subscribe()

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    raw = await asyncio.to_thread(subscriber.get, True, 15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                event = json.loads(encode_event(raw))
                if event.get("organisation_id") != str(user.organisation_id):
                    continue
                if user.branch_id and event.get("branch_id") not in (None, str(user.branch_id)):
                    continue
                yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        finally:
            unsubscribe(subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
