import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/horse_marketplace",
)
TEST_DATABASE_URL: str | None = os.getenv("TEST_DATABASE_URL")
AUTO_CREATE_SCHEMA: bool = os.getenv("AUTO_CREATE_SCHEMA", "false").lower() == "true"
SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-to-a-random-secret-key")
ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)

# ── Local Storage ─────────────────────────────────────────────────────────────
UPLOAD_DIR = "uploads"
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# ── Email Configuration ───────────────────────────────────────────────────────
SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM: str = os.getenv("EMAIL_FROM", "noreply@horsemarketplace.com")

# ── CORS ──────────────────────────────────────────────────────────────────────
# Comma-separated list of allowed origins, e.g.
# ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
# Leave as * only for local development.
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS: list[str] = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ── Soft Delete ───────────────────────────────────────────────────────────────
# Number of days a soft-deleted listing can be restored.
# Set to 0 or a negative value to disable expiry.
SOFT_DELETE_RESTORE_DAYS: int = int(os.getenv("SOFT_DELETE_RESTORE_DAYS", "30"))

# Token required by destructive admin purge actions.
PURGE_CONFIRM_TOKEN: str = os.getenv("PURGE_CONFIRM_TOKEN", "PURGE")
