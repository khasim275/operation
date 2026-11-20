from __future__ import annotations

import hashlib
import hmac
import base64
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from dataclasses import dataclass

import httpx

from config import get_settings


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class OtpDelivery:
    provider: str
    demo_code: str | None = None
    provider_reference: str | None = None


def normalize_phone(value: str, default_country_code: str | None = None) -> str:
    digits = re.sub(r"\D", "", value)
    if value.strip().startswith("+"):
        normalized = "+" + digits
    elif digits.startswith("00"):
        normalized = "+" + digits[2:]
    else:
        country_code = default_country_code or get_settings().default_country_code
        if digits.startswith(country_code) and len(digits) > 10:
            normalized = "+" + digits
        else:
            normalized = "+" + country_code + digits.lstrip("0")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", normalized):
        raise ValueError("Enter a valid mobile number with its country code.")
    return normalized


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(challenge_id: str, phone_e164: str, code: str) -> bytes:
    pepper = get_settings().otp_pepper.get_secret_value().encode("utf-8")
    message = f"{challenge_id}:{phone_e164}:{code}".encode("utf-8")
    return hmac.new(pepper, message, hashlib.sha256).digest()


def verify_otp_hash(challenge_id: str, phone_e164: str, code: str, expected_hash: bytes) -> bool:
    return hmac.compare_digest(hash_otp(challenge_id, phone_e164, code), expected_hash)


def verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    secret = get_settings().meta_app_secret.get_secret_value()
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[7:], expected)


def whatsapp_configured() -> bool:
    settings = get_settings()
    return bool(settings.meta_access_token.get_secret_value() and settings.meta_phone_number_id)


def send_whatsapp_text(phone_e164: str, text: str) -> str:
    settings = get_settings()
    if not whatsapp_configured():
        if settings.demo_mode:
            return "simulator"
        raise ProviderError("WhatsApp Business Cloud API is not configured.")
    url = f"https://graph.facebook.com/{settings.meta_graph_version}/{settings.meta_phone_number_id}/messages"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.meta_access_token.get_secret_value()}"},
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_e164.removeprefix("+"),
            "type": "text",
            "text": {"preview_url": False, "body": text},
        },
        timeout=15,
    )
    if response.is_error:
        raise ProviderError(f"WhatsApp message failed with HTTP {response.status_code}.")
    try:
        message_id = response.json()["messages"][0]["id"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("WhatsApp returned an invalid send response.") from exc
    return str(message_id)


def send_whatsapp_template(phone_e164: str, template_name: str, language: str, variables: list[str]) -> str:
    settings = get_settings()
    if not whatsapp_configured():
        raise ProviderError("WhatsApp Business Cloud API is not configured.")
    if not template_name:
        raise ProviderError("The required approved WhatsApp template is not configured.")
    components = []
    if variables:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": value} for value in variables],
        })
    url = f"https://graph.facebook.com/{settings.meta_graph_version}/{settings.meta_phone_number_id}/messages"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.meta_access_token.get_secret_value()}"},
        json={
            "messaging_product": "whatsapp",
            "to": phone_e164.removeprefix("+"),
            "type": "template",
            "template": {"name": template_name, "language": {"code": language}, "components": components},
        },
        timeout=15,
    )
    if response.is_error:
        raise ProviderError(f"WhatsApp template send failed with HTTP {response.status_code}.")
    try:
        return str(response.json()["messages"][0]["id"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("WhatsApp returned an invalid template-send response.") from exc


def send_whatsapp_card_template(phone_e164: str, image_path: Path) -> str:
    settings = get_settings()
    if not whatsapp_configured():
        if settings.demo_mode:
            return "simulator"
        raise ProviderError("WhatsApp Business Cloud API is not configured.")
    if not settings.meta_template_patient_card:
        raise ProviderError("The approved patient-card WhatsApp template is not configured.")
    if not image_path.is_file():
        raise ProviderError("The patient card image is not available.")

    base_url = f"https://graph.facebook.com/{settings.meta_graph_version}/{settings.meta_phone_number_id}"
    headers = {"Authorization": f"Bearer {settings.meta_access_token.get_secret_value()}"}
    try:
        with image_path.open("rb") as image_file:
            upload = httpx.post(
                f"{base_url}/media",
                headers=headers,
                data={"messaging_product": "whatsapp", "type": "image/png"},
                files={"file": (image_path.name, image_file, "image/png")},
                timeout=30,
            )
        if upload.is_error:
            raise ProviderError(f"WhatsApp card upload failed with HTTP {upload.status_code}.")
        media_id = str(upload.json()["id"])
        response = httpx.post(
            f"{base_url}/messages",
            headers=headers,
            json={
                "messaging_product": "whatsapp",
                "to": phone_e164.removeprefix("+"),
                "type": "template",
                "template": {
                    "name": settings.meta_template_patient_card,
                    "language": {"code": settings.meta_template_language},
                    "components": [{"type": "header", "parameters": [
                        {"type": "image", "image": {"id": media_id}},
                    ]}],
                },
            },
            timeout=30,
        )
        if response.is_error:
            raise ProviderError(f"WhatsApp card template failed with HTTP {response.status_code}.")
        return str(response.json()["messages"][0]["id"])
    except httpx.HTTPError as exc:
        raise ProviderError("WhatsApp card delivery could not reach the provider.") from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("WhatsApp returned an invalid card-send response.") from exc


def send_sms_otp(phone_e164: str, code: str) -> str:
    return send_sms_message(phone_e164, f"Dental Hospital OTP: {code}. Valid for 15 minutes. Do not share this OTP.")


def send_email_otp(recipient: str, code: str, reason: str) -> str:
    settings = get_settings()
    if settings.email_provider.lower() != "smtp" or not settings.smtp_host or not settings.smtp_from:
        if settings.demo_mode and settings.app_env.lower() != "production":
            return "local-demo"
        raise ProviderError("Email OTP delivery is not configured.")
    message = EmailMessage()
    message["Subject"] = "Your Dental Hospital verification code"
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(
        f"Your verification code is {code}.\n\nReason: {reason}\nValid for 15 minutes. Use it once and do not share it."
    )
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            client.ehlo()
            if settings.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise ProviderError("Email provider could not deliver the verification code.") from exc
    return "smtp"


def send_sms_message(phone_e164: str, message: str) -> str:
    """Send SMS using explicitly configured Twilio or a bearer-token JSON gateway."""
    settings = get_settings()
    try:
        if settings.sms_provider.lower() == "twilio":
            sid = settings.sms_twilio_account_sid
            token = settings.sms_twilio_auth_token.get_secret_value()
            if not sid or not token or not settings.sms_twilio_from:
                raise ProviderError("Twilio SMS credentials and sender are not configured.")
            response = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, token),
                data={
                    "To": phone_e164, "From": settings.sms_twilio_from, "Body": message,
                    "StatusCallback": settings.public_base_url.rstrip("/") + "/api/webhooks/sms/status",
                },
                timeout=15,
            )
        else:
            if settings.sms_provider == "disabled" or not settings.sms_api_url or not settings.sms_api_token.get_secret_value():
                raise ProviderError("No SMS provider is configured.")
            response = httpx.post(
                settings.sms_api_url,
                headers={"Authorization": f"Bearer {settings.sms_api_token.get_secret_value()}"},
                json={"to": phone_e164, "message": message},
                timeout=15,
            )
        if response.is_error:
            raise ProviderError(f"SMS provider failed with HTTP {response.status_code}.")
        provider_ref = response.json().get("sid") or response.json().get("message_id") or settings.sms_provider
        return str(provider_ref)
    except httpx.HTTPError as exc:
        raise ProviderError("SMS provider could not be reached.") from exc


def verify_twilio_signature(url: str, params: dict[str, str], signature: str | None) -> bool:
    secret = get_settings().sms_twilio_auth_token.get_secret_value()
    if not secret or not signature:
        return False
    message = url + "".join(key + params[key] for key in sorted(params))
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def send_otp(phone_e164: str, code: str) -> OtpDelivery:
    settings = get_settings()
    if whatsapp_configured():
        try:
            provider_reference = send_whatsapp_template(
                phone_e164,
                settings.meta_template_otp,
                settings.meta_template_language,
                [code],
            )
            return OtpDelivery("whatsapp", provider_reference=provider_reference)
        except ProviderError as whatsapp_error:
            if settings.sms_provider == "disabled":
                raise whatsapp_error
    if settings.sms_provider != "disabled":
        provider_reference = send_sms_otp(phone_e164, code)
        return OtpDelivery("sms", provider_reference=provider_reference)
    if settings.demo_mode:
        return OtpDelivery("simulator", code)
    raise ProviderError("No OTP delivery provider is configured.")
