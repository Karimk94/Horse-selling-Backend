"""Rider Apparel & Gear listing endpoints: CRUD, gender filter, location radius filtering, full-text search, and admin moderation."""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, get_current_admin, get_optional_current_user
from app.database import get_db
from app.models import (
    RiderGearListing, RiderGearImage, Category, RiderGender, User, UserRole
)
from app.schemas import (
    RiderGearCreateRequest,
    RiderGearUpdateRequest,
    RiderGearResponse,
    RiderGearListResponse,
    RiderGearImageResponse,
    AdminRejectListingRequest,
)

router = APIRouter(prefix="/api/v1", tags=["RiderGear"])


def _format_rider_gear_response(item: RiderGearListing) -> RiderGearResponse:
    sizes_list = None
    if item.sizes:
        try:
            sizes_list = json.loads(item.sizes) if isinstance(item.sizes, str) else item.sizes
        except Exception:
            sizes_list = [item.sizes]

    gender_val = item.gender.value if isinstance(item.gender, RiderGender) else str(item.gender)

    return RiderGearResponse(
        id=item.id,
        owner_id=item.owner_id,
        category_id=item.category_id,
        title=item.title,
        brand=item.brand,
        gender=gender_val,
        sizes=sizes_list,
        custom_size=item.custom_size,
        price=item.price,
        quantity=item.quantity,
        location_text=item.location_text,
        latitude=item.latitude,
        longitude=item.longitude,
        description=item.description,
        status=item.status,
        rejection_reason=item.rejection_reason,
        deleted_at=item.deleted_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        owner=item.owner,
        category=item.category,
        images=[
            RiderGearImageResponse(
                id=img.id,
                image_url=img.image_url,
                display_order=img.display_order,
                created_at=img.created_at,
            )
            for img in getattr(item, "images", [])
        ],
    )


@router.get("/rider-gear", response_model=RiderGearListResponse, summary="List rider apparel & gear listings")
async def list_rider_gear(
    db: AsyncSession = Depends(get_db),
    category_id: Optional[uuid.UUID] = Query(None),
    gender: Optional[str] = Query(None, description="male, female, or unisex"),
    price_min: Optional[float] = Query(None, ge=0),
    price_max: Optional[float] = Query(None, ge=0),
    brand: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search query across title, brand, description"),
    lat: Optional[float] = Query(None, description="Current latitude for radius filter"),
    lon: Optional[float] = Query(None, description="Current longitude for radius filter"),
    radius_km: Optional[float] = Query(None, gt=0, description="Max radius in km"),
    owner_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    query = (
        select(RiderGearListing)
        .options(
            selectinload(RiderGearListing.owner).selectinload(User.profile),
            selectinload(RiderGearListing.category),
            selectinload(RiderGearListing.images),
        )
        .where(RiderGearListing.deleted_at.is_(None))
    )

    show_all_statuses = False
    if current_user:
        if owner_id and owner_id == current_user.id:
            show_all_statuses = True
        elif current_user.role == UserRole.ADMIN:
            show_all_statuses = True

    if status_filter:
        query = query.where(RiderGearListing.status == status_filter)
    elif not show_all_statuses:
        query = query.where(RiderGearListing.status == "approved")

    if owner_id:
        query = query.where(RiderGearListing.owner_id == owner_id)
    if category_id:
        query = query.where(RiderGearListing.category_id == category_id)
    if gender:
        query = query.where(RiderGearListing.gender == gender)
    if price_min is not None:
        query = query.where(RiderGearListing.price >= price_min)
    if price_max is not None:
        query = query.where(RiderGearListing.price <= price_max)
    if brand:
        query = query.where(func.lower(RiderGearListing.brand).contains(brand.lower()))

    # Text Search Filter
    if q and q.strip():
        search_term = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(RiderGearListing.title).like(search_term),
                func.lower(RiderGearListing.brand).like(search_term),
                func.lower(RiderGearListing.description).like(search_term),
            )
        )

    # Location Radius Filter (Haversine)
    if lat is not None and lon is not None and radius_km is not None:
        haversine = 6371 * func.acos(
            func.cos(func.radians(lat))
            * func.cos(func.radians(RiderGearListing.latitude))
            * func.cos(func.radians(RiderGearListing.longitude) - func.radians(lon))
            + func.sin(func.radians(lat)) * func.sin(func.radians(RiderGearListing.latitude))
        )
        query = query.where(haversine <= radius_km)

    # Count query
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(RiderGearListing.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return RiderGearListResponse(
        total=total,
        items=[_format_rider_gear_response(item) for item in items],
    )


@router.get("/rider-gear/{rider_gear_id}", response_model=RiderGearResponse, summary="Get rider gear listing detail")
async def get_rider_gear(
    rider_gear_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    query = (
        select(RiderGearListing)
        .options(
            selectinload(RiderGearListing.owner).selectinload(User.profile),
            selectinload(RiderGearListing.category),
            selectinload(RiderGearListing.images),
        )
        .where(RiderGearListing.id == rider_gear_id)
        .where(RiderGearListing.deleted_at.is_(None))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rider gear listing not found")

    is_owner = current_user and current_user.id == item.owner_id
    is_admin = current_user and current_user.role == UserRole.ADMIN

    if item.status != "approved" and not is_owner and not is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rider gear listing not found")

    return _format_rider_gear_response(item)


@router.post("/rider-gear", response_model=RiderGearResponse, status_code=status.HTTP_201_CREATED, summary="Create rider gear listing")
async def create_rider_gear(
    payload: RiderGearCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.category_id:
        cat_stmt = select(Category).where(Category.id == payload.category_id)
        cat = (await db.execute(cat_stmt)).scalar_one_or_none()
        if not cat:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category not found")

    sizes_json = json.dumps(payload.sizes) if payload.sizes else None
    now = datetime.now(timezone.utc)

    item = RiderGearListing(
        owner_id=current_user.id,
        category_id=payload.category_id,
        title=payload.title,
        brand=payload.brand,
        gender=RiderGender(payload.gender),
        sizes=sizes_json,
        custom_size=payload.custom_size,
        price=payload.price,
        quantity=payload.quantity,
        location_text=payload.location_text,
        latitude=payload.latitude,
        longitude=payload.longitude,
        description=payload.description,
        status="pending_review",
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    await db.flush()

    if payload.image_urls:
        for idx, img_url in enumerate(payload.image_urls):
            img = RiderGearImage(
                rider_gear_id=item.id,
                image_url=img_url,
                display_order=idx,
            )
            db.add(img)

    await db.commit()

    query = (
        select(RiderGearListing)
        .options(
            selectinload(RiderGearListing.owner).selectinload(User.profile),
            selectinload(RiderGearListing.category),
            selectinload(RiderGearListing.images),
        )
        .where(RiderGearListing.id == item.id)
    )
    loaded_item = (await db.execute(query)).scalar_one()
    return _format_rider_gear_response(loaded_item)


@router.put("/rider-gear/{rider_gear_id}", response_model=RiderGearResponse, summary="Update rider gear listing")
async def update_rider_gear(
    rider_gear_id: uuid.UUID,
    payload: RiderGearUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        select(RiderGearListing)
        .options(
            selectinload(RiderGearListing.owner).selectinload(User.profile),
            selectinload(RiderGearListing.category),
            selectinload(RiderGearListing.images),
        )
        .where(RiderGearListing.id == rider_gear_id)
        .where(RiderGearListing.deleted_at.is_(None))
    )
    item = (await db.execute(query)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rider gear listing not found")

    if item.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this listing")

    if payload.title is not None:
        item.title = payload.title
    if payload.category_id is not None:
        item.category_id = payload.category_id
    if payload.brand is not None:
        item.brand = payload.brand
    if payload.gender is not None:
        item.gender = RiderGender(payload.gender)
    if payload.sizes is not None:
        item.sizes = json.dumps(payload.sizes)
    if payload.custom_size is not None:
        item.custom_size = payload.custom_size
    if payload.price is not None:
        item.price = payload.price
    if payload.quantity is not None:
        item.quantity = payload.quantity
    if payload.location_text is not None:
        item.location_text = payload.location_text
    if payload.latitude is not None:
        item.latitude = payload.latitude
    if payload.longitude is not None:
        item.longitude = payload.longitude
    if payload.description is not None:
        item.description = payload.description

    if payload.image_urls is not None:
        for existing_img in list(item.images):
            await db.delete(existing_img)
        for idx, img_url in enumerate(payload.image_urls):
            new_img = RiderGearImage(
                rider_gear_id=item.id,
                image_url=img_url,
                display_order=idx,
            )
            db.add(new_img)

    await db.commit()
    await db.refresh(item)

    result = await db.execute(query)
    updated_item = result.scalar_one()
    return _format_rider_gear_response(updated_item)


@router.delete("/rider-gear/{rider_gear_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete rider gear listing")
async def delete_rider_gear(
    rider_gear_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(RiderGearListing).where(RiderGearListing.id == rider_gear_id).where(RiderGearListing.deleted_at.is_(None))
    item = (await db.execute(stmt)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rider gear listing not found")

    if item.owner_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this listing")

    item.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return None


# ── Admin Moderation ─────────────────────────────────────────────────────────


@router.get("/admin/rider-gear/pending", response_model=RiderGearListResponse, summary="List pending rider gear listings (Admin)")
async def admin_list_pending_rider_gear(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    query = (
        select(RiderGearListing)
        .options(
            selectinload(RiderGearListing.owner).selectinload(User.profile),
            selectinload(RiderGearListing.category),
            selectinload(RiderGearListing.images),
        )
        .where(RiderGearListing.status == "pending_review")
        .where(RiderGearListing.deleted_at.is_(None))
    )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(RiderGearListing.created_at.asc()).offset(skip).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return RiderGearListResponse(
        total=total,
        items=[_format_rider_gear_response(item) for item in items],
    )


@router.post("/admin/rider-gear/{rider_gear_id}/approve", response_model=RiderGearResponse, summary="Approve rider gear listing (Admin)")
async def admin_approve_rider_gear(
    rider_gear_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = (
        select(RiderGearListing)
        .options(
            selectinload(RiderGearListing.owner).selectinload(User.profile),
            selectinload(RiderGearListing.category),
            selectinload(RiderGearListing.images),
        )
        .where(RiderGearListing.id == rider_gear_id)
        .where(RiderGearListing.deleted_at.is_(None))
    )
    item = (await db.execute(query)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rider gear listing not found")

    item.status = "approved"
    item.rejection_reason = None
    await db.commit()
    await db.refresh(item)
    return _format_rider_gear_response(item)


@router.post("/admin/rider-gear/{rider_gear_id}/reject", response_model=RiderGearResponse, summary="Reject rider gear listing (Admin)")
async def admin_reject_rider_gear(
    rider_gear_id: uuid.UUID,
    body: AdminRejectListingRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = (
        select(RiderGearListing)
        .options(
            selectinload(RiderGearListing.owner).selectinload(User.profile),
            selectinload(RiderGearListing.category),
            selectinload(RiderGearListing.images),
        )
        .where(RiderGearListing.id == rider_gear_id)
        .where(RiderGearListing.deleted_at.is_(None))
    )
    item = (await db.execute(query)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rider gear listing not found")

    item.status = "rejected"
    item.rejection_reason = body.reason
    await db.commit()
    await db.refresh(item)
    return _format_rider_gear_response(item)
