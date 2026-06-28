import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Tuple


def default_cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "x-info-generators"


class FetchCache:
    """On-disk cache for fetched source data and optimized images.

    One JSON file per entry at ``<root>/<namespace>/<sha1(key)>.json`` holding
    ``{"fetched_at": <epoch>, "data": <value>}``. Only truthy values are stored
    (successes only) — failed lookups are retried on every run.
    """

    def __init__(self, root: Path, ttl_days: int, enabled: bool = True,
                 refresh: bool = False, offline: bool = False):
        self.root = root
        self.ttl_seconds = ttl_days * 86400
        self.enabled = enabled
        # refresh=True: always re-fetch (read miss) but still write results back,
        # so stale entries get updated (unlike disabling the cache entirely).
        self.refresh = refresh
        # offline=True: serve only from cache (callers never hit the network).
        self.offline = offline

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return self.root / namespace / f"{digest}.json"

    def get(self, namespace: str, key: str) -> Tuple[bool, Any]:
        """Return (hit, value). Any error counts as a miss.

        Entries never expire on read — cleanup is explicit via ``purge_cache``.
        """
        if not self.enabled or self.refresh:
            return False, None
        path = self._path(namespace, key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return True, payload["data"]
        except Exception:
            return False, None

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Store value unless caching is disabled or the value is falsy."""
        if not self.enabled or not value:
            return
        path = self._path(namespace, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"fetched_at": time.time(), "data": value}
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def purge_cache(root: Path, max_age_days: int) -> Tuple[int, int]:
    """Delete cache entries older than ``max_age_days`` (0 = everything).

    Returns ``(entries_removed, bytes_freed)``. Unreadable files are skipped.
    """
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    freed = 0
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("fetched_at", 0) < cutoff:
                size = path.stat().st_size
                path.unlink()
                removed += 1
                freed += size
        except Exception:
            continue
    return removed, freed
