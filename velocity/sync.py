"""Sync merged PRs from GitHub into the local store and detect manual label overrides."""
from __future__ import annotations

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


def sync(cfg: Config, store: Store, gh: GitHub, log: Callable[[str], None] = print) -> dict:
    found = gh.search_merged_prs(cfg.author, cfg.since)
    prs = store.load_prs()
    excluded = set(cfg.exclude_repos)
    new = updated = 0
    for row in found:
        repo = row["repository"]["nameWithOwner"]
        if repo in excluded:
            continue
        key = pr_key(repo, row["number"])
        labels = [label["name"] for label in row.get("labels", [])]
        pr = prs.get(key)
        if pr is None:
            log(f"  fetching {key}  {row['title'][:70]}")
            details = gh.pr_details(repo, row["number"])
            diff = gh.pr_diff(repo, row["number"])
            store.save_diff(key, diff)
            info = gh.repo_info(repo)
            pr = {
                "key": key, "repo": repo, "number": row["number"],
                "title": details.get("title", row["title"]), "url": details.get("url", row["url"]),
                "merged_at": details.get("mergedAt"), "body": details.get("body") or "",
                "commits": [c.get("messageHeadline", "") for c in details.get("commits", [])],
                "repo_description": info["description"], "repo_language": info["language"],
                "metrics": compute_metrics(details.get("files") or [], diff),
                "score_history": [],
            }
            new += 1
        else:
            updated += 1
        apply_label_state(pr, labels, cfg.label_prefix)
        prs[key] = pr
    store.save_prs(prs)
    manual = sum(1 for p in prs.values() if p.get("score_source") == "manual")
    log(f"Synced {len(found)} merged PRs: {new} new, {updated} refreshed, {manual} with manual labels.")
    return {"found": len(found), "new": new, "refreshed": updated, "manual": manual}
