"""Tests for media upload endpoint behavior and validation."""

import uuid
from unittest.mock import MagicMock, mock_open, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import get_current_user
from app.main import app
from app.models import User, UserRole


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def make_user(email="uploader@example.com", role=UserRole.BUYER):
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="x",
        role=role,
        is_verified=True,
        language="en",
    )


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_upload_file_rejects_invalid_extension(client):
    user = make_user()

    async def override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await client.post(
        "/api/v1/media/upload",
        files={"file": ("payload.exe", b"bad", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]


@pytest.mark.asyncio
@patch("app.media.uuid.uuid4", return_value=uuid.UUID("11111111-1111-1111-1111-111111111111"))
@patch("app.media.shutil.copyfileobj")
@patch("app.media.open", new_callable=mock_open)
@patch("app.media.os.makedirs")
async def test_upload_file_success_returns_url(
    _mock_makedirs,
    _mock_open,
    _mock_copy,
    _mock_uuid,
    client,
):
    user = make_user()

    async def override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await client.post(
        "/api/v1/media/upload",
        files={"file": ("horse.jpg", b"binary-image", "image/jpeg")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["file_url"].endswith("/uploads/11111111-1111-1111-1111-111111111111.jpg")


@pytest.mark.asyncio
@patch("app.media.shutil.copyfileobj", side_effect=Exception("disk error"))
@patch("app.media.open", new_callable=mock_open)
@patch("app.media.os.makedirs")
async def test_upload_file_save_error_returns_500(
    _mock_makedirs,
    _mock_open,
    _mock_copy,
    client,
):
    user = make_user()

    async def override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_get_current_user

    response = await client.post(
        "/api/v1/media/upload",
        files={"file": ("horse.png", b"binary-image", "image/png")},
    )

    assert response.status_code == 500
    assert "Could not save file" in response.json()["detail"]


def test_normalize_media_url_replaces_domain():
    from app.schemas import normalize_media_url, HorseImageResponse
    from app.config import BASE_URL
    import uuid
    from datetime import datetime, timezone

    # Should replace localhost with BASE_URL
    res = normalize_media_url("http://localhost:8000/uploads/test.jpg")
    expected_base = (BASE_URL or "").rstrip("/")
    assert res == f"{expected_base}/uploads/test.jpg"

    # Should replace outdated domain with BASE_URL
    res2 = normalize_media_url("https://steedmarket-backend.up.railway.app/uploads/test.jpg")
    assert res2 == f"{expected_base}/uploads/test.jpg"

    # Preserves non-upload URLs
    res3 = normalize_media_url("https://s3.amazonaws.com/bucket/pic.jpg")
    assert res3 == "https://s3.amazonaws.com/bucket/pic.jpg"

    # Schema automatically normalizes
    img_resp = HorseImageResponse(
        id=uuid.uuid4(),
        image_url="http://localhost:8000/uploads/my-horse.jpg",
        display_order=0,
        created_at=datetime.now(timezone.utc),
    )
    assert img_resp.image_url == f"{expected_base}/uploads/my-horse.jpg"

