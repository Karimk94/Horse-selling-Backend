"""Tests for horse delete and favorites endpoints."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.main import app, get_db
from app.models import Favorite, Horse, HorseGender, ListingReview, User, UserProfile, UserRole


class FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return list(self._items)


class FakeResult:
    def __init__(self, scalar_value=None, scalars_items=None, rows=None):
        self._scalar_value = scalar_value
        self._scalars_items = scalars_items or []
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalars(self):
        return FakeScalars(self._scalars_items)

    def fetchall(self):
        return list(self._rows)


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

    async def commit(self):
        return None

    async def refresh(self, _obj):
        if getattr(_obj, "id", None) is None:
            _obj.id = uuid.uuid4()
        if getattr(_obj, "created_at", None) is None:
            _obj.created_at = datetime.now(timezone.utc)
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


def make_user(email="user@example.com", role=UserRole.BUYER):
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


def make_horse(owner_id):
    horse = Horse(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title="Test Horse",
        price=10000,
        breed="Arabian",
        age=7,
        gender=HorseGender.MARE,
        discipline="Endurance",
        height=1.6,
        description="A healthy horse that is ready for competitions.",
        status="approved",
        vet_check_available=False,
        vet_certificate_url=None,
        image_url="https://example.com/horse.jpg",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return horse


def make_favorite(user_id, horse_id):
    return Favorite(
        id=uuid.uuid4(),
        user_id=user_id,
        horse_id=horse_id,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_delete_horse_not_found_returns_404(client):
    user = make_user(role=UserRole.SELLER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.delete(f"/api/v1/horses/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_horse_forbidden_for_non_owner_non_admin(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)
    intruder = make_user(email="intruder@example.com", role=UserRole.BUYER)
    horse = make_horse(owner_id=owner.id)

    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return intruder

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.delete(f"/api/v1/horses/{horse.id}")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_horse_owner_success_returns_204(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)
    horse = make_horse(owner_id=owner.id)

    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.delete(f"/api/v1/horses/{horse.id}")
    assert response.status_code == 204
    assert horse.deleted_at is not None

    reviews = [obj for obj in fake_db.added if isinstance(obj, ListingReview)]
    assert len(reviews) == 1
    assert reviews[0].action == "delete"
    assert reviews[0].horse_id == horse.id


@pytest.mark.asyncio
async def test_restore_horse_owner_success_logs_audit(client):
    owner = make_user(email="owner@example.com", role=UserRole.SELLER)
    horse = make_horse(owner_id=owner.id)
    horse.deleted_at = datetime.now(timezone.utc)

    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(f"/api/v1/horses/{horse.id}/restore")
    assert response.status_code == 200
    assert horse.deleted_at is None

    reviews = [obj for obj in fake_db.added if isinstance(obj, ListingReview)]
    assert len(reviews) == 1
    assert reviews[0].action == "restore"
    assert reviews[0].horse_id == horse.id


@pytest.mark.asyncio
async def test_add_favorite_horse_not_found_returns_404(client):
    user = make_user()
    horse_id = uuid.uuid4()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post("/api/v1/favorites", json={"horse_id": str(horse_id)})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_add_favorite_duplicate_returns_409(client):
    user = make_user()
    horse_id = uuid.uuid4()
    fake_db = FakeDB([
        FakeResult(scalar_value=object()),
        FakeResult(scalar_value=object()),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post("/api/v1/favorites", json={"horse_id": str(horse_id)})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_add_favorite_success_returns_201(client):
    user = make_user()
    horse_id = uuid.uuid4()
    fake_db = FakeDB([
        FakeResult(scalar_value=object()),
        FakeResult(scalar_value=None),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post("/api/v1/favorites", json={"horse_id": str(horse_id)})
    assert response.status_code == 201
    data = response.json()
    assert data["user_id"] == str(user.id)
    assert data["horse_id"] == str(horse_id)


@pytest.mark.asyncio
async def test_remove_favorite_not_found_returns_404(client):
    user = make_user()
    horse_id = uuid.uuid4()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.delete(f"/api/v1/favorites/{horse_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_remove_favorite_success_returns_204(client):
    user = make_user()
    horse_id = uuid.uuid4()
    favorite = make_favorite(user.id, horse_id)
    fake_db = FakeDB([FakeResult(scalar_value=favorite)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.delete(f"/api/v1/favorites/{horse_id}")
    assert response.status_code == 204
    assert len(fake_db.deleted) == 1


@pytest.mark.asyncio
async def test_get_favorites_empty_returns_zero(client):
    user = make_user()
    fake_db = FakeDB([FakeResult(rows=[])])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/favorites")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["horses"] == []


@pytest.mark.asyncio
async def test_get_favorites_returns_horses(client):
    user = make_user()
    horse = make_horse(owner_id=user.id)
    horse.owner = user
    horse.images = []

    fake_db = FakeDB([
        FakeResult(rows=[(horse.id,)]),
        FakeResult(scalars_items=[horse]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/favorites")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["horses"][0]["id"] == str(horse.id)


@pytest.mark.asyncio
async def test_is_favorite_returns_true(client):
    user = make_user()
    horse_id = uuid.uuid4()
    favorite = make_favorite(user.id, horse_id)
    fake_db = FakeDB([FakeResult(scalar_value=favorite)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get(f"/api/v1/horses/{horse_id}/is-favorite")
    assert response.status_code == 200
    assert response.json()["is_favorite"] is True


@pytest.mark.asyncio
async def test_is_favorite_returns_false(client):
    user = make_user()
    horse_id = uuid.uuid4()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get(f"/api/v1/horses/{horse_id}/is-favorite")
    assert response.status_code == 200
    assert response.json()["is_favorite"] is False
