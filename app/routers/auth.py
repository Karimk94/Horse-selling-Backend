"""Authentication endpoints: signup, login, OTP, email verification."""

import random
import string
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_verification_token,
    verify_token,
)
from app.config import BASE_URL
from app.database import get_db
from app.email_service import send_verification_email, send_otp_email
from app.models import User, UserProfile, UserRole
from app.schemas import (
    SignupRequest,
    OTPRequest,
    VerifyOTPRequest,
    LoginRequest,
    TokenResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
@limiter.limit("5/minute")
async def signup(request: Request, body: SignupRequest, db: AsyncSession = Depends(get_db)):
    # Check for existing user
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    # Check for existing phone number
    if body.phone_number:
        result_phone = await db.execute(select(UserProfile).where(UserProfile.phone_number == body.phone_number))
        if result_phone.scalar_one_or_none() is not None:
             raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This phone number is already in use",
            )

    # Create user
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        role=UserRole(body.role),
        is_verified=False,
        language=body.language,
    )
    db.add(user)
    await db.flush()

    # Create profile if optional fields provided
    if body.first_name or body.last_name or body.phone_number or body.location:
        profile = UserProfile(
            user_id=user.id,
            first_name=body.first_name,
            last_name=body.last_name,
            phone_number=body.phone_number,
            location=body.location,
        )
        db.add(profile)

    await db.commit()
    await db.refresh(user)

    # Generate verification token and send email
    verification_token = create_verification_token(user.email)
    verification_link = f"{BASE_URL}/auth/verify-email?token={verification_token}"
    email_sent = send_verification_email(
        user.email,
        verification_token,
        verification_link,
        user.language,
    )
    if not email_sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account created, but verification email could not be sent",
        )

    access_token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=access_token)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate and receive a token",
)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access_token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=access_token)


@router.get(
    "/verify-email",
    summary="Verify user email address",
)
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    """Verify email by token from the verification link."""
    try:
        payload = verify_token(token)

        if payload.get("type") != "verification":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid token type",
            )

        user_email = payload.get("sub")
        if not user_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid token",
            )

        result = await db.execute(select(User).where(User.email == user_email))
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        if user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already verified",
            )

        user.is_verified = True
        db.add(user)
        await db.commit()

        return {"message": "Email verified successfully"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )


@router.post(
    "/send-otp",
    status_code=status.HTTP_200_OK,
    summary="Send OTP for email verification",
)
@limiter.limit("5/minute")
async def send_otp(request: Request, body: OTPRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already verified",
        )

    otp = ''.join(random.choices(string.digits, k=6))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    user.verification_code = hash_password(otp)
    user.verification_code_expires_at = expires_at

    await db.commit()

    email_sent = send_otp_email(user.email, otp, user.language)
    if not email_sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not send OTP email. Please check SMTP configuration.",
        )

    return {"message": "OTP sent successfully"}


@router.post(
    "/verify-otp",
    status_code=status.HTTP_200_OK,
    summary="Verify email with OTP",
)
async def verify_otp(body: VerifyOTPRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if user.is_verified:
        return {"message": "Email already verified"}

    if not user.verification_code or not user.verification_code_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No OTP requested",
        )

    if datetime.now(timezone.utc) > user.verification_code_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP expired",
        )

    if not verify_password(body.otp, user.verification_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )

    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires_at = None

    await db.commit()

    return {"message": "Email verified successfully"}