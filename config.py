from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic import EmailStr, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_secret_key: SecretStr
    otp_pepper: SecretStr
    database_url: SecretStr
    db_pool_max: int = Field(default=12, ge=2, le=100)

    clinic_name: str = "Your Dental Hospital"
    clinic_city: str = "Your City"
    clinic_country: str = "India"
    clinic_address: str = ""
    clinic_phone: str = ""
    clinic_whatsapp: str = ""
    clinic_timezone: str = "Asia/Kolkata"
    branch_name: str = "Main Branch"
    public_base_url: str = "https://example.invalid"
    patient_portal_url: str = ""
    staff_portal_url: str = ""
    otp_portal_url: str = ""
    cors_allowed_origins: list[str] = Field(default_factory=list)
    default_country_code: str = "91"
    storage_dir: Path = Path("./private_uploads")
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    cookie_secure: bool = False
    demo_mode: bool = True

    meta_verify_token: SecretStr = SecretStr("")
    meta_app_secret: SecretStr = SecretStr("")
    meta_access_token: SecretStr = SecretStr("")
    meta_phone_number_id: str = ""
    meta_graph_version: str = "v23.0"
    meta_template_otp: str = ""
    meta_template_confirmation: str = ""
    meta_template_patient_card: str = ""
    meta_template_reminder_24h: str = ""
    meta_template_reminder_2h: str = ""
    meta_template_language: str = "en"

    sms_provider: str = "disabled"
    sms_api_url: str = ""
    sms_api_token: SecretStr = SecretStr("")
    sms_twilio_account_sid: str = ""
    sms_twilio_auth_token: SecretStr = SecretStr("")
    sms_twilio_from: str = ""

    email_provider: str = "disabled"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_starttls: bool = True
    allow_test_otp: bool = False

    admin_email: str = ""
    admin_mobile_10_digit: str = ""
    admin_password: SecretStr = SecretStr("")

    @field_validator("clinic_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    def require_secrets(self) -> None:
        if len(self.app_secret_key.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must contain at least 32 characters.")
        if len(self.otp_pepper.get_secret_value()) < 32:
            raise ValueError("OTP_PEPPER must contain at least 32 characters.")
        if not self.database_url.get_secret_value():
            raise ValueError("DATABASE_URL is required.")
        if bool(self.admin_email) != bool(self.admin_password.get_secret_value()):
            raise ValueError("Set both ADMIN_EMAIL and ADMIN_PASSWORD to seed the initial administrator.")
        if self.admin_email:
            TypeAdapter(EmailStr).validate_python(self.admin_email)
        if self.admin_mobile_10_digit and not __import__("re").fullmatch(r"[6-9][0-9]{9}", self.admin_mobile_10_digit):
            raise ValueError("ADMIN_MOBILE_10_DIGIT must be exactly 10 valid Indian mobile digits.")
        if self.admin_password.get_secret_value() and len(self.admin_password.get_secret_value()) < 12:
            raise ValueError("ADMIN_PASSWORD must contain at least 12 characters.")
        if self.app_env.lower() == "production" and (not self.cookie_secure or self.demo_mode):
            raise ValueError("Production requires COOKIE_SECURE=true and DEMO_MODE=false.")
        if self.app_env.lower() == "production":
            if not self.public_base_url.startswith("https://") or "example.invalid" in self.public_base_url:
                raise ValueError("Production requires the clinic's HTTPS PUBLIC_BASE_URL.")
            if not self.admin_email or not self.admin_password.get_secret_value() or not self.admin_mobile_10_digit:
                raise ValueError("Production requires an initial administrator email, password, and registered 10-digit mobile.")
            if not self.meta_access_token.get_secret_value() or not self.meta_phone_number_id or not self.meta_template_confirmation:
                raise ValueError("Production requires a WhatsApp sender and approved appointment confirmation template.")
            if self.sms_provider.lower() == "twilio":
                if not self.sms_twilio_account_sid or not self.sms_twilio_auth_token.get_secret_value() or not self.sms_twilio_from:
                    raise ValueError("Production Twilio fallback requires account, auth token, and sender.")
            elif not self.sms_api_url or not self.sms_api_token.get_secret_value():
                raise ValueError("Production requires a configured SMS fallback provider.")
            if self.email_provider.lower() == "smtp" and (not self.smtp_host or not self.smtp_from):
                raise ValueError("SMTP email OTP requires SMTP_HOST and SMTP_FROM.")
            if any(not origin.startswith("https://") for origin in self.cors_allowed_origins):
                raise ValueError("Production CORS_ALLOWED_ORIGINS must contain HTTPS origins only.")

    @property
    def database_dsn(self) -> str:
        return self.database_url.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
