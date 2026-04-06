"""
Tests for FastAPI auth dependency injection (get_current_user, get_optional_current_user, get_current_admin).
"""
import pytest
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import (
    get_current_user,
    get_optional_current_user,
    get_current_admin,
    create_access_token,
)
from app.models import User, UserRole


class FakeResult:
    def __init__(self, scalar_value=None):
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        return self._scalar_value


class FakeDB:
    def __init__(self):
        self._result = None

    def add_result(self, query_type, value):
        self._result = FakeResult(scalar_value=value)

    async def execute(self, stmt):
        return self._result


def make_user(user_id, email="user@example.com", role=UserRole.BUYER):
    """Factory to create User instances for testing."""
    return User(
        id=user_id,
        email=email,
        password_hash="hashed_password",
        role=role,
        is_verified=True,
        language="en",
    )


@pytest.mark.anyio
async def test_get_current_user_valid_token():
    """get_current_user with valid token returns the User."""
    # Create a token for a user
    token = create_access_token({"sub": "seller@example.com"})
    
    # Mock the database to return a user
    fake_db = FakeDB()
    user = make_user(user_id=1, email="seller@example.com", role=UserRole.SELLER)
    fake_db.add_result("User", user)
    
    # Call the dependency
    result = await get_current_user(token=token, db=fake_db)
    
    assert result is not None
    assert result.email == "seller@example.com"
    assert result.role == UserRole.SELLER


@pytest.mark.anyio
async def test_get_current_user_user_not_found():
    """get_current_user raises 401 if user doesn't exist in database."""
    token = create_access_token({"sub": "nonexistent@example.com"})
    
    # Mock database returning None (user not found)
    fake_db = FakeDB()
    fake_db.add_result("User", None)
    
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token=token, db=fake_db)
    
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "User not found" in exc_info.value.detail


@pytest.mark.anyio
async def test_get_current_user_invalid_token():
    """get_current_user with invalid token raises 401."""
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token="invalid.token.here", db=FakeDB())
    
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_get_current_user_expired_token():
    """get_current_user with expired token raises 401."""
    from datetime import timedelta
    # Create an expired token
    token = create_access_token({"sub": "user@example.com"}, expires_delta=timedelta(seconds=-10))
    
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token=token, db=FakeDB())
    
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_get_optional_current_user_with_valid_token():
    """get_optional_current_user with valid token returns the User."""
    token = create_access_token({"sub": "buyer@example.com"})
    
    fake_db = FakeDB()
    user = make_user(user_id=2, email="buyer@example.com", role=UserRole.BUYER)
    fake_db.add_result("User", user)
    
    result = await get_optional_current_user(token=token, db=fake_db)
    
    assert result is not None
    assert result.email == "buyer@example.com"


@pytest.mark.anyio
async def test_get_optional_current_user_without_token():
    """get_optional_current_user without token returns None (doesn't fail)."""
    result = await get_optional_current_user(token=None, db=FakeDB())
    assert result is None


@pytest.mark.anyio
async def test_get_optional_current_user_user_not_found():
    """get_optional_current_user returns None if user doesn't exist."""
    token = create_access_token({"sub": "missing@example.com"})
    
    fake_db = FakeDB()
    fake_db.add_result("User", None)
    
    result = await get_optional_current_user(token=token, db=fake_db)
    assert result is None


@pytest.mark.anyio
async def test_get_optional_current_user_invalid_token():
    """get_optional_current_user returns None for invalid token."""
    result = await get_optional_current_user(token="bad.token", db=FakeDB())
    assert result is None


@pytest.mark.anyio
async def test_get_current_admin_admin_user():
    """get_current_admin allows admin users."""
    admin_user = make_user(user_id=3, email="admin@example.com", role=UserRole.ADMIN)
    
    result = await get_current_admin(current_user=admin_user)
    
    assert result.id == 3
    assert result.role == UserRole.ADMIN


@pytest.mark.anyio
async def test_get_current_admin_non_admin_user():
    """get_current_admin raises 403 for non-admin users."""
    seller_user = make_user(user_id=4, email="seller@example.com", role=UserRole.SELLER)
    
    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(current_user=seller_user)
    
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "doesn't have enough privileges" in exc_info.value.detail


@pytest.mark.anyio
async def test_get_current_admin_buyer_user():
    """get_current_admin raises 403 for buyer users."""
    buyer_user = make_user(user_id=5, email="buyer@example.com", role=UserRole.BUYER)
    
    with pytest.raises(HTTPException) as exc_info:
        await get_current_admin(current_user=buyer_user)
    
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
