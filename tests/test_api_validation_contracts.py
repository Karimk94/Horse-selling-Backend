import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app, get_current_user, get_db
from app.models import User, UserRole


class FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class FakeDB:
    def __init__(self, results=None):
        self._results = list(results or [])

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

    async def flush(self):
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


def make_user(user_id, role=UserRole.BUYER, is_verified=True):
    return User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash="x",
        role=role,
        is_verified=is_verified,
        language="en",
    )


@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_string_detail(client):
    existing_user = make_user(uuid.uuid4())
    db = FakeDB([FakeResult(scalar_value=existing_user)])

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/signup",
        json={
            "email": "existing@example.com",
            "password": "password123",
            "role": "buyer",
            "first_name": "Jane",
            "phone_number": "+1234567890",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A user with this email already exists"


@pytest.mark.asyncio
async def test_signup_invalid_password_returns_validation_detail_array(client):
    response = await client.post(
        "/auth/signup",
        json={
            "email": "new@example.com",
            "password": "short",
            "role": "buyer",
            "first_name": "Jane",
            "phone_number": "+1234567890",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(item["loc"][-1] == "password" for item in detail)


@pytest.mark.asyncio
async def test_create_horse_requires_verified_user_string_detail(client):
    async def override_get_db():
        yield FakeDB([])

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(
        uuid.uuid4(), role=UserRole.SELLER, is_verified=False
    )

    response = await client.post(
        "/api/v1/horses",
        json={
            "title": "Desert Star",
            "price": 12000,
            "breed": "Arabian",
            "age": 7,
            "gender": "mare",
            "description": "A calm, experienced mare with excellent ground manners.",
            "image_urls": ["https://example.com/horse.jpg"],
            "vet_check_available": False,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Please verify your email address before creating a listing"


@pytest.mark.asyncio
async def test_create_horse_model_validation_returns_detail_array(client):
    async def override_get_db():
        yield FakeDB([])

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(
        uuid.uuid4(), role=UserRole.SELLER, is_verified=True
    )

    response = await client.post(
        "/api/v1/horses",
        json={
            "title": "Desert Star",
            "price": 12000,
            "breed": "Arabian",
            "age": 7,
            "gender": "mare",
            "description": "Too short",
            "image_urls": [],
            "vet_check_available": False,
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("At least one listing image is required" in item["msg"] for item in detail)


@pytest.mark.asyncio
async def test_create_saved_search_invalid_payload_returns_detail_array(client):
    async def override_get_db():
        yield FakeDB([])

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: make_user(uuid.uuid4())

    response = await client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Arabian watch",
            "min_price": -10,
            "gender": "invalid",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(item["loc"][-1] == "min_price" for item in detail)
    assert any(item["loc"][-1] == "gender" for item in detail)


@pytest.mark.asyncio
async def test_openapi_manual_purge_requires_confirm_token_query_param(client):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    delete_op = spec["paths"]["/api/v1/admin/listings/deleted/expired"]["delete"]

    confirm_token_param = next(
        (p for p in delete_op.get("parameters", []) if p.get("name") == "confirm_token"),
        None,
    )

    assert confirm_token_param is not None
    assert confirm_token_param.get("in") == "query"
    assert confirm_token_param.get("required") is True


@pytest.mark.asyncio
async def test_openapi_bulk_purge_request_body_requires_confirm_token(client):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    post_op = spec["paths"]["/api/v1/admin/listings/bulk/purge"]["post"]

    request_schema_ref = (
        post_op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    )
    schema_name = request_schema_ref.rsplit("/", 1)[-1]
    request_schema = spec["components"]["schemas"][schema_name]

    assert "confirm_token" in request_schema.get("properties", {})
    assert "confirm_token" in request_schema.get("required", [])


@pytest.mark.asyncio
async def test_openapi_admin_security_status_exposes_only_safe_fields(client):
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    get_op = spec["paths"]["/api/v1/admin/security/status"]["get"]

    response_schema_ref = (
        get_op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    )
    schema_name = response_schema_ref.rsplit("/", 1)[-1]
    response_schema = spec["components"]["schemas"][schema_name]

    properties = response_schema.get("properties", {})
    assert set(properties.keys()) == {
        "purge_confirm_token_strong",
        "expiry_purge_enabled",
        "restore_window_days",
    }

    for field_name in properties.keys():
        lowered = field_name.lower()
        assert "token" not in lowered or lowered.endswith("_strong")
        assert "secret" not in lowered
        assert "password" not in lowered