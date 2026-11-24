"""Back up data/ (scores and calibration) by committing and pushing it to its own git repo.

data/ is a separate git repository (see README, "Backup"). Diffs are ignored there:
they're a large cache that Store re-downloads when missing. When data/ isn't a git
repo with a remote, backup is a no-op, so tests and the demo never push anything.
"""
from __future__ import annotations

import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import DATA_DIR

_LOCK = threading.Lock()


def _git(data_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(data_dir), *args], capture_output=True, text=True, timeout=120)


def is_configured(data_dir: Path = DATA_DIR) -> bool:
    return (data_dir / ".git").exists() and bool(_git(data_dir, "remote").stdout.strip())


def backup(data_dir: Path = DATA_DIR, reason: str = "", log: Callable[[str], None] = print) -> str:
    """Commit and push any changes in data_dir. Returns 'skipped', 'unchanged', 'pushed' or 'failed'.

    Never raises: a failed backup is reported but must not break scoring or the dashboard.
    """
    if not is_configured(data_dir):
        return "skipped"
    with _LOCK:
        try:
            _git(data_dir, "add", "-A")
            if _git(data_dir, "diff", "--cached", "--quiet").returncode != 0:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                msg = f"Backup {stamp}" + (f": {reason}" if reason else "")
                commit = _git(data_dir, "commit", "-q", "-m", msg)
                if commit.returncode != 0:
                    raise RuntimeError(commit.stderr.strip() or commit.stdout.strip())
            # Push new commits, including any left over from an earlier failed push.
            ahead = _git(data_dir, "rev-list", "--count", "@{u}..HEAD")
            if ahead.returncode == 0 and ahead.stdout.strip() == "0":
                return "unchanged"
            push = _git(data_dir, "push", "-q", "-u", "origin", "HEAD")
            if push.returncode != 0:
                # The commit is kept locally; the next backup pushes it.
                raise RuntimeError(push.stderr.strip())
            log(f"Backed up data ({reason or 'changes'}).")
            return "pushed"
        except Exception as e:  # noqa: BLE001
            log(f"Warning: data backup failed: {e}")
            return "failed"


def backup_in_background(data_dir: Path = DATA_DIR, reason: str = "") -> None:
    threading.Thread(target=backup, args=(data_dir, reason), daemon=True).start()
