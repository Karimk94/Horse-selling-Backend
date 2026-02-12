import os
import shutil
import uuid
from enum import Enum
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from pydantic import BaseModel

from app.auth import get_current_user
from app.config import UPLOAD_DIR, BASE_URL
from app.models import User


router = APIRouter(prefix="/api/v1", tags=["Media"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    file_url: str


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/media/upload",
    response_model=UploadResponse,
    summary="Upload a file to local storage",
)
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    # Ensure upload directory exists
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Validate file extension
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".mp4", ".mov", ".webm", ".pdf"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File extension '{ext}' is not allowed.",
        )

    # Generate unique filename
    unique_name = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)

    # Save file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not save file: {e}",
        )
    finally:
        file.file.close()

    # Construct full URL
    # If serving via ngrok, BASE_URL should be the ngrok URL
    file_url = f"{BASE_URL}/uploads/{unique_name}"

    return UploadResponse(file_url=file_url)
