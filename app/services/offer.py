"""Offer service: transition logic, notification, and response building."""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User, Horse, Offer, OfferStatus, OfferTransitionAudit,
    PushToken, PushDeliveryLog,
)
from app.schemas import OfferResponse
from app.email_service import (
    send_offer_update_email,
    send_expo_push_notifications_result,
)


async def notify_offer_event(
    db: AsyncSession,
    target_user: User,
    horse: Horse,
    title_en: str,
    body_en: str,
    title_ar: str,
    body_ar: str,
    data: dict | None = None,
) -> None:
    language = target_user.language or "en"
    title = title_ar if language == "ar" else title_en
    message = body_ar if language == "ar" else body_en

    send_offer_update_email(
        user_email=target_user.email,
        horse_title=horse.title,
        update_title=title,
        update_message=message,
        language=language,
    )

    push_tokens_result = await db.execute(
        select(PushToken.token).where(
            PushToken.user_id == target_user.id,
            PushToken.is_active == True,
        )
    )
    tokens = [row[0] for row in push_tokens_result.all()]
    push_result = send_expo_push_notifications_result(
        tokens=tokens,
        title=title,
        body=message,
        data=data or {},
    )

    db.add(
        PushDeliveryLog(
            target_user_id=target_user.id,
            provider="expo",
            event_type=(data or {}).get("type"),
            total_tokens=push_result.get("total_tokens", 0),
            accepted_count=push_result.get("accepted_count", 0),
            failed_count=push_result.get("failed_count", 0),
            status=push_result.get("status", "failed"),
            error_message=push_result.get("error_message"),
        )
    )
    await db.commit()


async def notify_offer_participant(
    db: AsyncSession,
    target_user: User,
    horse: Horse,
    offer: Offer,
    event_type: str,
    title_en: str,
    body_en: str,
    title_ar: str,
    body_ar: str,
) -> None:
    await notify_offer_event(
        db=db,
        target_user=target_user,
        horse=horse,
        title_en=title_en,
        body_en=body_en,
        title_ar=title_ar,
        body_ar=body_ar,
        data={
            "horse_id": str(horse.id),
            "offer_id": str(offer.id),
            "type": event_type,
        },
    )


def get_offer_actor(offer: Offer, current_user: User) -> str:
    if current_user.id == offer.seller_id:
        return "seller"
    if current_user.id == offer.buyer_id:
        return "buyer"
    return "unknown"


def add_offer_transition_audit(
    db: AsyncSession,
    offer: Offer,
    from_status: OfferStatus,
    to_status: OfferStatus,
    actor: str,
    changed_by_user_id: uuid.UUID | None,
    response_message: str | None,
) -> None:
    db.add(
        OfferTransitionAudit(
            offer_id=offer.id,
            changed_by_user_id=changed_by_user_id,
            from_status=from_status.value,
            to_status=to_status.value,
            actor=actor,
            response_message=response_message,
        )
    )


def apply_offer_transition(
    offer: Offer,
    to_status: OfferStatus,
    actor: str,
    response_message: str | None = None,
    counter_amount: float | None = None,
) -> None:
    """Validate and apply offer status transitions in one place."""
    current = offer.status

    allowed: dict[OfferStatus, dict[OfferStatus, set[str]]] = {
        OfferStatus.PENDING: {
            OfferStatus.COUNTERED: {"seller"},
            OfferStatus.ACCEPTED: {"seller"},
            OfferStatus.REJECTED: {"seller"},
            OfferStatus.CANCELLED: {"buyer", "system"},
        },
        OfferStatus.COUNTERED: {
            OfferStatus.ACCEPTED: {"buyer"},
            OfferStatus.REJECTED: {"buyer", "seller"},
            OfferStatus.CANCELLED: {"system"},
        },
    }

    if current not in allowed or to_status not in allowed[current]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid offer transition: {current.value} -> {to_status.value}",
        )

    if actor not in allowed[current][to_status]:
        raise HTTPException(
            status_code=403,
            detail="Not authorized for this offer transition",
        )

    if to_status == OfferStatus.COUNTERED:
        if counter_amount is None or counter_amount <= 0:
            raise HTTPException(status_code=400, detail="Counter amount must be greater than 0")
        offer.counter_amount = counter_amount

    offer.status = to_status
    offer.response_message = response_message
    offer.responded_at = datetime.now(timezone.utc)


async def persist_offer_transition(
    db: AsyncSession,
    offer: Offer,
    to_status: OfferStatus,
    actor: str,
    changed_by_user_id: uuid.UUID | None,
    response_message: str | None = None,
    counter_amount: float | None = None,
    *,
    commit: bool = True,
    refresh: bool = True,
) -> Offer:
    from_status = offer.status

    apply_offer_transition(
        offer=offer,
        to_status=to_status,
        actor=actor,
        response_message=response_message,
        counter_amount=counter_amount,
    )
    db.add(offer)
    add_offer_transition_audit(
        db=db,
        offer=offer,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        changed_by_user_id=changed_by_user_id,
        response_message=response_message,
    )

    if commit:
        await db.commit()
    if refresh:
        await db.refresh(offer)

    return offer


async def load_offer_context(
    db: AsyncSession,
    offer: Offer,
    *,
    horse: Horse | None = None,
) -> tuple[User, User, Horse]:
    buyer_result = await db.execute(select(User).where(User.id == offer.buyer_id))
    buyer = buyer_result.scalar_one()

    seller_result = await db.execute(select(User).where(User.id == offer.seller_id))
    seller = seller_result.scalar_one()

    resolved_horse = horse
    if resolved_horse is None:
        horse_result = await db.execute(select(Horse).where(Horse.id == offer.horse_id))
        resolved_horse = horse_result.scalar_one()

    return buyer, seller, resolved_horse


async def build_offer_response(db: AsyncSession, offer: Offer) -> OfferResponse:
    buyer, seller, horse = await load_offer_context(db, offer)

    return OfferResponse(
        id=offer.id,
        buyer_id=offer.buyer_id,
        seller_id=offer.seller_id,
        horse_id=offer.horse_id,
        amount=offer.amount,
        counter_amount=offer.counter_amount,
        status=offer.status.value,
        message=offer.message,
        response_message=offer.response_message,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
        responded_at=offer.responded_at,
        buyer_email=buyer.email,
        seller_email=seller.email,
        horse_title=horse.title,
    )
