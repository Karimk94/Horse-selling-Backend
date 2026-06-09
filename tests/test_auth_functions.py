"""
Tests for auth functions: password hashing, token generation, and verification.
"""
import pytest
from datetime import timedelta, datetime, timezone
from fastapi import HTTPException, status
from jose import jwt

from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_verification_token,
    verify_token,
)
from app.config import SECRET_KEY, ALGORITHM


class TestPasswordHashing:
    """Test password hashing and verification."""

    def test_hash_password_returns_hash(self):
        """Hashing a password returns a non-empty hash."""
        pwd = "MySecurePassword123!"
        hashed = hash_password(pwd)
        assert hashed
        assert hashed != pwd
        assert len(hashed) > 20

    def test_verify_password_accepts_correct_password(self):
        """Verifying correct password against hash returns True."""
        pwd = "TestPassword456"
        hashed = hash_password(pwd)
        assert verify_password(pwd, hashed) is True

    def test_verify_password_rejects_incorrect_password(self):
        """Verifying wrong password against hash returns False."""
        pwd = "TestPassword456"
        hashed = hash_password(pwd)
        assert verify_password("WrongPassword", hashed) is False

    def test_hash_password_is_deterministic(self):
        """Same password produces different hashes (bcrypt adds salt)."""
        pwd = "SamePassword"
        hash1 = hash_password(pwd)
        hash2 = hash_password(pwd)
        assert hash1 != hash2


class TestAccessTokenCreation:
    """Test JWT access token generation."""

    def test_create_access_token_default_expiry(self):
        """Creating token without expires_delta uses default 30 minutes."""
        data = {"sub": "user@example.com", "user_id": 123}
        token = create_access_token(data)
        assert token
        
        # Decode to verify payload
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "user@example.com"
        assert payload["user_id"] == 123
        assert "exp" in payload

    def test_create_access_token_custom_expiry(self):
        """Creating token with custom expires_delta uses that duration."""
        data = {"sub": "admin@example.com"}
        expires_delta = timedelta(hours=2)
        token = create_access_token(data, expires_delta=expires_delta)
        
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == "admin@example.com"
        
        # Verify expiry is reasonable (within ~120 seconds of now + 2 hours)
        now = datetime.now(timezone.utc).timestamp()
        assert payload["exp"] > now + 7000  # At least ~2 hours from now

    def test_create_access_token_preserves_custom_fields(self):
        """Token preserves any additional fields passed in data dict."""
        data = {"sub": "user@example.com", "role": "seller", "horse_count": 5}
        token = create_access_token(data)
        
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["role"] == "seller"
        assert payload["horse_count"] == 5


class TestVerificationTokenCreation:
    """Test JWT verification token generation."""

    def test_create_verification_token_default_expiry_24h(self):
        """Verification token defaults to 24-hour expiry."""
        email = "user@example.com"
        token = create_verification_token(email)
        
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == email
        assert payload["type"] == "verification"
        
        # Verify expiry is ~24 hours
        now = datetime.now(timezone.utc).timestamp()
        assert payload["exp"] > now + 86000  # At least ~24 hours

    def test_create_verification_token_custom_expiry(self):
        """Verification token can use custom expiry."""
        email = "new@example.com"
        expires_delta = timedelta(hours=1)
        token = create_verification_token(email, expires_delta=expires_delta)
        
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["sub"] == email
        assert payload["type"] == "verification"
        
        # Verify expiry is ~1 hour
        now = datetime.now(timezone.utc).timestamp()
        assert payload["exp"] > now + 3500  # At least ~1 hour


class TestTokenVerification:
    """Test token verification logic."""

    def test_verify_token_valid_access_token(self):
        """Verifying valid access token decodes successfully."""
        data = {"sub": "user@example.com", "user_id": 42}
        token = create_access_token(data, expires_delta=timedelta(minutes=30))
        
        payload = verify_token(token)
        assert payload["sub"] == "user@example.com"
        assert payload["user_id"] == 42

    def test_verify_token_invalid_signature_raises_401(self):
        """Verifying token with tampered signature raises 401."""
        data = {"sub": "user@example.com"}
        token = create_access_token(data)

        # Replace the signature segment with different base64url content.
        # This guarantees signature mismatch while keeping a JWT-like shape.
        header, payload, signature = token.split(".")
        replacement_char = "A" if not signature.startswith("A") else "B"
        tampered_signature = replacement_char * len(signature)
        tampered_token = f"{header}.{payload}.{tampered_signature}"
        
        with pytest.raises(HTTPException) as exc_info:
            verify_token(tampered_token)
        
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Could not validate credentials" in exc_info.value.detail

    def test_verify_token_missing_sub_raises_401(self):
        """Token without 'sub' claim raises 401."""
        # Manually create token without 'sub'
        to_encode = {"user_id": 42}
        token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)
        
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_token_expired_token_raises_401(self):
        """Verifying expired token raises 401."""
        data = {"sub": "user@example.com"}
        # Create token that expires in the past
        expires_delta = timedelta(seconds=-10)
        token = create_access_token(data, expires_delta=expires_delta)
        
        with pytest.raises(HTTPException) as exc_info:
            verify_token(token)
        
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_token_malformed_raises_401(self):
        """Verifying malformed token raises 401."""
        with pytest.raises(HTTPException) as exc_info:
            verify_token("not.a.valid.token.at.all")
        
        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_verify_token_includes_www_authenticate_header(self):
        """401 responses include WWW-Authenticate header."""
        with pytest.raises(HTTPException) as exc_info:
            verify_token("invalid")
        
        assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}
