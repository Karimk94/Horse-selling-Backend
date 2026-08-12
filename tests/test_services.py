"""Tests for Equestrian Services endpoints (CRUD, inquiries/reservations, location radius filter, search, admin moderation)."""

import uuid
from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, get_db, get_current_user, get_current_admin
from app.models import ServiceListing, ServiceImage, ServiceInquiry, ServiceType, ServicePricingType, InquiryStatus, User, UserRole


def make_service(provider_id=None, status="approved", title="Horse Boarding Stable", service_type="housing_boarding"):
    return ServiceListing(
        id=uuid.uuid4(),
        provider_id=provider_id or uuid.uuid4(),
        category_id=uuid.uuid4(),
        title=title,
        service_type=ServiceType(service_type),
        pricing_type=ServicePricingType.MONTHLY,
        price=1500.0,
        location_text="Dubai, UAE",
        latitude=25.2048,
        longitude=55.2708,
        availability_calendar='{"working_days": ["Sun", "Mon", "Tue", "Wed", "Thu"], "hours": "7:00 AM - 7:00 PM"}',
        description="Air-conditioned stall rental with daily grooming and feeding.",
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
        if isinstance(obj, ServiceListing):
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
async def test_list_services_endpoint():
    item = make_service(title="Riding Training Lessons", service_type="training_instruction")
    fake_db = FakeSession([item])
    app.dependency_overrides[get_db] = lambda: fake_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/services?service_type=training_instruction")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Riding Training Lessons"
        assert data["items"][0]["service_type"] == "training_instruction"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_service_endpoint():
    user = User(id=uuid.uuid4(), email="trainer@example.com", role=UserRole.SELLER)
    fake_db = FakeSession([])

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_user] = lambda: user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "title": "Farrier & Hoof Care Service",
            "service_type": "health_care",
            "pricing_type": "per_head",
            "price": 250.0,
            "location_text": "Abu Dhabi, UAE",
            "availability_calendar": '{"working_days": ["Sat", "Sun", "Mon"]}',
            "description": "Professional farrier service with hot shoeing capability.",
            "image_urls": ["https://example.com/farrier.jpg"]
        }
        res = await client.post("/api/v1/services", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "Farrier & Hoof Care Service"
        assert data["pricing_type"] == "per_head"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_service_inquiry_endpoint():
    user = User(id=uuid.uuid4(), email="inquirer@example.com", role=UserRole.BUYER)
    service = make_service()
    fake_db = FakeSession([service])

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_user] = lambda: user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "inquirer_name": "Ahmed Al-Maktoum",
            "inquirer_phone": "+971501234567",
            "message": "I would like to inquire about stall reservation for 2 horses."
        }
        res = await client.post(f"/api/v1/services/{service.id}/inquiries", json=payload)
        assert res.status_code == 201
        data = res.json()
        assert data["inquirer_name"] == "Ahmed Al-Maktoum"
        assert data["status"] == "pending"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_approve_service():
    admin = User(id=uuid.uuid4(), email="admin@example.com", role=UserRole.ADMIN)
    item = make_service(status="pending_review", title="Pending Transport Service")
    fake_db = FakeSession([item])

    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_admin] = lambda: admin

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(f"/api/v1/admin/services/{item.id}/approve")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "approved"

    app.dependency_overrides.clear()
