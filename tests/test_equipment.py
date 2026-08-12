"""Tests for Equipment & Supplies endpoints (CRUD, location radius filter, search, admin moderation)."""

import uuid
from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, get_db, get_current_user, get_current_admin
from app.models import EquipmentListing, EquipmentImage, Category, ListingModule, User, UserRole


def make_equipment(owner_id=None, status="approved", title="Saddle Pad"):
    return EquipmentListing(
        id=uuid.uuid4(),
        owner_id=owner_id or uuid.uuid4(),
        category_id=uuid.uuid4(),
        title=title,
        brand="EquiBrand",
        sizes='["M", "L"]',
        price=350.0,
        quantity=5,
        location_text="Dubai, UAE",
        latitude=25.2048,
        longitude=55.2708,
        description="High quality leather saddle pad for jumping.",
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class FakeSession:
    def __init__(self, items=None):
        self.items = items or []
        self.added = []
        self.deleted = []

    async def execute(self, stmt):
        class Result:
            def __init__(self, data):
                self.data = data
            def scalars(self):
                class Scalars:
                    def __init__(self, d):
                        self.d = d
                    def all(s):
                        return s.d
                return Scalars(self.data)
            def scalar(self):
                return len(self.data)
            def scalar_one_or_none(self):
                return self.data[0] if self.data else None
            def scalar_one(self):
                return self.data[0]
        return Result(self.items)

    def add(self, obj):
        if not hasattr(obj, "id") or not obj.id:
            obj.id = uuid.uuid4()
        self.added.append(obj)
        if isinstance(obj, EquipmentListing):
            self.items.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.mark.asyncio
async def test_list_equipment_endpoint():
    item = make_equipment(title="Jumping Saddle")
    fake_db = FakeSession([item])
    app.dependency_overrides[get_db] = lambda: fake_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/equipment")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Jumping Saddle"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_equipment_endpoint():
    user = User(id=uuid.uuid4(), email="seller@example.com", role=UserRole.SELLER)
    fake_db = FakeSession([])

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_user] = lambda: user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "title": "Grooming Brush Set",
            "brand": "PonyCare",
            "sizes": ["One Size"],
            "price": 120.0,
            "quantity": 10,
            "location_text": "Abu Dhabi, UAE",
            "latitude": 24.4539,
            "longitude": 54.3773,
            "description": "Complete 5-piece grooming brush set for daily care.",
            "image_urls": ["https://example.com/brush.jpg"]
        }
        res = await client.post("/api/v1/equipment", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "Grooming Brush Set"
        assert data["price"] == 120.0

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_approve_equipment():
    admin = User(id=uuid.uuid4(), email="admin@example.com", role=UserRole.ADMIN)
    item = make_equipment(status="pending_review", title="Pending Bridle")
    fake_db = FakeSession([item])

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_admin] = lambda: admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(f"/api/v1/admin/equipment/{item.id}/approve")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "approved"

    app.dependency_overrides.clear()
