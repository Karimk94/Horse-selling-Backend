"""Favorite endpoints."""

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
