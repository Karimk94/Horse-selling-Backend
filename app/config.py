import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:password@localhost:5432/horse_marketplace",
)
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
