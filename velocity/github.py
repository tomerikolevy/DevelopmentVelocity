"""GitHub access through the `gh` CLI (reuses its existing authentication)."""
from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from typing import Callable

from .store import DIFF_UNAVAILABLE

SEARCH_LIMIT = 1000  # GitHub search returns at most 1000 results per query
LABEL_COLORS = {  # light -> dark green-to-red, so the scale reads at a glance in GitHub
    1: "c2e0c6", 2: "a2d2a8", 3: "7fc48a", 4: "fbe7a1", 5: "f9d77e",
    6: "f6c05a", 7: "f4a261", 8: "ee8a5c", 9: "e66a5a", 10: "d73a4a",
}
WRITE_PERMISSIONS = {"ADMIN", "MAINTAIN", "WRITE", "TRIAGE"}

Runner = Callable[[list[str]], str]


class GhError(RuntimeError):
    pass


def _default_runner(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


class GitHub:
    def __init__(self, runner: Runner = _default_runner):
        self.run = runner
        self._repo_cache: dict[str, dict] = {}
        self._labels_ready: set[str] = set()

    def _json(self, args: list[str]):
        out = self.run(args)
        return json.loads(out) if out.strip() else None

    # --- Discovery -------------------------------------------------------
    def search_merged_prs(self, author: str, since: str, until: date | None = None) -> list[dict]:
        """All merged PRs by author. Splits the date range when a window hits the 1000 cap."""
        start = date.fromisoformat(since) if since else date(2008, 1, 1)
        end = until or date.today()
        return self._search_window(author, start, end)

    def _search_window(self, author: str, start: date, end: date) -> list[dict]:
        rows = self._json([
            "search", "prs", "--author", author, "--merged",
            "--merged-at", f"{start.isoformat()}..{end.isoformat()}",
            "--limit", str(SEARCH_LIMIT),
            "--json", "repository,number,title,url,labels",
        ]) or []
        if len(rows) >= SEARCH_LIMIT and start < end:
            mid = start + (end - start) // 2
            first = self._search_window(author, start, mid)
            second = self._search_window(author, mid + timedelta(days=1), end)
            return first + second
        return rows

    def pr_details(self, repo: str, number: int) -> dict:
        return self._json([
            "pr", "view", str(number), "--repo", repo,
            "--json", "title,body,url,mergedAt,additions,deletions,changedFiles,files,commits,labels",
        ])

    def pr_diff(self, repo: str, number: int) -> str:
        try:
            return self.run(["pr", "diff", str(number), "--repo", repo])
        except GhError as e:
            # Very large diffs are refused by the API; scoring falls back to the file list.
            return f"{DIFF_UNAVAILABLE}: {e}]"

    def pr_labels(self, repo: str, number: int) -> list[str]:
        data = self._json(["pr", "view", str(number), "--repo", repo, "--json", "labels"]) or {}
        return [label["name"] for label in data.get("labels", [])]

    def repo_info(self, repo: str) -> dict:
        if repo not in self._repo_cache:
            try:
                info = self._json([
                    "repo", "view", repo, "--json", "description,primaryLanguage,viewerPermission",
                ]) or {}
            except GhError:
                info = {}
            self._repo_cache[repo] = {
                "description": info.get("description") or "",
                "language": (info.get("primaryLanguage") or {}).get("name", ""),
                "permission": info.get("viewerPermission") or "",
            }
        return self._repo_cache[repo]

    def can_label(self, repo: str) -> bool:
        return self.repo_info(repo).get("permission") in WRITE_PERMISSIONS

    # --- Labels ----------------------------------------------------------
    def ensure_labels(self, repo: str, prefix: str) -> None:
        if repo in self._labels_ready:
            return
        existing = self._json(["label", "list", "--repo", repo, "--limit", "500", "--json", "name"]) or []
        names = {label["name"] for label in existing}
        for n in range(1, 11):
            name = f"{prefix}{n}"
            if name not in names:
                self.run([
                    "label", "create", name, "--repo", repo, "--color", LABEL_COLORS[n],
                    "--description", f"Complexity index {n}/10 (development velocity)",
                ])
        self._labels_ready.add(repo)

    def set_complexity_label(self, repo: str, number: int, score: int, prefix: str,
                             current_labels: list[str]) -> None:
        """Replace any existing complexity label on the PR with complexity:<score>."""
        self.ensure_labels(repo, prefix)
        target = f"{prefix}{score}"
        args = ["pr", "edit", str(number), "--repo", repo, "--add-label", target]
        stale = [label for label in current_labels if label.startswith(prefix) and label != target]
        for label in stale:
            args += ["--remove-label", label]
        if target in current_labels and not stale:
            return
        self.run(args)


def label_score(labels: list[str], prefix: str) -> int | None:
    """The complexity value from a PR's labels, or None. If several exist, the highest wins."""
    values = []
    for label in labels:
        if label.startswith(prefix):
            try:
                n = int(label[len(prefix):])
            except ValueError:
                continue
            if 1 <= n <= 10:
                values.append(n)
    return max(values) if values else None


def diff_fetcher(gh: "GitHub"):
    """Store.diff_fetcher that downloads a PR's diff by its key ("owner/repo#123")."""
    def fetch(key: str) -> str:
        repo, number = key.rsplit("#", 1)
        return gh.pr_diff(repo, int(number))
    return fetch
