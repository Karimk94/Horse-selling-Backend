import uuid
import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app, get_current_admin, get_current_user, get_db
from app.models import Horse, HorseGender, IdempotencyKey, Offer, OfferStatus, OfferTransitionAudit, PushDeliveryLog, User, UserRole


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeResult:
    def __init__(self, scalar_value=None, scalars_items=None):
        self._scalar_value = scalar_value
        self._scalars_items = scalars_items or []

    def scalar(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalars(self):
        return FakeScalars(self._scalars_items)


class FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

    def add(self, _obj):
        return None

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


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
async def test_reopen_horse_listing_not_found_http(client):
    db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(uuid.uuid4(), role=UserRole.SELLER)

    response = await client.post(f"/api/v1/horses/{uuid.uuid4()}/reopen")

    assert response.status_code == 404
    assert response.json()["detail"] == "Listing not found"


@pytest.mark.asyncio
async def test_reopen_horse_listing_is_idempotent_when_already_approved_http(client):
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="approved")
    db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(owner_id, role=UserRole.SELLER)

    response = await client.post(f"/api/v1/horses/{horse.id}/reopen")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reopen_horse_listing_requires_sold_status_http(client):
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="pending_review")
    db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(owner_id, role=UserRole.SELLER)

    response = await client.post(f"/api/v1/horses/{horse.id}/reopen")

    assert response.status_code == 400
    assert response.json()["detail"] == "Only sold listings can be reopened"


@pytest.mark.asyncio
async def test_mark_offer_horse_sold_offer_not_found_http(client):
    db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(uuid.uuid4(), role=UserRole.SELLER)

    response = await client.post(f"/api/v1/offers/{uuid.uuid4()}/mark-sold")

    assert response.status_code == 404
    assert response.json()["detail"] == "Offer not found"


@pytest.mark.asyncio
async def test_mark_offer_horse_sold_requires_accepted_offer_http(client):
    seller_id = uuid.uuid4()
    offer = make_offer(
        uuid.uuid4(),
        uuid.uuid4(),
        seller_id,
        uuid.uuid4(),
        status=OfferStatus.PENDING,
    )
    db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(seller_id, role=UserRole.SELLER)

    response = await client.post(f"/api/v1/offers/{offer.id}/mark-sold")

    assert response.status_code == 400
    assert response.json()["detail"] == "Only accepted offers can be marked sold"


@pytest.mark.asyncio
async def test_mark_offer_horse_sold_horse_not_found_http(client):
    seller_id = uuid.uuid4()
    offer = make_offer(
        uuid.uuid4(),
        uuid.uuid4(),
        seller_id,
        uuid.uuid4(),
        status=OfferStatus.ACCEPTED,
    )
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=None),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(seller_id, role=UserRole.SELLER)

    response = await client.post(f"/api/v1/offers/{offer.id}/mark-sold")

    assert response.status_code == 404
    assert response.json()["detail"] == "Horse not found"


@pytest.mark.asyncio
async def test_mark_offer_horse_sold_already_sold_contract_http(client):
    seller_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    offer = make_offer(
        uuid.uuid4(),
        uuid.uuid4(),
        seller_id,
        horse_id,
        status=OfferStatus.ACCEPTED,
    )
    horse = make_horse(horse_id, seller_id, status="sold")
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=horse),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(seller_id, role=UserRole.SELLER)

    response = await client.post(f"/api/v1/offers/{offer.id}/mark-sold")

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Horse already marked as sold"
    assert payload["horse_id"] == str(horse_id)


@pytest.mark.asyncio
async def test_cancel_offer_already_cancelled_returns_current_state_http(client):
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    offer = make_offer(
        uuid.uuid4(),
        buyer_id,
        seller_id,
        horse_id,
        status=OfferStatus.CANCELLED,
    )
    buyer = make_user(buyer_id, role=UserRole.BUYER)
    seller = make_user(seller_id, role=UserRole.SELLER)
    horse = make_horse(horse_id, seller_id, status="approved")
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=buyer),
        FakeResult(scalar_value=seller),
        FakeResult(scalar_value=horse),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: buyer

    response = await client.put(
        f"/api/v1/offers/{offer.id}/cancel",
        json={"response_message": "cancel"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == OfferStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_accept_offer_already_accepted_returns_current_state_http(client):
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    offer = make_offer(
        uuid.uuid4(),
        buyer_id,
        seller_id,
        horse_id,
        status=OfferStatus.ACCEPTED,
    )
    buyer = make_user(buyer_id, role=UserRole.BUYER)
    seller = make_user(seller_id, role=UserRole.SELLER)
    horse = make_horse(horse_id, seller_id, status="approved")
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=buyer),
        FakeResult(scalar_value=seller),
        FakeResult(scalar_value=horse),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: seller

    response = await client.put(
        f"/api/v1/offers/{offer.id}/accept",
        json={"response_message": "accept"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == OfferStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_admin_offer_transition_audits_history_success_http(client):
    admin_user = make_user(uuid.uuid4(), role=UserRole.ADMIN)
    offer = make_offer(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        status=OfferStatus.ACCEPTED,
    )
    audit = OfferTransitionAudit(
        id=uuid.uuid4(),
        offer_id=offer.id,
        changed_by_user_id=offer.seller_id,
        from_status=OfferStatus.PENDING.value,
        to_status=OfferStatus.ACCEPTED.value,
        actor="seller",
        response_message="accepted",
        created_at=datetime.now(timezone.utc),
    )
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[audit]),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = lambda: admin_user

    response = await client.get(f"/api/v1/admin/offers/{offer.id}/transitions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["count"] == 1
    assert payload["logs"][0]["offer_id"] == str(offer.id)
    assert payload["logs"][0]["from_status"] == OfferStatus.PENDING.value
    assert payload["logs"][0]["to_status"] == OfferStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_admin_offer_transition_audits_history_not_found_http(client):
    admin_user = make_user(uuid.uuid4(), role=UserRole.ADMIN)
    db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = lambda: admin_user

    response = await client.get(f"/api/v1/admin/offers/{uuid.uuid4()}/transitions")

    assert response.status_code == 404
    assert response.json()["detail"] == "Offer not found"


@pytest.mark.asyncio
async def test_admin_offer_transition_audits_history_filters_http(client):
    admin_user = make_user(uuid.uuid4(), role=UserRole.ADMIN)
    offer = make_offer(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        status=OfferStatus.ACCEPTED,
    )
    audit = OfferTransitionAudit(
        id=uuid.uuid4(),
        offer_id=offer.id,
        changed_by_user_id=offer.seller_id,
        from_status=OfferStatus.PENDING.value,
        to_status=OfferStatus.ACCEPTED.value,
        actor="seller",
        response_message="accepted",
        created_at=datetime.now(timezone.utc),
    )
    db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[audit]),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = lambda: admin_user

    response = await client.get(
        f"/api/v1/admin/offers/{offer.id}/transitions"
        "?actor=seller&to_status=accepted&skip=0&limit=10"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["count"] == 1
    assert payload["logs"][0]["actor"] == "seller"
    assert payload["logs"][0]["to_status"] == OfferStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_cancel_offer_replays_cached_response_with_idempotency_key_http(client):
    buyer = make_user(uuid.uuid4(), role=UserRole.BUYER)
    offer_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    key = "idem-cancel-1"
    payload = {
        "id": str(offer_id),
        "buyer_id": str(buyer.id),
        "seller_id": str(uuid.uuid4()),
        "horse_id": str(horse_id),
        "amount": 9000,
        "counter_amount": None,
        "status": OfferStatus.CANCELLED.value,
        "message": "offer",
        "response_message": "cancel",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "responded_at": datetime.now(timezone.utc).isoformat(),
        "buyer_email": buyer.email,
        "seller_email": "seller@example.com",
        "horse_title": "Replay Horse",
    }
    record = IdempotencyKey(
        id=uuid.uuid4(),
        user_id=buyer.id,
        request_key=key,
        action=f"offer:{offer_id}:cancel",
        response_body=json.dumps(payload),
        created_at=datetime.now(timezone.utc),
    )
    db = FakeDB([FakeResult(scalar_value=record)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: buyer

    response = await client.put(
        f"/api/v1/offers/{offer_id}/cancel",
        json={"response_message": "cancel"},
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(offer_id)
    assert response.json()["status"] == OfferStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_mark_sold_replays_cached_response_with_idempotency_key_http(client):
    seller = make_user(uuid.uuid4(), role=UserRole.SELLER)
    offer_id = uuid.uuid4()
    horse_id = uuid.uuid4()
    key = "idem-mark-sold-1"
    payload = {
        "message": "Horse marked as sold",
        "horse_id": str(horse_id),
        "closed_offers": 2,
    }
    record = IdempotencyKey(
        id=uuid.uuid4(),
        user_id=seller.id,
        request_key=key,
        action=f"offer:{offer_id}:mark-sold",
        response_body=json.dumps(payload),
        created_at=datetime.now(timezone.utc),
    )
    db = FakeDB([FakeResult(scalar_value=record)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: seller

    response = await client.post(
        f"/api/v1/offers/{offer_id}/mark-sold",
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 200
    assert response.json()["horse_id"] == str(horse_id)
    assert response.json()["closed_offers"] == 2


@pytest.mark.asyncio
async def test_admin_push_delivery_logs_filters_and_pagination_http(client):
    admin_user = make_user(uuid.uuid4(), role=UserRole.ADMIN)
    log_row = PushDeliveryLog(
        id=uuid.uuid4(),
        target_user_id=uuid.uuid4(),
        provider="expo",
        event_type="offer_accepted",
        total_tokens=2,
        accepted_count=1,
        failed_count=1,
        status="failed",
        error_message="network",
        created_at=datetime.now(timezone.utc),
    )
    db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[log_row]),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = lambda: admin_user

    response = await client.get(
        "/api/v1/admin/notifications/push-delivery-logs"
        "?status_filter=failed&event_type=offer_accepted&skip=0&limit=10"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["count"] == 1
    assert payload["logs"][0]["status"] == "failed"
    assert payload["logs"][0]["event_type"] == "offer_accepted"


@pytest.mark.asyncio
async def test_admin_push_delivery_logs_empty_result_http(client):
    admin_user = make_user(uuid.uuid4(), role=UserRole.ADMIN)
    db = FakeDB([
        FakeResult(scalar_value=0),
        FakeResult(scalars_items=[]),
    ])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = lambda: admin_user

    response = await client.get("/api/v1/admin/notifications/push-delivery-logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["count"] == 0
    assert payload["logs"] == []
