import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import engine, Base, get_db
from app.models import User, UserProfile, Horse, HorseGender, UserRole
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
)
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_admin,
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
    query = select(Horse).options(selectinload(Horse.owner)).order_by(Horse.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


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
):
    # Build base query
    query = select(Horse).options(selectinload(Horse.owner))

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
        image_url=body.image_url,
    )
    db.add(horse)
    await db.commit()
    await db.refresh(horse, attribute_names=["owner"])

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
    # Fetch horse
    result = await db.execute(
        select(Horse).where(Horse.id == horse_id).options(selectinload(Horse.owner))
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
    if body.image_url is not None:
        horse.image_url = body.image_url

    await db.commit()
    await db.refresh(horse)
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
    return None
