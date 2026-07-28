"""Saved search matching logic."""

from app.models import Horse, SavedSearch


def matches_saved_search(horse: Horse, search: SavedSearch) -> bool:
    """Check whether a horse matches the criteria of a saved search."""
    if search.breed and search.breed.lower() not in (horse.breed or "").lower():
        return False
    if search.discipline and search.discipline.lower() not in (horse.discipline or "").lower():
        return False
    if search.gender and search.gender != horse.gender.value:
        return False
    if search.min_price is not None and horse.price < search.min_price:
        return False
    if search.max_price is not None and horse.price > search.max_price:
        return False
    if search.min_age is not None and horse.age < search.min_age:
        return False
    if search.max_age is not None and horse.age > search.max_age:
        return False
    if search.vet_check_available is not None and horse.vet_check_available != search.vet_check_available:
        return False
    if search.verified_seller is not None and horse.owner and horse.owner.is_verified != search.verified_seller:
        return False
    return True