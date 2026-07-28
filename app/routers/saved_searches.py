"""Saved search and alert endpoints."""

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
