import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.main import app, get_current_user, get_db
from app.models import Horse, HorseGender, Offer, OfferStatus, User, UserRole


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


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_reopen_horse_listing_success_http(client):
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="sold")
    db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(owner_id, role=UserRole.SELLER)

    response = await client.post(f"/api/v1/horses/{horse.id}/reopen")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approved"


@pytest.mark.asyncio
async def test_reopen_horse_listing_forbidden_http(client):
    owner_id = uuid.uuid4()
    horse = make_horse(uuid.uuid4(), owner_id, status="sold")
    db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(uuid.uuid4(), role=UserRole.BUYER)

    response = await client.post(f"/api/v1/horses/{horse.id}/reopen")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_accept_offer_invalid_actor_http(client):
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), status=OfferStatus.PENDING)
    db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(uuid.uuid4(), role=UserRole.BUYER)

    response = await client.put(
        f"/api/v1/offers/{offer.id}/accept",
        json={"response_message": "accept"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cancel_offer_non_buyer_forbidden_http(client):
    offer = make_offer(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), status=OfferStatus.PENDING)
    db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(uuid.uuid4(), role=UserRole.SELLER)

    response = await client.put(
        f"/api/v1/offers/{offer.id}/cancel",
        json={"response_message": "cancel"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_mark_offer_horse_sold_success_http(monkeypatch, client):
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

    monkeypatch.setattr(main_module, "notify_offer_event", fake_notify_offer_event)

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(seller_id, role=UserRole.SELLER)

    response = await client.post(f"/api/v1/offers/{accepted_offer.id}/mark-sold")

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Horse marked as sold"
    assert payload["closed_offers"] == 1
    assert len(notifications) == 2
