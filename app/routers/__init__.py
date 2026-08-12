"""Router package - aggregates all API routers."""

from .auth import router as auth_router
from .profile import router as profile_router
from .horses import router as horses_router
from .favorites import router as favorites_router
from .vouchers import router as vouchers_router
from .saved_searches import router as saved_searches_router
from .notifications import router as notifications_router
from .categories import router as categories_router
from .equipment import router as equipment_router
from .rider_gear import router as rider_gear_router
from .services import router as services_router

__all__ = [
    "auth_router",
    "profile_router",
    "horses_router",
    "favorites_router",
    "vouchers_router",
    "saved_searches_router",
    "notifications_router",
    "categories_router",
    "equipment_router",
    "rider_gear_router",
    "services_router",
]
