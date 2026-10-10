"""A small on-disk cache for decoded API responses."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path(".cache") / "quakewatch"
DEFAULT_TTL_SECONDS = 300.0


class ResponseCache:
    """Stores one JSON document per request key, valid for ``ttl_seconds``.

    The cache is best-effort: an unreadable, corrupt or unwritable entry is
    treated as a miss rather than an error, so a read-only working directory
    never stops the CLI from working.
    """

    def __init__(
        self,
        directory: Path | str = DEFAULT_CACHE_DIR,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")
        self.directory = Path(directory)
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock

    def path_for(self, key: str) -> Path:
        """The file that holds (or would hold) the entry for ``key``."""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        """Return the cached payload for ``key``, or ``None`` if absent or expired."""
        try:
            with self.path_for(key).open(encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or entry.get("key") != key:
            return None
        stored_at = entry.get("stored_at")
        if not isinstance(stored_at, (int, float)):
            return None
        age = self._clock() - stored_at
        if age < 0 or age >= self.ttl_seconds:
            return None
        return entry.get("payload")

    def put(self, key: str, payload: Any) -> bool:
        """Store ``payload`` under ``key``. Returns ``False`` if it could not be written."""
        entry = {"key": key, "stored_at": self._clock(), "payload": payload}
        document = json.dumps(entry, separators=(",", ":"))
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self.directory, prefix=".tmp-", suffix=".part")
        except OSError:
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(document)
            os.replace(tmp_name, self.path_for(key))
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            return False
        return True

    def clear(self) -> int:
        """Delete every cache entry; returns how many files were removed."""
        removed = 0
        if not self.directory.is_dir():
            return removed
        for path in self.directory.glob("*.json"):
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
        return removed
