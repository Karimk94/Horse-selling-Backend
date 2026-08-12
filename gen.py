"""Generate all remaining router files."""
import os

ROUTERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "routers")
os.makedirs(ROUTERS_DIR, exist_ok=True)

HORSES_CONTENT = '''"""Horse listing endpoints: CRUD, restore, reopen, delete."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from sqlalchemy import select, func
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
async def create_horse(body: HorseCreateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your email address before creating a listing")
    image_urls = body.image_urls or ([body.image_url] if body.image_url else [])
    horse = Horse(owner_id=current_user.id, title=body.title, price=body.price, breed=body.breed, age=body.age, gender=HorseGender(body.gender), discipline=body.discipline, height=body.height, description=body.description, vet_check_available=body.vet_check_available, vet_certificate_url=body.vet_certificate_url, image_url=image_urls[0] if image_urls else None, discount_type=DiscountType(body.discount_type) if body.discount_type else None, discount_value=body.discount_value, status="pending_review")
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
        send_pending_review_notification(admins_data, horse.title, current_user.email)
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
'''

FAVORITES_CONTENT = '''"""Favorite endpoints."""

import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import User, Horse, Favorite
from app.schemas import AddFavoriteRequest, FavoriteResponse, HorseListResponse

router = APIRouter(prefix="/api/v1", tags=["Favorites"])


@router.post("/favorites", response_model=FavoriteResponse, status_code=status.HTTP_201_CREATED, summary="Add a horse to favorites")
async def add_favorite(body: AddFavoriteRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Horse).where(Horse.id == body.horse_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Horse not found")
    result = await db.execute(select(Favorite).where((Favorite.user_id == current_user.id) & (Favorite.horse_id == body.horse_id)))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Horse already in favorites")
    favorite = Favorite(user_id=current_user.id, horse_id=body.horse_id)
    db.add(favorite)
    await db.commit()
    await db.refresh(favorite)
    return favorite


@router.delete("/favorites/{horse_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a horse from favorites")
async def remove_favorite(horse_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Favorite).where((Favorite.user_id == current_user.id) & (Favorite.horse_id == horse_id)))
    favorite = result.scalar_one_or_none()
    if not favorite:
        raise HTTPException(status_code=404, detail="Favorite not found")
    await db.delete(favorite)
    await db.commit()


@router.get("/favorites", response_model=HorseListResponse, summary="Get user favorite horses")
async def get_favorites(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Favorite.horse_id).where(Favorite.user_id == current_user.id))
    favorite_ids = [row[0] for row in result.fetchall()]
    if not favorite_ids:
        return HorseListResponse(total=0, horses=[])
    result = await db.execute(select(Horse).where(Horse.id.in_(favorite_ids)).options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)).order_by(Horse.created_at.desc()))
    horses = result.scalars().all()
    return HorseListResponse(total=len(horses), horses=horses)


@router.get("/horses/{horse_id}/is-favorite", summary="Check if horse is favorited by current user")
async def is_favorite(horse_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(Favorite).where((Favorite.user_id == current_user.id) & (Favorite.horse_id == horse_id)))
    favorite = result.scalar_one_or_none()
    return {"is_favorite": favorite is not None}
'''

NOTIFICATIONS_CONTENT = '''"""Push notification token endpoints."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User, PushToken
from app.schemas import PushTokenRegisterRequest, PushTokenUnregisterRequest

router = APIRouter(prefix="/api/v1", tags=["Notifications"])


@router.post("/notifications/push-token", status_code=status.HTTP_200_OK, summary="Register or refresh a device push token")
async def register_push_token(body: PushTokenRegisterRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(PushToken).where(PushToken.token == body.token))
    token_row = result.scalar_one_or_none()
    if token_row:
        token_row.user_id = current_user.id
        token_row.platform = body.platform
        token_row.is_active = True
        token_row.last_seen_at = datetime.now(timezone.utc)
        db.add(token_row)
    else:
        db.add(PushToken(user_id=current_user.id, token=body.token, platform=body.platform, is_active=True, last_seen_at=datetime.now(timezone.utc)))
    await db.commit()
    return {"message": "Push token registered"}


@router.post("/notifications/push-token/unregister", status_code=status.HTTP_200_OK, summary="Unregister a device push token")
async def unregister_push_token(body: PushTokenUnregisterRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(PushToken).where(PushToken.user_id == current_user.id, PushToken.token == body.token))
    token_row = result.scalar_one_or_none()
    if token_row:
        token_row.is_active = False
        token_row.last_seen_at = datetime.now(timezone.utc)
        db.add(token_row)
        await db.commit()
    return {"message": "Push token unregistered"}
'''

VOUCHERS_CONTENT = '''"""Voucher endpoints."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_current_admin
from app.database import get_db
from app.models import User, Voucher, DiscountType
from app.schemas import (
    VoucherCreateRequest, VoucherResponse, VoucherValidateRequest, VoucherValidateResponse,
)

router = APIRouter(prefix="/api/v1", tags=["Vouchers"])


@router.post("/vouchers", response_model=VoucherResponse, status_code=status.HTTP_201_CREATED, summary="Create a voucher (Admin only)")
async def create_voucher(body: VoucherCreateRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    result = await db.execute(select(Voucher).where(Voucher.code == body.code))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Voucher code already exists")
    voucher = Voucher(code=body.code, discount_type=DiscountType(body.discount_type), discount_value=body.discount_value, valid_from=body.valid_from, valid_until=body.valid_until, usage_limit=body.usage_limit, is_active=body.is_active)
    db.add(voucher)
    await db.commit()
    await db.refresh(voucher)
    return voucher


@router.get("/vouchers", response_model=list[VoucherResponse], summary="List all vouchers (Admin only)")
async def list_vouchers(db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    result = await db.execute(select(Voucher).order_by(Voucher.created_at.desc()))
    return result.scalars().all()


@router.post("/vouchers/validate", response_model=VoucherValidateResponse, summary="Validate a voucher code")
async def validate_voucher(body: VoucherValidateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
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
    new_price = None
    if body.current_price is not None:
        if voucher.discount_type == DiscountType.PERCENTAGE:
            new_price = body.current_price - (body.current_price * (voucher.discount_value / 100))
        elif voucher.discount_type == DiscountType.FIXED:
            new_price = max(0, body.current_price - voucher.discount_value)
    return VoucherValidateResponse(valid=True, message="Voucher Applied", discount_type=voucher.discount_type, discount_value=voucher.discount_value, new_price=new_price)
'''

SAVED_SEARCHES_CONTENT = '''"""Saved search and alert endpoints."""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import User, Horse, SavedSearch, SavedSearchAlert
from app.schemas import (
    SavedSearchCreateRequest, SavedSearchUpdateRequest, SavedSearchResponse,
    SavedSearchAlertResponse, SavedSearchUnreadCountResponse, HorseListResponse,
)
from app.services.saved_search import matches_saved_search

router = APIRouter(prefix="/api/v1", tags=["Saved Searches"])


@router.post("/saved-searches", response_model=SavedSearchResponse, status_code=status.HTTP_201_CREATED, summary="Create a saved search alert")
async def create_saved_search(body: SavedSearchCreateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    saved_search = SavedSearch(user_id=current_user.id, name=body.name, breed=body.breed, discipline=body.discipline, gender=body.gender, min_price=body.min_price, max_price=body.max_price, min_age=body.min_age, max_age=body.max_age, vet_check_available=body.vet_check_available, verified_seller=body.verified_seller, is_active=body.is_active)
    db.add(saved_search)
    await db.commit()
    await db.refresh(saved_search)
    return saved_search


@router.get("/saved-searches", response_model=list[SavedSearchResponse], summary="List my saved search alerts")
async def list_saved_searches(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SavedSearch).where(SavedSearch.user_id == current_user.id).order_by(SavedSearch.created_at.desc()))
    return result.scalars().all()


@router.put("/saved-searches/{saved_search_id}", response_model=SavedSearchResponse, summary="Update a saved search alert")
async def update_saved_search(saved_search_id: uuid.UUID, body: SavedSearchUpdateRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SavedSearch).where(SavedSearch.id == saved_search_id, SavedSearch.user_id == current_user.id))
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


@router.delete("/saved-searches/{saved_search_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a saved search alert")
async def delete_saved_search(saved_search_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SavedSearch).where(SavedSearch.id == saved_search_id, SavedSearch.user_id == current_user.id))
    saved_search = result.scalar_one_or_none()
    if not saved_search:
        raise HTTPException(status_code=404, detail="Saved search not found")
    await db.delete(saved_search)
    await db.commit()


@router.get("/saved-searches/{saved_search_id}/matches", response_model=HorseListResponse, summary="Get horses matching a saved search")
async def get_saved_search_matches(saved_search_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SavedSearch).where(SavedSearch.id == saved_search_id, SavedSearch.user_id == current_user.id))
    saved_search = result.scalar_one_or_none()
    if not saved_search:
        raise HTTPException(status_code=404, detail="Saved search not found")
    horses_result = await db.execute(select(Horse).where(Horse.status == "approved").options(selectinload(Horse.owner).selectinload(User.profile), selectinload(Horse.images)).order_by(Horse.created_at.desc()))
    all_horses = horses_result.scalars().all()
    matched = [h for h in all_horses if matches_saved_search(h, saved_search)]
    return HorseListResponse(total=len(matched), horses=matched)


@router.get("/saved-search-alerts", response_model=list[SavedSearchAlertResponse], summary="List my saved search inbox alerts")
async def list_saved_search_alerts(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SavedSearchAlert).where(SavedSearchAlert.user_id == current_user.id).order_by(SavedSearchAlert.created_at.desc()).limit(100))
    return result.scalars().all()


@router.get("/saved-search-alerts/unread-count", response_model=SavedSearchUnreadCountResponse, summary="Get unread saved search alerts count")
async def saved_search_alerts_unread_count(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(func.count()).select_from(SavedSearchAlert).where(SavedSearchAlert.user_id == current_user.id, SavedSearchAlert.is_read == False))
    unread = result.scalar() or 0
    return SavedSearchUnreadCountResponse(unread_count=unread)


@router.post("/saved-search-alerts/{alert_id}/read", response_model=SavedSearchAlertResponse, summary="Mark one saved search alert as read")
async def mark_saved_search_alert_read(alert_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SavedSearchAlert).where(SavedSearchAlert.id == alert_id, SavedSearchAlert.user_id == current_user.id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_read = True
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


@router.post("/saved-search-alerts/read-all", response_model=SavedSearchUnreadCountResponse, summary="Mark all saved search alerts as read")
async def mark_all_saved_search_alerts_read(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(SavedSearchAlert).where(SavedSearchAlert.user_id == current_user.id, SavedSearchAlert.is_read == False))
    alerts = result.scalars().all()
    for alert in alerts:
        alert.is_read = True
        db.add(alert)
    await db.commit()
    return SavedSearchUnreadCountResponse(unread_count=0)
'''

files = {
    "horses.py": HORSES_CONTENT,
    "favorites.py": FAVORITES_CONTENT,
    "notifications.py": NOTIFICATIONS_CONTENT,
    "vouchers.py": VOUCHERS_CONTENT,
    "saved_searches.py": SAVED_SEARCHES_CONTENT,
}

for filename, content in files.items():
    filepath = os.path.join(ROUTERS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Created {filename}")

print("All router files generated successfully!")