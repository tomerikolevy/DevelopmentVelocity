"""Sync merged PRs from GitHub into the local store and detect manual label overrides."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .config import Config
from .github import GitHub, label_score
from .metrics import compute_metrics
from .store import Store, pr_key


def apply_label_state(pr: dict, labels: list[str], prefix: str) -> None:
    """Update a PR record from its current GitHub labels.

    A complexity label that differs from the one we last wrote (or exists before we
    ever wrote one) was set by hand: it becomes the PR's score with source "manual".
    """
    pr["labels"] = labels
    value = label_score(labels, prefix)
    written = pr.get("llm_label_written")
    if value is not None and value != written:
        pr["score"] = value
        pr["score_source"] = "manual"
    elif value is not None and value == written and pr.get("score_source") == "manual":
        # Label was changed back to exactly what the LLM wrote: it's an LLM score again.
        pr["score_source"] = "llm"
        pr["score"] = pr.get("llm_score", value)
    elif value is None and pr.get("score_source") == "manual":
        # Manual label removed: fall back to the LLM score if we have one.
        if pr.get("llm_score") is not None:
            pr["score"] = pr["llm_score"]
            pr["score_source"] = "llm"
            pr["label_status"] = "pending"
        else:
            pr.pop("score", None)
            pr.pop("score_source", None)


SAVE_EVERY_PRS = 25
SAVE_EVERY_SECONDS = 15


def fetch_pr(gh: GitHub, store: Store, row: dict) -> dict:
    """Download one PR's details, diff and repo info, and build its local record."""
    repo = row["repository"]["nameWithOwner"]
    key = pr_key(repo, row["number"])
    details = gh.pr_details(repo, row["number"])
    diff = gh.pr_diff(repo, row["number"])
    store.save_diff(key, diff)
    info = gh.repo_info(repo)
    return {
        "key": key, "repo": repo, "number": row["number"],
        "title": details.get("title", row["title"]), "url": details.get("url", row["url"]),
        "merged_at": details.get("mergedAt"), "body": details.get("body") or "",
        "commits": [c.get("messageHeadline", "") for c in details.get("commits", [])],
        "repo_description": info["description"], "repo_language": info["language"],
        "metrics": compute_metrics(details.get("files") or [], diff),
        "score_history": [],
    }


def sync(cfg: Config, store: Store, gh: GitHub, log: Callable[[str], None] = print,
         workers: int = 4) -> dict:
    """Fetch new merged PRs and refresh labels on known ones.

    New PRs are downloaded in parallel and saved in small batches, so an interrupted
    sync keeps what it fetched. A PR that fails to download is logged and skipped;
    the next sync tries it again.
    """
    found = gh.search_merged_prs(cfg.author, cfg.since)
    prs = store.load_prs()
    excluded = set(cfg.exclude_repos)
    rows = [r for r in found if r["repository"]["nameWithOwner"] not in excluded]

    # Known PRs: only labels can change, and the search results already carry them.
    refreshed = 0
    to_fetch = []
    for row in rows:
        key = pr_key(row["repository"]["nameWithOwner"], row["number"])
        if key in prs:
            apply_label_state(prs[key], [l["name"] for l in row.get("labels", [])], cfg.label_prefix)
            refreshed += 1
        else:
            to_fetch.append(row)
    store.save_prs(prs)

    # Only this thread touches `prs`; workers just download and build records.
    unsaved, last_save = 0, time.monotonic()
    new, failed = 0, []

    def save(force: bool = False) -> None:
        nonlocal unsaved, last_save
        due = unsaved >= SAVE_EVERY_PRS or time.monotonic() - last_save >= SAVE_EVERY_SECONDS
        if unsaved and (force or due):
            store.save_prs(prs)
            unsaved, last_save = 0, time.monotonic()

    total = len(to_fetch)
    if total:
        log(f"Fetching {total} new PRs ({workers} at a time)...")
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = {pool.submit(fetch_pr, gh, store, row): row for row in to_fetch}
        for done, fut in enumerate(as_completed(futures), 1):
            row = futures[fut]
            key = pr_key(row["repository"]["nameWithOwner"], row["number"])
            try:
                pr = fut.result()
            except Exception as e:  # one bad PR must not stop the sync
                failed.append(key)
                log(f"  [{done}/{total}] {key}: FAILED ({e}); will retry on next sync")
                continue
            apply_label_state(pr, [l["name"] for l in row.get("labels", [])], cfg.label_prefix)
            prs[key] = pr
            unsaved += 1
            new += 1
            log(f"  [{done}/{total}] {key}  {row['title'][:70]}")
            save()
    except KeyboardInterrupt:
        log(f"Interrupted: keeping the {new} PRs fetched so far. Run sync again to continue.")
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        save(force=True)

    manual = sum(1 for p in prs.values() if p.get("score_source") == "manual")
    msg = f"Synced {len(rows)} merged PRs: {new} new, {refreshed} refreshed, {manual} with manual labels."
    if failed:
        msg += f" {len(failed)} failed to download and will be retried next sync."
    log(msg)
    return {"found": len(rows), "new": new, "refreshed": refreshed, "manual": manual, "failed": failed}
