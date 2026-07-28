"""Push notification token endpoints."""

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
