"""Integration-style tests for auth endpoints in main.py."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.main import app, get_db
from app.models import User, UserRole


class FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


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


def make_user(email="user@example.com", verified=False, role=UserRole.BUYER):
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hashed",
        role=role,
        is_verified=verified,
        language="en",
        verification_code=None,
        verification_code_expires_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_signup_existing_user_returns_409(client):
    fake_db = FakeDB([FakeResult(scalar_value=make_user("existing@example.com"))])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/signup",
        json={"email": "existing@example.com", "password": "Password123"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_signup_phone_in_use_returns_409(client):
    fake_db = FakeDB([FakeResult(scalar_value=None), FakeResult(scalar_value=object())])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/signup",
        json={
            "email": "new@example.com",
            "password": "Password123",
            "phone_number": "123456789",
        },
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_signup_success_returns_token(client, monkeypatch):
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module, "hash_password", lambda _pw: "hashed")
    monkeypatch.setattr(main_module, "create_verification_token", lambda _email: "verify-token")
    monkeypatch.setattr(main_module, "send_verification_email", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "create_access_token", lambda **_kwargs: "access-token")

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/signup",
        json={"email": "new@example.com", "password": "Password123", "role": "buyer"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["access_token"] == "access-token"


@pytest.mark.asyncio
async def test_signup_with_optional_profile_fields_creates_profile(client, monkeypatch):
    fake_db = FakeDB([FakeResult(scalar_value=None), FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module, "hash_password", lambda _pw: "hashed")
    monkeypatch.setattr(main_module, "create_verification_token", lambda _email: "verify-token")
    monkeypatch.setattr(main_module, "send_verification_email", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "create_access_token", lambda **_kwargs: "access-token")

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/signup",
        json={
            "email": "profile@example.com",
            "password": "Password123",
            "role": "buyer",
            "first_name": "A",
            "last_name": "B",
            "phone_number": "777777",
            "location": "City",
        },
    )

    assert response.status_code == 201
    assert len(fake_db.added) >= 2


@pytest.mark.asyncio
async def test_signup_email_send_failure_returns_500(client, monkeypatch):
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module, "hash_password", lambda _pw: "hashed")
    monkeypatch.setattr(main_module, "create_verification_token", lambda _email: "verify-token")
    monkeypatch.setattr(main_module, "send_verification_email", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(main_module, "create_access_token", lambda **_kwargs: "access-token")

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/signup",
        json={"email": "new2@example.com", "password": "Password123", "role": "buyer"},
    )

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_login_invalid_credentials_returns_401(client, monkeypatch):
    fake_db = FakeDB([FakeResult(scalar_value=make_user("u@example.com", verified=True))])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module, "verify_password", lambda *_args, **_kwargs: False)
    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/login",
        json={"email": "u@example.com", "password": "wrong"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_success_returns_token(client, monkeypatch):
    fake_db = FakeDB([FakeResult(scalar_value=make_user("u@example.com", verified=True))])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module, "verify_password", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "create_access_token", lambda **_kwargs: "login-token")
    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/login",
        json={"email": "u@example.com", "password": "Password123"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "login-token"


@pytest.mark.asyncio
async def test_verify_email_invalid_token_type_returns_400(client, monkeypatch):
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module, "verify_token", lambda _token: {"type": "access", "sub": "u@example.com"})
    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/auth/verify-email?token=abc")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_email_success_marks_verified(client, monkeypatch):
    user = make_user("verifyme@example.com", verified=False)
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda _token: {"type": "verification", "sub": "verifyme@example.com"},
    )
    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/auth/verify-email?token=good")

    assert response.status_code == 200
    assert user.is_verified is True


@pytest.mark.asyncio
async def test_verify_email_missing_sub_returns_400(client, monkeypatch):
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module, "verify_token", lambda _token: {"type": "verification"})
    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/auth/verify-email?token=missing-sub")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_email_user_not_found_returns_404(client, monkeypatch):
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda _token: {"type": "verification", "sub": "nouser@example.com"},
    )
    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/auth/verify-email?token=nouser")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_verify_email_already_verified_returns_400(client, monkeypatch):
    user = make_user("done@example.com", verified=True)
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda _token: {"type": "verification", "sub": "done@example.com"},
    )
    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/auth/verify-email?token=already")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_email_unexpected_exception_returns_400(client, monkeypatch):
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    def _boom(_token):
        raise RuntimeError("decode failure")

    monkeypatch.setattr(main_module, "verify_token", _boom)
    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/auth/verify-email?token=boom")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_otp_user_not_found_returns_404(client):
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/auth/send-otp", json={"email": "missing@example.com"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_send_otp_success(client, monkeypatch):
    user = make_user("otp@example.com", verified=False)
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module.random, "choices", lambda *_args, **_kwargs: list("123456"))
    monkeypatch.setattr(main_module, "send_otp_email", lambda *_args, **_kwargs: True)
    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/auth/send-otp", json={"email": "otp@example.com"})

    assert response.status_code == 200
    assert user.verification_code == "123456"


@pytest.mark.asyncio
async def test_send_otp_already_verified_returns_400(client):
    user = make_user("verified@example.com", verified=True)
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/auth/send-otp", json={"email": "verified@example.com"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_send_otp_email_failure_returns_500(client, monkeypatch):
    user = make_user("otp-fail@example.com", verified=False)
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    monkeypatch.setattr(main_module.random, "choices", lambda *_args, **_kwargs: list("123456"))
    monkeypatch.setattr(main_module, "send_otp_email", lambda *_args, **_kwargs: False)
    app.dependency_overrides[get_db] = override_get_db

    response = await client.post("/auth/send-otp", json={"email": "otp-fail@example.com"})
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_verify_otp_success_marks_verified(client):
    user = make_user("otpv@example.com", verified=False)
    user.verification_code = "123456"
    user.verification_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/verify-otp",
        json={"email": "otpv@example.com", "otp": "123456"},
    )

    assert response.status_code == 200
    assert user.is_verified is True


@pytest.mark.asyncio
async def test_verify_otp_user_not_found_returns_404(client):
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/verify-otp",
        json={"email": "missing-otp@example.com", "otp": "123456"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_verify_otp_invalid_code_returns_400(client):
    user = make_user("otpbad@example.com", verified=False)
    user.verification_code = "123456"
    user.verification_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/verify-otp",
        json={"email": "otpbad@example.com", "otp": "999999"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_otp_user_already_verified_returns_200(client):
    user = make_user("otpdone@example.com", verified=True)
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/verify-otp",
        json={"email": "otpdone@example.com", "otp": "123456"},
    )

    assert response.status_code == 200
    assert "already verified" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_verify_otp_no_request_returns_400(client):
    user = make_user("otpnoreq@example.com", verified=False)
    user.verification_code = None
    user.verification_code_expires_at = None
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/verify-otp",
        json={"email": "otpnoreq@example.com", "otp": "123456"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_verify_otp_expired_returns_400(client):
    user = make_user("otpexp@example.com", verified=False)
    user.verification_code = "123456"
    user.verification_code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    fake_db = FakeDB([FakeResult(scalar_value=user)])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.post(
        "/auth/verify-otp",
        json={"email": "otpexp@example.com", "otp": "123456"},
    )

    assert response.status_code == 400
