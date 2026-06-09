"""Additional branch tests for offers and admin push log endpoints."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.auth import get_current_user
from app.main import app, get_current_admin, get_db
from app.models import Offer, OfferStatus, PushDeliveryLog, User, UserRole


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


def make_user(role=UserRole.BUYER):
    user_id = uuid.uuid4()
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="x",
        role=role,
        is_verified=True,
        language="en",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_offer(status=OfferStatus.PENDING):
    return Offer(
        id=uuid.uuid4(),
        buyer_id=uuid.uuid4(),
        seller_id=uuid.uuid4(),
        horse_id=uuid.uuid4(),
        amount=10000,
        counter_amount=None,
        status=status,
        message="Offer",
        response_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        responded_at=None,
    )


def make_push_log(status_value="success", event_type="offer_new"):
    return PushDeliveryLog(
        id=uuid.uuid4(),
        target_user_id=uuid.uuid4(),
        provider="expo",
        event_type=event_type,
        total_tokens=2,
        accepted_count=2,
        failed_count=0,
        status=status_value,
        error_message=None,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_get_action_required_offers_count_returns_value(client):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([FakeResult(scalar_value=4)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/offers/action-required-count")

    assert response.status_code == 200
    assert response.json()["actionable_count"] == 4


@pytest.mark.asyncio
async def test_list_push_delivery_logs_admin_with_filters(client):
    admin = make_user(role=UserRole.ADMIN)
    log_item = make_push_log(status_value="failed", event_type="offer_counter")
    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[log_item]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.get(
        "/api/v1/admin/notifications/push-delivery-logs?status_filter=failed&event_type=offer_counter"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["count"] == 1
    assert payload["logs"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_list_push_delivery_logs_admin_without_filters(client):
    admin = make_user(role=UserRole.ADMIN)
    log_item = make_push_log(status_value="success", event_type="offer_new")
    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[log_item]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.get("/api/v1/admin/notifications/push-delivery-logs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["count"] == 1


@pytest.mark.asyncio
async def test_accept_offer_already_accepted_unknown_actor_forbidden(client):
    unknown_user = make_user(role=UserRole.BUYER)
    offer = make_offer(status=OfferStatus.ACCEPTED)
    # Ensure caller is neither buyer nor seller
    offer.buyer_id = uuid.uuid4()
    offer.seller_id = uuid.uuid4()

    fake_db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return unknown_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{offer.id}/accept",
        json={"response_message": "accept"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reject_offer_already_rejected_unknown_actor_forbidden(client):
    unknown_user = make_user(role=UserRole.BUYER)
    offer = make_offer(status=OfferStatus.REJECTED)
    # Ensure caller is neither buyer nor seller
    offer.buyer_id = uuid.uuid4()
    offer.seller_id = uuid.uuid4()

    fake_db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return unknown_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{offer.id}/reject",
        json={"response_message": "reject"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cancel_offer_replay_short_circuit(client, monkeypatch):
    user = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    async def fake_replay(*_args, **_kwargs):
        return {
            "id": str(uuid.uuid4()),
            "buyer_id": str(user.id),
            "seller_id": str(uuid.uuid4()),
            "horse_id": str(uuid.uuid4()),
            "amount": 10000,
            "counter_amount": None,
            "status": "cancelled",
            "message": "Offer",
            "response_message": "cancel",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "responded_at": None,
            "buyer_email": user.email,
            "seller_email": "seller@example.com",
            "horse_title": "Replay Horse",
        }

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_replay)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/cancel",
        json={"response_message": "cancel"},
        headers={"Idempotency-Key": "abc"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_mark_sold_replay_short_circuit(client, monkeypatch):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    async def fake_replay(*_args, **_kwargs):
        return {"message": "Horse marked as sold", "horse_id": str(uuid.uuid4()), "closed_offers": 0}

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_replay)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(
        f"/api/v1/offers/{uuid.uuid4()}/mark-sold",
        headers={"Idempotency-Key": "abc"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Horse marked as sold"


@pytest.mark.asyncio
async def test_counter_replay_short_circuit(client, monkeypatch):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    async def fake_replay(*_args, **_kwargs):
        return {
            "id": str(uuid.uuid4()),
            "buyer_id": str(uuid.uuid4()),
            "seller_id": str(user.id),
            "horse_id": str(uuid.uuid4()),
            "amount": 10000,
            "counter_amount": 9900,
            "status": "countered",
            "message": "Offer",
            "response_message": "counter",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "responded_at": None,
            "buyer_email": "buyer@example.com",
            "seller_email": user.email,
            "horse_title": "Replay Horse",
        }

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_replay)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/counter",
        json={"counter_amount": 9900, "response_message": "counter"},
        headers={"Idempotency-Key": "abc"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "countered"


@pytest.mark.asyncio
async def test_accept_replay_short_circuit(client, monkeypatch):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    async def fake_replay(*_args, **_kwargs):
        return {
            "id": str(uuid.uuid4()),
            "buyer_id": str(uuid.uuid4()),
            "seller_id": str(user.id),
            "horse_id": str(uuid.uuid4()),
            "amount": 10000,
            "counter_amount": None,
            "status": "accepted",
            "message": "Offer",
            "response_message": "accepted",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "responded_at": None,
            "buyer_email": "buyer@example.com",
            "seller_email": user.email,
            "horse_title": "Replay Horse",
        }

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_replay)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/accept",
        json={"response_message": "accepted"},
        headers={"Idempotency-Key": "abc"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_reject_replay_short_circuit(client, monkeypatch):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    async def fake_replay(*_args, **_kwargs):
        return {
            "id": str(uuid.uuid4()),
            "buyer_id": str(uuid.uuid4()),
            "seller_id": str(user.id),
            "horse_id": str(uuid.uuid4()),
            "amount": 10000,
            "counter_amount": None,
            "status": "rejected",
            "message": "Offer",
            "response_message": "rejected",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "responded_at": None,
            "buyer_email": "buyer@example.com",
            "seller_email": user.email,
            "horse_title": "Replay Horse",
        }

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_replay)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/reject",
        json={"response_message": "rejected"},
        headers={"Idempotency-Key": "abc"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_reopen_listing_replay_short_circuit(client, monkeypatch):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    replay_payload = {
        "id": str(uuid.uuid4()),
        "owner_id": str(user.id),
        "title": "Replay Horse",
        "price": 10000,
        "breed": "Arabian",
        "age": 7,
        "gender": "mare",
        "discipline": None,
        "height": None,
        "description": None,
        "vet_check_available": False,
        "vet_certificate_url": None,
        "image_url": None,
        "discount_type": None,
        "discount_value": None,
        "discount_price": None,
        "status": "approved",
        "rejection_reason": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "owner": None,
        "images": [],
    }

    async def fake_replay(*_args, **_kwargs):
        return replay_payload

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_replay)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(
        f"/api/v1/horses/{uuid.uuid4()}/reopen",
        headers={"Idempotency-Key": "abc"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_cancel_offer_not_found_returns_404(client):
    user = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/cancel",
        json={"response_message": "cancel"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mark_sold_forbidden_non_seller_returns_403(client):
    seller_id = uuid.uuid4()
    offer = make_offer(status=OfferStatus.ACCEPTED)
    offer.seller_id = seller_id
    fake_db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return make_user(role=UserRole.BUYER)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(f"/api/v1/offers/{offer.id}/mark-sold")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_mark_sold_skips_missing_other_buyer_branch(client, monkeypatch):
    seller = make_user(role=UserRole.SELLER)
    accepted_buyer_id = uuid.uuid4()
    other_buyer_id = uuid.uuid4()
    horse_id = uuid.uuid4()

    accepted_offer = make_offer(status=OfferStatus.ACCEPTED)
    accepted_offer.seller_id = seller.id
    accepted_offer.buyer_id = accepted_buyer_id
    accepted_offer.horse_id = horse_id

    other_offer = make_offer(status=OfferStatus.PENDING)
    other_offer.buyer_id = other_buyer_id
    other_offer.seller_id = seller.id
    other_offer.horse_id = horse_id

    from app.models import Horse, HorseGender
    horse = Horse(
        id=horse_id,
        owner_id=seller.id,
        title="Horse",
        price=10000,
        breed="Arabian",
        age=7,
        gender=HorseGender.MARE,
        discipline=None,
        height=None,
        description=None,
        status="approved",
        vet_check_available=False,
        vet_certificate_url=None,
        image_url=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    notifications = []

    async def fake_notify(**kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr(main_module, "notify_offer_participant", fake_notify)

    fake_db = FakeDB([
        FakeResult(scalar_value=accepted_offer),
        FakeResult(scalar_value=horse),
        FakeResult(scalars_items=[other_offer]),
        FakeResult(scalar_value=None),  # other buyer missing -> continue
        FakeResult(scalar_value=None),  # accepted buyer missing -> no notify
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return seller

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(f"/api/v1/offers/{accepted_offer.id}/mark-sold")

    assert response.status_code == 200
    assert response.json()["closed_offers"] == 1
    assert len(notifications) == 0


@pytest.mark.asyncio
async def test_counter_offer_not_found_returns_404(client):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/counter",
        json={"counter_amount": 9800, "response_message": "counter"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_accept_offer_not_found_returns_404(client):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/accept",
        json={"response_message": "accept"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reject_offer_not_found_returns_404(client):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{uuid.uuid4()}/reject",
        json={"response_message": "reject"},
    )

    assert response.status_code == 404
