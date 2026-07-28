"""Purge confirmation token helpers and security posture checks."""

import logging

from app.config import PURGE_CONFIRM_TOKEN

logger = logging.getLogger(__name__)

PURGE_TOKEN_WEAK_WARNING = (
    "PURGE_CONFIRM_TOKEN appears weak or default. "
    "Set a longer, non-default token in environment for production."
)


def is_purge_confirm_token_strong() -> bool:
    """Returns True when purge confirm token appears non-default and sufficiently long."""
    token = (PURGE_CONFIRM_TOKEN or "").strip()
    weak_defaults = {"PURGE", "DELETE", "CONFIRM", "ADMIN"}
    return len(token) >= 8 and token.upper() not in weak_defaults


def warn_if_weak_purge_confirm_token() -> None:
    """Warn when the configured purge confirmation token is weak/default."""
    if not is_purge_confirm_token_strong():
        logger.warning(PURGE_TOKEN_WEAK_WARNING)