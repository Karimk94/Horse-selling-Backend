import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import delete, select, text

import app.main as main_module
from app.config import TEST_DATABASE_URL
from app.database import Base
from app.main import mark_offer_horse_sold, reopen_horse_listing
from app.models import Horse, HorseGender, Offer, OfferStatus, User, UserRole


@pytest_asyncio.fixture
async def db_session():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not configured for DB integration tests")

    engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            yield session
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Dedicated test database unavailable for integration tests: {exc}")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_db_mark_offer_horse_sold_persists_updates(monkeypatch, db_session):
    seller = User(
        id=uuid.uuid4(),
        email=f"seller-{uuid.uuid4()}@example.com",
        password_hash="x",
        role=UserRole.SELLER,
        is_verified=True,
        language="en",
    )
    buyer_accepted = User(
        id=uuid.uuid4(),
        email=f"buyer-a-{uuid.uuid4()}@example.com",
        password_hash="x",
        role=UserRole.BUYER,
        is_verified=True,
        language="en",
    )
    buyer_other = User(
        id=uuid.uuid4(),
        email=f"buyer-b-{uuid.uuid4()}@example.com",
        password_hash="x",
        role=UserRole.BUYER,
        is_verified=True,
        language="en",
    )

    horse = Horse(
        id=uuid.uuid4(),
        owner_id=seller.id,
        title="Integration Horse",
        price=12000,
        breed="Arabian",
        age=8,
        gender=HorseGender.MARE,
        discipline="Dressage",
        description="Well trained horse for integration tests.",
        status="approved",
        vet_check_available=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    accepted_offer = Offer(
        id=uuid.uuid4(),
        buyer_id=buyer_accepted.id,
        seller_id=seller.id,
        horse_id=horse.id,
        amount=11500,
        status=OfferStatus.ACCEPTED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    pending_offer = Offer(
        id=uuid.uuid4(),
        buyer_id=buyer_other.id,
        seller_id=seller.id,
        horse_id=horse.id,
        amount=11000,
        status=OfferStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async def fake_notify_offer_event(**_kwargs):
        return None

    monkeypatch.setattr(main_module, "notify_offer_event", fake_notify_offer_event)

    db_session.add(seller)
    db_session.add(buyer_accepted)
    db_session.add(buyer_other)
    db_session.add(horse)
    db_session.add(accepted_offer)
    db_session.add(pending_offer)
    await db_session.commit()

    try:
        result = await mark_offer_horse_sold(
            accepted_offer.id,
            db=db_session,
            current_user=seller,
        )

        assert result["message"] == "Horse marked as sold"
        assert result["closed_offers"] == 1

        horse_db = (
            await db_session.execute(select(Horse).where(Horse.id == horse.id))
        ).scalar_one()
        pending_offer_db = (
            await db_session.execute(select(Offer).where(Offer.id == pending_offer.id))
        ).scalar_one()

        assert horse_db.status == "sold"
        assert pending_offer_db.status == OfferStatus.CANCELLED
        assert pending_offer_db.response_message == "Listing sold to another buyer"
    finally:
        await db_session.execute(delete(Offer).where(Offer.id.in_([accepted_offer.id, pending_offer.id])))
        await db_session.execute(delete(Horse).where(Horse.id == horse.id))
        await db_session.execute(
            delete(User).where(User.id.in_([seller.id, buyer_accepted.id, buyer_other.id]))
        )
        await db_session.commit()


@pytest.mark.asyncio
async def test_db_reopen_horse_listing_persists_status_change(db_session):
    seller = User(
        id=uuid.uuid4(),
        email=f"seller-{uuid.uuid4()}@example.com",
        password_hash="x",
        role=UserRole.SELLER,
        is_verified=True,
        language="en",
    )
    horse = Horse(
        id=uuid.uuid4(),
        owner_id=seller.id,
        title="Sold Horse",
        price=9000,
        breed="Andalusian",
        age=9,
        gender=HorseGender.STALLION,
        discipline="Show Jumping",
        description="A sold listing to reopen in integration testing.",
        status="sold",
        vet_check_available=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db_session.add(seller)
    db_session.add(horse)
    await db_session.commit()

    try:
        reopened = await reopen_horse_listing(
            horse.id,
            db=db_session,
            current_user=seller,
        )
        assert reopened.status == "approved"

        horse_db = (
            await db_session.execute(select(Horse).where(Horse.id == horse.id))
        ).scalar_one()
        assert horse_db.status == "approved"
    finally:
        await db_session.execute(delete(Horse).where(Horse.id == horse.id))
        await db_session.execute(delete(User).where(User.id == seller.id))
        await db_session.commit()
