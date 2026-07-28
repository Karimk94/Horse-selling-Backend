"""Router package - aggregates all API routers."""

from .auth import router as auth_router
from .profile import router as profile_router
from .horses import router as horses_router
from .favorites import router as favorites_router
from .vouchers import router as vouchers_router
from .saved_searches import router as saved_searches_router
from .notifications import router as notifications_router
from .admin import router as admin_router
from .offers import router as offers_router

__all__ = [
    "auth_router",
    "profile_router",
    "horses_router",
    "favorites_router",
    "vouchers_router",
    "saved_searches_router",
    "notifications_router",
    "admin_router",
    "offers_router",
]
