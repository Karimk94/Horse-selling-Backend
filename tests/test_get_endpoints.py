"""
Tests for GET endpoints (list/detail/profile).
"""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app, get_current_user, get_optional_current_user, get_db
from app.models import Horse, HorseGender, User, UserRole, UserProfile
from app.auth import create_access_token


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

    def scalar_one(self):
        return self._scalar_value
    
    def scalar(self):
        """Alias for scalar_one_or_none() - used by some endpoints."""
        return self._scalar_value

    def scalars(self):
        return FakeScalars(self._scalars_items)


class FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("No fake results left for execute()")
        return self._results.pop(0)

    def add(self, _obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def make_user(user_id=None, email="user@example.com", role=UserRole.BUYER):
    return User(
        id=user_id or uuid.uuid4(),
        email=email,
        password_hash="x",
        role=role,
        is_verified=True,
        language="en",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def make_horse(horse_id=None, owner_id=None, status="approved"):
    return Horse(
        id=horse_id or uuid.uuid4(),
        owner_id=owner_id or uuid.uuid4(),
        title="Test Horse",
        price=10000,
        breed="Arabian",
        age=7,
        gender=HorseGender.MARE,
        discipline=None,
        height=None,
        description=None,
        status=status,
        vet_check_available=False,
        vet_certificate_url=None,
        image_url=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_list_horses_returns_200(client):
    """GET /horses returns status 200."""
    owner_id = uuid.uuid4()
    owner = make_user(user_id=owner_id)
    owner.profile = UserProfile(user_id=owner_id)
    
    horse = make_horse(owner_id=owner_id)
    horse.owner = owner
    horse.images = []
    
    fake_db = FakeDB([
        FakeResult(scalar_value=1),  # count query
        FakeResult(scalars_items=[horse]),  # list query
    ])
    
    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/api/v1/horses")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_horses_empty(client):
    """GET /horses returns empty items when no matches."""
    fake_db = FakeDB([
        FakeResult(scalar_value=0),  # count query
        FakeResult(scalars_items=[]),  # empty result
    ])
    
    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/api/v1/horses?min_price=999999")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_horses_owner_query_shows_all_statuses_branch(client):
    """Owner querying own listings hits show_all_statuses path."""
    owner_id = uuid.uuid4()
    owner = make_user(user_id=owner_id, email="owner@example.com", role=UserRole.SELLER)
    owner.profile = UserProfile(user_id=owner_id)

    pending_horse = make_horse(owner_id=owner_id, status="pending_review")
    pending_horse.owner = owner
    pending_horse.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[pending_horse]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_optional_current_user():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_optional_current_user] = override_get_optional_current_user

    response = await client.get(f"/api/v1/horses?owner_id={owner_id}")
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_horses_with_explicit_status_filter_branch(client):
    """Explicit horse_status parameter uses direct status filter branch."""
    owner_id = uuid.uuid4()
    owner = make_user(user_id=owner_id)
    owner.profile = UserProfile(user_id=owner_id)

    sold_horse = make_horse(owner_id=owner_id, status="sold")
    sold_horse.owner = owner
    sold_horse.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[sold_horse]),
    ])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/api/v1/horses?horse_status=sold")
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_horses_admin_user_branch(client):
    """Authenticated admin path hits the admin role visibility branch."""
    admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
    admin.profile = UserProfile(user_id=admin.id)

    horse = make_horse(owner_id=uuid.uuid4(), status="approved")
    horse.owner = admin
    horse.images = []

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[horse]),
    ])

    async def override_get_db():
        return fake_db

    async def override_get_optional_current_user():
        return admin

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_optional_current_user] = override_get_optional_current_user

    response = await client.get("/api/v1/horses")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_horses_with_additional_filters_branches(client):
    """Covers min/max age, discipline, vet_check_available, verified_seller branches."""
    owner = make_user()
    owner.profile = UserProfile(user_id=owner.id)

    horse = make_horse(owner_id=owner.id, status="approved")
    horse.owner = owner
    horse.images = []
    horse.discipline = "Endurance"
    horse.vet_check_available = True

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[horse]),
    ])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get(
        "/api/v1/horses?min_age=4&max_age=10&discipline=Endurance&vet_check_available=true&verified_seller=true"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_horses_with_price_and_breed_filter_branches(client):
    """Covers max_price and breed filter branches."""
    owner = make_user()
    owner.profile = UserProfile(user_id=owner.id)

    horse = make_horse(owner_id=owner.id, status="approved")
    horse.owner = owner
    horse.images = []
    horse.breed = "Arabian"

    fake_db = FakeDB([
        FakeResult(scalar_value=1),
        FakeResult(scalars_items=[horse]),
    ])

    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/api/v1/horses?min_price=9000&max_price=11000&breed=Arab")
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_get_horse_approved_visible_unauthenticated(client):
    """GET approved horse visible without authentication."""
    owner_id = uuid.uuid4()
    owner = make_user(user_id=owner_id)
    owner.profile = UserProfile(user_id=owner_id)
    
    horse = make_horse(owner_id=owner_id, status="approved")
    horse.owner = owner
    horse.images = []
    
    fake_db = FakeDB([
        FakeResult(scalar_value=horse),  # horse detail query
    ])
    
    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get(f"/api/v1/horses/{horse.id}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_horse_pending_hidden_from_anonymous(client):
    """GET pending horse returns 404 for unauthenticated user."""
    owner_id = uuid.uuid4()
    owner = make_user(user_id=owner_id)
    owner.profile = UserProfile(user_id=owner_id)
    
    horse = make_horse(owner_id=owner_id, status="pending_review")
    horse.owner = owner
    horse.images = []
    
    fake_db = FakeDB([
        FakeResult(scalar_value=horse),  # horse detail query
    ])
    
    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get(f"/api/v1/horses/{horse.id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_horse_pending_visible_to_owner(client):
    """GET pending horse visible to owner."""
    owner_id = uuid.uuid4()
    owner = make_user(user_id=owner_id, email="owner@example.com")
    owner.profile = UserProfile(user_id=owner_id)
    
    horse = make_horse(horse_id=uuid.uuid4(), owner_id=owner_id, status="pending_review")
    horse.owner = owner
    horse.images = []
    
    fake_db = FakeDB([
        FakeResult(scalar_value=horse),  # horse detail query
    ])
    
    async def override_get_db():
        return fake_db

    async def override_get_optional_current_user():
        return owner

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_optional_current_user] = override_get_optional_current_user

    token = create_access_token({"sub": owner.email})
    response = await client.get(
        f"/api/v1/horses/{horse.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_horse_not_found(client):
    """GET horse returns 404 when not found."""
    fake_db = FakeDB([
        FakeResult(scalar_value=None),  # horse not found
    ])
    
    async def override_get_db():
        return fake_db

    app.dependency_overrides[get_db] = override_get_db

    fake_id = uuid.uuid4()
    response = await client.get(f"/api/v1/horses/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_profile_requires_auth(client):
    """GET /profile without token returns 401."""
    response = await client.get("/api/v1/profile")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_profile_returns_user(client):
    """GET /profile returns current user profile."""
    user_id = uuid.uuid4()
    user = make_user(user_id=user_id, email="user@example.com")
    user.profile = UserProfile(user_id=user_id, first_name="John")
    
    fake_db = FakeDB([
        FakeResult(scalar_value=user),  # profile query
    ])
    
    async def override_get_db():
        return fake_db
    
    async def override_get_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    token = create_access_token({"sub": user.email})
    response = await client.get(
        "/api/v1/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == user.email


@pytest.mark.asyncio
async def test_admin_list_users_requires_admin(client):
    """GET /admin/users requires admin role."""
    user = make_user(email="user@example.com", role=UserRole.BUYER)
    user.profile = UserProfile(user_id=user.id)
    
    fake_db = FakeDB([])  # no results - dependency should fail before
    
    async def override_get_db():
        return fake_db
    
    async def override_get_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    # Import after dependencies are overridden
    from app.main import get_current_admin
    
    async def override_get_current_admin():
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not admin",
        )
    
    app.dependency_overrides[get_current_admin] = override_get_current_admin

    token = create_access_token({"sub": user.email})
    response = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_users_returns_users(client):
    """GET /admin/users returns list of users."""
    admin_id = uuid.uuid4()
    admin = make_user(user_id=admin_id, email="admin@example.com", role=UserRole.ADMIN)
    admin.profile = UserProfile(user_id=admin_id)
    
    user1_id = uuid.uuid4()
    user1 = make_user(user_id=user1_id)
    user1.profile = UserProfile(user_id=user1_id)
    
    fake_db = FakeDB([
        FakeResult(scalars_items=[admin, user1]),  # users list query
    ])
    
    async def override_get_db():
        return fake_db

    from app.main import get_current_admin
    
    async def override_get_current_admin():
        return admin
    
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin

    token = create_access_token({"sub": admin.email})
    response = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_admin_list_pending(client):
    """GET /admin/listings/pending returns pending horses."""
    admin_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    
    admin = make_user(user_id=admin_id, email="admin@example.com", role=UserRole.ADMIN)
    admin.profile = UserProfile(user_id=admin_id)
    owner = make_user(user_id=owner_id)
    owner.profile = UserProfile(user_id=owner_id)
    
    horse = make_horse(owner_id=owner_id, status="pending_review")
    horse.owner = owner
    horse.images = []
    
    fake_db = FakeDB([
        FakeResult(scalars_items=[horse]),  # pending horses query
    ])
    
    async def override_get_db():
        return fake_db

    from app.main import get_current_admin
    
    async def override_get_current_admin():
        return admin
    
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin] = override_get_current_admin

    token = create_access_token({"sub": admin.email})
    response = await client.get(
        "/api/v1/admin/listings/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
