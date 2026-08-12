"""Tests for PUT /api/v1/horses/{horse_id} endpoint behavior."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.main import app, get_db
from app.models import DiscountType, Horse, HorseGender, HorseImage, User, UserProfile, UserRole


class FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value


class FakeDB:
    def __init__(self, results):
        self._results = list(results)
        self.deleted = []
        self.added = []

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

    async def delete(self, obj):
        self.deleted.append(obj)

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


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


def make_user(email="seller@example.com", role=UserRole.SELLER):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=email,
        password_hash="x",
        role=role,
        is_verified=True,
        language="en",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    user.profile = UserProfile(user_id=user_id, phone_number="123456")
    return user


def make_horse(owner_id, title="Original Title"):
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
        description="Original description for this horse listing.",
        vet_check_available=True,
        vet_certificate_url="https://example.com/vet.pdf",
        image_url="https://example.com/old.jpg",
        discount_type=None,
        discount_value=None,
        discount_price=None,
        status="approved",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return horse


def make_image(horse_id, url, order=0):
    return HorseImage(
        id=uuid.uuid4(),
        horse_id=horse_id,
        image_url=url,
        display_order=order,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_update_horse_not_found_returns_404(client):
    current_user = make_user()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return current_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(f"/api/v1/horses/{uuid.uuid4()}", json={"title": "Updated"})

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_horse_forbidden_for_non_owner_non_admin(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)
    intruder = make_user(email="intruder@example.com", role=UserRole.BUYER)

    horse = make_horse(owner_id=owner.id)
    horse.owner = owner
    horse.images = []

    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return intruder

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(f"/api/v1/horses/{horse.id}", json={"title": "Hacked"})

    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_horse_owner_updates_fields_and_discount_percentage(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)

    horse = make_horse(owner_id=owner.id)
    horse.owner = owner
    horse.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(scalar_value=horse),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "title": "Updated Title",
        "price": 20000,
        "discount_type": "percentage",
        "discount_value": 10,
        "image_url": "https://example.com/new-main.jpg",
    }

    response = await client.put(f"/api/v1/horses/{horse.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["price"] == 20000
    assert data["discount_type"] == DiscountType.PERCENTAGE.value
    assert data["discount_price"] == 18000
    assert data["image_url"] == "https://example.com/new-main.jpg"


@pytest.mark.asyncio
async def test_update_horse_admin_can_edit_other_users_listing(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)

    horse = make_horse(owner_id=owner.id)
    horse.owner = owner
    horse.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(scalar_value=horse),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/horses/{horse.id}",
        json={"discipline": "Show Jumping", "discount_type": "fixed", "discount_value": 9000},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["discipline"] == "Show Jumping"
    assert data["discount_type"] == DiscountType.FIXED.value
    assert data["discount_price"] == 9000


@pytest.mark.asyncio
async def test_update_horse_image_urls_replaces_existing_images(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)

    horse = make_horse(owner_id=owner.id)
    old_image_1 = make_image(horse.id, "https://example.com/old1.jpg", 0)
    old_image_2 = make_image(horse.id, "https://example.com/old2.jpg", 1)
    horse.images = [old_image_1, old_image_2]
    horse.owner = owner

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(scalar_value=horse),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "image_urls": [
            "https://example.com/new1.jpg",
            "https://example.com/new2.jpg",
            "https://example.com/new3.jpg",
        ]
    }

    response = await client.put(f"/api/v1/horses/{horse.id}", json=payload)

    assert response.status_code == 200
    assert len(fake_db.deleted) == 2
    assert len(fake_db.added) == 3
    assert horse.image_url == "https://example.com/new1.jpg"


@pytest.mark.asyncio
async def test_update_horse_updates_all_optional_fields(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)

    horse = make_horse(owner_id=owner.id)
    horse.owner = owner
    horse.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(scalar_value=horse),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    payload = {
        "title": "Updated All Fields",
        "price": 25000,
        "breed": "Friesian",
        "age": 9,
        "gender": "stallion",
        "discipline": "Dressage",
        "height": 1.72,
        "description": "Updated description that is longer and more detailed.",
        "vet_check_available": False,
        "vet_certificate_url": "https://example.com/new-vet.pdf",
    }

    response = await client.put(f"/api/v1/horses/{horse.id}", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated All Fields"
    assert data["price"] == 25000
    assert data["breed"] == "Friesian"
    assert data["age"] == 9
    assert data["gender"] == "stallion"
    assert data["discipline"] == "Dressage"
    assert data["height"] == 1.72
    assert data["description"] == "Updated description that is longer and more detailed."
    assert data["vet_check_available"] is False
    assert data["vet_certificate_url"] == "https://example.com/new-vet.pdf"
