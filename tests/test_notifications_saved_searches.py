"""Tests for push token and saved search endpoints."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.main import app, get_db
from app.models import (
    Horse,
    HorseGender,
    PushToken,
    SavedSearch,
    SavedSearchAlert,
    User,
    UserProfile,
    UserRole,
)


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

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        return FakeScalars(self._scalars_items)


class FakeDB:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.deleted = []
        self.commit_count = 0

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commit_count += 1
        return None

    async def refresh(self, obj):
        now = datetime.now(timezone.utc)
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if hasattr(obj, "updated_at") and getattr(obj, "updated_at", None) is None:
            obj.updated_at = now
        if hasattr(obj, "last_seen_at") and getattr(obj, "last_seen_at", None) is None:
            obj.last_seen_at = now
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


def make_saved_search(user_id, name="Arabian Alerts"):
    return SavedSearch(
        id=uuid.uuid4(),
        user_id=user_id,
        name=name,
        breed="Arabian",
        discipline="Endurance",
        gender="mare",
        min_price=8000,
        max_price=25000,
        min_age=4,
        max_age=12,
        vet_check_available=True,
        verified_seller=True,
        is_active=True,
        last_alerted_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_horse(owner_id, breed="Arabian", discipline="Endurance", gender=HorseGender.MARE):
    horse = Horse(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title="Candidate Horse",
        price=12000,
        breed=breed,
        age=7,
        gender=gender,
        discipline=discipline,
        height=1.6,
        description="A healthy horse suitable for endurance competitions.",
        status="approved",
        vet_check_available=True,
        vet_certificate_url="https://example.com/vet.pdf",
        image_url="https://example.com/horse.jpg",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return horse


def make_push_token(user_id, token="ExponentPushToken[token123456789]"):
    return PushToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token=token,
        platform="ios",
        is_active=True,
        last_seen_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )


def make_alert(user_id, saved_search_id, horse_id):
    return SavedSearchAlert(
        id=uuid.uuid4(),
        user_id=user_id,
        saved_search_id=saved_search_id,
        horse_id=horse_id,
        title="Match found",
        message="A new horse matches your search.",
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_register_push_token_updates_existing_token(client):
    user = make_user()
    token_row = make_push_token(user_id=uuid.uuid4())
    fake_db = FakeDB([FakeResult(scalar_value=token_row)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(
        "/api/v1/notifications/push-token",
        json={"token": "ExponentPushToken[updated123456]", "platform": "android"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Push token registered"
    assert token_row.user_id == user.id
    assert token_row.platform == "android"


@pytest.mark.asyncio
async def test_register_push_token_creates_new_token(client):
    user = make_user()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(
        "/api/v1/notifications/push-token",
        json={"token": "ExponentPushToken[new123456789]", "platform": "ios"},
    )

    assert response.status_code == 200
    assert len(fake_db.added) == 1


@pytest.mark.asyncio
async def test_unregister_push_token_found_marks_inactive(client):
    user = make_user()
    token_row = make_push_token(user_id=user.id, token="ExponentPushToken[remove123456]")
    fake_db = FakeDB([FakeResult(scalar_value=token_row)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(
        "/api/v1/notifications/push-token/unregister",
        json={"token": "ExponentPushToken[remove123456]"},
    )

    assert response.status_code == 200
    assert token_row.is_active is False
    assert fake_db.commit_count == 1


@pytest.mark.asyncio
async def test_create_saved_search_returns_201(client):
    user = make_user()
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Arabian Hunters",
            "breed": "Arabian",
            "discipline": "Endurance",
            "gender": "mare",
            "min_price": 5000,
            "max_price": 20000,
            "min_age": 4,
            "max_age": 12,
            "vet_check_available": True,
            "verified_seller": True,
            "is_active": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Arabian Hunters"


@pytest.mark.asyncio
async def test_list_saved_searches_returns_items(client):
    user = make_user()
    saved_search = make_saved_search(user.id)
    fake_db = FakeDB([FakeResult(scalars_items=[saved_search])])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/saved-searches")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["name"] == saved_search.name


@pytest.mark.asyncio
async def test_update_saved_search_not_found_returns_404(client):
    user = make_user()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/saved-searches/{uuid.uuid4()}",
        json={"name": "Updated Name"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_saved_search_success(client):
    user = make_user()
    saved_search = make_saved_search(user.id)
    fake_db = FakeDB([FakeResult(scalar_value=saved_search)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        f"/api/v1/saved-searches/{saved_search.id}",
        json={"name": "Updated Name", "is_active": False},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"
    assert response.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_saved_search_success_returns_204(client):
    user = make_user()
    saved_search = make_saved_search(user.id)
    fake_db = FakeDB([FakeResult(scalar_value=saved_search)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.delete(f"/api/v1/saved-searches/{saved_search.id}")
    assert response.status_code == 204
    assert len(fake_db.deleted) == 1


@pytest.mark.asyncio
async def test_delete_saved_search_not_found_returns_404(client):
    user = make_user()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.delete(f"/api/v1/saved-searches/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_saved_search_alerts_returns_items(client):
    user = make_user(role=UserRole.BUYER)
    search = make_saved_search(user.id)
    alert = make_alert(user.id, search.id, uuid.uuid4())

    fake_db = FakeDB([FakeResult(scalars_items=[alert])])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/saved-search-alerts")
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_get_saved_search_matches_returns_filtered_results(client):
    user = make_user(role=UserRole.BUYER)
    saved_search = make_saved_search(user.id)

    matching = make_horse(owner_id=uuid.uuid4(), breed="Arabian", discipline="Endurance", gender=HorseGender.MARE)
    non_matching = make_horse(owner_id=uuid.uuid4(), breed="Friesian", discipline="Dressage", gender=HorseGender.STALLION)

    matching.owner = make_user(email="seller1@example.com", role=UserRole.SELLER)
    matching.owner.profile = UserProfile(user_id=matching.owner.id)
    matching.images = []

    non_matching.owner = make_user(email="seller2@example.com", role=UserRole.SELLER)
    non_matching.owner.profile = UserProfile(user_id=non_matching.owner.id)
    non_matching.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=saved_search),
        FakeResult(scalars_items=[matching, non_matching]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get(f"/api/v1/saved-searches/{saved_search.id}/matches")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["horses"][0]["id"] == str(matching.id)


@pytest.mark.asyncio
async def test_get_saved_search_matches_not_found_returns_404(client):
    user = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get(f"/api/v1/saved-searches/{uuid.uuid4()}/matches")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_saved_search_unread_count_returns_number(client):
    user = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([FakeResult(scalar_value=3)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/saved-search-alerts/unread-count")
    assert response.status_code == 200
    assert response.json()["unread_count"] == 3


@pytest.mark.asyncio
async def test_saved_search_unread_count_defaults_to_zero(client):
    user = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.get("/api/v1/saved-search-alerts/unread-count")
    assert response.status_code == 200
    assert response.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_mark_saved_search_alert_read_success(client):
    user = make_user(role=UserRole.BUYER)
    search = make_saved_search(user.id)
    alert = make_alert(user.id, search.id, uuid.uuid4())
    fake_db = FakeDB([FakeResult(scalar_value=alert)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(f"/api/v1/saved-search-alerts/{alert.id}/read")
    assert response.status_code == 200
    assert response.json()["is_read"] is True


@pytest.mark.asyncio
async def test_mark_saved_search_alert_read_not_found_returns_404(client):
    user = make_user(role=UserRole.BUYER)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post(f"/api/v1/saved-search-alerts/{uuid.uuid4()}/read")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_mark_all_saved_search_alerts_read_returns_zero(client):
    user = make_user(role=UserRole.BUYER)
    search = make_saved_search(user.id)
    alerts = [
        make_alert(user.id, search.id, uuid.uuid4()),
        make_alert(user.id, search.id, uuid.uuid4()),
    ]
    fake_db = FakeDB([FakeResult(scalars_items=alerts)])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.post("/api/v1/saved-search-alerts/read-all")
    assert response.status_code == 200
    assert response.json()["unread_count"] == 0
    assert sum(1 for a in fake_db.added if isinstance(a, SavedSearchAlert)) == 2
