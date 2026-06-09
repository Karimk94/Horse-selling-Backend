"""
Tests for main.py helper functions and additional endpoint coverage.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.main import (
    finalize_idempotent_replay,
    lifespan,
    matches_saved_search,
    sanitize_idempotency_key,
    get_idempotent_replay,
    store_idempotent_replay,
    warn_if_weak_purge_confirm_token,
)
from app.models import Horse, HorseGender, SavedSearch, User, UserRole


class TestMatchesSavedSearch:
    """Test the horse-to-saved-search matching logic."""

    def make_horse(self, **kwargs):
        """Factory for Horse objects."""
        defaults = {
            "id": uuid.uuid4(),
            "owner_id": uuid.uuid4(),
            "title": "Test Horse",
            "price": 10000,
            "breed": "Arabian",
            "age": 5,
            "gender": HorseGender.MARE,
            "discipline": "Dressage",
            "height": 15.2,
            "description": None,
            "status": "approved",
            "vet_check_available": True,
            "vet_certificate_url": None,
            "image_url": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        defaults.update(kwargs)
        return Horse(**defaults)

    def make_search(self, **kwargs):
        """Factory for SavedSearch objects."""
        defaults = {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "breed": None,
            "discipline": None,
            "gender": None,
            "min_price": None,
            "max_price": None,
            "min_age": None,
            "max_age": None,
            "vet_check_available": None,
            "verified_seller": None,
        }
        defaults.update(kwargs)
        return SavedSearch(**defaults)

    def test_matches_all_criteria_returns_true(self):
        """Horse matching all search criteria returns True."""
        horse = self.make_horse(
            breed="Arabian",
            discipline="Dressage",
            gender=HorseGender.MARE,
            price=10000,
            age=5,
            vet_check_available=True,
        )
        search = self.make_search(
            breed="Arabian",
            discipline="Dressage",
            gender=HorseGender.MARE,
            min_price=5000,
            max_price=15000,
            min_age=3,
            max_age=7,
            vet_check_available=True,
        )
        
        assert matches_saved_search(horse, search) is True

    def test_breed_mismatch_returns_false(self):
        """Horse with different breed returns False."""
        horse = self.make_horse(breed="Thoroughbred")
        search = self.make_search(breed="Arabian")
        
        assert matches_saved_search(horse, search) is False

    def test_breed_substring_match_returns_true(self):
        """Breed matching is case-insensitive substring."""
        horse = self.make_horse(breed="Arabian")
        search = self.make_search(breed="arabian")  # lowercase
        
        assert matches_saved_search(horse, search) is True

    def test_discipline_mismatch_returns_false(self):
        """Horse with different discipline returns False."""
        horse = self.make_horse(discipline="Dressage")
        search = self.make_search(discipline="Jumping")
        
        assert matches_saved_search(horse, search) is False

    def test_gender_mismatch_returns_false(self):
        """Horse with different gender returns False."""
        horse = self.make_horse(gender=HorseGender.MARE)
        search = self.make_search(gender=HorseGender.STALLION)
        
        assert matches_saved_search(horse, search) is False

    def test_price_below_minimum_returns_false(self):
        """Horse price below min_price returns False."""
        horse = self.make_horse(price=5000)
        search = self.make_search(min_price=8000)
        
        assert matches_saved_search(horse, search) is False

    def test_price_above_maximum_returns_false(self):
        """Horse price above max_price returns False."""
        horse = self.make_horse(price=15000)
        search = self.make_search(max_price=12000)
        
        assert matches_saved_search(horse, search) is False

    def test_age_below_minimum_returns_false(self):
        """Horse age below min_age returns False."""
        horse = self.make_horse(age=2)
        search = self.make_search(min_age=3)
        
        assert matches_saved_search(horse, search) is False

    def test_age_above_maximum_returns_false(self):
        """Horse age above max_age returns False."""
        horse = self.make_horse(age=10)
        search = self.make_search(max_age=8)
        
        assert matches_saved_search(horse, search) is False

    def test_vet_check_requirement_mismatch_returns_false(self):
        """Horse without vet check doesn't match requirement."""
        horse = self.make_horse(vet_check_available=False)
        search = self.make_search(vet_check_available=True)
        
        assert matches_saved_search(horse, search) is False

    def test_no_search_filters_matches_any_horse(self):
        """SavedSearch with no filters matches any horse."""
        horse = self.make_horse(breed="Quarterhorse")
        search = self.make_search()  # No filters set
        
        assert matches_saved_search(horse, search) is True

    def test_null_horse_breed_with_breed_filter_returns_false(self):
        """Horse with no breed (None) doesn't match breed filter."""
        horse = self.make_horse(breed=None)
        search = self.make_search(breed="Arabian")
        
        assert matches_saved_search(horse, search) is False

    def test_null_horse_discipline_matches(self):
        """Horse with no discipline matches (not filtered)."""
        horse = self.make_horse(discipline=None)
        search = self.make_search(discipline=None)  # No discipline requirement
        
        assert matches_saved_search(horse, search) is True

    def test_verified_seller_mismatch_returns_false(self):
        """Horse owner verification mismatch returns False."""
        horse = self.make_horse()
        horse.owner = User(
            id=uuid.uuid4(),
            email="owner@example.com",
            password_hash="x",
            role=UserRole.SELLER,
            is_verified=False,
            language="en",
        )
        search = self.make_search(verified_seller=True)

        assert matches_saved_search(horse, search) is False


class TestSanitizeIdempotencyKey:
    """Test idempotency key sanitization."""

    def test_sanitize_none_returns_none(self):
        """Sanitizing None returns None."""
        assert sanitize_idempotency_key(None) is None

    def test_sanitize_empty_string_returns_none(self):
        """Sanitizing empty string returns None."""
        assert sanitize_idempotency_key("") is None

    def test_sanitize_whitespace_only_returns_none(self):
        """Sanitizing whitespace-only string returns None."""
        assert sanitize_idempotency_key("   ") is None

    def test_sanitize_strips_leading_trailing_whitespace(self):
        """Key is stripped of leading/trailing whitespace."""
        result = sanitize_idempotency_key("  my-key-123  ")
        assert result == "my-key-123"

    def test_sanitize_preserves_internal_whitespace(self):
        """Internal whitespace in key is preserved."""
        result = sanitize_idempotency_key("  my key 123  ")
        assert result == "my key 123"

    def test_sanitize_preserves_valid_key(self):
        """Valid key is returned as-is after strip."""
        key = "unique-request-key-uuid"
        assert sanitize_idempotency_key(key) == key


class TestIdempotencyKeyMocking:
    """Test get/store idempotent replay with mocked database."""

    def make_fake_db(self):
        """Factory for FakeDB instances."""
        class FakeResult:
            def __init__(self, scalar_value=None):
                self._scalar_value = scalar_value

            def scalar_one_or_none(self):
                return self._scalar_value

        class FakeDB:
            def __init__(self):
                self._result = None
                self.added = []
                self.commit_calls = 0

            def add_result(self, value):
                self._result = FakeResult(scalar_value=value)

            async def execute(self, stmt):
                return self._result

            def add(self, obj):
                self.added.append(obj)

            async def commit(self):
                self.commit_calls += 1

        return FakeDB()


    @pytest.mark.anyio
    async def test_get_idempotent_replay_none_key_returns_none(self):
        """get_idempotent_replay with None key returns None."""
        db = self.make_fake_db()
        user_id = uuid.uuid4()
        
        result = await get_idempotent_replay(
            db=db,
            user_id=user_id,
            action="test_action",
            idempotency_key=None,
        )
        
        assert result is None

    @pytest.mark.anyio
    async def test_get_idempotent_replay_empty_key_returns_none(self):
        """get_idempotent_replay with empty key returns None."""
        db = self.make_fake_db()
        user_id = uuid.uuid4()
        
        result = await get_idempotent_replay(
            db=db,
            user_id=user_id,
            action="test_action",
            idempotency_key="   ",
        )
        
        assert result is None

    @pytest.mark.anyio
    async def test_get_idempotent_replay_no_record_returns_none(self):
        """get_idempotent_replay returns None when no record found."""
        db = self.make_fake_db()
        db.add_result(None)  # No record in database
        user_id = uuid.uuid4()
        
        result = await get_idempotent_replay(
            db=db,
            user_id=user_id,
            action="test_action",
            idempotency_key="valid-key",
        )
        
        assert result is None

    @pytest.mark.anyio
    async def test_get_idempotent_replay_returns_parsed_json(self):
        """get_idempotent_replay parses and returns cached response."""
        # Create a mock IdempotencyKey record
        class FakeDatabaseRecord:
            response_body = '{"status": "success", "offer_id": 123}'

        db = self.make_fake_db()
        db.add_result(FakeDatabaseRecord())
        user_id = uuid.uuid4()
        
        result = await get_idempotent_replay(
            db=db,
            user_id=user_id,
            action="create_offer",
            idempotency_key="test-key",
        )
        
        assert result == {"status": "success", "offer_id": 123}

    @pytest.mark.anyio
    async def test_get_idempotent_replay_invalid_json_returns_none(self):
        """get_idempotent_replay returns None for invalid JSON."""
        class FakeDatabaseRecord:
            response_body = "not valid json {{"

        db = self.make_fake_db()
        db.add_result(FakeDatabaseRecord())
        user_id = uuid.uuid4()
        
        result = await get_idempotent_replay(
            db=db,
            user_id=user_id,
            action="create_offer",
            idempotency_key="test-key",
        )
        
        assert result is None

    @pytest.mark.anyio
    async def test_get_idempotent_replay_non_dict_json_returns_none(self):
        """get_idempotent_replay returns None if JSON is not a dict."""
        class FakeDatabaseRecord:
            response_body = '["array", "of", "values"]'

        db = self.make_fake_db()
        db.add_result(FakeDatabaseRecord())
        user_id = uuid.uuid4()
        
        result = await get_idempotent_replay(
            db=db,
            user_id=user_id,
            action="create_offer",
            idempotency_key="test-key",
        )
        
        assert result is None

    @pytest.mark.anyio
    async def test_store_idempotent_replay_none_key_does_nothing(self):
        """store_idempotent_replay with None key doesn't add record."""
        db = self.make_fake_db()
        user_id = uuid.uuid4()
        
        await store_idempotent_replay(
            db=db,
            user_id=user_id,
            action="test_action",
            idempotency_key=None,
            response_payload={"result": "success"},
        )
        
        assert len(db.added) == 0

    @pytest.mark.anyio
    async def test_store_idempotent_replay_empty_key_does_nothing(self):
        """store_idempotent_replay with empty key doesn't add record."""
        db = self.make_fake_db()
        user_id = uuid.uuid4()
        
        await store_idempotent_replay(
            db=db,
            user_id=user_id,
            action="test_action",
            idempotency_key="   ",
            response_payload={"result": "success"},
        )
        
        assert len(db.added) == 0

    @pytest.mark.anyio
    async def test_store_idempotent_replay_existing_key_does_nothing(self):
        """store_idempotent_replay skips if key already exists."""
        # Create a mock existing record
        class FakeDatabaseRecord:
            pass

        db = self.make_fake_db()
        db.add_result(FakeDatabaseRecord())  # Existing record found
        user_id = uuid.uuid4()
        
        await store_idempotent_replay(
            db=db,
            user_id=user_id,
            action="test_action",
            idempotency_key="existing-key",
            response_payload={"result": "success"},
        )
        
        assert len(db.added) == 0  # Should not add duplicate

    @pytest.mark.anyio
    async def test_store_idempotent_replay_adds_new_record(self):
        """store_idempotent_replay adds new IdempotencyKey when key doesn't exist."""
        db = self.make_fake_db()
        db.add_result(None)  # No existing record
        user_id = uuid.uuid4()
        
        await store_idempotent_replay(
            db=db,
            user_id=user_id,
            action="create_offer",
            idempotency_key="new-key",
            response_payload={"offer_id": 42, "status": "pending"},
        )
        
        assert len(db.added) == 1
        record = db.added[0]
        assert record.user_id == user_id
        assert record.request_key == "new-key"
        assert record.action == "create_offer"
        # Verify JSON encoding of response
        parsed = json.loads(record.response_body)
        assert parsed == {"offer_id": 42, "status": "pending"}

    @pytest.mark.anyio
    async def test_finalize_idempotent_replay_commits_when_key_is_present(self):
        """finalize_idempotent_replay stores and commits for non-empty keys."""
        db = self.make_fake_db()
        db.add_result(None)
        user_id = uuid.uuid4()

        await finalize_idempotent_replay(
            db=db,
            user_id=user_id,
            action="create_offer",
            idempotency_key="  final-key  ",
            response_payload={"ok": True},
        )

        assert db.commit_calls == 1
        assert len(db.added) == 1


@pytest.mark.anyio
async def test_lifespan_creates_schema_when_enabled(monkeypatch):
    """lifespan executes create_all when AUTO_CREATE_SCHEMA is enabled."""
    import app.main as main_module

    calls = {"begin": 0, "run_sync": 0}

    class FakeConn:
        async def run_sync(self, _fn):
            calls["run_sync"] += 1

    class FakeBeginContext:
        async def __aenter__(self):
            calls["begin"] += 1
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def begin(self):
            return FakeBeginContext()

    monkeypatch.setattr(main_module, "AUTO_CREATE_SCHEMA", True)
    monkeypatch.setattr(main_module, "engine", FakeEngine())
    monkeypatch.setattr(main_module, "start_scheduler", lambda: None)
    monkeypatch.setattr(main_module, "stop_scheduler", lambda: None)

    async with lifespan(None):
        pass

    assert calls["begin"] == 1
    assert calls["run_sync"] == 1


def test_warn_if_weak_purge_confirm_token_logs_warning(monkeypatch):
    """Weak/default purge token triggers a startup warning."""
    import app.main as main_module

    warnings = []

    monkeypatch.setattr(main_module, "PURGE_CONFIRM_TOKEN", "PURGE")
    monkeypatch.setattr(main_module.logger, "warning", lambda message: warnings.append(message))

    warn_if_weak_purge_confirm_token()

    assert len(warnings) == 1
    assert "PURGE_CONFIRM_TOKEN" in warnings[0]


def test_warn_if_weak_purge_confirm_token_warning_message_is_stable(monkeypatch):
    """Warning text remains stable for log monitoring rules."""
    import app.main as main_module

    warnings = []

    monkeypatch.setattr(main_module, "PURGE_CONFIRM_TOKEN", "PURGE")
    monkeypatch.setattr(main_module.logger, "warning", lambda message: warnings.append(message))

    warn_if_weak_purge_confirm_token()

    assert warnings == [main_module.PURGE_TOKEN_WEAK_WARNING]


def test_warn_if_weak_purge_confirm_token_skips_warning_for_strong_token(monkeypatch):
    """Strong purge token does not trigger warning."""
    import app.main as main_module

    warnings = []

    monkeypatch.setattr(main_module, "PURGE_CONFIRM_TOKEN", "X9v2!kL0_s3cure")
    monkeypatch.setattr(main_module.logger, "warning", lambda message: warnings.append(message))

    warn_if_weak_purge_confirm_token()

    assert warnings == []
