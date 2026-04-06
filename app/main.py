import random
import string
import logging
from datetime import datetime, timedelta, timezone
import uuid
import json

from contextlib import asynccontextmanager
from typing import Optional, Annotated

from fastapi import FastAPI, Depends, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import engine, Base, get_db
from app.models import User, UserProfile, Horse, HorseGender, UserRole, HorseImage, Favorite, Voucher, DiscountType, ListingReview, SavedSearch, SavedSearchAlert, PushToken, Offer, OfferStatus, OfferTransitionAudit, IdempotencyKey, PushDeliveryLog
from app.config import (
    AUTO_CREATE_SCHEMA,
    BASE_URL,
    ALLOWED_ORIGINS,
    SOFT_DELETE_RESTORE_DAYS,
    PURGE_CONFIRM_TOKEN,
)
from app.background_tasks import start_scheduler, stop_scheduler
from app.schemas import (
    SignupRequest,
    OTPRequest,
    VerifyOTPRequest,
    LoginRequest,
    TokenResponse,
    UserResponse,
    UserProfileUpdate,
    HorseCreateRequest,
    HorseResponse,
    HorseListResponse,
    UserListResponse,
    ListingListResponse,
    AdminSecurityStatusResponse,
    PurgeDeletedListingsResponse,
    BulkRestoreListingsRequest,
    BulkRestoreListingsResponse,
    BulkPurgeDeletedListingsRequest,
    BulkPurgeDeletedListingsResponse,
    HorseUpdateRequest,
    UserRoleUpdate,
    AdminUserUpdate,
    AddFavoriteRequest,
    FavoriteResponse,
    AdminApproveListingRequest,
    AdminRejectListingRequest,
    ListingReviewResponse,
    VoucherCreateRequest,
    VoucherUpdateRequest,
    VoucherResponse,
    VoucherValidateRequest,
    VoucherValidateResponse,
    SavedSearchCreateRequest,
    SavedSearchUpdateRequest,
    SavedSearchResponse,
    SavedSearchAlertResponse,
    SavedSearchUnreadCountResponse,
    PushTokenRegisterRequest,
    PushTokenUnregisterRequest,
    OfferCreateRequest,
    OfferCounterRequest,
    OfferRejectRequest,
    OfferAcceptRequest,
    OfferCancelRequest,
    OfferResponse,
    OfferHistoryResponse,
    OfferActionRequiredCountResponse,
    OfferTransitionAuditResponse,
    OfferTransitionAuditListResponse,
    PushDeliveryLogListResponse,
)
from app.email_service import (
    send_pending_review_notification,
    send_listing_approved_email,
    send_listing_rejected_email,
    send_verification_email,
    send_otp_email,
    send_saved_search_match_email,
    send_offer_update_email,
    send_expo_push_notifications,
    send_expo_push_notifications_result,
)
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_verification_token,
    verify_token,
    get_current_user,
    get_current_admin,
    get_optional_current_user,
)


logger = logging.getLogger(__name__)

PURGE_TOKEN_WEAK_WARNING = (
    "PURGE_CONFIRM_TOKEN appears weak or default. "
    "Set a longer, non-default token in environment for production."
)


def is_purge_confirm_token_strong() -> bool:
    """Returns True when purge confirm token appears non-default and sufficiently long."""
    token = (PURGE_CONFIRM_TOKEN or "").strip()
    weak_defaults = {"PURGE", "DELETE", "CONFIRM", "ADMIN"}
    return len(token) >= 8 and token.upper() not in weak_defaults


def warn_if_weak_purge_confirm_token() -> None:
    """Warn when the configured purge confirmation token is weak/default."""
    if not is_purge_confirm_token_strong():
        logger.warning(PURGE_TOKEN_WEAK_WARNING)

# ── Lifespan: create tables on startup ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if AUTO_CREATE_SCHEMA:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    warn_if_weak_purge_confirm_token()
    
    # Start background scheduler for periodic cleanup tasks
    start_scheduler()
    
    yield
    
    # Cleanup on shutdown
    stop_scheduler()


def matches_saved_search(horse: Horse, search: SavedSearch) -> bool:
    if search.breed and search.breed.lower() not in (horse.breed or "").lower():
        return False
    if search.discipline and search.discipline.lower() not in (horse.discipline or "").lower():
        return False
    if search.gender and search.gender != horse.gender.value:
        return False
    if search.min_price is not None and horse.price < search.min_price:
        return False
    if search.max_price is not None and horse.price > search.max_price:
        return False
    if search.min_age is not None and horse.age < search.min_age:
        return False
    if search.max_age is not None and horse.age > search.max_age:
        return False
    if search.vet_check_available is not None and horse.vet_check_available != search.vet_check_available:
        return False
    if search.verified_seller is not None and horse.owner and horse.owner.is_verified != search.verified_seller:
        return False
    return True


async def notify_offer_event(
    db: AsyncSession,
    target_user: User,
    horse: Horse,
    title_en: str,
    body_en: str,
    title_ar: str,
    body_ar: str,
    data: dict | None = None,
) -> None:
    language = target_user.language or "en"
    title = title_ar if language == "ar" else title_en
    message = body_ar if language == "ar" else body_en

    send_offer_update_email(
        user_email=target_user.email,
        horse_title=horse.title,
        update_title=title,
        update_message=message,
        language=language,
    )

    push_tokens_result = await db.execute(
        select(PushToken.token).where(
            PushToken.user_id == target_user.id,
            PushToken.is_active == True,
        )
    )
    tokens = [row[0] for row in push_tokens_result.all()]
    push_result = send_expo_push_notifications_result(
        tokens=tokens,
        title=title,
        body=message,
        data=data or {},
    )

    db.add(
        PushDeliveryLog(
            target_user_id=target_user.id,
            provider="expo",
            event_type=(data or {}).get("type"),
            total_tokens=push_result.get("total_tokens", 0),
            accepted_count=push_result.get("accepted_count", 0),
            failed_count=push_result.get("failed_count", 0),
            status=push_result.get("status", "failed"),
            error_message=push_result.get("error_message"),
        )
    )
    await db.commit()


async def notify_offer_participant(
    db: AsyncSession,
    target_user: User,
    horse: Horse,
    offer: Offer,
    event_type: str,
    title_en: str,
    body_en: str,
    title_ar: str,
    body_ar: str,
) -> None:
    await notify_offer_event(
        db=db,
        target_user=target_user,
        horse=horse,
        title_en=title_en,
        body_en=body_en,
        title_ar=title_ar,
        body_ar=body_ar,
        data={
            "horse_id": str(horse.id),
            "offer_id": str(offer.id),
            "type": event_type,
        },
    )


def get_offer_actor(offer: Offer, current_user: User) -> str:
    if current_user.id == offer.seller_id:
        return "seller"
    if current_user.id == offer.buyer_id:
        return "buyer"
    return "unknown"


def add_offer_transition_audit(
    db: AsyncSession,
    offer: Offer,
    from_status: OfferStatus,
    to_status: OfferStatus,
    actor: str,
    changed_by_user_id: uuid.UUID | None,
    response_message: str | None,
) -> None:
    db.add(
        OfferTransitionAudit(
            offer_id=offer.id,
            changed_by_user_id=changed_by_user_id,
            from_status=from_status.value,
            to_status=to_status.value,
            actor=actor,
            response_message=response_message,
        )
    )


async def persist_offer_transition(
    db: AsyncSession,
    offer: Offer,
    to_status: OfferStatus,
    actor: str,
    changed_by_user_id: uuid.UUID | None,
    response_message: str | None = None,
    counter_amount: float | None = None,
    *,
    commit: bool = True,
    refresh: bool = True,
) -> Offer:
    from_status = offer.status

    apply_offer_transition(
        offer=offer,
        to_status=to_status,
        actor=actor,
        response_message=response_message,
        counter_amount=counter_amount,
    )
    db.add(offer)
    add_offer_transition_audit(
        db=db,
        offer=offer,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        changed_by_user_id=changed_by_user_id,
        response_message=response_message,
    )

    if commit:
        await db.commit()
    if refresh:
        await db.refresh(offer)

    return offer


def sanitize_idempotency_key(idempotency_key: str | None) -> str | None:
    if idempotency_key is None:
        return None
    value = idempotency_key.strip()
    return value or None


async def get_idempotent_replay(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    idempotency_key: str | None,
) -> dict | None:
    key = sanitize_idempotency_key(idempotency_key)
    if not key:
        return None

    result = await db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.request_key == key,
            IdempotencyKey.action == action,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return None

    try:
        parsed = json.loads(record.response_body)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


async def store_idempotent_replay(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    idempotency_key: str | None,
    response_payload: dict,
) -> None:
    key = sanitize_idempotency_key(idempotency_key)
    if not key:
        return

    existing = await db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.request_key == key,
            IdempotencyKey.action == action,
        )
    )
    if existing.scalar_one_or_none():
        return

    db.add(
        IdempotencyKey(
            user_id=user_id,
            request_key=key,
            action=action,
            response_body=json.dumps(response_payload),
        )
    )


async def finalize_idempotent_replay(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    idempotency_key: str | None,
    response_payload: dict,
) -> None:
    await store_idempotent_replay(db, user_id, action, idempotency_key, response_payload)
    if sanitize_idempotency_key(idempotency_key):
        await db.commit()


async def load_offer_context(
    db: AsyncSession,
    offer: Offer,
    *,
    horse: Horse | None = None,
) -> tuple[User, User, Horse]:
    buyer_result = await db.execute(select(User).where(User.id == offer.buyer_id))
    buyer = buyer_result.scalar_one()

    seller_result = await db.execute(select(User).where(User.id == offer.seller_id))
    seller = seller_result.scalar_one()

    resolved_horse = horse
    if resolved_horse is None:
        horse_result = await db.execute(select(Horse).where(Horse.id == offer.horse_id))
        resolved_horse = horse_result.scalar_one()

    return buyer, seller, resolved_horse


async def build_offer_response(db: AsyncSession, offer: Offer) -> OfferResponse:
    buyer, seller, horse = await load_offer_context(db, offer)

    return OfferResponse(
        id=offer.id,
        buyer_id=offer.buyer_id,
        seller_id=offer.seller_id,
        horse_id=offer.horse_id,
        amount=offer.amount,
        counter_amount=offer.counter_amount,
        status=offer.status.value,
        message=offer.message,
        response_message=offer.response_message,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
        responded_at=offer.responded_at,
        buyer_email=buyer.email,
        seller_email=seller.email,
        horse_title=horse.title,
    )


def apply_offer_transition(
    offer: Offer,
    to_status: OfferStatus,
    actor: str,
    response_message: str | None = None,
    counter_amount: float | None = None,
) -> None:
    """Validate and apply offer status transitions in one place."""
    current = offer.status

    allowed: dict[OfferStatus, dict[OfferStatus, set[str]]] = {
        OfferStatus.PENDING: {
            OfferStatus.COUNTERED: {"seller"},
            OfferStatus.ACCEPTED: {"seller"},
            OfferStatus.REJECTED: {"seller"},
            OfferStatus.CANCELLED: {"buyer", "system"},
        },
        OfferStatus.COUNTERED: {
            OfferStatus.ACCEPTED: {"buyer"},
            OfferStatus.REJECTED: {"buyer", "seller"},
            OfferStatus.CANCELLED: {"system"},
        },
    }

    if current not in allowed or to_status not in allowed[current]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid offer transition: {current.value} -> {to_status.value}",
        )

    if actor not in allowed[current][to_status]:
        raise HTTPException(
            status_code=403,
            detail="Not authorized for this offer transition",
        )

    if to_status == OfferStatus.COUNTERED:
        if counter_amount is None or counter_amount <= 0:
            raise HTTPException(status_code=400, detail="Counter amount must be greater than 0")
        offer.counter_amount = counter_amount

    offer.status = to_status
    offer.response_message = response_message
    offer.responded_at = datetime.now(timezone.utc)


# ── FastAPI application ───────────────────────────────────────────────────────

app = FastAPI(
    title="Horse Marketplace API",
    description="Backend API for a Horse Selling marketplace",
    version="0.1.0",
    lifespan=lifespan,
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles
from app.config import UPLOAD_DIR

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ── Include routers ───────────────────────────────────────────────────────────
from app.media import router as media_router  # noqa: E402

app.include_router(media_router)


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post(
    "/auth/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
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
        is_verified=False,  # Start as unverified
        language=body.language,
    )
    db.add(user)
    await db.flush()  # populate user.id before creating profile

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


@app.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Authentication"],
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


@app.get(
    "/auth/verify-email",
    tags=["Authentication"],
    summary="Verify user email address",
)
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    """Verify email by token from the verification link."""
    try:
        payload = verify_token(token)
        
        # Check if it's a verification token
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
        
        # Find user and mark as verified
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
        
        # Mark as verified
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


@app.post(
    "/auth/send-otp",
    status_code=status.HTTP_200_OK,
    tags=["Authentication"],
    summary="Send OTP for email verification",
)
@limiter.limit("5/minute")
async def send_otp(request: Request, body: OTPRequest, db: AsyncSession = Depends(get_db)):
    # Find user
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
    
    # Generate 6-digit OTP
    otp = ''.join(random.choices(string.digits, k=6))
    
    # Set expiry (10 minutes)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    
    user.verification_code = hash_password(otp)  # Store hashed, never plain-text
    user.verification_code_expires_at = expires_at
    
    await db.commit()
    
    # Send email (plain otp — never stored)
    email_sent = send_otp_email(user.email, otp, user.language)
    if not email_sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not send OTP email. Please check SMTP configuration.",
        )
    
    return {"message": "OTP sent successfully"}


@app.post(
    "/auth/verify-otp",
    status_code=status.HTTP_200_OK,
    tags=["Authentication"],
    summary="Verify email with OTP",
)
async def verify_otp(body: VerifyOTPRequest, db: AsyncSession = Depends(get_db)):
    # Find user
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
        
    # Check expiry
    if datetime.now(timezone.utc) > user.verification_code_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP expired",
        )
        
    # Check match (compare against stored hash)
    if not verify_password(body.otp, user.verification_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP",
        )
        
    # Mark verified and clear OTP
    user.is_verified = True
    user.verification_code = None
    user.verification_code_expires_at = None
    
    await db.commit()
    
    return {"message": "Email verified successfully"}









# ── User Profile endpoints ────────────────────────────────────────────────────

@app.get(
    "/api/v1/profile",
    response_model=UserResponse,
    tags=["User"],
    summary="Get current user profile",
)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(User).where(User.id == current_user.id).options(selectinload(User.profile))
    result = await db.execute(query)
    user = result.scalar_one()
    return user


@app.put(
    "/api/v1/profile",
    response_model=UserResponse,
    tags=["User"],
    summary="Update user profile",
)
async def update_profile_endpoint(
    body: UserProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(User).where(User.id == current_user.id).options(selectinload(User.profile))
    result = await db.execute(query)
    user = result.scalar_one()
    
    if body.role:
        # Prevent Admin self-demotion
        if user.role != UserRole.ADMIN:
            user.role = UserRole(body.role)
    
    if not user.profile:
        user.profile = UserProfile(user_id=user.id)
    
    if body.first_name is not None:
        user.profile.first_name = body.first_name
    if body.last_name is not None:
        user.profile.last_name = body.last_name
    if body.phone_number is not None:
        # Check uniqueness if changing
        if user.profile.phone_number != body.phone_number:
            stmt = select(UserProfile).where(UserProfile.phone_number == body.phone_number)
            result_phone = await db.execute(stmt)
            if result_phone.scalar_one_or_none():
                 raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This phone number is already in use",
                )
        user.profile.phone_number = body.phone_number
    if body.location is not None:
        user.profile.location = body.location

    if body.language is not None:
        user.language = body.language
        
    await db.commit()
    
    # Reload user with profile to ensure relationship is loaded for response model
    # db.refresh(user) might not load relationships in async
    result = await db.execute(
        select(User).where(User.id == user.id).options(selectinload(User.profile))
    )
    user = result.scalar_one()
    return user


# ── Admin endpoints ───────────────────────────────────────────────────────────

@app.get(
    "/api/v1/admin/users",
    response_model=UserListResponse,
    tags=["Admin"],
    summary="List all users (Admin only)",
)
async def admin_list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
):
    base_query = select(User).options(selectinload(User.profile))
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    query = base_query.order_by(User.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return UserListResponse(total=total, users=users)


@app.get(
    "/api/v1/admin/security/status",
    response_model=AdminSecurityStatusResponse,
    tags=["Admin"],
    summary="Get non-sensitive admin security status flags",
)
async def admin_security_status(
    admin: User = Depends(get_current_admin),
):
    return AdminSecurityStatusResponse(
        purge_confirm_token_strong=is_purge_confirm_token_strong(),
        expiry_purge_enabled=SOFT_DELETE_RESTORE_DAYS > 0,
        restore_window_days=SOFT_DELETE_RESTORE_DAYS,
    )


@app.put(
    "/api/v1/admin/users/{user_id}/role",
    response_model=UserResponse,
    tags=["Admin"],
    summary="Update user role (Admin only)",
)
async def admin_update_user_role(
    user_id: uuid.UUID,
    body: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    # Fetch target user
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent editing other admins
    if target_user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=403, 
            detail="Cannot modify the role of another Administrator."
        )

    # Update role
    target_user.role = UserRole(body.role)
    await db.commit()
    await db.refresh(target_user)
    return target_user


@app.put(
    "/api/v1/admin/users/{user_id}",
    response_model=UserResponse,
    tags=["Admin"],
    summary="Update full user details (Admin only)",
)
async def admin_update_user_details(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    # Fetch target user
    stmt = select(User).where(User.id == user_id).options(selectinload(User.profile))
    result = await db.execute(stmt)
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent editing other admins (optional policy, but good for safety)
    # Allows editing self though? Or should we block editing other admins wholly?
    # Requirement: "admin should have the ability to edit the entire user information"
    # Let's allow editing anyone, except maybe demoting other admins if we want to be strict.
    # But usually "edit entire user information" implies broad power. 
    # Let's stick to: if target is ANOTHER admin, block role change, but allow other edits?
    # Simplifying: just block role change for other admins to prevent takeover/accidents.

    if target_user.role == UserRole.ADMIN and target_user.id != admin.id:
         if body.role and body.role != "admin":
             raise HTTPException(status_code=403, detail="Cannot demote another Administrator.")

    # Update User fields
    if body.role:
        target_user.role = UserRole(body.role)
    
    if body.is_verified is not None:
        target_user.is_verified = body.is_verified

    # Update Profile fields
    if not target_user.profile:
        target_user.profile = UserProfile(user_id=target_user.id)
    
    if body.phone_number is not None:
        if target_user.profile.phone_number != body.phone_number:
            stmt = select(UserProfile).where(UserProfile.phone_number == body.phone_number)
            result_phone = await db.execute(stmt)
            if result_phone.scalar_one_or_none():
                 raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This phone number is already in use",
                )
        target_user.profile.phone_number = body.phone_number
        
    if body.location is not None:
        target_user.profile.location = body.location

    await db.commit()
    
    # Refresh to return full data
    result = await db.execute(stmt)
    target_user = result.scalar_one()
    
    return target_user


@app.get(
    "/api/v1/admin/listings",
    response_model=ListingListResponse,
    tags=["Admin"],
    summary="List all listings (Admin only)",
)
async def admin_list_listings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
):
    base_query = select(Horse).options(
        selectinload(Horse.owner).selectinload(User.profile),
        selectinload(Horse.images)
    ).where(Horse.deleted_at.is_(None))
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    query = base_query.order_by(Horse.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    listings = result.scalars().all()
    return ListingListResponse(total=total, listings=listings)


@app.get(
    "/api/v1/admin/listings/pending",
    response_model=ListingListResponse,
    tags=["Admin"],
    summary="List pending listings for review (Admin only)",
)
async def admin_list_pending_listings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
):
    base_query = select(Horse).where(
        Horse.status == "pending_review",
        Horse.deleted_at.is_(None)
    ).options(
        selectinload(Horse.owner).selectinload(User.profile),
        selectinload(Horse.images)
    )
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    query = base_query.order_by(Horse.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    listings = result.scalars().all()
    return ListingListResponse(total=total, listings=listings)


@app.get(
    "/api/v1/admin/listings/deleted",
    response_model=ListingListResponse,
    tags=["Admin"],
    summary="List soft-deleted listings (Admin only)",
)
async def admin_list_deleted_listings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
):
    base_query = select(Horse).where(Horse.deleted_at.is_not(None)).options(
        selectinload(Horse.owner).selectinload(User.profile),
        selectinload(Horse.images)
    )
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    # Nearest expiry first: oldest deletions are closest to expiry when a fixed restore window is used.
    if SOFT_DELETE_RESTORE_DAYS > 0:
        query = base_query.order_by(Horse.deleted_at.asc()).offset(skip).limit(limit)
    else:
        # Unlimited restore window: keep most recently deleted first.
        query = base_query.order_by(Horse.deleted_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    listings = result.scalars().all()
    return ListingListResponse(
        total=total,
        listings=listings,
        restore_window_days=SOFT_DELETE_RESTORE_DAYS,
    )


@app.delete(
    "/api/v1/admin/listings/deleted/expired",
    response_model=PurgeDeletedListingsResponse,
    tags=["Admin"],
    summary="Purge expired soft-deleted listings (Admin only)",
)
async def admin_purge_expired_deleted_listings(
    confirm_token: str = Query(..., description="Confirmation token. Must be PURGE"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    if confirm_token != PURGE_CONFIRM_TOKEN:
        raise HTTPException(status_code=400, detail="Invalid confirmation token")

    if SOFT_DELETE_RESTORE_DAYS <= 0:
        raise HTTPException(
            status_code=400,
            detail="Restore window is unlimited; expiry-based purge is disabled",
        )

    cutoff_at = datetime.now(timezone.utc) - timedelta(days=SOFT_DELETE_RESTORE_DAYS)
    result = await db.execute(
        select(Horse).where(
            Horse.deleted_at.is_not(None),
            Horse.deleted_at <= cutoff_at,
        )
    )
    listings_to_purge = result.scalars().all()

    purged_count = 0
    for listing in listings_to_purge:
        await db.delete(listing)
        purged_count += 1

    await db.commit()
    return PurgeDeletedListingsResponse(
        purged_count=purged_count,
        retention_days=SOFT_DELETE_RESTORE_DAYS,
        cutoff_at=cutoff_at,
    )


@app.post(
    "/api/v1/admin/listings/bulk/restore",
    response_model=BulkRestoreListingsResponse,
    tags=["Admin"],
    summary="Bulk restore soft-deleted listings (Admin only)",
)
async def admin_bulk_restore_listings(
    request: BulkRestoreListingsRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Restore multiple soft-deleted listings in bulk."""
    unique_horse_ids = list(dict.fromkeys(request.horse_ids))
    restored_count = 0
    failed_count = 0
    expired_count = 0
    already_active_count = 0
    now = datetime.now(timezone.utc)
    
    for horse_id in unique_horse_ids:
        try:
            result = await db.execute(select(Horse).where(Horse.id == horse_id))
            horse = result.scalar_one_or_none()
            
            if not horse:
                failed_count += 1
                continue
            
            if horse.deleted_at is None:
                already_active_count += 1
                continue
            
            if SOFT_DELETE_RESTORE_DAYS > 0:
                restore_deadline = horse.deleted_at + timedelta(days=SOFT_DELETE_RESTORE_DAYS)
                if now > restore_deadline:
                    expired_count += 1
                    continue
            
            horse.deleted_at = None
            review = ListingReview(
                horse_id=horse.id,
                admin_id=admin.id,
                action="restore",
                reason="soft_delete_restore",
            )
            db.add(horse)
            db.add(review)
            restored_count += 1
        except Exception:
            failed_count += 1
    
    await db.commit()
    return BulkRestoreListingsResponse(
        restored_count=restored_count,
        failed_count=failed_count,
        expired_count=expired_count,
        already_active_count=already_active_count,
    )


@app.post(
    "/api/v1/admin/listings/bulk/purge",
    response_model=BulkPurgeDeletedListingsResponse,
    tags=["Admin"],
    summary="Bulk purge soft-deleted listings (Admin only)",
)
async def admin_bulk_purge_deleted_listings(
    request: BulkPurgeDeletedListingsRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Permanently delete multiple soft-deleted listings in bulk."""
    if request.confirm_token != PURGE_CONFIRM_TOKEN:
        raise HTTPException(status_code=400, detail="Invalid confirmation token")

    if SOFT_DELETE_RESTORE_DAYS <= 0:
        raise HTTPException(
            status_code=400,
            detail="Restore window is unlimited; expiry-based purge is disabled",
        )

    unique_horse_ids = list(dict.fromkeys(request.horse_ids))
    purged_count = 0
    not_deleted_count = 0
    not_expired_count = 0
    now = datetime.now(timezone.utc)
    cutoff_at = now - timedelta(days=SOFT_DELETE_RESTORE_DAYS)
    
    for horse_id in unique_horse_ids:
        try:
            result = await db.execute(select(Horse).where(Horse.id == horse_id))
            horse = result.scalar_one_or_none()
            
            if not horse:
                not_deleted_count += 1
                continue
            
            if horse.deleted_at is None:
                not_deleted_count += 1
                continue
            
            if SOFT_DELETE_RESTORE_DAYS > 0 and horse.deleted_at > cutoff_at:
                not_expired_count += 1
                continue
            
            await db.delete(horse)
            purged_count += 1
        except Exception:
            not_deleted_count += 1
    
    await db.commit()
    return BulkPurgeDeletedListingsResponse(
        purged_count=purged_count,
        not_deleted_count=not_deleted_count,
        not_expired_count=not_expired_count,
    )


@app.post(
    "/api/v1/admin/listings/{horse_id}/approve",
    response_model=HorseResponse,
    tags=["Admin"],
    summary="Approve a horse listing (Admin only)",
)
async def admin_approve_listing(
    horse_id: uuid.UUID,
    body: AdminApproveListingRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    # Get the horse listing
    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse_id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images)
        )
    )
    horse = result.scalar_one_or_none()
    
    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")
    
    if horse.status != "pending_review":
        raise HTTPException(status_code=400, detail="Only pending listings can be approved")
    
    # Update status to approved
    horse.status = "approved"
    horse.rejection_reason = None  # Clear any previous rejection reason
    review = ListingReview(
        horse_id=horse.id,
        admin_id=admin.id,
        action="approve",
        reason=None,
    )
    db.add(horse)
    db.add(review)
    await db.commit()
    await db.refresh(horse)
    
    # Send approval email to seller
    seller = horse.owner
    send_listing_approved_email(seller.email, horse.title, seller.language)

    # Notify buyers with matching saved searches
    searches_result = await db.execute(
        select(SavedSearch, User)
        .join(User, User.id == SavedSearch.user_id)
        .where(
            SavedSearch.is_active == True,
            SavedSearch.user_id != seller.id,
        )
    )
    for saved_search, buyer in searches_result.all():
        if not matches_saved_search(horse, saved_search):
            continue

        app_alert = SavedSearchAlert(
            user_id=buyer.id,
            saved_search_id=saved_search.id,
            horse_id=horse.id,
            title=(
                "حصان جديد يطابق تنبيهك" if buyer.language == "ar" else "New horse matches your alert"
            ),
            message=f"{horse.title} ({horse.breed})",
            is_read=False,
        )
        db.add(app_alert)

        sent = send_saved_search_match_email(
            user_email=buyer.email,
            horse_title=horse.title,
            horse_breed=horse.breed,
            horse_price=horse.price,
            search_name=saved_search.name,
            language=buyer.language,
        )
        if sent:
            saved_search.last_alerted_at = datetime.now(timezone.utc)
            db.add(saved_search)

    await db.commit()
    
    return horse


@app.post(
    "/api/v1/admin/listings/{horse_id}/reject",
    response_model=HorseResponse,
    tags=["Admin"],
    summary="Reject a horse listing with reason (Admin only)",
)
async def admin_reject_listing(
    horse_id: uuid.UUID,
    body: AdminRejectListingRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    # Get the horse listing
    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse_id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images)
        )
    )
    horse = result.scalar_one_or_none()
    
    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")
    
    if horse.status != "pending_review":
        raise HTTPException(status_code=400, detail="Only pending listings can be rejected")
    
    # Update status to rejected and store reason
    horse.status = "rejected"
    horse.rejection_reason = body.reason
    review = ListingReview(
        horse_id=horse.id,
        admin_id=admin.id,
        action="reject",
        reason=body.reason,
    )
    db.add(horse)
    db.add(review)
    await db.commit()
    await db.refresh(horse)
    
    # Send rejection email to seller
    seller = horse.owner
    send_listing_rejected_email(seller.email, horse.title, body.reason, seller.language)
    
    return horse


@app.get(
    "/api/v1/admin/reviews",
    response_model=list[ListingReviewResponse],
    tags=["Admin"],
    summary="List moderation reviews (Admin only)",
)
async def admin_list_reviews(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    result = await db.execute(
        select(ListingReview, User.email)
        .join(User, User.id == ListingReview.admin_id)
        .order_by(ListingReview.created_at.desc())
    )
    rows = result.all()

    return [
        ListingReviewResponse(
            id=review.id,
            horse_id=review.horse_id,
            admin_id=review.admin_id,
            admin_email=admin_email,
            action=review.action,
            reason=review.reason,
            created_at=review.created_at,
        )
        for review, admin_email in rows
    ]


# ── Horse listing endpoints ───────────────────────────────────────────────────

@app.get(
    "/api/v1/horses",
    response_model=HorseListResponse,
    tags=["Horses"],
    summary="List horses with optional filters",
)
async def list_horses(
    db: AsyncSession = Depends(get_db),
    min_price: Optional[float] = Query(None, ge=0, description="Minimum price"),
    max_price: Optional[float] = Query(None, ge=0, description="Maximum price"),
    breed: Optional[str] = Query(None, description="Filter by breed (case-insensitive)"),
    min_age: Optional[int] = Query(None, ge=0, description="Minimum age"),
    max_age: Optional[int] = Query(None, ge=0, description="Maximum age"),
    discipline: Optional[str] = Query(None, description="Filter by discipline (case-insensitive)"),
    horse_status: Optional[str] = Query(None, description="Filter by listing status (approved, sold, pending_review, rejected)"),
    vet_check_available: Optional[bool] = Query(None, description="Filter vet-checked listings"),
    verified_seller: Optional[bool] = Query(None, description="Filter by seller verification"),
    gender: Optional[str] = Query(None, description="Filter by gender (mare, gelding, stallion)"),
    sort_by: Optional[str] = Query(None, description="Sort order: price_asc, price_desc, age_asc, age_desc (default: newest)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max records to return"),
    owner_id: Optional[uuid.UUID] = Query(None, description="Filter by owner ID"),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    # Build base query
    query = select(Horse).options(
        selectinload(Horse.owner).selectinload(User.profile),
        selectinload(Horse.images)
    )

    # Determine visibility
    # Default: Show only approved
    # Exception: User is viewing their OWN listings (owner_id matches current_user.id)
    # Exception: User is ADMIN (can see everything - optionally, but sticking to owner rule for "My Listings")
    
    show_all_statuses = False
    
    if current_user:
        if current_user.role == UserRole.ADMIN:
             # Admin can see all statuses if they want? 
             # Usually Admin uses /admin/listings. 
             # If Admin uses this endpoint, maybe they want to see what public sees?
             # But let's allow Admin to see all if they filter by owner_id or just browsing?
             # Let's stick to the requirement: "if the horse listing is rejected... kept in the list of admin... and inside should show the reason... as well as for the seller"
             # So owner needs to see it.
             pass
        
        if owner_id and owner_id == current_user.id:
            show_all_statuses = True

    if horse_status is not None:
        query = query.where(Horse.status == horse_status)
    elif not show_all_statuses:
        query = query.where(Horse.status.in_(["approved", "sold"]))

    # Exclude soft-deleted listings
    query = query.where(Horse.deleted_at.is_(None))

    # Apply dynamic filters
    if owner_id is not None:
        query = query.where(Horse.owner_id == owner_id)

    # Apply dynamic filters
    if min_price is not None:
        query = query.where(Horse.price >= min_price)
    if max_price is not None:
        query = query.where(Horse.price <= max_price)
    if breed is not None:
        query = query.where(Horse.title.ilike(f"%{breed}%") | Horse.breed.ilike(f"%{breed}%"))
    if min_age is not None:
        query = query.where(Horse.age >= min_age)
    if max_age is not None:
        query = query.where(Horse.age <= max_age)
    if discipline is not None:
        query = query.where(Horse.discipline.ilike(f"%{discipline}%"))
    if vet_check_available is not None:
        query = query.where(Horse.vet_check_available == vet_check_available)
    if verified_seller is not None:
        query = query.where(Horse.owner.has(User.is_verified == verified_seller))
    if gender is not None:
        query = query.where(Horse.gender == gender)

    # Count total matching records (before pagination)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Apply sort order then pagination
    _sort_map = {
        "price_asc": Horse.price.asc(),
        "price_desc": Horse.price.desc(),
        "age_asc": Horse.age.asc(),
        "age_desc": Horse.age.desc(),
    }
    order_col = _sort_map.get(sort_by, Horse.created_at.desc())
    query = query.order_by(order_col).offset(skip).limit(limit)
    result = await db.execute(query)
    horses = result.scalars().all()

    return HorseListResponse(total=total, horses=horses)


@app.post(
    "/api/v1/horses",
    response_model=HorseResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Horses"],
    summary="Create a new horse listing",
)
async def create_horse(
    body: HorseCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check if user email is verified
    if not current_user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before creating a listing",
        )
    
    # Determine image URLs to use
    image_urls = body.image_urls or ([body.image_url] if body.image_url else [])
    
    # Create horse with status as PENDING_REVIEW
    horse = Horse(
        owner_id=current_user.id,
        title=body.title,
        price=body.price,
        breed=body.breed,
        age=body.age,
        gender=HorseGender(body.gender),
        discipline=body.discipline,
        height=body.height,
        description=body.description,
        vet_check_available=body.vet_check_available,
        vet_certificate_url=body.vet_certificate_url,
        image_url=image_urls[0] if image_urls else None,  # Primary image for backward compatibility
        discount_type=DiscountType(body.discount_type) if body.discount_type else None,
        discount_value=body.discount_value,
        status="pending_review",
    )
    
    # Calculate discount price
    if horse.discount_type and horse.discount_value:
        if horse.discount_type == DiscountType.PERCENTAGE:
            horse.discount_price = horse.price * (1 - horse.discount_value / 100)
        elif horse.discount_type == DiscountType.FIXED:
             # Fixed means "New Reduced Price" as per user request
            horse.discount_price = horse.discount_value
    
    db.add(horse)
    await db.flush()  # Get horse.id before creating images
    
    # Create HorseImage records for all images
    for idx, url in enumerate(image_urls):
        horse_image = HorseImage(
            horse_id=horse.id,
            image_url=url,
            display_order=idx,
        )
        db.add(horse_image)
    
    await db.commit()
    
    # Reload with relationships
    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse.id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images)
        )
    )
    horse = result.scalar_one()
    
    admin_result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
    admin_users = admin_result.scalars().all()
    # Create list of dicts with email and language
    # We'll need to update the service to handle this structure
    admins_data = [{"email": admin.email, "language": admin.language} for admin in admin_users]
    
    if admins_data:
        send_pending_review_notification(admins_data, horse.title, current_user.email)
    
    return horse


@app.get(
    "/api/v1/horses/{horse_id}",
    response_model=HorseResponse,
    tags=["Horses"],
    summary="Get a specific horse listing",
)
async def get_horse(
    horse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    # Fetch horse with images and owner profile
    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse_id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images)
        )
    )
    horse = result.scalar_one_or_none()
    
    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Soft-deleted listings are hidden from public access.
    if horse.deleted_at is not None:
        is_owner = current_user and horse.owner_id == current_user.id
        is_admin = current_user and current_user.role == UserRole.ADMIN
        if not (is_owner or is_admin):
            raise HTTPException(status_code=404, detail="Listing not found")

    # Visibility logic:
    # If approved or sold: Visible to everyone.
    # If not approved: Visible only to Owner and Admin.
    if horse.status not in ["approved", "sold"]:
        is_owner = current_user and horse.owner_id == current_user.id
        is_admin = current_user and current_user.role == UserRole.ADMIN
        
        if not (is_owner or is_admin):
             # Return 404 to hide existence regarding non-public items, or 403?
             # Usually 404 is safer for "hidden" items.
             raise HTTPException(status_code=404, detail="Listing not found")

    return horse


@app.put(
    "/api/v1/horses/{horse_id}",
    response_model=HorseResponse,
    tags=["Horses"],
    summary="Update a horse listing",
)
async def update_horse(
    horse_id: uuid.UUID,
    body: HorseUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Fetch horse with images
    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse_id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images)
        )
    )
    horse = result.scalar_one_or_none()
    
    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Check permissions: Owner OR Admin
    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to edit this listing")

    # Update fields
    if body.title is not None:
        horse.title = body.title
    if body.price is not None:
        horse.price = body.price
    if body.breed is not None:
        horse.breed = body.breed
    if body.age is not None:
        horse.age = body.age
    if body.gender is not None:
        horse.gender = HorseGender(body.gender)
    if body.discipline is not None:
        horse.discipline = body.discipline
    if body.height is not None:
        horse.height = body.height
    if body.description is not None:
        horse.description = body.description
    if body.vet_check_available is not None:
        horse.vet_check_available = body.vet_check_available
    if body.vet_certificate_url is not None:
        horse.vet_certificate_url = body.vet_certificate_url
    
    # Update discount fields
    if body.discount_type is not None:
        horse.discount_type = DiscountType(body.discount_type) if body.discount_type else None
    if body.discount_value is not None:
        horse.discount_value = body.discount_value
        
    # Recalculate discount price if any relevant field changed (price, type, value)
    # We check if they are set on the object
    if horse.discount_type and horse.discount_value:
        if horse.discount_type == DiscountType.PERCENTAGE:
            horse.discount_price = horse.price * (1 - horse.discount_value / 100)
        elif horse.discount_type == DiscountType.FIXED:
             # Fixed means "New Reduced Price" as per user request
             # Ensure discount price is not higher than original? (Optional logic, but good practice)
            horse.discount_price = horse.discount_value
    else:
        # If discount removed or incomplete
        horse.discount_price = None

    # Handle image updates
    if body.image_urls is not None:
        # Delete existing images
        for img in horse.images:
            await db.delete(img)
        await db.flush()
        
        # Create new images
        for idx, url in enumerate(body.image_urls):
            horse_image = HorseImage(
                horse_id=horse.id,
                image_url=url,
                display_order=idx,
            )
            db.add(horse_image)
        
        # Update primary image_url for backward compatibility
        horse.image_url = body.image_urls[0] if body.image_urls else None
    elif body.image_url is not None:
        # Backward compatibility: single image_url update
        horse.image_url = body.image_url

    await db.commit()
    
    # Reload with relationships
    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse.id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images)
        )
    )
    horse = result.scalar_one()
    
    return horse


@app.post(
    "/api/v1/horses/{horse_id}/reopen",
    response_model=HorseResponse,
    tags=["Horses"],
    summary="Reopen a sold horse listing",
)
async def reopen_horse_listing(
    horse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    action = f"horse:{horse_id}:reopen"
    replay = await get_idempotent_replay(db, current_user.id, action, idempotency_key)
    if replay is not None:
        return replay

    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse_id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images),
        )
    )
    horse = result.scalar_one_or_none()

    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")

    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to reopen this listing")

    if horse.status == "approved":
        payload = HorseResponse.model_validate(horse).model_dump(mode="json")
        await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
        return horse

    if horse.status != "sold":
        raise HTTPException(status_code=400, detail="Only sold listings can be reopened")

    horse.status = "approved"
    db.add(horse)
    payload = HorseResponse.model_validate(horse).model_dump(mode="json")
    await store_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
    await db.commit()
    await db.refresh(horse)
    return horse


@app.delete(
    "/api/v1/horses/{horse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Horses"],
    summary="Delete a horse listing (soft delete)",
)
async def delete_horse(
    horse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Horse).where(Horse.id == horse_id))
    horse = result.scalar_one_or_none()

    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")

    # Check permissions: Owner OR Admin
    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to delete this listing")

    # Soft delete: set deleted_at timestamp
    horse.deleted_at = datetime.now(timezone.utc)
    review = ListingReview(
        horse_id=horse.id,
        admin_id=current_user.id,
        action="delete",
        reason="soft_delete",
    )
    db.add(horse)
    db.add(review)
    await db.commit()


@app.post(
    "/api/v1/horses/{horse_id}/restore",
    response_model=HorseResponse,
    tags=["Horses"],
    summary="Restore a soft-deleted horse listing",
)
async def restore_horse(
    horse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Horse)
        .where(Horse.id == horse_id)
        .options(
            selectinload(Horse.owner).selectinload(User.profile),
            selectinload(Horse.images),
        )
    )
    horse = result.scalar_one_or_none()

    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")

    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to restore this listing")

    if horse.deleted_at is None:
        return horse

    if SOFT_DELETE_RESTORE_DAYS > 0:
        restore_deadline = horse.deleted_at + timedelta(days=SOFT_DELETE_RESTORE_DAYS)
        if datetime.now(timezone.utc) > restore_deadline:
            raise HTTPException(status_code=410, detail="Restore window expired")

    horse.deleted_at = None
    review = ListingReview(
        horse_id=horse.id,
        admin_id=current_user.id,
        action="restore",
        reason="soft_delete_restore",
    )
    db.add(horse)
    db.add(review)
    await db.commit()
    await db.refresh(horse)
    return horse


# ── Favorite endpoints ────────────────────────────────────────────────────────

@app.post(
    "/api/v1/favorites",
    response_model=FavoriteResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Favorites"],
    summary="Add a horse to favorites",
)
async def add_favorite(
    body: AddFavoriteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check if horse exists
    result = await db.execute(select(Horse).where(Horse.id == body.horse_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Horse not found")
    
    # Check if already favorited
    result = await db.execute(
        select(Favorite).where(
            (Favorite.user_id == current_user.id) & (Favorite.horse_id == body.horse_id)
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Horse already in favorites")
    
    favorite = Favorite(user_id=current_user.id, horse_id=body.horse_id)
    db.add(favorite)
    await db.commit()
    await db.refresh(favorite)
    
    return favorite


@app.delete(
    "/api/v1/favorites/{horse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Favorites"],
    summary="Remove a horse from favorites",
)
async def remove_favorite(
    horse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Favorite).where(
            (Favorite.user_id == current_user.id) & (Favorite.horse_id == horse_id)
        )
    )
    favorite = result.scalar_one_or_none()
    
    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")
    
    await db.delete(favorite)
    await db.commit()


@app.get(
    "/api/v1/favorites",
    response_model=HorseListResponse,
    tags=["Favorites"],
    summary="Get user's favorite horses",
)
async def get_favorites(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Get all favorite horse_ids for current user
    result = await db.execute(
        select(Favorite.horse_id).where(Favorite.user_id == current_user.id)
    )
    favorite_ids = [row[0] for row in result.fetchall()]
    
    if not favorite_ids:
        return HorseListResponse(total=0, horses=[])
    
    # Get all horses with these IDs
    result = await db.execute(
        select(Horse)
        .where(Horse.id.in_(favorite_ids))
        .options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images))
        .order_by(Horse.created_at.desc())
    )
    horses = result.scalars().all()
    
    return HorseListResponse(total=len(horses), horses=horses)


@app.get(
    "/api/v1/horses/{horse_id}/is-favorite",
    tags=["Favorites"],
    summary="Check if horse is favorited by current user",
)
async def is_favorite(
    horse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Favorite).where(
            (Favorite.user_id == current_user.id) & (Favorite.horse_id == horse_id)
        )
    )
    favorite = result.scalar_one_or_none()
    
    return {"is_favorite": favorite is not None}


# ── Notification endpoints ───────────────────────────────────────────────────

@app.post(
    "/api/v1/notifications/push-token",
    status_code=status.HTTP_200_OK,
    tags=["Notifications"],
    summary="Register or refresh a device push token",
)
async def register_push_token(
    body: PushTokenRegisterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(PushToken).where(PushToken.token == body.token))
    token_row = result.scalar_one_or_none()

    if token_row:
        token_row.user_id = current_user.id
        token_row.platform = body.platform
        token_row.is_active = True
        token_row.last_seen_at = datetime.now(timezone.utc)
        db.add(token_row)
    else:
        db.add(
            PushToken(
                user_id=current_user.id,
                token=body.token,
                platform=body.platform,
                is_active=True,
                last_seen_at=datetime.now(timezone.utc),
            )
        )

    await db.commit()
    return {"message": "Push token registered"}


@app.post(
    "/api/v1/notifications/push-token/unregister",
    status_code=status.HTTP_200_OK,
    tags=["Notifications"],
    summary="Unregister a device push token",
)
async def unregister_push_token(
    body: PushTokenUnregisterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PushToken).where(
            PushToken.user_id == current_user.id,
            PushToken.token == body.token,
        )
    )
    token_row = result.scalar_one_or_none()
    if token_row:
        token_row.is_active = False
        token_row.last_seen_at = datetime.now(timezone.utc)
        db.add(token_row)
        await db.commit()

    return {"message": "Push token unregistered"}


# ── Saved Search endpoints ───────────────────────────────────────────────────

@app.post(
    "/api/v1/saved-searches",
    response_model=SavedSearchResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Saved Searches"],
    summary="Create a saved search alert",
)
async def create_saved_search(
    body: SavedSearchCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    saved_search = SavedSearch(
        user_id=current_user.id,
        name=body.name,
        breed=body.breed,
        discipline=body.discipline,
        gender=body.gender,
        min_price=body.min_price,
        max_price=body.max_price,
        min_age=body.min_age,
        max_age=body.max_age,
        vet_check_available=body.vet_check_available,
        verified_seller=body.verified_seller,
        is_active=body.is_active,
    )
    db.add(saved_search)
    await db.commit()
    await db.refresh(saved_search)
    return saved_search


@app.get(
    "/api/v1/saved-searches",
    response_model=list[SavedSearchResponse],
    tags=["Saved Searches"],
    summary="List my saved search alerts",
)
async def list_saved_searches(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearch)
        .where(SavedSearch.user_id == current_user.id)
        .order_by(SavedSearch.created_at.desc())
    )
    return result.scalars().all()


@app.put(
    "/api/v1/saved-searches/{saved_search_id}",
    response_model=SavedSearchResponse,
    tags=["Saved Searches"],
    summary="Update a saved search alert",
)
async def update_saved_search(
    saved_search_id: uuid.UUID,
    body: SavedSearchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearch).where(
            SavedSearch.id == saved_search_id,
            SavedSearch.user_id == current_user.id,
        )
    )
    saved_search = result.scalar_one_or_none()
    if not saved_search:
        raise HTTPException(status_code=404, detail="Saved search not found")

    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(saved_search, key, value)

    db.add(saved_search)
    await db.commit()
    await db.refresh(saved_search)
    return saved_search


@app.delete(
    "/api/v1/saved-searches/{saved_search_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Saved Searches"],
    summary="Delete a saved search alert",
)
async def delete_saved_search(
    saved_search_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearch).where(
            SavedSearch.id == saved_search_id,
            SavedSearch.user_id == current_user.id,
        )
    )
    saved_search = result.scalar_one_or_none()
    if not saved_search:
        raise HTTPException(status_code=404, detail="Saved search not found")

    await db.delete(saved_search)
    await db.commit()


@app.get(
    "/api/v1/saved-searches/{saved_search_id}/matches",
    response_model=HorseListResponse,
    tags=["Saved Searches"],
    summary="Get horses matching a saved search",
)
async def get_saved_search_matches(
    saved_search_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearch).where(
            SavedSearch.id == saved_search_id,
            SavedSearch.user_id == current_user.id,
        )
    )
    saved_search = result.scalar_one_or_none()
    if not saved_search:
        raise HTTPException(status_code=404, detail="Saved search not found")

    horses_result = await db.execute(
        select(Horse)
        .where(Horse.status == "approved")
        .options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images))
        .order_by(Horse.created_at.desc())
    )
    all_horses = horses_result.scalars().all()
    matched = [h for h in all_horses if matches_saved_search(h, saved_search)]
    return HorseListResponse(total=len(matched), horses=matched)


@app.get(
    "/api/v1/saved-search-alerts",
    response_model=list[SavedSearchAlertResponse],
    tags=["Saved Searches"],
    summary="List my saved search inbox alerts",
)
async def list_saved_search_alerts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearchAlert)
        .where(SavedSearchAlert.user_id == current_user.id)
        .order_by(SavedSearchAlert.created_at.desc())
        .limit(100)
    )
    return result.scalars().all()


@app.get(
    "/api/v1/saved-search-alerts/unread-count",
    response_model=SavedSearchUnreadCountResponse,
    tags=["Saved Searches"],
    summary="Get unread saved search alerts count",
)
async def saved_search_alerts_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(func.count())
        .select_from(SavedSearchAlert)
        .where(
            SavedSearchAlert.user_id == current_user.id,
            SavedSearchAlert.is_read == False,
        )
    )
    unread = result.scalar() or 0
    return SavedSearchUnreadCountResponse(unread_count=unread)


@app.post(
    "/api/v1/saved-search-alerts/{alert_id}/read",
    response_model=SavedSearchAlertResponse,
    tags=["Saved Searches"],
    summary="Mark one saved search alert as read",
)
async def mark_saved_search_alert_read(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearchAlert).where(
            SavedSearchAlert.id == alert_id,
            SavedSearchAlert.user_id == current_user.id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.is_read = True
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


@app.post(
    "/api/v1/saved-search-alerts/read-all",
    response_model=SavedSearchUnreadCountResponse,
    tags=["Saved Searches"],
    summary="Mark all saved search alerts as read",
)
async def mark_all_saved_search_alerts_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearchAlert).where(
            SavedSearchAlert.user_id == current_user.id,
            SavedSearchAlert.is_read == False,
        )
    )
    alerts = result.scalars().all()
    for alert in alerts:
        alert.is_read = True
        db.add(alert)

    await db.commit()
    return SavedSearchUnreadCountResponse(unread_count=0)


# ── Voucher endpoints ─────────────────────────────────────────────────────────

@app.post(
    "/api/v1/vouchers",
    response_model=VoucherResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Vouchers"],
    summary="Create a voucher (Admin only)",
)
async def create_voucher(
    body: VoucherCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    # Check uniqueness
    result = await db.execute(select(Voucher).where(Voucher.code == body.code))
    if result.scalar_one_or_none():
         raise HTTPException(status_code=409, detail="Voucher code already exists")

    voucher = Voucher(
        code=body.code,
        discount_type=DiscountType(body.discount_type),
        discount_value=body.discount_value,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        usage_limit=body.usage_limit,
        is_active=body.is_active,
    )
    db.add(voucher)
    await db.commit()
    await db.refresh(voucher)
    return voucher


@app.get(
    "/api/v1/vouchers",
    response_model=list[VoucherResponse],
    tags=["Vouchers"],
    summary="List all vouchers (Admin only)",
)
async def list_vouchers(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    result = await db.execute(select(Voucher).order_by(Voucher.created_at.desc()))
    return result.scalars().all()


@app.post(
    "/api/v1/vouchers/validate",
    response_model=VoucherValidateResponse,
    tags=["Vouchers"],
    summary="Validate a voucher code",
)
async def validate_voucher(
    body: VoucherValidateRequest,
    db: AsyncSession = Depends(get_db),
    # Optional: require login? User didn't specify, but usually yes. Let's allow public for now or assume user logic handles it.
    # User said "validate if the voucher is valid... showing the user". 
):
    stmt = select(Voucher).where(Voucher.code == body.code)
    result = await db.execute(stmt)
    voucher = result.scalar_one_or_none()
    
    if not voucher:
        return VoucherValidateResponse(valid=False, message="Invalid voucher code")
        
    if not voucher.is_active:
         return VoucherValidateResponse(valid=False, message="Voucher is inactive")
         
    now = datetime.now(timezone.utc)
    if voucher.valid_from and voucher.valid_from > now:
         return VoucherValidateResponse(valid=False, message="Voucher is not yet active")
         
    if voucher.valid_until and voucher.valid_until < now:
         return VoucherValidateResponse(valid=False, message="Voucher has expired")
         
    if voucher.usage_limit is not None and voucher.used_count >= voucher.usage_limit:
         return VoucherValidateResponse(valid=False, message="Voucher usage limit reached")

    # Calculate potential discount if price context provided
    new_price = None
    applied_discount_value = None
    
    if body.current_price is not None:
        if voucher.discount_type == DiscountType.PERCENTAGE:
            applied_discount_value = body.current_price * (voucher.discount_value / 100)
            new_price = body.current_price - applied_discount_value
        elif voucher.discount_type == DiscountType.FIXED:
             # For voucher, fixed usually means "amount off" OR "fixed price"? 
             # Usually vouchers are "amount off" (e.g. $10 off).
             # Listing Discount was "Fixed Price" (override).
             # Let's assume Voucher Fixed = Amount Off for now, as that's standard for checked out items.
             # Wait, user said "voucher... apply the discount and showing the user... the discounted price".
             # If I have a $1000 horse and $100 off voucher -> $900.
             # If I have a $1000 horse and Fixed Price $500 voucher -> $500.
             # "Discount" context usually implies "Off".
             # But let's look at "Discount" logic I used for listings: "Fixed" = "New Reduced Price".
             # For Vouchers, it's safer to assume "Fixed Amount Off" or "Percentage Off".
             # If `discount_type` is shared Enum, `FIXED` might be ambiguous.
             # Let's assume Fixed means "Amount Off" for Vouchers to be useful across different priced horses.
             # AND Fixed means "New Price" for Listings (specific item override).
             # Implementation:
             applied_discount_value = voucher.discount_value
             new_price = max(0, body.current_price - voucher.discount_value)

    return VoucherValidateResponse(
        valid=True,
        message="Voucher Applied",
        discount_type=voucher.discount_type,
        discount_value=voucher.discount_value,
        new_price=new_price
    )


# ── Offer Endpoints (Buyer Negotiation) ──────────────────────────────────────

@app.post(
    "/api/v1/horses/{horse_id}/offers",
    response_model=OfferResponse,
    tags=["Offers"],
    summary="Create a new offer on a horse",
)
async def create_offer(
    horse_id: uuid.UUID,
    body: OfferCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Get the horse
    result = await db.execute(
        select(Horse).where(Horse.id == horse_id, Horse.status == "approved")
    )
    horse = result.scalar_one_or_none()
    if not horse:
        raise HTTPException(status_code=404, detail="Horse not found")

    # Cannot offer on own listing
    if horse.owner_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot offer on your own listing")

    # Create the offer
    offer = Offer(
        buyer_id=current_user.id,
        seller_id=horse.owner_id,
        horse_id=horse_id,
        amount=body.amount,
        message=body.message,
    )
    db.add(offer)
    await db.commit()
    await db.refresh(offer)

    # Fetch full details for response
    buyer_result = await db.execute(
        select(User).where(User.id == offer.buyer_id)
    )
    buyer = buyer_result.scalar_one()
    
    seller_result = await db.execute(
        select(User).where(User.id == offer.seller_id)
    )
    seller = seller_result.scalar_one()

    await notify_offer_event(
        db=db,
        target_user=seller,
        horse=horse,
        title_en="New offer received",
        body_en=f"A buyer offered ${offer.amount:,.0f} on {horse.title}.",
        title_ar="تم استلام عرض جديد",
        body_ar=f"قام مشتري بتقديم عرض بقيمة ${offer.amount:,.0f} على {horse.title}.",
        data={"horse_id": str(horse.id), "offer_id": str(offer.id), "type": "offer_new"},
    )
    
    return OfferResponse(
        id=offer.id,
        buyer_id=offer.buyer_id,
        seller_id=offer.seller_id,
        horse_id=offer.horse_id,
        amount=offer.amount,
        counter_amount=offer.counter_amount,
        status=offer.status.value,
        message=offer.message,
        response_message=offer.response_message,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
        responded_at=offer.responded_at,
        buyer_email=buyer.email,
        seller_email=seller.email,
        horse_title=horse.title,
    )


@app.get(
    "/api/v1/offers",
    response_model=OfferHistoryResponse,
    tags=["Offers"],
    summary="List all offers (as buyer or seller)",
)
async def list_my_offers(
    role: str = Query("all", description="Filter by role: 'buyer', 'seller', or 'all'"),
    status_filter: str | None = Query(None, description="Filter by status: pending, countered, accepted, rejected, cancelled"),
    skip: int = Query(0, ge=0, description="Number of offers to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max offers to return"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(Offer)
    count_query = select(func.count()).select_from(Offer)

    if role == "buyer":
        query = query.where(Offer.buyer_id == current_user.id)
        count_query = count_query.where(Offer.buyer_id == current_user.id)
    elif role == "seller":
        query = query.where(Offer.seller_id == current_user.id)
        count_query = count_query.where(Offer.seller_id == current_user.id)
    else:  # all
        role_filter = (
            (Offer.buyer_id == current_user.id) | (Offer.seller_id == current_user.id)
        )
        query = query.where(role_filter)
        count_query = count_query.where(role_filter)

    if status_filter:
        try:
            status_enum = OfferStatus(status_filter)
            query = query.where(Offer.status == status_enum)
            count_query = count_query.where(Offer.status == status_enum)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status filter")

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        query.options(
            selectinload(Offer.buyer),
            selectinload(Offer.seller),
            selectinload(Offer.horse),
        )
        .order_by(Offer.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    offers = result.scalars().all()

    offer_responses = [
        OfferResponse(
            id=offer.id,
            buyer_id=offer.buyer_id,
            seller_id=offer.seller_id,
            horse_id=offer.horse_id,
            amount=offer.amount,
            counter_amount=offer.counter_amount,
            status=offer.status.value,
            message=offer.message,
            response_message=offer.response_message,
            created_at=offer.created_at,
            updated_at=offer.updated_at,
            responded_at=offer.responded_at,
            buyer_email=offer.buyer.email,
            seller_email=offer.seller.email,
            horse_title=offer.horse.title,
        )
        for offer in offers
    ]

    return OfferHistoryResponse(
        offers=offer_responses,
        count=len(offers),
        total=total,
        skip=skip,
        limit=limit,
        has_more=(skip + len(offers)) < total,
    )


@app.get(
    "/api/v1/horses/{horse_id}/offers",
    response_model=OfferHistoryResponse,
    tags=["Offers"],
    summary="List all offers for a specific horse (seller view)",
)
async def get_horse_offers(
    horse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify current user is the owner
    result = await db.execute(
        select(Horse).where(Horse.id == horse_id, Horse.owner_id == current_user.id)
    )
    horse = result.scalar_one_or_none()
    if not horse:
        raise HTTPException(status_code=404, detail="Horse not found or not owned by you")

    # Get all offers for this horse
    offers_result = await db.execute(
        select(Offer)
        .where(Offer.horse_id == horse_id)
        .options(
            selectinload(Offer.buyer),
            selectinload(Offer.seller),
            selectinload(Offer.horse),
        )
        .order_by(Offer.created_at.desc())
    )
    offers = offers_result.scalars().all()

    offer_responses = [
        OfferResponse(
            id=offer.id,
            buyer_id=offer.buyer_id,
            seller_id=offer.seller_id,
            horse_id=offer.horse_id,
            amount=offer.amount,
            counter_amount=offer.counter_amount,
            status=offer.status.value,
            message=offer.message,
            response_message=offer.response_message,
            created_at=offer.created_at,
            updated_at=offer.updated_at,
            responded_at=offer.responded_at,
            buyer_email=offer.buyer.email,
            seller_email=offer.seller.email,
            horse_title=offer.horse.title,
        )
        for offer in offers
    ]

    return OfferHistoryResponse(offers=offer_responses, count=len(offers))


@app.get(
    "/api/v1/admin/offers/{offer_id}/transitions",
    response_model=OfferTransitionAuditListResponse,
    tags=["Admin"],
    summary="List transition audit history for an offer (Admin only)",
)
async def list_offer_transition_audits_admin(
    offer_id: uuid.UUID,
    actor: str | None = Query(None, description="Filter by actor: buyer, seller, system"),
    to_status: str | None = Query(None, description="Filter by resulting offer status"),
    skip: int = Query(0, ge=0, description="Number of rows to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max rows to return"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    offer_result = await db.execute(select(Offer).where(Offer.id == offer_id))
    offer = offer_result.scalar_one_or_none()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    count_query = select(func.count()).select_from(OfferTransitionAudit).where(
        OfferTransitionAudit.offer_id == offer_id
    )
    logs_query = select(OfferTransitionAudit).where(OfferTransitionAudit.offer_id == offer_id)

    if actor:
        count_query = count_query.where(OfferTransitionAudit.actor == actor)
        logs_query = logs_query.where(OfferTransitionAudit.actor == actor)

    if to_status:
        count_query = count_query.where(OfferTransitionAudit.to_status == to_status)
        logs_query = logs_query.where(OfferTransitionAudit.to_status == to_status)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        logs_query
        .order_by(OfferTransitionAudit.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    logs = result.scalars().all()
    return OfferTransitionAuditListResponse(total=total, count=len(logs), logs=logs)


@app.get(
    "/api/v1/admin/notifications/push-delivery-logs",
    response_model=PushDeliveryLogListResponse,
    tags=["Admin"],
    summary="List push delivery logs with filters (Admin only)",
)
async def list_push_delivery_logs_admin(
    status_filter: str | None = Query(None, description="Filter by status: success, partial, failed, no_tokens"),
    event_type: str | None = Query(None, description="Filter by notification event type"),
    skip: int = Query(0, ge=0, description="Number of rows to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max rows to return"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    del admin

    count_query = select(func.count()).select_from(PushDeliveryLog)
    logs_query = select(PushDeliveryLog)

    if status_filter:
        count_query = count_query.where(PushDeliveryLog.status == status_filter)
        logs_query = logs_query.where(PushDeliveryLog.status == status_filter)

    if event_type:
        count_query = count_query.where(PushDeliveryLog.event_type == event_type)
        logs_query = logs_query.where(PushDeliveryLog.event_type == event_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    logs_result = await db.execute(
        logs_query
        .order_by(PushDeliveryLog.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    logs = logs_result.scalars().all()

    return PushDeliveryLogListResponse(total=total, count=len(logs), logs=logs)


@app.get(
    "/api/v1/offers/action-required-count",
    response_model=OfferActionRequiredCountResponse,
    tags=["Offers"],
    summary="Count offers requiring current user's action",
)
async def get_action_required_offers_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(func.count())
        .select_from(Offer)
        .where(
            ((Offer.seller_id == current_user.id) & (Offer.status == OfferStatus.PENDING))
            |
            ((Offer.buyer_id == current_user.id) & (Offer.status == OfferStatus.COUNTERED))
        )
    )
    actionable_count = result.scalar() or 0
    return OfferActionRequiredCountResponse(actionable_count=actionable_count)


@app.put(
    "/api/v1/offers/{offer_id}/cancel",
    response_model=OfferResponse,
    tags=["Offers"],
    summary="Cancel a pending offer (buyer only)",
)
async def cancel_offer(
    offer_id: uuid.UUID,
    body: OfferCancelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    action = f"offer:{offer_id}:cancel"
    replay = await get_idempotent_replay(db, current_user.id, action, idempotency_key)
    if replay is not None:
        return replay

    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    offer = result.scalar_one_or_none()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    if offer.buyer_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only buyer can cancel offer")

    if offer.status == OfferStatus.CANCELLED:
        response_model = await build_offer_response(db, offer)
        payload = response_model.model_dump(mode="json")
        await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
        return response_model

    await persist_offer_transition(
        db=db,
        offer=offer,
        to_status=OfferStatus.CANCELLED,
        actor="buyer",
        changed_by_user_id=current_user.id,
        response_message=body.response_message,
    )

    buyer, seller, horse = await load_offer_context(db, offer)

    await notify_offer_participant(
        db=db,
        target_user=seller,
        horse=horse,
        offer=offer,
        event_type="offer_cancelled",
        title_en="Offer cancelled",
        body_en=f"Buyer cancelled their offer for {horse.title}.",
        title_ar="تم إلغاء العرض",
        body_ar=f"قام المشتري بإلغاء عرضه على {horse.title}.",
    )

    response_model = await build_offer_response(db, offer)
    payload = response_model.model_dump(mode="json")
    await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
    return response_model


@app.post(
    "/api/v1/offers/{offer_id}/mark-sold",
    status_code=status.HTTP_200_OK,
    tags=["Offers"],
    summary="Mark horse as sold from an accepted offer (seller only)",
)
async def mark_offer_horse_sold(
    offer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    action = f"offer:{offer_id}:mark-sold"
    replay = await get_idempotent_replay(db, current_user.id, action, idempotency_key)
    if replay is not None:
        return replay

    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    accepted_offer = result.scalar_one_or_none()
    if not accepted_offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    if accepted_offer.seller_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only seller can mark as sold")

    if accepted_offer.status != OfferStatus.ACCEPTED:
        raise HTTPException(status_code=400, detail="Only accepted offers can be marked sold")

    horse_result = await db.execute(select(Horse).where(Horse.id == accepted_offer.horse_id))
    horse = horse_result.scalar_one_or_none()
    if not horse:
        raise HTTPException(status_code=404, detail="Horse not found")

    if horse.status == "sold":
        payload = {"message": "Horse already marked as sold", "horse_id": str(horse.id)}
        await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
        return payload

    horse.status = "sold"
    db.add(horse)

    other_offers_result = await db.execute(
        select(Offer)
        .where(
            Offer.horse_id == horse.id,
            Offer.id != accepted_offer.id,
            Offer.status.in_([OfferStatus.PENDING, OfferStatus.COUNTERED]),
        )
    )
    other_offers = other_offers_result.scalars().all()

    for offer in other_offers:
        await persist_offer_transition(
            db=db,
            offer=offer,
            to_status=OfferStatus.CANCELLED,
            actor="system",
            changed_by_user_id=None,
            response_message="Listing sold to another buyer",
            commit=False,
            refresh=False,
        )

    await db.commit()

    for offer in other_offers:
        buyer_result = await db.execute(select(User).where(User.id == offer.buyer_id))
        buyer = buyer_result.scalar_one_or_none()
        if not buyer:
            continue
        await notify_offer_participant(
            db=db,
            target_user=buyer,
            horse=horse,
            offer=offer,
            event_type="listing_sold",
            title_en="Listing sold",
            body_en=f"{horse.title} has been sold to another buyer.",
            title_ar="تم بيع الإعلان",
            body_ar=f"تم بيع {horse.title} إلى مشتري آخر.",
        )

    accepted_buyer_result = await db.execute(select(User).where(User.id == accepted_offer.buyer_id))
    accepted_buyer = accepted_buyer_result.scalar_one_or_none()
    if accepted_buyer:
        await notify_offer_participant(
            db=db,
            target_user=accepted_buyer,
            horse=horse,
            offer=accepted_offer,
            event_type="offer_sold",
            title_en="Purchase confirmed",
            body_en=f"{horse.title} was marked as sold. Congratulations!",
            title_ar="تم تأكيد الشراء",
            body_ar=f"تم تأكيد بيع {horse.title}. مبروك!",
        )

    response_payload = {
        "message": "Horse marked as sold",
        "horse_id": str(horse.id),
        "closed_offers": len(other_offers),
    }
    await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, response_payload)
    return response_payload


@app.put(
    "/api/v1/offers/{offer_id}/counter",
    response_model=OfferResponse,
    tags=["Offers"],
    summary="Send a counter-offer",
)
async def counter_offer(
    offer_id: uuid.UUID,
    body: OfferCounterRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    action = f"offer:{offer_id}:counter"
    replay = await get_idempotent_replay(db, current_user.id, action, idempotency_key)
    if replay is not None:
        return replay

    # Get the offer
    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    offer = result.scalar_one_or_none()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    # Only seller can counter
    if offer.seller_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only seller can send counter-offer")

    await persist_offer_transition(
        db=db,
        offer=offer,
        to_status=OfferStatus.COUNTERED,
        actor="seller",
        changed_by_user_id=current_user.id,
        response_message=body.response_message,
        counter_amount=body.counter_amount,
    )

    # Fetch full details
    buyer, seller, horse = await load_offer_context(db, offer)

    await notify_offer_participant(
        db=db,
        target_user=buyer,
        horse=horse,
        offer=offer,
        event_type="offer_counter",
        title_en="Counter-offer received",
        body_en=f"Seller sent a counter-offer of ${offer.counter_amount:,.0f} for {horse.title}.",
        title_ar="تم إرسال عرض مقابل",
        body_ar=f"أرسل البائع عرضًا مقابلًا بقيمة ${offer.counter_amount:,.0f} للحصان {horse.title}.",
    )
    response_model = await build_offer_response(db, offer)
    payload = response_model.model_dump(mode="json")
    await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
    return response_model


@app.put(
    "/api/v1/offers/{offer_id}/accept",
    response_model=OfferResponse,
    tags=["Offers"],
    summary="Accept an offer or counter-offer",
)
async def accept_offer(
    offer_id: uuid.UUID,
    body: OfferAcceptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    action = f"offer:{offer_id}:accept"
    replay = await get_idempotent_replay(db, current_user.id, action, idempotency_key)
    if replay is not None:
        return replay

    # Get the offer
    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    offer = result.scalar_one_or_none()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    actor = get_offer_actor(offer, current_user)
    if offer.status == OfferStatus.ACCEPTED:
        if actor == "unknown":
            raise HTTPException(status_code=403, detail="Not authorized for this offer transition")
        response_model = await build_offer_response(db, offer)
        payload = response_model.model_dump(mode="json")
        await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
        return response_model

    await persist_offer_transition(
        db=db,
        offer=offer,
        to_status=OfferStatus.ACCEPTED,
        actor=actor,
        changed_by_user_id=current_user.id,
        response_message=body.response_message,
    )

    # Fetch full details
    buyer, seller, horse = await load_offer_context(db, offer)

    accepter_is_seller = current_user.id == offer.seller_id
    target_user = buyer if accepter_is_seller else seller

    await notify_offer_participant(
        db=db,
        target_user=target_user,
        horse=horse,
        offer=offer,
        event_type="offer_accepted",
        title_en="Offer accepted",
        body_en=f"The offer for {horse.title} has been accepted.",
        title_ar="تم قبول العرض",
        body_ar=f"تم قبول العرض الخاص بالحصان {horse.title}.",
    )

    response_model = await build_offer_response(db, offer)
    payload = response_model.model_dump(mode="json")
    await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
    return response_model


@app.put(
    "/api/v1/offers/{offer_id}/reject",
    response_model=OfferResponse,
    tags=["Offers"],
    summary="Reject an offer",
)
async def reject_offer(
    offer_id: uuid.UUID,
    body: OfferRejectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    action = f"offer:{offer_id}:reject"
    replay = await get_idempotent_replay(db, current_user.id, action, idempotency_key)
    if replay is not None:
        return replay

    # Get the offer
    result = await db.execute(select(Offer).where(Offer.id == offer_id))
    offer = result.scalar_one_or_none()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    actor = get_offer_actor(offer, current_user)
    if offer.status == OfferStatus.REJECTED:
        if actor == "unknown":
            raise HTTPException(status_code=403, detail="Not authorized for this offer transition")
        response_model = await build_offer_response(db, offer)
        payload = response_model.model_dump(mode="json")
        await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
        return response_model

    await persist_offer_transition(
        db=db,
        offer=offer,
        to_status=OfferStatus.REJECTED,
        actor=actor,
        changed_by_user_id=current_user.id,
        response_message=body.response_message,
    )

    # Fetch full details
    buyer, seller, horse = await load_offer_context(db, offer)

    reject_target_user = buyer if current_user.id == offer.seller_id else seller

    await notify_offer_participant(
        db=db,
        target_user=reject_target_user,
        horse=horse,
        offer=offer,
        event_type="offer_rejected",
        title_en="Offer rejected",
        body_en=f"The offer flow for {horse.title} was rejected.",
        title_ar="تم رفض العرض",
        body_ar=f"تم رفض مسار العرض على الحصان {horse.title}.",
    )

    response_model = await build_offer_response(db, offer)
    payload = response_model.model_dump(mode="json")
    await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
    return response_model

