"""Tests for PUT /api/v1/profile endpoint branches."""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.main import app, get_db
from app.models import User, UserProfile, UserRole


class FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one(self):
        return self._scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

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
    user.profile = UserProfile(
        user_id=user_id,
        first_name="Old",
        last_name="Name",
        phone_number="111111",
        location="Old City",
    )
    return user


@pytest.mark.asyncio
async def test_update_profile_non_admin_can_change_role_and_profile(client):
    user = make_user(role=UserRole.BUYER)

    fake_db = FakeDB([
        FakeResult(scalar_value=user),
        FakeResult(scalar_value=None),
        FakeResult(scalar_value=user),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        "/api/v1/profile",
        json={
            "first_name": "New",
            "last_name": "Person",
            "phone_number": "222222",
            "location": "New City",
            "role": "seller",
            "language": "ar",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "seller"
    assert payload["language"] == "ar"
    assert payload["profile"]["first_name"] == "New"
    assert payload["profile"]["phone_number"] == "222222"


@pytest.mark.asyncio
async def test_update_profile_admin_self_demotion_blocked(client):
    user = make_user(email="admin@example.com", role=UserRole.ADMIN)

    fake_db = FakeDB([
        FakeResult(scalar_value=user),
        FakeResult(scalar_value=user),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        "/api/v1/profile",
        json={"role": "seller"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_update_profile_phone_conflict_returns_409(client):
    user = make_user(role=UserRole.BUYER)

    fake_db = FakeDB([
        FakeResult(scalar_value=user),
        FakeResult(scalar_value=object()),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        "/api/v1/profile",
        json={"phone_number": "999999"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_profile_creates_profile_if_missing(client):
    user = make_user(role=UserRole.BUYER)
    user.profile = None

    fake_db = FakeDB([
        FakeResult(scalar_value=user),
        FakeResult(scalar_value=user),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_user_dep():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user_dep

    response = await client.put(
        "/api/v1/profile",
        json={"first_name": "Created"},
    )

    assert response.status_code == 200
    assert response.json()["profile"]["first_name"] == "Created"
