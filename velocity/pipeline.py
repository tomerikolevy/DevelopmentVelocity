"""Scoring, rescoring and label-writing workflows shared by the CLI and the web server."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Protocol

from .config import DEFAULT_EFFORT, Config
from .github import GhError, GitHub
from .scorer import apply_result
from .store import Store


class Scorer(Protocol):
    version: int

    def score(self, pr: dict, diff: str) -> dict: ...


def is_outdated(pr: dict, version: int, model: str | None = None, effort: str | None = None) -> bool:
    """Scored with an older rubric, or (when given) a different model or effort than the current settings."""
    if (pr.get("rubric_version") or 0) < version:
        return True
    if model and pr.get("model") != model:
        return True
    # Scores recorded before effort was tracked were made at the default.
    return bool(effort) and (pr.get("effort") or DEFAULT_EFFORT) != effort


def select_targets(prs: dict[str, dict], mode: str, version: int, repo: str | None = None,
                   since: str | None = None, limit: int | None = None,
                   model: str | None = None, effort: str | None = None) -> list[str]:
    """mode: 'unscored' (never scored), 'outdated' (unscored, or scored with an older rubric or
    different model/effort), 'all'."""
    keys = []
    for key, pr in sorted(prs.items(), key=lambda kv: kv[1].get("merged_at") or "", reverse=True):
        if repo and pr["repo"] != repo:
            continue
        if since and (pr.get("merged_at") or "") < since:
            continue
        scored = pr.get("llm_score") is not None
        if mode == "unscored" and scored:
            continue
        if mode == "outdated" and scored and not is_outdated(pr, version, model, effort):
            continue
        keys.append(key)
    return keys[:limit] if limit else keys


def run_scoring(keys: list[str], scorer: Scorer, store: Store, model: str, workers: int = 4,
                progress: Callable[[int, int, str, str], None] | None = None,
                effort: str | None = None) -> dict:
    """Scores each PR and saves it right away, so an interrupted run resumes where it stopped.

    Returns {"changes": [{key, old, new}], "errors": [{key, error}]} where old/new are
    the PR's effective score (manual overrides stay as they are).
    """
    prs = store.load_prs()
    changes, errors = [], []
    total = len(keys)

    def work(key: str):
        pr = prs[key]
        diff = store.load_diff(key) or ""
        return key, scorer.score(pr, diff)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, k) for k in keys]
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                key, result = fut.result()
            except Exception as e:  # noqa: BLE001 - one bad PR must not stop the run
                key = keys[futures.index(fut)]
                errors.append({"key": key, "error": str(e)})
                if progress:
                    progress(i, total, key, f"error: {e}")
                continue
            with store.lock:
                current = store.load_prs()
                pr = current[key]
                old_effective, old_llm = pr.get("score"), pr.get("llm_score")
                apply_result(pr, result, scorer.version, model, effort)
                store.save_prs(current)
            changes.append({
                "key": key, "title": pr["title"], "repo": pr["repo"], "merged_at": pr.get("merged_at"),
                "old": old_effective, "new": pr.get("score"), "old_llm": old_llm, "new_llm": result["score"],
                "source": pr.get("score_source"),
            })
            if progress:
                progress(i, total, key, f"{old_llm} -> {result['score']}")
    return {"changes": changes, "errors": errors}


def diff_report(run: dict) -> dict:
    """Summary of how a (re)scoring run moved LLM scores and monthly complexity totals."""
    changes = run["changes"]
    moved = [c for c in changes if c["old_llm"] is not None and c["old_llm"] != c["new_llm"]]
    before, after = defaultdict(int), defaultdict(int)
    for c in changes:
        month = (c.get("merged_at") or "")[:7] or "unknown"
        if c["old_llm"] is not None:
            before[month] += c["old_llm"]
        after[month] += c["new_llm"]
    months = sorted(set(before) | set(after))
    deltas = [c["new_llm"] - c["old_llm"] for c in changes if c["old_llm"] is not None]
    return {
        "scored": len(changes),
        "previously_scored": len(deltas),
        "changed": len(moved),
        "up": sum(1 for d in deltas if d > 0),
        "down": sum(1 for d in deltas if d < 0),
        "avg_delta": round(sum(deltas) / len(deltas), 2) if deltas else 0,
        "biggest_movers": sorted(moved, key=lambda c: abs(c["new_llm"] - c["old_llm"]), reverse=True)[:10],
        "monthly": [{"month": m, "before": before.get(m, 0), "after": after.get(m, 0)} for m in months],
        "errors": run["errors"],
    }


def format_report(rep: dict) -> str:
    lines = [
        f"Scored {rep['scored']} PRs ({rep['previously_scored']} had an earlier LLM score).",
        f"Changed: {rep['changed']}  (up {rep['up']}, down {rep['down']}, average change {rep['avg_delta']:+})",
    ]
    if rep["biggest_movers"]:
        lines.append("Biggest movers:")
        for c in rep["biggest_movers"]:
            lines.append(f"  {c['old_llm']:>2} -> {c['new_llm']:<2} {c['key']}  {c['title'][:60]}")
    if rep["previously_scored"]:
        lines.append("Monthly complexity (before -> after):")
        for m in rep["monthly"]:
            lines.append(f"  {m['month']}: {m['before']} -> {m['after']}")
    if rep["errors"]:
        lines.append(f"{len(rep['errors'])} errors:")
        lines += [f"  {e['key']}: {e['error']}" for e in rep["errors"]]
    return "\n".join(lines)


def pending_labels(prs: dict[str, dict], include_manual: bool = False) -> list[str]:
    keys = []
    for key, pr in prs.items():
        if pr.get("llm_score") is None:
            continue
        if pr.get("score_source") == "manual" and not include_manual:
            continue
        if pr.get("score_source") == "manual" or pr.get("label_status") in ("pending", "error"):
            keys.append(key)
    return keys


def write_labels(keys: Iterable[str], cfg: Config, store: Store, gh: GitHub,
                 include_manual: bool = False, log: Callable[[str], None] = print) -> dict:
    """Writes complexity:<llm_score> to each PR. Manual overrides are skipped unless include_manual."""
    counts = defaultdict(int)
    for key in keys:
        with store.lock:
            pr = store.load_prs()[key]
        if pr.get("score_source") == "manual" and not include_manual:
            counts["skipped_manual"] += 1
            continue
        score = pr["llm_score"]
        fields: dict = {}
        if not gh.can_label(pr["repo"]):
            fields = {"label_status": "no_permission"}
            counts["no_permission"] += 1
        else:
            try:
                gh.set_complexity_label(pr["repo"], pr["number"], score, cfg.label_prefix, pr.get("labels", []))
                labels = [x for x in pr.get("labels", []) if not x.startswith(cfg.label_prefix)]
                fields = {"label_status": "written", "llm_label_written": score, "score": score,
                          "score_source": "llm", "labels": labels + [f"{cfg.label_prefix}{score}"]}
                counts["written"] += 1
            except GhError as e:
                fields = {"label_status": "error", "label_error": str(e)}
                counts["error"] += 1
                log(f"  label failed for {key}: {e}")
        store.update_pr(key, fields)
    log("Labels: " + ", ".join(f"{k} {v}" for k, v in counts.items()) if counts else "Labels: nothing to write.")
    return dict(counts)
