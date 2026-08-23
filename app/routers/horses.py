"""Horse listing endpoints: CRUD, restore, reopen, delete."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, get_optional_current_user
from app.config import SOFT_DELETE_RESTORE_DAYS
from app.database import get_db
from app.email_service import send_pending_review_notification
from app.models import (
    User, Horse, HorseGender, UserRole, HorseImage, DiscountType,
    ListingReview,
)
from app.schemas import (
    HorseCreateRequest, HorseResponse, HorseListResponse, HorseUpdateRequest,
)
from app.services.idempotency import (
    get_idempotent_replay, finalize_idempotent_replay, store_idempotent_replay,
)

router = APIRouter(prefix="/api/v1", tags=["Horses"])


@router.get("/horses", response_model=HorseListResponse, summary="List horses with optional filters")
async def list_horses(
    db: AsyncSession = Depends(get_db),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    breed: Optional[str] = Query(None),
    min_age: Optional[int] = Query(None, ge=0),
    max_age: Optional[int] = Query(None, ge=0),
    discipline: Optional[str] = Query(None),
    horse_status: Optional[str] = Query(None),
    vet_check_available: Optional[bool] = Query(None),
    verified_seller: Optional[bool] = Query(None),
    gender: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    owner_id: Optional[uuid.UUID] = Query(None),
    q: Optional[str] = Query(None, description="Search query across title, breed, description, location"),
    lat: Optional[float] = Query(None, description="Current latitude for radius filter"),
    lon: Optional[float] = Query(None, description="Current longitude for radius filter"),
    radius_km: Optional[float] = Query(None, gt=0, description="Max radius in km"),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    query = select(Horse).options(
        selectinload(Horse.owner).selectinload(User.profile),
        selectinload(Horse.images)
    )
    show_all_statuses = False
    if current_user:
        if owner_id and owner_id == current_user.id:
            show_all_statuses = True
    if horse_status is not None:
        query = query.where(Horse.status == horse_status)
    elif not show_all_statuses:
        query = query.where(Horse.status.in_(["approved", "sold"]))
    query = query.where(Horse.deleted_at.is_(None))
    if owner_id is not None:
        query = query.where(Horse.owner_id == owner_id)
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

    # Text search across title, breed, description, location
    if q and q.strip():
        search_term = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Horse.title).like(search_term),
                func.lower(Horse.breed).like(search_term),
                func.lower(Horse.description).like(search_term),
                func.lower(Horse.location_text).like(search_term),
            )
        )

    # Location Radius Filter (Haversine)
    if lat is not None and lon is not None and radius_km is not None:
        haversine = 6371 * func.acos(
            func.cos(func.radians(lat))
            * func.cos(func.radians(Horse.latitude))
            * func.cos(func.radians(Horse.longitude) - func.radians(lon))
            + func.sin(func.radians(lat)) * func.sin(func.radians(Horse.latitude))
        )
        query = query.where(haversine <= radius_km)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0
    _sort_map = {
        "price_asc": Horse.price.asc(), "price_desc": Horse.price.desc(),
        "age_asc": Horse.age.asc(), "age_desc": Horse.age.desc(),
    }
    order_col = _sort_map.get(sort_by, Horse.created_at.desc())
    query = query.order_by(order_col).offset(skip).limit(limit)
    result = await db.execute(query)
    horses = result.scalars().all()
    return HorseListResponse(total=total, horses=horses)


@router.post("/horses", response_model=HorseResponse, status_code=status.HTTP_201_CREATED, summary="Create a new horse listing")
async def create_horse(
    body: HorseCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your email address before creating a listing")
    image_urls = body.image_urls or ([body.image_url] if body.image_url else [])
    horse = Horse(owner_id=current_user.id, title=body.title, price=body.price, breed=body.breed, age=body.age, gender=HorseGender(body.gender), discipline=body.discipline, height=body.height, description=body.description, vet_check_available=body.vet_check_available, vet_certificate_url=body.vet_certificate_url, location_text=body.location_text, latitude=body.latitude, longitude=body.longitude, image_url=image_urls[0] if image_urls else None, discount_type=DiscountType(body.discount_type) if body.discount_type else None, discount_value=body.discount_value, status="pending_review")
    if horse.discount_type and horse.discount_value:
        if horse.discount_type == DiscountType.PERCENTAGE:
            horse.discount_price = horse.price * (1 - horse.discount_value / 100)
        elif horse.discount_type == DiscountType.FIXED:
            horse.discount_price = horse.discount_value
    db.add(horse)
    await db.flush()
    for idx, url in enumerate(image_urls):
        db.add(HorseImage(horse_id=horse.id, image_url=url, display_order=idx))
    await db.commit()
    result = await db.execute(select(Horse).where(Horse.id == horse.id).options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)))
    horse = result.scalar_one()
    admin_result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
    admin_users = admin_result.scalars().all()
    admins_data = [{"email": admin.email, "language": admin.language} for admin in admin_users]
    if admins_data:
        background_tasks.add_task(
            send_pending_review_notification,
            admins_data,
            horse.title,
            current_user.email,
        )
    return horse


@router.get("/horses/{horse_id}", response_model=HorseResponse, summary="Get a specific horse listing")
async def get_horse(horse_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: Optional[User] = Depends(get_optional_current_user)):
    result = await db.execute(select(Horse).where(Horse.id == horse_id).options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)))
    horse = result.scalar_one_or_none()
    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")
    if horse.deleted_at is not None:
        is_owner = current_user and horse.owner_id == current_user.id
        is_admin = current_user and current_user.role == UserRole.ADMIN
        if not (is_owner or is_admin):
            raise HTTPException(status_code=404, detail="Listing not found")
    if horse.status not in ["approved", "sold"]:
        is_owner = current_user and horse.owner_id == current_user.id
        is_admin = current_user and current_user.role == UserRole.ADMIN
        if not (is_owner or is_admin):
            raise HTTPException(status_code=404, detail="Listing not found")
    return horse


@router.put("/horses/{horse_id}", response_model=HorseResponse, summary="Update a horse listing")
async def update_horse(horse_id: uuid.UUID, body: HorseUpdateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Horse).where(Horse.id == horse_id).options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)))
    horse = result.scalar_one_or_none()
    if not horse:
        raise HTTPException(status_code=404, detail="Listing not found")
    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to edit this listing")
    if body.title is not None: horse.title = body.title
    if body.price is not None: horse.price = body.price
    if body.breed is not None: horse.breed = body.breed
    if body.age is not None: horse.age = body.age
    if body.gender is not None: horse.gender = HorseGender(body.gender)
    if body.discipline is not None: horse.discipline = body.discipline
    if body.height is not None: horse.height = body.height
    if body.description is not None: horse.description = body.description
    if body.vet_check_available is not None: horse.vet_check_available = body.vet_check_available
    if body.vet_certificate_url is not None: horse.vet_certificate_url = body.vet_certificate_url
    if body.location_text is not None: horse.location_text = body.location_text
    if body.latitude is not None: horse.latitude = body.latitude
    if body.longitude is not None: horse.longitude = body.longitude
    if body.discount_type is not None: horse.discount_type = DiscountType(body.discount_type) if body.discount_type else None
    if body.discount_value is not None: horse.discount_value = body.discount_value
    if horse.discount_type and horse.discount_value:
        if horse.discount_type == DiscountType.PERCENTAGE: horse.discount_price = horse.price * (1 - horse.discount_value / 100)
        elif horse.discount_type == DiscountType.FIXED: horse.discount_price = horse.discount_value
    else: horse.discount_price = None
    if body.image_urls is not None:
        for img in horse.images: await db.delete(img)
        await db.flush()
        for idx, url in enumerate(body.image_urls): db.add(HorseImage(horse_id=horse.id, image_url=url, display_order=idx))
        horse.image_url = body.image_urls[0] if body.image_urls else None
    elif body.image_url is not None: horse.image_url = body.image_url
    await db.commit()
    result = await db.execute(select(Horse).where(Horse.id == horse.id).options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)))
    horse = result.scalar_one()
    return horse


@router.post("/horses/{horse_id}/reopen", response_model=HorseResponse, summary="Reopen a sold horse listing")
async def reopen_horse_listing(horse_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user), idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
    action = f"horse:{horse_id}:reopen"
    replay = await get_idempotent_replay(db, current_user.id, action, idempotency_key)
    if replay is not None: return replay
    result = await db.execute(select(Horse).where(Horse.id == horse_id).options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)))
    horse = result.scalar_one_or_none()
    if not horse: raise HTTPException(status_code=404, detail="Listing not found")
    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN: raise HTTPException(status_code=403, detail="Not authorized to reopen this listing")
    if horse.status == "approved":
        payload = HorseResponse.model_validate(horse).model_dump(mode="json")
        await finalize_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
        return horse
    if horse.status != "sold": raise HTTPException(status_code=400, detail="Only sold listings can be reopened")
    horse.status = "approved"
    db.add(horse)
    payload = HorseResponse.model_validate(horse).model_dump(mode="json")
    await store_idempotent_replay(db, current_user.id, action, idempotency_key, payload)
    await db.commit()
    await db.refresh(horse)
    return horse


@router.delete("/horses/{horse_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a horse listing (soft delete)")
async def delete_horse(horse_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Horse).where(Horse.id == horse_id))
    horse = result.scalar_one_or_none()
    if not horse: raise HTTPException(status_code=404, detail="Listing not found")
    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN: raise HTTPException(status_code=403, detail="Not authorized to delete this listing")
    horse.deleted_at = datetime.now(timezone.utc)
    db.add(horse)
    db.add(ListingReview(horse_id=horse.id, admin_id=current_user.id, action="delete", reason="soft_delete"))
    await db.commit()


@router.post("/horses/{horse_id}/restore", response_model=HorseResponse, summary="Restore a soft-deleted horse listing")
async def restore_horse(horse_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Horse).where(Horse.id == horse_id).options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)))
    horse = result.scalar_one_or_none()
    if not horse: raise HTTPException(status_code=404, detail="Listing not found")
    if horse.owner_id != current_user.id and current_user.role != UserRole.ADMIN: raise HTTPException(status_code=403, detail="Not authorized to restore this listing")
    if horse.deleted_at is None: return horse
    if SOFT_DELETE_RESTORE_DAYS > 0:
        restore_deadline = horse.deleted_at + timedelta(days=SOFT_DELETE_RESTORE_DAYS)
        if datetime.now(timezone.utc) > restore_deadline: raise HTTPException(status_code=410, detail="Restore window expired")
    horse.deleted_at = None
    db.add(horse)
    db.add(ListingReview(horse_id=horse.id, admin_id=current_user.id, action="restore", reason="soft_delete_restore"))
    await db.commit()
    await db.refresh(horse)
    return horse
