"""Background scheduled tasks for the Horse selling platform."""

import logging
from importlib import import_module
from datetime import datetime, timezone, timedelta
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL, SOFT_DELETE_RESTORE_DAYS
from app.models import Horse

logger = logging.getLogger(__name__)
scheduler: Any | None = None


async def purge_expired_deleted_listings() -> None:
    """Hard-delete listings that have been soft-deleted for longer than SOFT_DELETE_RESTORE_DAYS."""
    try:
        if SOFT_DELETE_RESTORE_DAYS <= 0:
            logger.info("Skipping scheduled purge because restore window is unlimited")
            return

        # Create engine and session for this task
        engine = create_async_engine(DATABASE_URL, echo=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        
        async with async_session() as session:
            # Calculate cutoff date
            cutoff_at = datetime.now(timezone.utc) - timedelta(days=SOFT_DELETE_RESTORE_DAYS)
            
            # Find expired soft-deleted listings
            result = await session.execute(
                select(Horse).where(
                    (Horse.deleted_at.isnot(None)) & (Horse.deleted_at <= cutoff_at)
                )
            )
            expired_listings = result.scalars().all()
            purged_count = len(expired_listings)
            
            if purged_count > 0:
                # Delete all expired listings
                for listing in expired_listings:
                    await session.delete(listing)
                
                await session.commit()
                logger.info(
                    f"Purged {purged_count} expired soft-deleted listings "
                    f"(deleted before {cutoff_at.isoformat()})"
                )
            else:
                logger.debug("No expired listings to purge")
        
        await engine.dispose()
    except Exception as e:
        logger.error(f"Error during scheduled purge of expired listings: {str(e)}", exc_info=True)


def start_scheduler() -> None:
    """Start the background task scheduler."""
    global scheduler
    
    if scheduler is None:
        scheduler_cls = getattr(import_module("apscheduler.schedulers.asyncio"), "AsyncIOScheduler")
        scheduler = scheduler_cls(timezone="UTC")
        
        # Schedule purge to run daily at 2 AM UTC
        scheduler.add_job(
            purge_expired_deleted_listings,
            trigger="cron",
            hour=2,
            minute=0,
            id="purge_expired_listings",
            name="Purge expired soft-deleted listings",
            replace_existing=True,
            misfire_grace_time=60,
        )
        
        scheduler.start()
        logger.info("Background task scheduler started. Purge job scheduled for 2:00 AM UTC daily")


def stop_scheduler() -> None:
    """Stop the background task scheduler."""
    global scheduler
    
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=True)
        scheduler = None
        logger.info("Background task scheduler stopped")
