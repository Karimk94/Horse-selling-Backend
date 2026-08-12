"""Equestrian Services & Reservations listing endpoints: CRUD, inquiries, schedule availability, radius filtering, and admin moderation."""

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
    ServiceListing, ServiceImage, ServiceInquiry, Category, ServiceType, ServicePricingType, InquiryStatus, User, UserRole
)
from app.schemas import (
    ServiceCreateRequest,
    ServiceUpdateRequest,
    ServiceResponse,
    ServiceListResponse,
    ServiceImageResponse,
    ServiceInquiryCreateRequest,
    ServiceInquiryResponse,
    AdminRejectListingRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Services"])


def _format_service_response(item: ServiceListing) -> ServiceResponse:
    st_val = item.service_type.value if isinstance(item.service_type, ServiceType) else str(item.service_type)
    pt_val = item.pricing_type.value if isinstance(item.pricing_type, ServicePricingType) else str(item.pricing_type)

    return ServiceResponse(
        id=item.id,
        provider_id=item.provider_id,
        category_id=item.category_id,
        title=item.title,
        service_type=st_val,
        pricing_type=pt_val,
        price=item.price,
        location_text=item.location_text,
        latitude=item.latitude,
        longitude=item.longitude,
        availability_calendar=item.availability_calendar,
        description=item.description,
        status=item.status,
        rejection_reason=item.rejection_reason,
        deleted_at=item.deleted_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        provider=item.provider,
        category=item.category,
        images=[
            ServiceImageResponse(
                id=img.id,
                image_url=img.image_url,
                display_order=img.display_order,
                created_at=img.created_at,
            )
            for img in getattr(item, "images", [])
        ],
    )


@router.get("/services", response_model=ServiceListResponse, summary="List equestrian services")
async def list_services(
    db: AsyncSession = Depends(get_db),
    category_id: Optional[uuid.UUID] = Query(None),
    service_type: Optional[str] = Query(None),
    pricing_type: Optional[str] = Query(None),
    price_min: Optional[float] = Query(None, ge=0),
    price_max: Optional[float] = Query(None, ge=0),
    q: Optional[str] = Query(None, description="Search query across title, description, location"),
    lat: Optional[float] = Query(None, description="Current latitude for radius filter"),
    lon: Optional[float] = Query(None, description="Current longitude for radius filter"),
    radius_km: Optional[float] = Query(None, gt=0, description="Max radius in km"),
    provider_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    query = (
        select(ServiceListing)
        .options(
            selectinload(ServiceListing.provider).selectinload(User.profile),
            selectinload(ServiceListing.category),
            selectinload(ServiceListing.images),
        )
        .where(ServiceListing.deleted_at.is_(None))
    )

    show_all_statuses = False
    if current_user:
        if provider_id and provider_id == current_user.id:
            show_all_statuses = True
        elif current_user.role == UserRole.ADMIN:
            show_all_statuses = True

    if status_filter:
        query = query.where(ServiceListing.status == status_filter)
    elif not show_all_statuses:
        query = query.where(ServiceListing.status == "approved")

    if provider_id:
        query = query.where(ServiceListing.provider_id == provider_id)
    if category_id:
        query = query.where(ServiceListing.category_id == category_id)
    if service_type:
        query = query.where(ServiceListing.service_type == service_type)
    if pricing_type:
        query = query.where(ServiceListing.pricing_type == pricing_type)
    if price_min is not None:
        query = query.where(ServiceListing.price >= price_min)
    if price_max is not None:
        query = query.where(ServiceListing.price <= price_max)

    # Text Search Filter
    if q and q.strip():
        search_term = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(ServiceListing.title).like(search_term),
                func.lower(ServiceListing.description).like(search_term),
                func.lower(ServiceListing.location_text).like(search_term),
            )
        )

    # Location Radius Filter (Haversine)
    if lat is not None and lon is not None and radius_km is not None:
        haversine = 6371 * func.acos(
            func.cos(func.radians(lat))
            * func.cos(func.radians(ServiceListing.latitude))
            * func.cos(func.radians(ServiceListing.longitude) - func.radians(lon))
            + func.sin(func.radians(lat)) * func.sin(func.radians(ServiceListing.latitude))
        )
        query = query.where(haversine <= radius_km)

    # Count query
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(ServiceListing.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return ServiceListResponse(
        total=total,
        items=[_format_service_response(item) for item in items],
    )


@router.get("/services/{service_id}", response_model=ServiceResponse, summary="Get service listing detail")
async def get_service(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    query = (
        select(ServiceListing)
        .options(
            selectinload(ServiceListing.provider).selectinload(User.profile),
            selectinload(ServiceListing.category),
            selectinload(ServiceListing.images),
        )
        .where(ServiceListing.id == service_id)
        .where(ServiceListing.deleted_at.is_(None))
    )
    result = await db.execute(query)
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    is_provider = current_user and current_user.id == item.provider_id
    is_admin = current_user and current_user.role == UserRole.ADMIN

    if item.status != "approved" and not is_provider and not is_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    return _format_service_response(item)


@router.post("/services", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED, summary="Create service listing")
async def create_service(
    payload: ServiceCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.category_id:
        cat_stmt = select(Category).where(Category.id == payload.category_id)
        cat = (await db.execute(cat_stmt)).scalar_one_or_none()
        if not cat:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Category not found")

    now = datetime.now(timezone.utc)

    item = ServiceListing(
        provider_id=current_user.id,
        category_id=payload.category_id,
        title=payload.title,
        service_type=ServiceType(payload.service_type),
        pricing_type=ServicePricingType(payload.pricing_type),
        price=payload.price,
        location_text=payload.location_text,
        latitude=payload.latitude,
        longitude=payload.longitude,
        availability_calendar=payload.availability_calendar,
        description=payload.description,
        status="pending_review",
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    await db.flush()

    if payload.image_urls:
        for idx, img_url in enumerate(payload.image_urls):
            img = ServiceImage(
                service_id=item.id,
                image_url=img_url,
                display_order=idx,
            )
            db.add(img)

    await db.commit()

    query = (
        select(ServiceListing)
        .options(
            selectinload(ServiceListing.provider).selectinload(User.profile),
            selectinload(ServiceListing.category),
            selectinload(ServiceListing.images),
        )
        .where(ServiceListing.id == item.id)
    )
    loaded_item = (await db.execute(query)).scalar_one()
    return _format_service_response(loaded_item)


@router.put("/services/{service_id}", response_model=ServiceResponse, summary="Update service listing")
async def update_service(
    service_id: uuid.UUID,
    payload: ServiceUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        select(ServiceListing)
        .options(
            selectinload(ServiceListing.provider).selectinload(User.profile),
            selectinload(ServiceListing.category),
            selectinload(ServiceListing.images),
        )
        .where(ServiceListing.id == service_id)
        .where(ServiceListing.deleted_at.is_(None))
    )
    item = (await db.execute(query)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    if item.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to edit this listing")

    if payload.title is not None:
        item.title = payload.title
    if payload.category_id is not None:
        item.category_id = payload.category_id
    if payload.service_type is not None:
        item.service_type = ServiceType(payload.service_type)
    if payload.pricing_type is not None:
        item.pricing_type = ServicePricingType(payload.pricing_type)
    if payload.price is not None:
        item.price = payload.price
    if payload.location_text is not None:
        item.location_text = payload.location_text
    if payload.latitude is not None:
        item.latitude = payload.latitude
    if payload.longitude is not None:
        item.longitude = payload.longitude
    if payload.availability_calendar is not None:
        item.availability_calendar = payload.availability_calendar
    if payload.description is not None:
        item.description = payload.description

    if payload.image_urls is not None:
        for existing_img in list(item.images):
            await db.delete(existing_img)
        for idx, img_url in enumerate(payload.image_urls):
            new_img = ServiceImage(
                service_id=item.id,
                image_url=img_url,
                display_order=idx,
            )
            db.add(new_img)

    await db.commit()
    await db.refresh(item)

    result = await db.execute(query)
    updated_item = result.scalar_one()
    return _format_service_response(updated_item)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete service listing")
async def delete_service(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(ServiceListing).where(ServiceListing.id == service_id).where(ServiceListing.deleted_at.is_(None))
    item = (await db.execute(stmt)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    if item.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this listing")

    item.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return None


# ── Service Inquiries & Reservations ─────────────────────────────────────────


@router.post("/services/{service_id}/inquiries", response_model=ServiceInquiryResponse, status_code=status.HTTP_201_CREATED, summary="Send service inquiry / reservation request")
async def create_service_inquiry(
    service_id: uuid.UUID,
    payload: ServiceInquiryCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(ServiceListing).where(ServiceListing.id == service_id).where(ServiceListing.deleted_at.is_(None))
    service = (await db.execute(stmt)).scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    inquiry = ServiceInquiry(
        service_id=service.id,
        inquirer_id=current_user.id,
        inquirer_name=payload.inquirer_name,
        inquirer_phone=payload.inquirer_phone,
        message=payload.message,
        requested_date=payload.requested_date,
        status=InquiryStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    db.add(inquiry)
    await db.commit()
    await db.refresh(inquiry)

    return ServiceInquiryResponse(
        id=inquiry.id,
        service_id=inquiry.service_id,
        inquirer_id=inquiry.inquirer_id,
        inquirer_name=inquiry.inquirer_name,
        inquirer_phone=inquiry.inquirer_phone,
        message=inquiry.message,
        requested_date=inquiry.requested_date,
        status=inquiry.status.value if isinstance(inquiry.status, InquiryStatus) else str(inquiry.status),
        created_at=inquiry.created_at,
    )


@router.get("/services/{service_id}/inquiries", response_model=list[ServiceInquiryResponse], summary="List inquiries for a service (Provider / Admin)")
async def list_service_inquiries(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(ServiceListing).where(ServiceListing.id == service_id)
    service = (await db.execute(stmt)).scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    if service.provider_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view inquiries for this service")

    inq_stmt = select(ServiceInquiry).where(ServiceInquiry.service_id == service_id).order_by(ServiceInquiry.created_at.desc())
    result = await db.execute(inq_stmt)
    inquiries = list(result.scalars().all())

    return [
        ServiceInquiryResponse(
            id=inq.id,
            service_id=inq.service_id,
            inquirer_id=inq.inquirer_id,
            inquirer_name=inq.inquirer_name,
            inquirer_phone=inq.inquirer_phone,
            message=inq.message,
            requested_date=inq.requested_date,
            status=inq.status.value if isinstance(inq.status, InquiryStatus) else str(inq.status),
            created_at=inq.created_at,
        )
        for inq in inquiries
    ]


# ── Admin Moderation ─────────────────────────────────────────────────────────


@router.get("/admin/services/pending", response_model=ServiceListResponse, summary="List pending service listings (Admin)")
async def admin_list_pending_services(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    query = (
        select(ServiceListing)
        .options(
            selectinload(ServiceListing.provider).selectinload(User.profile),
            selectinload(ServiceListing.category),
            selectinload(ServiceListing.images),
        )
        .where(ServiceListing.status == "pending_review")
        .where(ServiceListing.deleted_at.is_(None))
    )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(ServiceListing.created_at.asc()).offset(skip).limit(limit)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return ServiceListResponse(
        total=total,
        items=[_format_service_response(item) for item in items],
    )


@router.post("/admin/services/{service_id}/approve", response_model=ServiceResponse, summary="Approve service listing (Admin)")
async def admin_approve_service(
    service_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = (
        select(ServiceListing)
        .options(
            selectinload(ServiceListing.provider).selectinload(User.profile),
            selectinload(ServiceListing.category),
            selectinload(ServiceListing.images),
        )
        .where(ServiceListing.id == service_id)
        .where(ServiceListing.deleted_at.is_(None))
    )
    item = (await db.execute(query)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    item.status = "approved"
    item.rejection_reason = None
    await db.commit()
    await db.refresh(item)
    return _format_service_response(item)


@router.post("/admin/services/{service_id}/reject", response_model=ServiceResponse, summary="Reject service listing (Admin)")
async def admin_reject_service(
    service_id: uuid.UUID,
    body: AdminRejectListingRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    query = (
        select(ServiceListing)
        .options(
            selectinload(ServiceListing.provider).selectinload(User.profile),
            selectinload(ServiceListing.category),
            selectinload(ServiceListing.images),
        )
        .where(ServiceListing.id == service_id)
        .where(ServiceListing.deleted_at.is_(None))
    )
    item = (await db.execute(query)).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service listing not found")

    item.status = "rejected"
    item.rejection_reason = body.reason
    await db.commit()
    await db.refresh(item)
    return _format_service_response(item)
