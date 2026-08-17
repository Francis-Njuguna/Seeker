"""Application configuration, loaded from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://seeker:seeker@db:5432/seeker"

    # Auth
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12

    # Account verification. The sandbox sender logs a code and can be replaced
    # with an SMS provider without changing claim or payout logic.
    otp_ttl_minutes: int = 10
    otp_max_attempts: int = 5
    otp_provider: str = "sandbox"
    sms_api_key: str = ""

    # Storage
    upload_dir: str = "/data/uploads"

    # OCR
    tesseract_cmd: str = "tesseract"
    ocr_languages: str = "eng"

    # Email notifications. When smtp_host is empty, emails are written to the
    # application log instead of being sent. This keeps the MVP runnable with
    # zero external configuration.
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    email_from: str = "no-reply@seeker.local"

    # Abuse control. Searching by ID number is an enumeration vector, so it is
    # rate limited per authenticated user.
    search_rate_limit: int = 30
    search_rate_window_seconds: int = 60

    # Face verification. Euclidean distance below this counts as a match; 0.6 is
    # the face_recognition library default, not a value tuned on our own data.
    face_match_threshold: float = 0.6
    face_max_attempts: int = 5

    # How long a claimant may hold a document out of public search before the
    # lock lapses and it returns to circulation. Long enough to find a selfie in
    # decent light and read the OCR fields; short enough that a document is not
    # parked indefinitely by someone who abandoned the flow. Enforced server-side
    # by ``claims.expire_stale_claims`` — a browser countdown releases nothing.
    claim_timeout_hours: int = 48

    # Retention windows, in days, for the periodic purge (see ``retention.py``).
    # Kenya's Data Protection Act 2019 s.39 requires personal data not be kept
    # longer than necessary; these encode "necessary" as a number the operator
    # can defend, rather than leaving it to whenever someone remembers.
    retention_returned_document_days: int = 30
    retention_unclaimed_document_days: int = 90
    retention_notification_days: int = 180
    retention_audit_log_days: int = 730

    # Payments. "mock" is a self-contained sandbox that needs no credentials and
    # is what the tests exercise; "loop" talks to the real NCBA Loop sandbox once
    # keys exist. Amounts are in minor units — 20000 = 200.00 KES.
    payment_provider: str = "mock"
    payment_amount_minor: int = 20000
    payment_currency: str = "KES"
    platform_share_ratio: float = 0.5

    loop_api_url: str = "https://sandbox.loop.co.ke/api/v1/payments"
    loop_api_key: str = ""
    loop_webhook_secret: str = ""

    # Used to build the callback URL handed to the payment provider.
    public_base_url: str = "http://localhost:8000"

    # M-Pesa Daraja. Sandbox and production use the same STK adapter with
    # different credentials and endpoint base URLs.
    mpesa_consumer_key: str = ""
    mpesa_consumer_secret: str = ""
    mpesa_shortcode: str = ""
    mpesa_passkey: str = ""
    mpesa_callback_url: str = ""
    mpesa_base_url: str = "https://sandbox.safaricom.co.ke"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
