from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from psycopg import Connection

from config import get_settings
from db import get_db
from messaging import ProviderError, generate_otp, send_sms_message, send_email_otp, send_otp, whatsapp_configured
from security import UserInfo, hash_password, hash_session_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/secure-portal", tags=["secure staff portal"])
Db = Annotated[Connection, Depends(get_db)]
PORTAL_COOKIE = "staff_portal_session"
PORTAL_MINUTES = 15
OTP_MINUTES = 15


class OtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    purpose: Literal["PORTAL_LOGIN", "PASSWORD_RESET"] = "PORTAL_LOGIN"
    channel: Literal["SMS", "EMAIL", "WHATSAPP"] = "WHATSAPP"
    otp_reason: Literal["Staff 15-Minute OTP Portal Login", "Staff Credential Password Reset (30-day renewal)"]


class OtpVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    purpose: Literal["PORTAL_LOGIN", "PASSWORD_RESET"] = "PORTAL_LOGIN"
    channel: Literal["SMS", "EMAIL", "WHATSAPP"] = "WHATSAPP"
    otp_reason: Literal["Staff 15-Minute OTP Portal Login", "Staff Credential Password Reset (30-day renewal)"]
    code: str = Field(pattern=r"^[0-9]{6}$")


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=12, max_length=256)


def _otp_hash(challenge_id: UUID, user_id: UUID, code: str) -> bytes:
    pepper = get_settings().otp_pepper.get_secret_value().encode()
    message = f"{challenge_id}:{user_id}:{code}".encode()
    return hmac.new(pepper, message, hashlib.sha256).digest()


def _staff_payload(row) -> dict:
    return {
        "id": str(row[0]), "full_name": row[1], "email": row[2],
        "roles": list(row[3] or []), "session_minutes": PORTAL_MINUTES,
    }


def _expected_otp_reason(purpose: str) -> str:
    return "Staff 15-Minute OTP Portal Login" if purpose == "PORTAL_LOGIN" else "Staff Credential Password Reset (30-day renewal)"


@router.post("/otp/request")
def request_staff_otp(payload: OtpRequest, connection: Db):
    if payload.otp_reason != _expected_otp_reason(payload.purpose):
        raise HTTPException(422, "OTP reason does not match the requested action.")
    email = str(payload.email).strip().lower()
    with connection.transaction():
        user = connection.execute(
            """
            SELECT id, mobile_10_digit FROM users
            WHERE lower(email) = %s AND is_active AND soft_deleted_at IS NULL
            """,
            (email,),
        ).fetchone()
        if not user or (payload.channel in {"SMS", "WHATSAPP"} and not user[1]):
            # Same reply avoids confirming which emails are staff accounts.
            return {"ok": True, "otp_reason": payload.otp_reason, "channel": payload.channel,
                "message": f"If the account is eligible, a code for {payload.otp_reason} has been sent by {payload.channel.lower()}."}
        recent = connection.execute(
            """
            SELECT count(*) FROM staff_otp_challenges
            WHERE user_id = %s AND created_at > now() - interval '15 minutes'
            """,
            (user[0],),
        ).fetchone()[0]
        settings_for_limit = get_settings()
        local_demo = settings_for_limit.app_env.lower() == "development" and settings_for_limit.demo_mode
        if recent >= 3 and not local_demo:
            raise HTTPException(429, "Too many code requests. Try again in 15 minutes.")
        connection.execute(
            "UPDATE staff_otp_challenges SET consumed_at = now() WHERE user_id = %s AND consumed_at IS NULL",
            (user[0],),
        )
        code = generate_otp()
        challenge_id = connection.execute(
            """
            INSERT INTO staff_otp_challenges(user_id, code_hash, purpose, expires_at, delivery_channel, otp_reason)
            VALUES (%s, %s, %s, now() + %s, %s, %s) RETURNING id
            """,
            (user[0], b"pending", payload.purpose, timedelta(minutes=OTP_MINUTES), payload.channel, payload.otp_reason),
        ).fetchone()[0]
        digest = _otp_hash(challenge_id, user[0], code)
        connection.execute("UPDATE staff_otp_challenges SET code_hash = %s WHERE id = %s", (digest, challenge_id))
        user_id, mobile = user[0], user[1]

    demo_code = None
    settings = get_settings()
    try:
        delivery_configured = (
            (payload.channel == "SMS" and (settings.sms_provider.lower() == "twilio" or (settings.sms_api_url and settings.sms_api_token.get_secret_value())))
            or (payload.channel == "EMAIL" and settings.email_provider.lower() == "smtp" and settings.smtp_host and settings.smtp_from)
            or (payload.channel == "WHATSAPP" and whatsapp_configured())
        )
        if settings.allow_test_otp or (settings.app_env.lower() != "production" and settings.demo_mode and not delivery_configured):
            provider_ref = "local-demo"
            demo_code = code
            status_value = "FAILED"
            error = "Development test code displayed; no external delivery was attempted."
            log_channel = "SMS" if payload.channel == "WHATSAPP" else payload.channel
        elif payload.channel == "EMAIL":
            provider_ref = send_email_otp(email, code, payload.otp_reason)
            status_value, error = "SENT", None
            log_channel = "EMAIL"
        elif payload.channel == "WHATSAPP":
            delivery = send_otp("+91" + mobile, code)
            provider_ref = delivery.provider_reference or delivery.provider
            log_channel = "WHATSAPP" if delivery.provider.lower() == "whatsapp" else "SMS" if delivery.provider.lower() == "sms" else "WHATSAPP"
            status_value = "SENT" if delivery.provider.lower() in {"whatsapp", "sms"} else "FAILED"
            error = None if status_value == "SENT" else "Development test code displayed; no external delivery was attempted."
            demo_code = delivery.demo_code
        else:
            provider_ref = send_sms_message(
                "+91" + mobile,
                f"Dental Hospital OTP for {payload.otp_reason}: {code}. Valid for 15 minutes. Do not share this OTP.",
            )
            status_value, error = "SENT", None
            log_channel = "SMS"
    except ProviderError as exc:
        status_value = "FAILED"
        provider_ref = None
        error = str(exc)[:300]
        log_channel = payload.channel if payload.channel in {"SMS", "EMAIL"} else "SMS"
        logger.warning("Staff OTP delivery failed for user %s: %s", user_id, exc)

    with connection.transaction():
        connection.execute(
            """
            INSERT INTO notification_delivery_log
              (recipient_mobile_10_digit, recipient_email, message_type, channel, provider_status, provider_reference, error_summary)
            VALUES (%s, %s, 'OTP', %s, %s, %s, %s)
            """,
            (mobile, email, log_channel, status_value, provider_ref, error),
        )
    response = {"ok": True, "otp_reason": payload.otp_reason, "channel": log_channel,
                "message": f"If the account is eligible, a code for {payload.otp_reason} has been sent. Delivery: {log_channel.lower()}."}
    if demo_code:
        response["demo_code"] = demo_code
        response["message"] = "Local demo code only; configure SMS delivery before production."
    return response


@router.post("/otp/verify")
def verify_staff_otp(payload: OtpVerify, response: Response, connection: Db):
    if payload.otp_reason != _expected_otp_reason(payload.purpose):
        raise HTTPException(422, "OTP reason does not match the requested action.")
    email = str(payload.email).strip().lower()
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    invalid_code = False
    with connection.transaction():
        row = connection.execute(
            """
            SELECT u.id, u.full_name, u.email, c.id, c.code_hash
            FROM users u
            JOIN staff_otp_challenges c ON c.user_id = u.id
            WHERE lower(u.email) = %s AND u.is_active AND u.soft_deleted_at IS NULL
              AND c.purpose = %s AND c.delivery_channel = %s AND c.otp_reason = %s
              AND c.consumed_at IS NULL AND c.expires_at > now() AND c.attempts < 5
            ORDER BY c.created_at DESC LIMIT 1 FOR UPDATE OF c
            """,
            (email, payload.purpose, payload.channel, payload.otp_reason),
        ).fetchone()
        if not row:
            raise HTTPException(401, "Code is invalid or expired. Request a new one.")
        expected = _otp_hash(row[3], row[0], payload.code)
        if not hmac.compare_digest(expected, row[4]):
            connection.execute(
                "UPDATE staff_otp_challenges SET attempts = attempts + 1 WHERE id = %s", (row[3],)
            )
            invalid_code = True
        else:
            connection.execute("UPDATE staff_otp_challenges SET consumed_at = now() WHERE id = %s", (row[3],))
            connection.execute(
                "INSERT INTO staff_portal_sessions(user_id, token_hash, purpose, expires_at) VALUES (%s, %s, %s, now() + interval '15 minutes')",
                (row[0], hash_session_token(token), payload.purpose),
            )
    if invalid_code:
        raise HTTPException(401, "Code is invalid or expired. Request a new one.")
    roles = connection.execute(
        """
        SELECT array_remove(array_agg(r.name), NULL)
        FROM user_roles ur JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = %s
        """,
        (row[0],),
    ).fetchone()[0]
    settings = get_settings()
    response.set_cookie(PORTAL_COOKIE, token, max_age=PORTAL_MINUTES * 60, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/")
    response.set_cookie("dental_crm_csrf", csrf, max_age=PORTAL_MINUTES * 60,
                        httponly=False, secure=settings.cookie_secure, samesite="strict", path="/")
    return {**_staff_payload((row[0], row[1], row[2], roles)), "session_expires_in": PORTAL_MINUTES * 60}


def current_portal_user(
    connection: Db,
    staff_portal_session: str | None = Cookie(default=None, alias=PORTAL_COOKIE),
) -> UserInfo:
    if not staff_portal_session:
        raise HTTPException(401, "A fresh OTP login is required.")
    row = connection.execute(
        """
        SELECT u.id, u.organisation_id, u.branch_id, u.full_name, u.email,
               array_remove(array_agg(r.name), NULL)
        FROM staff_portal_sessions s JOIN users u ON u.id = s.user_id
        LEFT JOIN user_roles ur ON ur.user_id = u.id LEFT JOIN roles r ON r.id = ur.role_id
        WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now()
          AND u.is_active AND u.soft_deleted_at IS NULL
        GROUP BY u.id LIMIT 1
        """,
        (hash_session_token(staff_portal_session),),
    ).fetchone()
    if not row:
        raise HTTPException(401, "The 15-minute OTP session expired. Sign in again.")
    return UserInfo(id=str(row[0]), organisation_id=str(row[1]),
                    branch_id=str(row[2]) if row[2] else None, full_name=row[3],
                    email=row[4], roles=list(row[5] or []))


PortalUser = Annotated[UserInfo, Depends(current_portal_user)]


@router.get("/me")
def portal_me(user: PortalUser, connection: Db):
    row = connection.execute(
        """
        SELECT s.expires_at, u.password_expires_at
        FROM staff_portal_sessions s JOIN users u ON u.id = s.user_id
        WHERE s.user_id = %s AND s.revoked_at IS NULL AND s.expires_at > now()
        ORDER BY s.created_at DESC LIMIT 1
        """,
        (user.id,),
    ).fetchone()
    return {**user.model_dump(), "session_expires_at": row[0], "password_expires_at": row[1]}


@router.post("/password")
def change_password(payload: PasswordChange, request: Request, user: PortalUser, connection: Db):
    token = request.cookies.get(PORTAL_COOKIE)
    if not token:
        raise HTTPException(401, "A fresh OTP login is required.")
    with connection.transaction():
        session = connection.execute(
            "SELECT purpose FROM staff_portal_sessions WHERE token_hash = %s AND user_id = %s AND expires_at > now() AND revoked_at IS NULL FOR UPDATE",
            (hash_session_token(token), user.id),
        ).fetchone()
        if not session:
            raise HTTPException(401, "The OTP session expired.")
        connection.execute(
            "UPDATE users SET password_hash = %s, password_updated_at = now(), password_expires_at = now() + interval '30 days', updated_at = now() WHERE id = %s",
            (hash_password(payload.password), user.id),
        )
        connection.execute(
            "UPDATE auth_sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL",
            (user.id,),
        )
    return {"ok": True, "valid_for_days": 30}


@router.post("/logout")
def portal_logout(request: Request, response: Response, connection: Db):
    token = request.cookies.get(PORTAL_COOKIE)
    if token:
        connection.execute(
            "UPDATE staff_portal_sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
            (hash_session_token(token),),
        )
        connection.commit()
    response.delete_cookie(PORTAL_COOKIE, path="/", samesite="strict")
    response.delete_cookie("dental_crm_csrf", path="/", samesite="strict")
    return {"ok": True}
