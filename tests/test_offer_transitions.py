import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.main import (
    accept_offer,
    add_offer_transition_audit,
    apply_offer_transition,
    cancel_offer,
    counter_offer,
    get_offer_actor,
    get_action_required_offers_count,
    mark_offer_horse_sold,
    notify_offer_event,
    persist_offer_transition,
    reject_offer,
    reopen_horse_listing,
)
from app.models import Horse, HorseGender, Offer, OfferStatus, OfferTransitionAudit, PushDeliveryLog, User, UserRole
from app.schemas import (
    OfferAcceptRequest,
    OfferCancelRequest,
    OfferCounterRequest,
    OfferRejectRequest,
)


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeResult:
    def __init__(self, scalar_value=None, scalars_items=None, all_items=None):
        self._scalar_value = scalar_value
        self._scalars_items = scalars_items or []
        self._all_items = all_items or []

    def scalar(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalars(self):
        return FakeScalars(self._scalars_items)

    def all(self):
        return list(self._all_items)


class FakeDB:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commit_calls = 0
        self.refreshed = []

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_calls += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)


def make_user(user_id, role=UserRole.BUYER):
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="x",
        role=role,
        is_verified=True,
        language="en",
    )


def make_horse(horse_id, owner_id, status="approved"):
    return Horse(
        id=horse_id,
        owner_id=owner_id,
        title="Test Horse",
        price=10000,
        breed="Arabian",
        age=7,
        gender=HorseGender.MARE,
        discipline=None,
        height=None,
        description=None,
        status=status,
        vet_check_available=False,
        vet_certificate_url=None,
        image_url=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_offer(offer_id, buyer_id, seller_id, horse_id, status=OfferStatus.PENDING):
    return Offer(
        id=offer_id,
        buyer_id=buyer_id,
        seller_id=seller_id,
        horse_id=horse_id,
        amount=9000,
        status=status,
        message="offer",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_apply_offer_transition_pending_to_countered_sets_fields():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.PENDING)

    apply_offer_transition(
        offer=offer,
        to_status=OfferStatus.COUNTERED,
        actor="seller",
        response_message="counter",
        counter_amount=9500,
    )

    assert offer.status == OfferStatus.COUNTERED
    assert offer.counter_amount == 9500
    assert offer.response_message == "counter"
    assert offer.responded_at is not None


@pytest.mark.asyncio
async def test_apply_offer_transition_invalid_actor_is_forbidden():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.PENDING)

    with pytest.raises(HTTPException) as exc:
        apply_offer_transition(
            offer=offer,
            to_status=OfferStatus.ACCEPTED,
            actor="buyer",
            response_message="accept",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_apply_offer_transition_invalid_state_change_rejected():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.ACCEPTED)

    with pytest.raises(HTTPException) as exc:
        apply_offer_transition(
            offer=offer,
            to_status=OfferStatus.REJECTED,
            actor="seller",
            response_message="reject",
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_apply_offer_transition_counter_requires_positive_amount():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.PENDING)

    with pytest.raises(HTTPException) as exc:
        apply_offer_transition(
            offer=offer,
            to_status=OfferStatus.COUNTERED,
            actor="seller",
            counter_amount=0,
        )

    assert exc.value.status_code == 400


def test_get_offer_actor_maps_offer_participants():
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    offer = make_offer(uuid.uuid4(), buyer_id, seller_id, uuid.uuid4(), OfferStatus.PENDING)

    assert get_offer_actor(offer, make_user(seller_id, role=UserRole.SELLER)) == "seller"
    assert get_offer_actor(offer, make_user(buyer_id, role=UserRole.BUYER)) == "buyer"
    assert get_offer_actor(offer, make_user(uuid.uuid4(), role=UserRole.BUYER)) == "unknown"


def test_add_offer_transition_audit_adds_row_to_session():
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    offer = make_offer(uuid.uuid4(), buyer_id, seller_id, uuid.uuid4(), OfferStatus.PENDING)
    db = FakeDB([])

    add_offer_transition_audit(
        db=db,
        offer=offer,
        from_status=OfferStatus.PENDING,
        to_status=OfferStatus.COUNTERED,
        actor="seller",
        changed_by_user_id=seller_id,
        response_message="counter",
    )

    audits = [obj for obj in db.added if isinstance(obj, OfferTransitionAudit)]
    assert len(audits) == 1
    assert audits[0].offer_id == offer.id
    assert audits[0].from_status == OfferStatus.PENDING.value
    assert audits[0].to_status == OfferStatus.COUNTERED.value


@pytest.mark.asyncio
async def test_persist_offer_transition_commits_and_refreshes_by_default():
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    offer = make_offer(uuid.uuid4(), buyer_id, seller_id, uuid.uuid4(), OfferStatus.PENDING)
    db = FakeDB([])

    result = await persist_offer_transition(
        db=db,
        offer=offer,
        to_status=OfferStatus.COUNTERED,
        actor="seller",
        changed_by_user_id=seller_id,
        response_message="counter",
        counter_amount=9500,
    )

    assert result.status == OfferStatus.COUNTERED
    assert db.commit_calls == 1
    assert db.refreshed == [offer]


@pytest.mark.asyncio
async def test_reopen_horse_listing_owner_success():
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="sold")
    current_user = make_user(owner_id, role=UserRole.SELLER)
    db = FakeDB([FakeResult(scalar_value=horse)])

    result = await reopen_horse_listing(horse.id, db=db, current_user=current_user)

    assert result.status == "approved"
    assert db.commit_calls == 1


@pytest.mark.asyncio
async def test_reopen_horse_listing_forbidden_for_non_owner_non_admin():
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="sold")
    current_user = make_user(uuid.uuid4(), role=UserRole.BUYER)
    db = FakeDB([FakeResult(scalar_value=horse)])

    with pytest.raises(HTTPException) as exc:
        await reopen_horse_listing(horse.id, db=db, current_user=current_user)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reopen_horse_listing_only_for_sold_status():
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="pending_review")
    current_user = make_user(owner_id, role=UserRole.SELLER)
    db = FakeDB([FakeResult(scalar_value=horse)])

    with pytest.raises(HTTPException) as exc:
        await reopen_horse_listing(horse.id, db=db, current_user=current_user)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reopen_horse_listing_is_idempotent_when_already_approved():
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="approved")
    current_user = make_user(owner_id, role=UserRole.SELLER)
    db = FakeDB([FakeResult(scalar_value=horse)])

    result = await reopen_horse_listing(horse.id, db=db, current_user=current_user)

    assert result.status == "approved"
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_get_action_required_offers_count_returns_scalar_count():
    current_user = make_user(uuid.uuid4(), role=UserRole.BOTH)
    db = FakeDB([FakeResult(scalar_value=4)])

    result = await get_action_required_offers_count(db=db, current_user=current_user)

    assert result.actionable_count == 4


@pytest.mark.asyncio
async def test_cancel_offer_only_buyer_can_cancel():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.PENDING)
    current_user = make_user(uuid.uuid4(), role=UserRole.SELLER)
    db = FakeDB([FakeResult(scalar_value=offer)])

    with pytest.raises(HTTPException) as exc:
        await cancel_offer(
            offer.id,
            OfferCancelRequest(response_message="cancel"),
            db=db,
            current_user=current_user,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cancel_offer_is_idempotent_for_existing_cancelled_offer():
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    offer = make_offer(uuid.uuid4(), buyer_id, seller_id, horse_id, OfferStatus.CANCELLED)
    buyer = make_user(buyer_id, role=UserRole.BUYER)
    seller = make_user(seller_id, role=UserRole.SELLER)
    horse = make_horse(horse_id, seller_id, status="approved")
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=buyer),
        FakeResult(scalar_value=seller),
        FakeResult(scalar_value=horse),
    ])

    result = await cancel_offer(
        offer.id,
        OfferCancelRequest(response_message="cancel"),
        db=db,
        current_user=buyer,
    )

    assert result.status == OfferStatus.CANCELLED.value
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_counter_offer_only_seller_can_counter():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.PENDING)
    current_user = make_user(uuid.uuid4(), role=UserRole.BUYER)
    db = FakeDB([FakeResult(scalar_value=offer)])

    with pytest.raises(HTTPException) as exc:
        await counter_offer(
            offer.id,
            OfferCounterRequest(counter_amount=9200, response_message="counter"),
            db=db,
            current_user=current_user,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_accept_offer_unknown_actor_forbidden():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.PENDING)
    current_user = make_user(uuid.uuid4(), role=UserRole.BUYER)
    db = FakeDB([FakeResult(scalar_value=offer)])

    with pytest.raises(HTTPException) as exc:
        await accept_offer(
            offer.id,
            OfferAcceptRequest(response_message="accept"),
            db=db,
            current_user=current_user,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_accept_offer_is_idempotent_for_existing_accepted_offer():
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    offer = make_offer(uuid.uuid4(), buyer_id, seller_id, horse_id, OfferStatus.ACCEPTED)
    buyer = make_user(buyer_id, role=UserRole.BUYER)
    seller = make_user(seller_id, role=UserRole.SELLER)
    horse = make_horse(horse_id, seller_id, status="approved")
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=buyer),
        FakeResult(scalar_value=seller),
        FakeResult(scalar_value=horse),
    ])

    result = await accept_offer(
        offer.id,
        OfferAcceptRequest(response_message="accept"),
        db=db,
        current_user=seller,
    )

    assert result.status == OfferStatus.ACCEPTED.value
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_reject_offer_unknown_actor_forbidden():
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), OfferStatus.PENDING)
    current_user = make_user(uuid.uuid4(), role=UserRole.BUYER)
    db = FakeDB([FakeResult(scalar_value=offer)])

    with pytest.raises(HTTPException) as exc:
        await reject_offer(
            offer.id,
            OfferRejectRequest(response_message="reject"),
            db=db,
            current_user=current_user,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_reject_offer_is_idempotent_for_existing_rejected_offer():
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    offer = make_offer(uuid.uuid4(), buyer_id, seller_id, horse_id, OfferStatus.REJECTED)
    buyer = make_user(buyer_id, role=UserRole.BUYER)
    seller = make_user(seller_id, role=UserRole.SELLER)
    horse = make_horse(horse_id, seller_id, status="approved")
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=buyer),
        FakeResult(scalar_value=seller),
        FakeResult(scalar_value=horse),
    ])

    result = await reject_offer(
        offer.id,
        OfferRejectRequest(response_message="reject"),
        db=db,
        current_user=buyer,
    )

    assert result.status == OfferStatus.REJECTED.value
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_mark_offer_horse_sold_closes_other_open_offers(monkeypatch):
    seller_id = uuid.uuid4()
    accepted_buyer_id = uuid.uuid4()
    other_buyer_id = uuid.uuid4()
    horse_id = uuid.uuid4()

    accepted_offer = make_offer(
        uuid.uuid4(),
        accepted_buyer_id,
        seller_id,
        horse_id,
        status=OfferStatus.ACCEPTED,
    )
    other_offer = make_offer(
        uuid.uuid4(),
        other_buyer_id,
        seller_id,
        horse_id,
        status=OfferStatus.PENDING,
    )
    horse = make_horse(horse_id, seller_id, status="approved")

    other_buyer = make_user(other_buyer_id, role=UserRole.BUYER)
    accepted_buyer = make_user(accepted_buyer_id, role=UserRole.BUYER)

    db = FakeDB(
        [
            FakeResult(scalar_value=accepted_offer),
            FakeResult(scalar_value=horse),
            FakeResult(scalars_items=[other_offer]),
            FakeResult(scalar_value=other_buyer),
            FakeResult(scalar_value=accepted_buyer),
        ]
    )

    notifications = []

    async def fake_notify_offer_event(**kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr("app.main.notify_offer_event", fake_notify_offer_event)

    current_user = make_user(seller_id, role=UserRole.SELLER)
    result = await mark_offer_horse_sold(accepted_offer.id, db=db, current_user=current_user)

    assert result["message"] == "Horse marked as sold"
    assert result["closed_offers"] == 1
    assert horse.status == "sold"
    assert other_offer.status == OfferStatus.CANCELLED
    assert len(notifications) == 2
    assert any(isinstance(obj, OfferTransitionAudit) for obj in db.added)


@pytest.mark.asyncio
async def test_mark_offer_horse_sold_requires_accepted_offer():
    seller_id = uuid.uuid4()
    offer = make_offer(
        uuid.uuid4(),
        uuid.uuid4(),
        seller_id,
        uuid.uuid4(),
        status=OfferStatus.PENDING,
    )
    db = FakeDB([FakeResult(scalar_value=offer)])
    current_user = make_user(seller_id, role=UserRole.SELLER)

    with pytest.raises(HTTPException) as exc:
        await mark_offer_horse_sold(offer.id, db=db, current_user=current_user)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_notify_offer_event_records_push_delivery_log(monkeypatch):
    target_user = make_user(uuid.uuid4(), role=UserRole.BUYER)
    horse = make_horse(uuid.uuid4(), uuid.uuid4(), status="approved")
    db = FakeDB([
        FakeResult(all_items=[("ExponentPushToken[test]",)]),
    ])

    monkeypatch.setattr("app.main.send_offer_update_email", lambda **kwargs: True)
    monkeypatch.setattr(
        "app.main.send_expo_push_notifications_result",
        lambda **kwargs: {
            "total_tokens": 1,
            "accepted_count": 1,
            "failed_count": 0,
            "status": "success",
            "error_message": None,
        },
    )

    await notify_offer_event(
        db=db,
        target_user=target_user,
        horse=horse,
        title_en="t",
        body_en="b",
        title_ar="t",
        body_ar="b",
        data={"type": "offer_accepted"},
    )

    logs = [obj for obj in db.added if isinstance(obj, PushDeliveryLog)]
    assert len(logs) == 1
    assert logs[0].target_user_id == target_user.id
    assert logs[0].status == "success"
    assert logs[0].accepted_count == 1
