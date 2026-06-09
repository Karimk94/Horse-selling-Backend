"""Tests for voucher create/list/validate endpoints."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app, get_current_admin, get_db
from app.models import DiscountType, User, UserRole, Voucher


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
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if hasattr(obj, "used_count") and getattr(obj, "used_count", None) is None:
            obj.used_count = 0
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


def make_admin():
    return User(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
        is_verified=True,
        language="en",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_voucher(code="SPRING25", discount_type=DiscountType.PERCENTAGE, discount_value=25):
    return Voucher(
        id=uuid.uuid4(),
        code=code,
        discount_type=discount_type,
        discount_value=discount_value,
        valid_from=datetime.now(timezone.utc) - timedelta(days=1),
        valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        usage_limit=10,
        used_count=2,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_create_voucher_duplicate_code_returns_409(client):
    admin = make_admin()
    existing = make_voucher(code="DUPLICATE")
    fake_db = FakeDB([FakeResult(scalar_value=existing)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    payload = {
        "code": "DUPLICATE",
        "discount_type": "percentage",
        "discount_value": 10,
        "valid_from": datetime.now(timezone.utc).isoformat(),
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "usage_limit": 100,
        "is_active": True,
    }

    response = await client.post("/api/v1/vouchers", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_voucher_success_returns_201(client):
    admin = make_admin()
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    payload = {
        "code": "NEWCODE",
        "discount_type": "fixed",
        "discount_value": 150,
        "valid_from": datetime.now(timezone.utc).isoformat(),
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        "usage_limit": 50,
        "is_active": True,
    }

    response = await client.post("/api/v1/vouchers", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "NEWCODE"
    assert body["discount_type"] == "fixed"


@pytest.mark.asyncio
async def test_list_vouchers_returns_items(client):
    admin = make_admin()
    voucher = make_voucher(code="SHOW10")
    fake_db = FakeDB([FakeResult(scalars_items=[voucher])])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.get("/api/v1/vouchers")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["code"] == "SHOW10"


@pytest.mark.asyncio
async def test_validate_voucher_invalid_code(client):
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/api/v1/vouchers/validate", json={"code": "NOPE"})
    assert response.status_code == 200
    assert response.json()["valid"] is False


@pytest.mark.asyncio
async def test_validate_voucher_inactive(client):
    voucher = make_voucher(code="INACTIVE")
    voucher.is_active = False
    fake_db = FakeDB([FakeResult(scalar_value=voucher)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/api/v1/vouchers/validate", json={"code": "INACTIVE"})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "inactive" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_validate_voucher_not_yet_active(client):
    voucher = make_voucher(code="FUTURE")
    voucher.valid_from = datetime.now(timezone.utc) + timedelta(days=1)
    fake_db = FakeDB([FakeResult(scalar_value=voucher)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/api/v1/vouchers/validate", json={"code": "FUTURE"})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "not yet active" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_validate_voucher_expired(client):
    voucher = make_voucher(code="OLD")
    voucher.valid_until = datetime.now(timezone.utc) - timedelta(days=1)
    fake_db = FakeDB([FakeResult(scalar_value=voucher)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/api/v1/vouchers/validate", json={"code": "OLD"})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "expired" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_validate_voucher_usage_limit_reached(client):
    voucher = make_voucher(code="FULL")
    voucher.usage_limit = 3
    voucher.used_count = 3
    fake_db = FakeDB([FakeResult(scalar_value=voucher)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/api/v1/vouchers/validate", json={"code": "FULL"})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert "usage limit" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_validate_voucher_percentage_calculates_new_price(client):
    voucher = make_voucher(code="PCT20", discount_type=DiscountType.PERCENTAGE, discount_value=20)
    fake_db = FakeDB([FakeResult(scalar_value=voucher)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/api/v1/vouchers/validate",
        json={"code": "PCT20", "current_price": 1000},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["new_price"] == 800


@pytest.mark.asyncio
async def test_validate_voucher_fixed_calculates_new_price_floor_zero(client):
    voucher = make_voucher(code="FIX1500", discount_type=DiscountType.FIXED, discount_value=1500)
    fake_db = FakeDB([FakeResult(scalar_value=voucher)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/api/v1/vouchers/validate",
        json={"code": "FIX1500", "current_price": 1000},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["new_price"] == 0
