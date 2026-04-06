"""Tests for model __repr__ methods and schema validator branches."""
import uuid
from datetime import datetime, timezone

import pytest

from app.models import (
    Favorite,
    Horse,
    HorseGender,
    HorseImage,
    IdempotencyKey,
    ListingReview,
    Offer,
    OfferStatus,
    OfferTransitionAudit,
    PushDeliveryLog,
    PushToken,
    SavedSearch,
    SavedSearchAlert,
    User,
    UserProfile,
    UserRole,
    Voucher,
    DiscountType,
)
from app.schemas import HorseCreateRequest, HorseUpdateRequest, HorseOwnerResponse


# ---------------------------------------------------------------------------
# Model __repr__ coverage
# ---------------------------------------------------------------------------

def _uid():
    return uuid.uuid4()


def test_user_repr():
    user = User(id=_uid(), email="x@y.com", password_hash="x", role=UserRole.BUYER, is_verified=True, language="en")
    assert "x@y.com" in repr(user)


def test_user_profile_repr():
    uid = _uid()
    profile = UserProfile(user_id=uid, phone_number="1234")
    assert str(uid) in repr(profile)


def test_horse_repr():
    horse = Horse(
        id=_uid(), owner_id=_uid(), title="Speedy", price=10000, breed="Arabian",
        age=5, gender=HorseGender.MARE, status="approved", vet_check_available=False,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    assert "Speedy" in repr(horse)
    assert "Arabian" in repr(horse)


def test_horse_image_repr():
    hid = _uid()
    img = HorseImage(id=_uid(), horse_id=hid, image_url="https://x.com/img.jpg", display_order=0,
                     created_at=datetime.now(timezone.utc))
    assert str(hid) in repr(img)


def test_voucher_repr():
    v = Voucher(
        id=_uid(), code="SAVE10", discount_type=DiscountType.PERCENTAGE,
        discount_value=10, valid_from=datetime.now(timezone.utc),
        valid_until=datetime.now(timezone.utc), usage_limit=100, used_count=0,
        is_active=True, created_at=datetime.now(timezone.utc),
    )
    assert "SAVE10" in repr(v)


def test_favorite_repr():
    uid = _uid()
    hid = _uid()
    fav = Favorite(id=_uid(), user_id=uid, horse_id=hid, created_at=datetime.now(timezone.utc))
    assert str(uid) in repr(fav)
    assert str(hid) in repr(fav)


def test_listing_review_repr():
    hid = _uid()
    lr = ListingReview(
        id=_uid(), horse_id=hid, admin_id=_uid(), action="approved",
        reason=None, created_at=datetime.now(timezone.utc),
    )
    assert str(hid) in repr(lr)
    assert "approved" in repr(lr)


def test_saved_search_repr():
    uid = _uid()
    ss = SavedSearch(
        id=_uid(), user_id=uid, name="MyAlert",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    assert "MyAlert" in repr(ss)
    assert str(uid) in repr(ss)


def test_saved_search_alert_repr():
    uid = _uid()
    hid = _uid()
    alert = SavedSearchAlert(
        id=_uid(), user_id=uid, saved_search_id=_uid(), horse_id=hid,
        title="Match", message="Found one", is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    assert str(uid) in repr(alert)
    assert str(hid) in repr(alert)


def test_push_token_repr():
    uid = _uid()
    pt = PushToken(
        id=_uid(), user_id=uid, token="ExponentPushToken[abc]", platform="ios",
        is_active=True, last_seen_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    assert str(uid) in repr(pt)
    assert "ios" in repr(pt)


def test_offer_repr():
    bid, sid, hid = _uid(), _uid(), _uid()
    offer = Offer(
        id=_uid(), buyer_id=bid, seller_id=sid, horse_id=hid,
        amount=9000, status=OfferStatus.PENDING,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    assert str(bid) in repr(offer)
    assert str(hid) in repr(offer)


def test_offer_transition_audit_repr():
    oid = _uid()
    audit = OfferTransitionAudit(
        id=_uid(), offer_id=oid, changed_by_user_id=_uid(),
        from_status="pending", to_status="accepted", actor="seller",
        created_at=datetime.now(timezone.utc),
    )
    assert str(oid) in repr(audit)
    assert "seller" in repr(audit)


def test_idempotency_key_repr():
    uid = _uid()
    ik = IdempotencyKey(
        id=_uid(), user_id=uid, request_key="k", action="create_offer",
        response_body="{}", created_at=datetime.now(timezone.utc),
    )
    assert str(uid) in repr(ik)
    assert "create_offer" in repr(ik)


def test_push_delivery_log_repr():
    uid = _uid()
    log = PushDeliveryLog(
        id=_uid(), target_user_id=uid, provider="expo", event_type="offer_new",
        total_tokens=2, accepted_count=2, failed_count=0, status="success",
        created_at=datetime.now(timezone.utc),
    )
    assert str(uid) in repr(log)
    assert "success" in repr(log)


# ---------------------------------------------------------------------------
# Schema validator branch coverage
# ---------------------------------------------------------------------------

def _valid_create_payload(**overrides):
    base = {
        "title": "Beautiful Arabian",
        "price": 15000,
        "breed": "Arabian",
        "age": 5,
        "gender": "mare",
        "description": "A well-trained horse suitable for competitions and trail riding.",
        "image_url": "https://example.com/horse.jpg",
        "vet_check_available": False,
    }
    base.update(overrides)
    return base


def test_horse_create_vet_cert_required_when_vet_check_true():
    with pytest.raises(Exception) as exc_info:
        HorseCreateRequest(**{**_valid_create_payload(), "vet_check_available": True, "vet_certificate_url": None})
    assert "vet_certificate_url" in str(exc_info.value)


def test_horse_create_short_description_raises():
    with pytest.raises(Exception) as exc_info:
        HorseCreateRequest(**{**_valid_create_payload(), "description": "Too short"})
    assert "30" in str(exc_info.value) or "Description" in str(exc_info.value)


def test_horse_create_numeral_string_in_price_is_normalized():
    payload = _valid_create_payload()
    payload["price"] = "15000"  # string that can be normalized
    obj = HorseCreateRequest(**payload)
    assert obj.price == 15000


def test_horse_update_vet_cert_required_when_vet_check_true():
    with pytest.raises(Exception) as exc_info:
        HorseUpdateRequest(vet_check_available=True, vet_certificate_url=None)
    assert "vet_certificate_url" in str(exc_info.value)


def test_horse_update_numeral_string_in_price_is_normalized():
    obj = HorseUpdateRequest(price="20000")
    assert obj.price == 20000


def test_horse_owner_response_plain_dict_passthrough():
    """extract_phone_from_profile returns raw dict when no profile attribute."""
    data = {
        "id": uuid.uuid4(),
        "email": "owner@example.com",
        "is_verified": True,
        "phone_number": "555-0100",
    }
    owner = HorseOwnerResponse.model_validate(data)
    assert owner.email == "owner@example.com"
    assert owner.phone_number == "555-0100"
