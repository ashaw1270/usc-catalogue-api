"""In-memory cache for scraped program data with TTL and force-refresh."""
import time
from typing import Any

from app.config import settings
from app.models import Program


class ProgramCache:
    """Simple TTL cache keyed by (catoid, poid)."""

    def __init__(self, ttl_seconds: int | None = None):
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.cache_ttl_seconds
        self._store: dict[tuple[int, int], tuple[Program, float]] = {}

    def get(self, catoid: int, poid: int, force_refresh: bool = False) -> Program | None:
        """Return cached Program if present and not expired. None otherwise."""
        if force_refresh:
            return None
        key = (catoid, poid)
        if key not in self._store:
            return None
        program, fetched_at = self._store[key]
        if time.monotonic() - fetched_at > self._ttl:
            del self._store[key]
            return None
        return program

    def set(self, catoid: int, poid: int, program: Program) -> None:
        """Store program in cache."""
        self._store[(catoid, poid)] = (program, time.monotonic())


# Module-level singleton
_cache: ProgramCache | None = None


def get_cache() -> ProgramCache:
    global _cache
    if _cache is None:
        _cache = ProgramCache()
    return _cache
