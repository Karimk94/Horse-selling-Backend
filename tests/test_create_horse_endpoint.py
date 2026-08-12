"""Tests for POST /api/v1/horses endpoint behavior."""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app, get_db
from app.auth import get_current_user
from app.models import Horse, HorseGender, User, UserProfile, UserRole


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeResult:
    def __init__(self, scalar_value=None, scalars_items=None):
        self._scalar_value = scalar_value
        self._scalars_items = scalars_items or []

    def scalar_one(self):
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

    async def flush(self):
        return None

    async def commit(self):
        return None


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def make_user(email="seller@example.com", role=UserRole.SELLER, is_verified=True):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=email,
        password_hash="x",
        role=role,
        is_verified=is_verified,
        language="en",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    user.profile = UserProfile(user_id=user_id, phone_number="123456")
    return user


def make_horse(owner_id, title="Speed Star"):
    horse = Horse(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title=title,
        price=10000,
        breed="Arabian",
        age=6,
        gender=HorseGender.MARE,
        discipline="Endurance",
        height=1.6,
        description="A very strong and healthy endurance horse.",
        vet_check_available=True,
        vet_certificate_url="https://example.com/vet.pdf",
        image_url="https://example.com/img1.jpg",
        status="pending_review",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return horse


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_create_horse_requires_verified_email(client):
    seller = make_user(is_verified=False)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return seller

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "title": "Speed Star",
        "price": 12000,
        "breed": "Arabian",
        "age": 6,
        "gender": "mare",
        "discipline": "Endurance",
        "height": 1.6,
        "description": "This horse is in excellent condition and ready for competition.",
        "vet_check_available": True,
        "vet_certificate_url": "https://example.com/vet.pdf",
        "image_urls": ["https://example.com/img1.jpg"],
    }

    response = await client.post("/api/v1/horses", json=payload)

    assert response.status_code == 403
    assert "verify your email" in response.json()["detail"].lower()


@pytest.mark.asyncio
@patch("app.main.send_pending_review_notification")
async def test_create_horse_success_notifies_admins(mock_notify, client):
    seller = make_user(is_verified=True)
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN, is_verified=True)

    horse = make_horse(owner_id=seller.id)
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB(
        [
            FakeResult(scalar_value=horse),
            FakeResult(scalars_items=[admin]),
        ]
    )

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return seller

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "title": "Speed Star",
        "price": 12000,
        "breed": "Arabian",
        "age": 6,
        "gender": "mare",
        "discipline": "Endurance",
        "height": 1.6,
        "description": "This horse is in excellent condition and ready for competition.",
        "vet_check_available": True,
        "vet_certificate_url": "https://example.com/vet.pdf",
        "image_urls": ["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
    }

    response = await client.post("/api/v1/horses", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "pending_review"
    assert data["title"] == "Speed Star"
    assert data["owner"]["email"] == seller.email
    assert mock_notify.called


@pytest.mark.asyncio
@patch("app.main.send_pending_review_notification")
async def test_create_horse_success_without_admins_skips_notification(mock_notify, client):
    seller = make_user(is_verified=True)

    horse = make_horse(owner_id=seller.id)
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB(
        [
            FakeResult(scalar_value=horse),
            FakeResult(scalars_items=[]),
        ]
    )

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return seller

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "title": "Speed Star",
        "price": 12000,
        "breed": "Arabian",
        "age": 6,
        "gender": "mare",
        "discipline": "Endurance",
        "height": 1.6,
        "description": "This horse is in excellent condition and ready for competition.",
        "vet_check_available": True,
        "vet_certificate_url": "https://example.com/vet.pdf",
        "image_urls": ["https://example.com/img1.jpg"],
    }

    response = await client.post("/api/v1/horses", json=payload)

    assert response.status_code == 201
    assert mock_notify.call_count == 0


@pytest.mark.asyncio
@patch("app.main.send_pending_review_notification")
async def test_create_horse_discount_percentage_sets_discount_price(mock_notify, client):
    seller = make_user(is_verified=True)
    horse = make_horse(owner_id=seller.id)
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB(
        [
            FakeResult(scalar_value=horse),
            FakeResult(scalars_items=[]),
        ]
    )

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return seller

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "title": "Speed Star",
        "price": 20000,
        "breed": "Arabian",
        "age": 6,
        "gender": "mare",
        "discipline": "Endurance",
        "height": 1.6,
        "description": "This horse is in excellent condition and ready for competition.",
        "vet_check_available": True,
        "vet_certificate_url": "https://example.com/vet.pdf",
        "image_urls": ["https://example.com/img1.jpg"],
        "discount_type": "percentage",
        "discount_value": 10,
    }

    response = await client.post("/api/v1/horses", json=payload)

    assert response.status_code == 201
    created_horse = next(obj for obj in fake_db.added if isinstance(obj, Horse))
    assert created_horse.discount_price == 18000
    assert mock_notify.call_count == 0


@pytest.mark.asyncio
@patch("app.main.send_pending_review_notification")
async def test_create_horse_discount_fixed_sets_discount_price(mock_notify, client):
    seller = make_user(is_verified=True)
    horse = make_horse(owner_id=seller.id)
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB(
        [
            FakeResult(scalar_value=horse),
            FakeResult(scalars_items=[]),
        ]
    )

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return seller

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "title": "Speed Star",
        "price": 20000,
        "breed": "Arabian",
        "age": 6,
        "gender": "mare",
        "discipline": "Endurance",
        "height": 1.6,
        "description": "This horse is in excellent condition and ready for competition.",
        "vet_check_available": True,
        "vet_certificate_url": "https://example.com/vet.pdf",
        "image_urls": ["https://example.com/img1.jpg"],
        "discount_type": "fixed",
        "discount_value": 15000,
    }

    response = await client.post("/api/v1/horses", json=payload)

    assert response.status_code == 201
    created_horse = next(obj for obj in fake_db.added if isinstance(obj, Horse))
    assert created_horse.discount_price == 15000
    assert mock_notify.call_count == 0
