"""Local cache: data/prs.json (PR records) and data/calibration.json (calibrate log).

Writes are atomic (temp file + rename) and guarded by a process-wide lock so the
web server's background rescore and request handlers don't clobber each other.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR

_LOCK = threading.RLock()
DIFF_UNAVAILABLE = "[diff unavailable"


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def pr_key(repo: str, number: int) -> str:
    return f"{repo}#{number}"


class Store:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.prs_path = data_dir / "prs.json"
        self.cal_path = data_dir / "calibration.json"
        self.diff_dir = data_dir / "diffs"
        self.lock = _LOCK
        # Diffs are a local cache (not backed up). When set, a missing diff is
        # downloaded again on first use: fn(key) -> diff text.
        self.diff_fetcher: Callable[[str], str] | None = None

    # --- PRs -------------------------------------------------------------
    def load_prs(self) -> dict[str, dict]:
        with self.lock:
            return _read(self.prs_path, {})

    def save_prs(self, prs: dict[str, dict]) -> None:
        with self.lock:
            _write(self.prs_path, prs)

    def update_pr(self, key: str, fields: dict) -> dict:
        """Merge fields into one PR record and persist immediately (used for resumable runs)."""
        with self.lock:
            prs = self.load_prs()
            prs.setdefault(key, {}).update(fields)
            self.save_prs(prs)
            return prs[key]

    # --- Diffs (cached on disk, can be large) -----------------------------
    def diff_path(self, key: str) -> Path:
        safe = key.replace("/", "__").replace("#", "_")
        return self.diff_dir / f"{safe}.diff"

    def load_diff(self, key: str) -> str | None:
        p = self.diff_path(key)
        if p.exists():
            return p.read_text(errors="replace")
        if self.diff_fetcher is None:
            return None
        diff = self.diff_fetcher(key)
        if not diff.startswith(DIFF_UNAVAILABLE):  # don't cache a failed download
            self.save_diff(key, diff)
        return diff

    def save_diff(self, key: str, diff: str) -> None:
        p = self.diff_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(diff)

    # --- Calibration -----------------------------------------------------
    def load_calibration(self) -> dict:
        with self.lock:
            data = _read(self.cal_path, {})
            data.setdefault("reviews", [])
            data.setdefault("proposals", [])
            return data

    def save_calibration(self, data: dict) -> None:
        with self.lock:
            _write(self.cal_path, data)
