import random
import string
from datetime import datetime, timedelta, timezone
import uuid

# ... imports ...
from app.schemas import (
    SignupRequest,
    LoginRequest,
    TokenResponse,
    TokenResponse,
    UserResponse,
    UserProfileUpdate,
    HorseCreateRequest,
    HorseResponse,
    HorseListResponse,
    HorseUpdateRequest,
    HorseUpdateRequest,
    UserRoleUpdate,
    AdminUserUpdate,
    AddFavoriteRequest,
    FavoriteResponse,
    AdminApproveListingRequest,
    AdminRejectListingRequest,
    OTPRequest,
    VerifyOTPRequest,
    VoucherCreateRequest,
    VoucherUpdateRequest,
    VoucherResponse,
    VoucherValidateRequest,
    VoucherValidateResponse,
)
from app.email_service import (
    send_pending_review_notification,
    send_listing_approved_email,
    send_listing_rejected_email,
    send_verification_email,
    send_otp_email,
)
# ...



from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import engine, Base, get_db
from app.models import User, UserProfile, Horse, HorseGender, UserRole, HorseImage, Favorite, Voucher, DiscountType
from app.config import BASE_URL
from app.schemas import (
    SignupRequest,
    LoginRequest,
    TokenResponse,
    TokenResponse,
    UserResponse,
    UserProfileUpdate,
    HorseCreateRequest,
    HorseResponse,
    HorseListResponse,
    HorseUpdateRequest,
    HorseUpdateRequest,
    UserRoleUpdate,
    AdminUserUpdate,
    AddFavoriteRequest,
    FavoriteResponse,
    AdminApproveListingRequest,
    AdminRejectListingRequest,
)
from app.email_service import (
    send_pending_review_notification,
    send_listing_approved_email,
    send_listing_rejected_email,
    send_verification_email,
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


# ── Lifespan: create tables on startup ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


# ── FastAPI application ───────────────────────────────────────────────────────

app = FastAPI(
    title="Horse Marketplace API",
    description="Backend API for a Horse Selling marketplace",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
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
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)):
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
    if body.phone_number or body.location:
        profile = UserProfile(
            user_id=user.id,
            phone_number=body.phone_number,
            location=body.location,
        )
        db.add(profile)

    await db.commit()
    await db.refresh(user)

    # Generate verification token and send email
    verification_token = create_verification_token(user.email)
    verification_link = f"{BASE_URL}/auth/verify-email?token={verification_token}"
    send_verification_email(user.email, verification_token, verification_link, user.language)

    access_token = create_access_token(data={"sub": user.email})
    return TokenResponse(access_token=access_token)


@app.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="Authenticate and receive a token",
)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
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
async def send_otp(body: OTPRequest, db: AsyncSession = Depends(get_db)):
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
    
    user.verification_code = otp
    user.verification_code_expires_at = expires_at
    
    await db.commit()
    
    # Send email
    send_otp_email(user.email, otp, user.language)
    
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
        
    # Check match
    if user.verification_code != body.otp:
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
    response_model=list[UserResponse],
    tags=["Admin"],
    summary="List all users (Admin only)",
)
async def admin_list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = select(User).options(selectinload(User.profile)).order_by(User.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


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
    response_model=list[HorseResponse],
    tags=["Admin"],
    summary="List all listings (Admin only)",
)
async def admin_list_listings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = select(Horse).options(
        selectinload(Horse.owner).selectinload(User.profile),
        selectinload(Horse.images)
    ).order_by(Horse.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@app.get(
    "/api/v1/admin/listings/pending",
    response_model=list[HorseResponse],
    tags=["Admin"],
    summary="List pending listings for review (Admin only)",
)
async def admin_list_pending_listings(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = select(Horse).where(
        Horse.status == "pending_review"
    ).options(
        selectinload(Horse.owner).selectinload(User.profile),
        selectinload(Horse.images)
    ).order_by(Horse.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


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
    db.add(horse)
    await db.commit()
    await db.refresh(horse)
    
    # Send approval email to seller
    seller = horse.owner
    send_listing_approved_email(seller.email, horse.title, seller.language)
    
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
    db.add(horse)
    await db.commit()
    await db.refresh(horse)
    
    # Send rejection email to seller
    seller = horse.owner
    send_listing_rejected_email(seller.email, horse.title, body.reason, seller.language)
    
    return horse


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

    if not show_all_statuses:
        query = query.where(Horse.status == "approved")

    # Apply dynamic filters
    if owner_id is not None:
        query = query.where(Horse.owner_id == owner_id)

    # Apply dynamic filters
    if min_price is not None:
        query = query.where(Horse.price >= min_price)
    if max_price is not None:
        query = query.where(Horse.price <= max_price)
    if breed is not None:
        query = query.where(Horse.breed.ilike(f"%{breed}%"))
    if min_age is not None:
        query = query.where(Horse.age >= min_age)
    if max_age is not None:
        query = query.where(Horse.age <= max_age)
    if discipline is not None:
        query = query.where(Horse.discipline.ilike(f"%{discipline}%"))

    # Count total matching records (before pagination)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Apply pagination and ordering
    query = query.order_by(Horse.created_at.desc()).offset(skip).limit(limit)
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

    # Visibility logic:
    # If approved: Visible to everyone.
    # If not approved: Visible only to Owner and Admin.
    if horse.status != "approved":
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


@app.delete(
    "/api/v1/horses/{horse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Horses"],
    summary="Delete a horse listing",
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

    await db.delete(horse)
    await db.commit()


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
    return None
