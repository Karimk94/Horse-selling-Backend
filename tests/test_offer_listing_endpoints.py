"""Tests for create/list offer endpoints and transition audit listing."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.auth import get_current_user
from app.main import app, get_current_admin, get_db
from app.models import Horse, HorseGender, Offer, OfferStatus, OfferTransitionAudit, User, UserRole


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeResult:
    def __init__(self, scalar_value=None, scalars_items=None):
        self._scalar_value = scalar_value
        self._scalars_items = scalars_items or []

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        return FakeScalars(self._scalars_items)


class FakeDB:
    def __init__(self, results):
        self._results = list(results)
        self.added = []

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        now = datetime.now(timezone.utc)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "status", None) is None:
            obj.status = OfferStatus.PENDING
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = now
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


def make_user(user_id=None, role=UserRole.BUYER, email=None):
    uid = user_id or uuid.uuid4()
    return User(
        id=uid,
        email=email or f"{uid}@example.com",
        password_hash="x",
        role=role,
        is_verified=True,
        language="en",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_horse(owner_id, status="approved"):
    return Horse(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title="Offer Horse",
        price=15000,
        breed="Arabian",
        age=8,
        gender=HorseGender.MARE,
        discipline="Endurance",
        height=1.6,
        description="A horse ready for competitive riding.",
        status=status,
        vet_check_available=False,
        vet_certificate_url=None,
        image_url="https://example.com/horse.jpg",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_offer(buyer_id, seller_id, horse_id, amount=9000, status=OfferStatus.PENDING):
    return Offer(
        id=uuid.uuid4(),
        buyer_id=buyer_id,
        seller_id=seller_id,
        horse_id=horse_id,
        amount=amount,
        counter_amount=None,
        status=status,
        message="Offer message",
        response_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        responded_at=None,
    )


@pytest.mark.asyncio
async def test_create_offer_horse_not_found_returns_404(client):
    buyer = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return buyer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(f"/api/v1/horses/{uuid.uuid4()}/offers", json={"amount": 9000})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_offer_cannot_offer_on_own_listing(client):
    buyer = make_user(role=UserRole.BUYER)
    horse = make_horse(owner_id=buyer.id, status="approved")
    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return buyer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(f"/api/v1/horses/{horse.id}/offers", json={"amount": 9000})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_offer_success_returns_offer_response(client, monkeypatch):
    seller = make_user(role=UserRole.SELLER)
    buyer = make_user(role=UserRole.BUYER)
    horse = make_horse(owner_id=seller.id, status="approved")

    async def fake_notify_offer_event(**kwargs):
        return None

    monkeypatch.setattr(main_module, "notify_offer_event", fake_notify_offer_event)

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(scalar_value=buyer),
        FakeResult(scalar_value=seller),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return buyer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(
        f"/api/v1/horses/{horse.id}/offers",
        json={"amount": 9500, "message": "Interested"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["buyer_id"] == str(buyer.id)
    assert payload["seller_id"] == str(seller.id)
    assert payload["horse_id"] == str(horse.id)
    assert payload["status"] == OfferStatus.PENDING.value


@pytest.mark.asyncio
async def test_list_my_offers_invalid_status_filter_returns_400(client):
    user = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/offers?status_filter=bogus")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_my_offers_success_returns_history(client):
    seller = make_user(role=UserRole.SELLER, email="seller@example.com")
    buyer = make_user(role=UserRole.BUYER, email="buyer@example.com")
    horse = make_horse(owner_id=seller.id)
    offer = make_offer(buyer_id=buyer.id, seller_id=seller.id, horse_id=horse.id, amount=9100)
    offer.buyer = buyer
    offer.seller = seller
    offer.horse = horse

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[offer]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return buyer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/offers?role=all&skip=0&limit=20")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["count"] == 1
    assert payload["offers"][0]["horse_title"] == horse.title


@pytest.mark.asyncio
async def test_list_my_offers_role_buyer_branch(client):
    seller = make_user(role=UserRole.SELLER, email="seller@example.com")
    buyer = make_user(role=UserRole.BUYER, email="buyer@example.com")
    horse = make_horse(owner_id=seller.id)
    offer = make_offer(buyer_id=buyer.id, seller_id=seller.id, horse_id=horse.id)
    offer.buyer = buyer
    offer.seller = seller
    offer.horse = horse

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[offer]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return buyer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/offers?role=buyer")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.asyncio
async def test_list_my_offers_role_seller_branch(client):
    seller = make_user(role=UserRole.SELLER, email="seller@example.com")
    buyer = make_user(role=UserRole.BUYER, email="buyer@example.com")
    horse = make_horse(owner_id=seller.id)
    offer = make_offer(buyer_id=buyer.id, seller_id=seller.id, horse_id=horse.id)
    offer.buyer = buyer
    offer.seller = seller
    offer.horse = horse

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[offer]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return seller

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/offers?role=seller")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.asyncio
async def test_list_my_offers_with_valid_status_filter_branch(client):
    seller = make_user(role=UserRole.SELLER, email="seller@example.com")
    buyer = make_user(role=UserRole.BUYER, email="buyer@example.com")
    horse = make_horse(owner_id=seller.id)
    offer = make_offer(
        buyer_id=buyer.id,
        seller_id=seller.id,
        horse_id=horse.id,
        status=OfferStatus.PENDING,
    )
    offer.buyer = buyer
    offer.seller = seller
    offer.horse = horse

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[offer]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return buyer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/offers?role=all&status_filter=pending")
    assert response.status_code == 200
    assert response.json()["count"] == 1


@pytest.mark.asyncio
async def test_list_my_offers_has_more_true_branch(client):
    seller = make_user(role=UserRole.SELLER, email="seller@example.com")
    buyer = make_user(role=UserRole.BUYER, email="buyer@example.com")
    horse = make_horse(owner_id=seller.id)
    offer = make_offer(buyer_id=buyer.id, seller_id=seller.id, horse_id=horse.id)
    offer.buyer = buyer
    offer.seller = seller
    offer.horse = horse

    fake_db = FakeDB([
        FakeResult(scalar_value=3),
        FakeResult(scalars_items=[offer]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return buyer

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/offers?role=all&skip=0&limit=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_more"] is True


@pytest.mark.asyncio
async def test_get_horse_offers_not_owned_returns_404(client):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get(f"/api/v1/horses/{uuid.uuid4()}/offers")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_horse_offers_success_returns_count(client):
    owner = make_user(role=UserRole.SELLER, email="owner@example.com")
    buyer = make_user(role=UserRole.BUYER, email="buyer@example.com")
    horse = make_horse(owner_id=owner.id)
    offer = make_offer(buyer_id=buyer.id, seller_id=owner.id, horse_id=horse.id)
    offer.buyer = buyer
    offer.seller = owner
    offer.horse = horse

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(scalars_items=[offer]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get(f"/api/v1/horses/{horse.id}/offers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1


@pytest.mark.asyncio
async def test_admin_list_offer_transitions_not_found_returns_404(client):
    admin = make_user(role=UserRole.ADMIN)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.get(f"/api/v1/admin/offers/{uuid.uuid4()}/transitions")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_list_offer_transitions_success(client):
    admin = make_user(role=UserRole.ADMIN)
    offer = make_offer(buyer_id=uuid.uuid4(), seller_id=uuid.uuid4(), horse_id=uuid.uuid4())
    audit = OfferTransitionAudit(
        id=uuid.uuid4(),
        offer_id=offer.id,
        changed_by_user_id=offer.seller_id,
        from_status=OfferStatus.PENDING.value,
        to_status=OfferStatus.COUNTERED.value,
        actor="seller",
        response_message="Counter offer",
        created_at=datetime.now(timezone.utc),
    )

    fake_db = FakeDB([
        FakeResult(scalar_value=offer),
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[audit]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.get(
        f"/api/v1/admin/offers/{offer.id}/transitions?actor=seller&to_status=countered"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["count"] == 1
