"""Tests for Category endpoints and tree building logic."""

import uuid
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app, get_current_admin, get_db
from app.models import Category, ListingModule, User, UserRole
from app.routers.categories import build_tree


def test_build_tree_hierarchy():
    cat1 = Category(
        id=uuid.uuid4(),
        name_ar="المعدات",
        name_en="Equipment",
        slug="equipment",
        module=ListingModule.EQUIPMENT,
        parent_id=None,
        display_order=1,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    cat2 = Category(
        id=uuid.uuid4(),
        name_ar="السروج",
        name_en="Saddles",
        slug="saddles",
        module=ListingModule.EQUIPMENT,
        parent_id=cat1.id,
        display_order=1,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    tree = build_tree([cat1, cat2])
    assert len(tree) == 1
    assert tree[0].name_en == "Equipment"
    assert len(tree[0].children) == 1
    assert tree[0].children[0].name_en == "Saddles"


class FakeSession:
    def __init__(self, categories=None):
        self.categories = categories or []
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
            def scalar_one_or_none(self):
                return self.data[0] if self.data else None
        return Result(self.categories)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        if not hasattr(obj, "id") or not obj.id:
            obj.id = uuid.uuid4()
        if not hasattr(obj, "created_at") or not obj.created_at:
            obj.created_at = datetime.now(timezone.utc)
        if not hasattr(obj, "updated_at") or not obj.updated_at:
            obj.updated_at = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_list_categories_endpoint():
    cat1 = Category(
        id=uuid.uuid4(),
        name_ar="مستلزمات الخيل",
        name_en="Horse Equipment",
        slug="horse-equipment",
        module=ListingModule.EQUIPMENT,
        parent_id=None,
        display_order=1,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    fake_db = FakeSession([cat1])
    app.dependency_overrides[get_db] = lambda: fake_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/api/v1/categories?tree=false")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 1
        assert data[0]["slug"] == "horse-equipment"

    app.dependency_overrides.clear()
