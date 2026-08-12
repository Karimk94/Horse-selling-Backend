"""Tests for Rider Apparel & Gear endpoints (CRUD, gender filtering, search, admin moderation)."""

import uuid
from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, get_db, get_current_user, get_current_admin
from app.models import RiderGearListing, RiderGearImage, RiderGender, User, UserRole


def make_rider_gear(owner_id=None, status="approved", title="Full Seat Breeches", gender="female"):
    return RiderGearListing(
        id=uuid.uuid4(),
        owner_id=owner_id or uuid.uuid4(),
        category_id=uuid.uuid4(),
        title=title,
        brand="Pikeur",
        gender=RiderGender(gender),
        sizes='["36", "38", "40"]',
        price=650.0,
        quantity=3,
        location_text="Dubai, UAE",
        latitude=25.2048,
        longitude=55.2708,
        description="High performance silicone full seat breeches.",
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
        if isinstance(obj, RiderGearListing):
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
async def test_list_rider_gear_endpoint():
    item = make_rider_gear(title="Tall Riding Boots", gender="male")
    fake_db = FakeSession([item])
    app.dependency_overrides[get_db] = lambda: fake_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/rider-gear?gender=male")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Tall Riding Boots"
        assert data["items"][0]["gender"] == "male"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_rider_gear_endpoint():
    user = User(id=uuid.uuid4(), email="rider@example.com", role=UserRole.SELLER)
    fake_db = FakeSession([])

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_user] = lambda: user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "title": "Safety Air Vest",
            "brand": "Helite",
            "gender": "unisex",
            "sizes": ["M", "L"],
            "price": 2200.0,
            "quantity": 2,
            "location_text": "Sharjah, UAE",
            "description": "Equestrian airbag protector vest with CO2 cartridge.",
            "image_urls": ["https://example.com/airvest.jpg"]
        }
        res = await client.post("/api/v1/rider-gear", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "Safety Air Vest"
        assert data["gender"] == "unisex"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_approve_rider_gear():
    admin = User(id=uuid.uuid4(), email="admin@example.com", role=UserRole.ADMIN)
    item = make_rider_gear(status="pending_review", title="Pending Show Jacket")
    fake_db = FakeSession([item])

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_admin] = lambda: admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(f"/api/v1/admin/rider-gear/{item.id}/approve")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "approved"

    app.dependency_overrides.clear()
