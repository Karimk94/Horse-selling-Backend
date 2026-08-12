"""Idempotency key helpers for safe request replay."""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdempotencyKey


def sanitize_idempotency_key(idempotency_key: str | None) -> str | None:
    if idempotency_key is None:
        return None
    value = idempotency_key.strip()
    return value or None


async def get_idempotent_replay(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    idempotency_key: str | None,
) -> dict | None:
    key = sanitize_idempotency_key(idempotency_key)
    if not key:
        return None

    result = await db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.request_key == key,
            IdempotencyKey.action == action,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return None

    try:
        parsed = json.loads(record.response_body)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


async def store_idempotent_replay(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    idempotency_key: str | None,
    response_payload: dict,
) -> None:
    key = sanitize_idempotency_key(idempotency_key)
    if not key:
        return

    existing = await db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.request_key == key,
            IdempotencyKey.action == action,
        )
    )
    if existing.scalar_one_or_none():
        return

    db.add(
        IdempotencyKey(
            user_id=user_id,
            request_key=key,
            action=action,
            response_body=json.dumps(response_payload),
        )
    )


async def finalize_idempotent_replay(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    idempotency_key: str | None,
    response_payload: dict,
) -> None:
    await store_idempotent_replay(db, user_id, action, idempotency_key, response_payload)
    if sanitize_idempotency_key(idempotency_key):
        await db.commit()