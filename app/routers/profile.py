"""User profile endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import User, UserProfile, UserRole
from app.schemas import UserResponse, UserProfileUpdate

router = APIRouter(prefix="/api/v1", tags=["User"])


@router.get(
    "/profile",
    response_model=UserResponse,
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


@router.put(
    "/profile",
    response_model=UserResponse,
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
        if user.role != UserRole.ADMIN:
            user.role = UserRole(body.role)

    if not user.profile:
        user.profile = UserProfile(user_id=user.id)

    if body.first_name is not None:
        user.profile.first_name = body.first_name
    if body.last_name is not None:
        user.profile.last_name = body.last_name
    if body.phone_number is not None:
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

    result = await db.execute(
        select(User).where(User.id == user.id).options(selectinload(User.profile))
    )
    user = result.scalar_one()
    return user