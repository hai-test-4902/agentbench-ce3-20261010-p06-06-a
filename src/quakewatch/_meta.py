"""Package metadata that other modules can import without a cycle."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

PROJECT_URL = "https://github.com/hai-test-4902/agentbench-ce3-20261010-p06-06-a"

try:
    __version__ = version("quakewatch")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.0.0"
