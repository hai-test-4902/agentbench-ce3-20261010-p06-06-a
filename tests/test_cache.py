from __future__ import annotations

import json
from pathlib import Path

import pytest

from quakewatch.cache import ResponseCache


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_round_trip(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c", ttl_seconds=60)
    assert cache.get("k") is None
    assert cache.put("k", {"features": [1, 2, 3]}) is True
    assert cache.get("k") == {"features": [1, 2, 3]}
    assert cache.get("other") is None


def test_entry_expires_after_ttl(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ResponseCache(tmp_path, ttl_seconds=60, clock=clock)
    cache.put("k", "payload")
    clock.now += 59.9
    assert cache.get("k") == "payload"
    clock.now += 0.2
    assert cache.get("k") is None


def test_zero_ttl_never_hits(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, ttl_seconds=0, clock=FakeClock())
    cache.put("k", "payload")
    assert cache.get("k") is None


def test_entry_from_the_future_is_ignored(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ResponseCache(tmp_path, ttl_seconds=60, clock=clock)
    cache.put("k", "payload")
    clock.now -= 3600  # e.g. the system clock was corrected backwards
    assert cache.get("k") is None


def test_negative_ttl_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        ResponseCache(tmp_path, ttl_seconds=-1)


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "[]",
        json.dumps({"key": "someone-else", "stored_at": 1_000_000.0, "payload": 1}),
        json.dumps({"key": "k", "stored_at": "yesterday", "payload": 1}),
        json.dumps({"key": "k", "payload": 1}),
    ],
)
def test_corrupt_or_foreign_entries_are_misses(tmp_path: Path, content: str) -> None:
    cache = ResponseCache(tmp_path, ttl_seconds=60, clock=FakeClock())
    cache.path_for("k").write_text(content, encoding="utf-8")
    assert cache.get("k") is None


def test_put_overwrites_and_leaves_no_temp_files(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, ttl_seconds=60)
    cache.put("k", 1)
    cache.put("k", 2)
    assert cache.get("k") == 2
    assert [path.name for path in tmp_path.iterdir()] == [cache.path_for("k").name]


def test_put_reports_failure_when_directory_cannot_be_created(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("i am not a directory", encoding="utf-8")
    cache = ResponseCache(blocker / "cache", ttl_seconds=60)
    assert cache.put("k", 1) is False
    assert cache.get("k") is None


def test_put_leaves_nothing_behind_when_payload_is_not_serializable(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, ttl_seconds=60)
    with pytest.raises(TypeError):
        cache.put("k", object())
    assert list(tmp_path.iterdir()) == []


def test_put_reports_failure_when_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    cache = ResponseCache(tmp_path, ttl_seconds=60)
    monkeypatch.setattr("quakewatch.cache.os.replace", boom)
    assert cache.put("k", 1) is False
    assert list(tmp_path.iterdir()) == []


def test_clear(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "c", ttl_seconds=60)
    assert cache.clear() == 0
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.clear() == 2
    assert cache.get("a") is None
