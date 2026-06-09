"""Tests for admin moderation and management endpoints."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.main import app, get_current_admin, get_db
from app.models import (
    Horse,
    HorseGender,
    ListingReview,
    SavedSearch,
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
    def __init__(self, scalar_value=None, scalars_items=None, rows=None):
        self._scalar_value = scalar_value
        self._scalars_items = scalars_items or []
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar_value

    def scalar(self):
        return self._scalar_value

    def scalar_one(self):
        return self._scalar_value

    def scalars(self):
        return FakeScalars(self._scalars_items)

    def all(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.deleted = []

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


async def _forbidden_non_admin_override():
    from fastapi import HTTPException

    raise HTTPException(status_code=403, detail="The user doesn't have enough privileges")


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
        first_name="John",
        last_name="Doe",
        phone_number="1234567",
        location="City",
    )
    return user


def make_horse(owner_id, status="pending_review"):
    horse = Horse(
        id=uuid.uuid4(),
        owner_id=owner_id,
        title="Moderation Horse",
        price=11000,
        breed="Arabian",
        age=7,
        gender=HorseGender.MARE,
        discipline="Endurance",
        height=1.6,
        description="Healthy and trained horse for endurance.",
        status=status,
        vet_check_available=True,
        vet_certificate_url="https://example.com/vet.pdf",
        image_url="https://example.com/horse.jpg",
        rejection_reason=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return horse


def make_saved_search(user_id):
    return SavedSearch(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Arabian alert",
        breed="Arabian",
        discipline="Endurance",
        gender="mare",
        min_price=None,
        max_price=None,
        min_age=None,
        max_age=None,
        vet_check_available=None,
        verified_seller=None,
        is_active=True,
        last_alerted_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_admin_update_user_role_not_found(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{uuid.uuid4()}/role",
        json={"role": "seller"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_update_user_role_cannot_modify_admin(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    target_admin = make_user(email="other-admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([FakeResult(scalar_value=target_admin)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{target_admin.id}/role",
        json={"role": "seller"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_update_user_role_success(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    target_user = make_user(email="target@example.com", role=UserRole.BUYER)
    fake_db = FakeDB([FakeResult(scalar_value=target_user)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{target_user.id}/role",
        json={"role": "seller"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "seller"


@pytest.mark.asyncio
async def test_admin_security_status_returns_flags(client, monkeypatch):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep
    monkeypatch.setattr(main_module, "SOFT_DELETE_RESTORE_DAYS", 30)
    monkeypatch.setattr(main_module, "PURGE_CONFIRM_TOKEN", "X9v2!kL0_s3cure")

    response = await client.get("/api/v1/admin/security/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["purge_confirm_token_strong"] is True
    assert payload["expiry_purge_enabled"] is True
    assert payload["restore_window_days"] == 30


@pytest.mark.asyncio
async def test_admin_security_status_reports_weak_token(client, monkeypatch):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep
    monkeypatch.setattr(main_module, "SOFT_DELETE_RESTORE_DAYS", 0)
    monkeypatch.setattr(main_module, "PURGE_CONFIRM_TOKEN", "PURGE")

    response = await client.get("/api/v1/admin/security/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["purge_confirm_token_strong"] is False
    assert payload["expiry_purge_enabled"] is False
    assert payload["restore_window_days"] == 0


@pytest.mark.asyncio
async def test_admin_security_status_does_not_expose_raw_purge_token(client, monkeypatch):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep
    monkeypatch.setattr(main_module, "PURGE_CONFIRM_TOKEN", "VerySecretToken123!")

    response = await client.get("/api/v1/admin/security/status")

    assert response.status_code == 200
    payload = response.json()
    assert "confirm_token" not in payload
    assert "purge_confirm_token" not in payload
    assert "VerySecretToken123!" not in response.text


@pytest.mark.asyncio
async def test_admin_security_status_requires_authentication(client):
    response = await client.get("/api/v1/admin/security/status")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_security_status_forbids_non_admin_user(client):
    app.dependency_overrides[get_current_admin] = _forbidden_non_admin_override

    response = await client.get("/api/v1/admin/security/status")

    assert response.status_code == 403
    assert "privileges" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_update_user_details_phone_conflict(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    target_user = make_user(email="target@example.com", role=UserRole.BUYER)
    fake_db = FakeDB([
        FakeResult(scalar_value=target_user),
        FakeResult(scalar_value=object()),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{target_user.id}",
        json={"phone_number": "999999"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_admin_update_user_details_not_found(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{uuid.uuid4()}",
        json={"location": "New City"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_update_user_details_cannot_demote_other_admin(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    other_admin = make_user(email="other-admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([FakeResult(scalar_value=other_admin)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{other_admin.id}",
        json={"role": "seller"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_update_user_details_success(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    target_user = make_user(email="target@example.com", role=UserRole.BUYER)
    target_user.profile.phone_number = "111111"

    fake_db = FakeDB([
        FakeResult(scalar_value=target_user),
        FakeResult(scalar_value=None),
        FakeResult(scalar_value=target_user),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{target_user.id}",
        json={"role": "seller", "phone_number": "222222", "location": "New City"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["role"] == "seller"
    assert payload["profile"]["phone_number"] == "222222"


@pytest.mark.asyncio
async def test_admin_update_user_details_sets_verified_and_creates_profile(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    target_user = make_user(email="target2@example.com", role=UserRole.BUYER)
    target_user.profile = None

    fake_db = FakeDB([
        FakeResult(scalar_value=target_user),
        FakeResult(scalar_value=target_user),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.put(
        f"/api/v1/admin/users/{target_user.id}",
        json={"is_verified": False, "location": "Nowhere"},
    )

    assert response.status_code == 200
    assert response.json()["is_verified"] is False


@pytest.mark.asyncio
async def test_admin_approve_listing_not_found(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(f"/api/v1/admin/listings/{uuid.uuid4()}/approve", json={})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_approve_listing_bad_status(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    seller = make_user(email="seller@example.com", role=UserRole.SELLER)
    horse = make_horse(owner_id=seller.id, status="approved")
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(f"/api/v1/admin/listings/{horse.id}/approve", json={})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_admin_list_listings_returns_items(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    seller = make_user(email="seller@example.com", role=UserRole.SELLER)
    horse = make_horse(owner_id=seller.id, status="approved")
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[horse]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.get("/api/v1/admin/listings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert len(payload["listings"]) == 1


@pytest.mark.asyncio
async def test_admin_approve_listing_success(client, monkeypatch):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    seller = make_user(email="seller@example.com", role=UserRole.SELLER)
    buyer = make_user(email="buyer@example.com", role=UserRole.BUYER)

    horse = make_horse(owner_id=seller.id, status="pending_review")
    horse.owner = seller
    horse.images = []

    saved_search = make_saved_search(user_id=buyer.id)

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(rows=[(saved_search, buyer)]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    monkeypatch.setattr(main_module, "send_listing_approved_email", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "matches_saved_search", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "send_saved_search_match_email", lambda *_args, **_kwargs: True)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(f"/api/v1/admin/listings/{horse.id}/approve", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_admin_approve_listing_no_saved_search_match_branch(client, monkeypatch):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    seller = make_user(email="seller@example.com", role=UserRole.SELLER)
    buyer = make_user(email="buyer@example.com", role=UserRole.BUYER)

    horse = make_horse(owner_id=seller.id, status="pending_review")
    horse.owner = seller
    horse.images = []

    saved_search = make_saved_search(user_id=buyer.id)

    fake_db = FakeDB([
        FakeResult(scalar_value=horse),
        FakeResult(rows=[(saved_search, buyer)]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    monkeypatch.setattr(main_module, "send_listing_approved_email", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_module, "matches_saved_search", lambda *_args, **_kwargs: False)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(f"/api/v1/admin/listings/{horse.id}/approve", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_admin_reject_listing_success(client, monkeypatch):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    seller = make_user(email="seller@example.com", role=UserRole.SELLER)

    horse = make_horse(owner_id=seller.id, status="pending_review")
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    monkeypatch.setattr(main_module, "send_listing_rejected_email", lambda *_args, **_kwargs: True)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        f"/api/v1/admin/listings/{horse.id}/reject",
        json={"reason": "Incomplete documents"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "rejected"
    assert payload["rejection_reason"] == "Incomplete documents"


@pytest.mark.asyncio
async def test_admin_reject_listing_not_found(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([FakeResult(scalar_value=None)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        f"/api/v1/admin/listings/{uuid.uuid4()}/reject",
        json={"reason": "Incomplete"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_reject_listing_bad_status(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    seller = make_user(email="seller@example.com", role=UserRole.SELLER)
    horse = make_horse(owner_id=seller.id, status="approved")
    horse.owner = seller
    horse.images = []

    fake_db = FakeDB([FakeResult(scalar_value=horse)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        f"/api/v1/admin/listings/{horse.id}/reject",
        json={"reason": "Incomplete"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_admin_list_reviews_success(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    review = ListingReview(
        id=uuid.uuid4(),
        horse_id=uuid.uuid4(),
        admin_id=admin.id,
        action="approve",
        reason=None,
        created_at=datetime.now(timezone.utc),
    )

    fake_db = FakeDB([FakeResult(rows=[(review, admin.email)])])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.get("/api/v1/admin/reviews")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["admin_email"] == admin.email
    assert payload[0]["action"] == "approve"


@pytest.mark.asyncio
async def test_admin_purge_expired_deleted_listings_success(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    old_deleted_horse = make_horse(owner_id=admin.id, status="approved")
    old_deleted_horse.deleted_at = datetime.now(timezone.utc) - timedelta(days=45)

    fake_db = FakeDB([FakeResult(scalars_items=[old_deleted_horse])])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.delete("/api/v1/admin/listings/deleted/expired?confirm_token=PURGE")

    assert response.status_code == 200
    payload = response.json()
    assert payload["purged_count"] == 1
    assert payload["retention_days"] > 0
    assert len(fake_db.deleted) == 1


@pytest.mark.asyncio
async def test_admin_purge_expired_deleted_listings_invalid_confirm_token(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.delete(
        "/api/v1/admin/listings/deleted/expired?confirm_token=NOPE"
    )

    assert response.status_code == 400
    assert "invalid confirmation token" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_purge_expired_deleted_listings_missing_confirm_token(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.delete("/api/v1/admin/listings/deleted/expired")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_bulk_restore_listings_mixed_outcomes(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    recent_deleted = make_horse(owner_id=admin.id, status="approved")
    recent_deleted.deleted_at = datetime.now(timezone.utc) - timedelta(days=1)

    expired_deleted = make_horse(owner_id=admin.id, status="approved")
    expired_deleted.deleted_at = datetime.now(timezone.utc) - timedelta(days=45)

    active_listing = make_horse(owner_id=admin.id, status="approved")
    active_listing.deleted_at = None

    fake_db = FakeDB([
        FakeResult(scalar_value=recent_deleted),
        FakeResult(scalar_value=expired_deleted),
        FakeResult(scalar_value=active_listing),
        FakeResult(scalar_value=None),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/restore",
        json={
            "horse_ids": [
                str(recent_deleted.id),
                str(expired_deleted.id),
                str(active_listing.id),
                str(uuid.uuid4()),
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["restored_count"] == 1
    assert payload["expired_count"] == 1
    assert payload["already_active_count"] == 1
    assert payload["failed_count"] == 1
    assert recent_deleted.deleted_at is None

    reviews = [obj for obj in fake_db.added if isinstance(obj, ListingReview)]
    assert len(reviews) == 1
    assert reviews[0].action == "restore"
    assert reviews[0].horse_id == recent_deleted.id


@pytest.mark.asyncio
async def test_admin_bulk_purge_deleted_listings_mixed_outcomes(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    expired_deleted = make_horse(owner_id=admin.id, status="approved")
    expired_deleted.deleted_at = datetime.now(timezone.utc) - timedelta(days=45)

    active_listing = make_horse(owner_id=admin.id, status="approved")
    active_listing.deleted_at = None

    not_expired_deleted = make_horse(owner_id=admin.id, status="approved")
    not_expired_deleted.deleted_at = datetime.now(timezone.utc) - timedelta(days=2)

    fake_db = FakeDB([
        FakeResult(scalar_value=expired_deleted),
        FakeResult(scalar_value=active_listing),
        FakeResult(scalar_value=not_expired_deleted),
        FakeResult(scalar_value=None),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/purge",
        json={
            "horse_ids": [
                str(expired_deleted.id),
                str(active_listing.id),
                str(not_expired_deleted.id),
                str(uuid.uuid4()),
            ],
            "confirm_token": "PURGE",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["purged_count"] == 1
    assert payload["not_deleted_count"] == 2
    assert payload["not_expired_count"] == 1
    assert len(fake_db.deleted) == 1
    assert fake_db.deleted[0].id == expired_deleted.id


@pytest.mark.asyncio
async def test_admin_bulk_restore_listings_rejects_empty_horse_ids(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/restore",
        json={"horse_ids": []},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_bulk_restore_listings_deduplicates_ids(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    listing = make_horse(owner_id=admin.id, status="approved")
    listing.deleted_at = datetime.now(timezone.utc) - timedelta(days=1)

    fake_db = FakeDB([FakeResult(scalar_value=listing)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/restore",
        json={"horse_ids": [str(listing.id), str(listing.id)]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["restored_count"] == 1
    assert payload["already_active_count"] == 0
    assert payload["failed_count"] == 0


@pytest.mark.asyncio
async def test_admin_bulk_purge_deleted_listings_rejects_empty_horse_ids(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/purge",
        json={"horse_ids": [], "confirm_token": "PURGE"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_bulk_purge_deleted_listings_deduplicates_ids(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    expired_deleted = make_horse(owner_id=admin.id, status="approved")
    expired_deleted.deleted_at = datetime.now(timezone.utc) - timedelta(days=45)

    fake_db = FakeDB([FakeResult(scalar_value=expired_deleted)])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/purge",
        json={
            "horse_ids": [str(expired_deleted.id), str(expired_deleted.id)],
            "confirm_token": "PURGE",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["purged_count"] == 1
    assert payload["not_deleted_count"] == 0
    assert payload["not_expired_count"] == 0
    assert len(fake_db.deleted) == 1


@pytest.mark.asyncio
async def test_admin_bulk_purge_deleted_listings_disabled_when_unlimited_window(client, monkeypatch):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep
    monkeypatch.setattr(main_module, "SOFT_DELETE_RESTORE_DAYS", 0)

    response = await client.post(
        "/api/v1/admin/listings/bulk/purge",
        json={"horse_ids": [str(uuid.uuid4())], "confirm_token": "PURGE"},
    )

    assert response.status_code == 400
    assert "unlimited" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_bulk_purge_deleted_listings_invalid_confirm_token(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/purge",
        json={"horse_ids": [str(uuid.uuid4())], "confirm_token": "WRONG"},
    )

    assert response.status_code == 400
    assert "invalid confirmation token" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_bulk_purge_deleted_listings_missing_confirm_token(client):
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    fake_db = FakeDB([])

    async def override_get_db():
        return fake_db

    async def override_get_current_admin_dep():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin_dep

    response = await client.post(
        "/api/v1/admin/listings/bulk/purge",
        json={"horse_ids": [str(uuid.uuid4())]},
    )

    assert response.status_code == 422
