"""Focused branch tests for offer transition endpoints."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.auth import get_current_user
from app.main import app, get_db
from app.models import Horse, HorseGender, Offer, OfferStatus, User, UserRole
from app.schemas import OfferResponse


class FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


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
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_horse(owner_id):
    return Horse(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title="Transition Horse",
        price=12000,
        breed="Arabian",
        age=7,
        gender=HorseGender.MARE,
        discipline="Endurance",
        height=1.6,
        description="Ready for competition.",
        status="approved",
        vet_check_available=False,
        vet_certificate_url=None,
        image_url=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_offer(buyer_id, seller_id, horse_id, status=OfferStatus.PENDING):
    return Offer(
        id=uuid.uuid4(),
        buyer_id=buyer_id,
        seller_id=seller_id,
        horse_id=horse_id,
        amount=9500,
        counter_amount=None,
        status=status,
        message="Offer msg",
        response_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        responded_at=None,
    )


def build_offer_response(offer, buyer_email, seller_email, horse_title):
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
        buyer_email=buyer_email,
        seller_email=seller_email,
        horse_title=horse_title,
    )


@pytest.mark.asyncio
async def test_cancel_offer_success_branch(client, monkeypatch):
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse = make_horse(owner_id=seller_id)
    offer = make_offer(buyer_id=buyer_id, seller_id=seller_id, horse_id=horse.id, status=OfferStatus.PENDING)

    fake_db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return make_user(buyer_id, role=UserRole.BUYER)

    async def fake_get_replay(*_args, **_kwargs):
        return None

    async def fake_persist_transition(**kwargs):
        kwargs["offer"].status = OfferStatus.CANCELLED

    async def fake_load_offer_context(*_args, **_kwargs):
        return (
            make_user(buyer_id, role=UserRole.BUYER),
            make_user(seller_id, role=UserRole.SELLER),
            horse,
        )

    async def fake_notify(*_args, **_kwargs):
        return None

    async def fake_build_offer_response(*_args, **_kwargs):
        return build_offer_response(
            offer,
            buyer_email=f"{buyer_id}@example.com",
            seller_email=f"{seller_id}@example.com",
            horse_title=horse.title,
        )

    async def fake_finalize(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_get_replay)
    monkeypatch.setattr(main_module, "persist_offer_transition", fake_persist_transition)
    monkeypatch.setattr(main_module, "load_offer_context", fake_load_offer_context)
    monkeypatch.setattr(main_module, "notify_offer_participant", fake_notify)
    monkeypatch.setattr(main_module, "build_offer_response", fake_build_offer_response)
    monkeypatch.setattr(main_module, "finalize_idempotent_replay", fake_finalize)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{offer.id}/cancel",
        json={"response_message": "buyer cancels"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == OfferStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_counter_offer_success_branch(client, monkeypatch):
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse = make_horse(owner_id=seller_id)
    offer = make_offer(buyer_id=buyer_id, seller_id=seller_id, horse_id=horse.id, status=OfferStatus.PENDING)

    fake_db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return make_user(seller_id, role=UserRole.SELLER)

    async def fake_get_replay(*_args, **_kwargs):
        return None

    async def fake_persist_transition(**kwargs):
        kwargs["offer"].status = OfferStatus.COUNTERED
        kwargs["offer"].counter_amount = kwargs.get("counter_amount")

    async def fake_load_offer_context(*_args, **_kwargs):
        return (
            make_user(buyer_id, role=UserRole.BUYER),
            make_user(seller_id, role=UserRole.SELLER),
            horse,
        )

    async def fake_notify(*_args, **_kwargs):
        return None

    async def fake_build_offer_response(*_args, **_kwargs):
        return build_offer_response(
            offer,
            buyer_email=f"{buyer_id}@example.com",
            seller_email=f"{seller_id}@example.com",
            horse_title=horse.title,
        )

    async def fake_finalize(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_get_replay)
    monkeypatch.setattr(main_module, "persist_offer_transition", fake_persist_transition)
    monkeypatch.setattr(main_module, "load_offer_context", fake_load_offer_context)
    monkeypatch.setattr(main_module, "notify_offer_participant", fake_notify)
    monkeypatch.setattr(main_module, "build_offer_response", fake_build_offer_response)
    monkeypatch.setattr(main_module, "finalize_idempotent_replay", fake_finalize)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{offer.id}/counter",
        json={"counter_amount": 9800, "response_message": "counter"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == OfferStatus.COUNTERED.value
    assert response.json()["counter_amount"] == 9800


@pytest.mark.asyncio
async def test_accept_offer_success_branch(client, monkeypatch):
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse = make_horse(owner_id=seller_id)
    offer = make_offer(buyer_id=buyer_id, seller_id=seller_id, horse_id=horse.id, status=OfferStatus.COUNTERED)

    fake_db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return make_user(seller_id, role=UserRole.SELLER)

    async def fake_get_replay(*_args, **_kwargs):
        return None

    async def fake_persist_transition(**kwargs):
        kwargs["offer"].status = OfferStatus.ACCEPTED

    async def fake_load_offer_context(*_args, **_kwargs):
        return (
            make_user(buyer_id, role=UserRole.BUYER),
            make_user(seller_id, role=UserRole.SELLER),
            horse,
        )

    async def fake_notify(*_args, **_kwargs):
        return None

    async def fake_build_offer_response(*_args, **_kwargs):
        return build_offer_response(
            offer,
            buyer_email=f"{buyer_id}@example.com",
            seller_email=f"{seller_id}@example.com",
            horse_title=horse.title,
        )

    async def fake_finalize(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_get_replay)
    monkeypatch.setattr(main_module, "persist_offer_transition", fake_persist_transition)
    monkeypatch.setattr(main_module, "load_offer_context", fake_load_offer_context)
    monkeypatch.setattr(main_module, "notify_offer_participant", fake_notify)
    monkeypatch.setattr(main_module, "build_offer_response", fake_build_offer_response)
    monkeypatch.setattr(main_module, "finalize_idempotent_replay", fake_finalize)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{offer.id}/accept",
        json={"response_message": "accepted"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == OfferStatus.ACCEPTED.value


@pytest.mark.asyncio
async def test_reject_offer_success_branch(client, monkeypatch):
    buyer_id = uuid.uuid4()
    seller_id = uuid.uuid4()
    horse = make_horse(owner_id=seller_id)
    offer = make_offer(buyer_id=buyer_id, seller_id=seller_id, horse_id=horse.id, status=OfferStatus.PENDING)

    fake_db = FakeDB([FakeResult(scalar_value=offer)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return make_user(buyer_id, role=UserRole.BUYER)

    async def fake_get_replay(*_args, **_kwargs):
        return None

    async def fake_persist_transition(**kwargs):
        kwargs["offer"].status = OfferStatus.REJECTED

    async def fake_load_offer_context(*_args, **_kwargs):
        return (
            make_user(buyer_id, role=UserRole.BUYER),
            make_user(seller_id, role=UserRole.SELLER),
            horse,
        )

    async def fake_notify(*_args, **_kwargs):
        return None

    async def fake_build_offer_response(*_args, **_kwargs):
        return build_offer_response(
            offer,
            buyer_email=f"{buyer_id}@example.com",
            seller_email=f"{seller_id}@example.com",
            horse_title=horse.title,
        )

    async def fake_finalize(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main_module, "get_idempotent_replay", fake_get_replay)
    monkeypatch.setattr(main_module, "persist_offer_transition", fake_persist_transition)
    monkeypatch.setattr(main_module, "load_offer_context", fake_load_offer_context)
    monkeypatch.setattr(main_module, "notify_offer_participant", fake_notify)
    monkeypatch.setattr(main_module, "build_offer_response", fake_build_offer_response)
    monkeypatch.setattr(main_module, "finalize_idempotent_replay", fake_finalize)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/offers/{offer.id}/reject",
        json={"response_message": "rejected"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == OfferStatus.REJECTED.value
