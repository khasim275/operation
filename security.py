from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from psycopg import Connection

from config import get_settings
from db import get_db

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
SESSION_COOKIE = "dental_crm_session"
SESSION_HOURS = 12


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserInfo(BaseModel):
    id: str
    full_name: str
    email: str
    organisation_id: str
    branch_id: str | None
    roles: list[str]


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def hash_session_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def login_key_hash(email: str) -> bytes:
    pepper = get_settings().otp_pepper.get_secret_value().encode("utf-8")
    return hmac.new(pepper, email.strip().lower().encode("utf-8"), hashlib.sha256).digest()


def create_session(
    connection: Connection,
    user_id: str,
    response: Response,
) -> None:
    token = secrets.token_urlsafe(48)
    settings = get_settings()
    connection.execute(
        """
        INSERT INTO auth_sessions (user_id, token_hash, expires_at)
        VALUES (%s, %s, now() + %s)
        """,
        (user_id, hash_session_token(token), timedelta(hours=SESSION_HOURS)),
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        "dental_crm_csrf",
        csrf_token,
        max_age=SESSION_HOURS * 60 * 60,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def login(
    payload: LoginInput,
    response: Response,
    connection: Connection = Depends(get_db),
) -> UserInfo:
    normalized_email = str(payload.email).strip().lower()
    key_hash = login_key_hash(normalized_email)
    too_many = False
    valid = False
    user = None
    with connection.transaction():
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (key_hash.hex(),),
        )
        failures = connection.execute(
            """
            SELECT count(*) FROM login_attempts
            WHERE login_key_hash = %s
              AND succeeded = false
              AND attempted_at > now() - interval '15 minutes'
            """,
            (key_hash,),
        ).fetchone()[0]
        if failures >= 5:
            too_many = True
        else:
            user = connection.execute(
                """
                SELECT u.id, u.organisation_id, u.branch_id, u.full_name, u.email,
                       u.password_hash, u.password_expires_at,
                       array_remove(array_agg(r.name), NULL) AS roles
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                WHERE lower(u.email) = %s AND u.is_active AND u.soft_deleted_at IS NULL
                GROUP BY u.id
                LIMIT 1
                """,
                (normalized_email,),
            ).fetchone()
            valid = bool(user and user[6] > datetime.now(timezone.utc)
                         and verify_password(user[5], payload.password))
            connection.execute(
                "INSERT INTO login_attempts (login_key_hash, succeeded) VALUES (%s, %s)",
                (key_hash, valid),
            )
            if valid:
                create_session(connection, str(user[0]), response)

    if too_many:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed sign-in attempts. Try again in 15 minutes.",
            headers={"Retry-After": "900"},
        )
    if user and user[6] <= datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Credentials expired. Use the secure OTP portal to reset them.")
    if not valid or not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect.")

    return UserInfo(
        id=str(user[0]),
        organisation_id=str(user[1]),
        branch_id=str(user[2]) if user[2] else None,
        full_name=user[3],
        email=user[4],
        roles=list(user[7] or []),
    )


def logout(
    request: Request,
    response: Response,
    connection: Connection = Depends(get_db),
) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        connection.execute(
            "UPDATE auth_sessions SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
            (hash_session_token(token),),
        )
        connection.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    response.delete_cookie("dental_crm_csrf", path="/", samesite="strict")
    return {"ok": True}


def current_user(
    request: Request,
    connection: Connection = Depends(get_db),
) -> UserInfo:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required.")
    with connection.transaction():
        user = connection.execute(
            """
            SELECT u.id, u.organisation_id, u.branch_id, u.full_name, u.email,
                   array_remove(array_agg(r.name), NULL) AS roles
            FROM auth_sessions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now()
              AND u.is_active AND u.soft_deleted_at IS NULL
            GROUP BY u.id
            LIMIT 1
            """,
            (hash_session_token(token),),
        ).fetchone()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")
    return UserInfo(
        id=str(user[0]),
        organisation_id=str(user[1]),
        branch_id=str(user[2]) if user[2] else None,
        full_name=user[3],
        email=user[4],
        roles=list(user[5] or []),
    )


def require_roles(*allowed_roles: str) -> Callable[..., UserInfo]:
    def dependency(user: Annotated[UserInfo, Depends(current_user)]) -> UserInfo:
        if not set(user.roles).intersection(allowed_roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account cannot access this action.")
        return user

    return dependency


CurrentUser = Annotated[UserInfo, Depends(current_user)]
ConnectionDep = Annotated[Connection, Depends(get_db)]
