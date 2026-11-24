"""Calibration: pick same-score PR pairs, record verdicts, turn disputes into rubric proposals."""
from __future__ import annotations

import difflib
import random
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from .config import RUBRIC_DIR, Config, set_rubric_version
from .prompt import build_user_message
from .scorer import DIMENSIONS, call_claude_json, load_rubric, rubric_path
from .store import Store

DISPUTE_TYPES = {
    "a_more": "PR A is more complex than PR B",
    "b_more": "PR B is more complex than PR A",
    "both_high": "Both are scored too high",
    "both_low": "Both are scored too low",
}
IMPACT_TABLE_LIMIT = 400

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "string"}},
        "new_rubric": {"type": "string"},
        "predicted_scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "score": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["key", "score", "reason"],
                "additionalProperties": False,
            },
        },
        "affected_estimate": {"type": "integer"},
        "affected_examples": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["diagnosis", "changes", "new_rubric", "predicted_scores", "affected_estimate", "affected_examples"],
    "additionalProperties": False,
}

ANALYST_SYSTEM = """You maintain the rubric that an LLM uses to rate the complexity of merged pull requests (1-10).
The developer reviewed PRs that received the same score and disputed the result. Your job:
1. Diagnose which rule, anchor, or gap in the current rubric produced the mismatch with the developer's judgment.
2. Propose the smallest edit to the rubric that fixes it: a new or reworded rule, a reworded anchor, an added calibration example, or a change in how dimensions combine. Keep everything else verbatim. Make rules general; never mention specific PRs, repositories, or file names in the rubric.
3. Do not undo changes the developer accepted earlier, and do not re-propose changes they rejected, unless the new feedback clearly asks for it.
4. Predict the score each disputed PR would get under the edited rubric.
5. Estimate how many PRs in the provided table the edit would change, and list up to 10 example keys.
Return the complete edited rubric in new_rubric (Markdown), and a short bullet-style description of each change in changes."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pair_id(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))  # type: ignore[return-value]


# --- Pair selection -------------------------------------------------------
def pick_pair(prs: dict[str, dict], calibration: dict, rubric_version: int,
              rng: random.Random | None = None) -> dict | None:
    """Random pair of LLM-scored PRs (current rubric) sharing a score, never reviewed before.

    Scores with fewer past reviews are preferred, so calibration spreads across the scale.
    """
    rng = rng or random.Random()
    by_score: dict[int, list[str]] = defaultdict(list)
    for key, pr in prs.items():
        if (pr.get("score_source") == "llm" and pr.get("llm_score") is not None
                and pr.get("rubric_version") == rubric_version):
            by_score[pr["llm_score"]].append(key)
    reviewed = {_pair_id(r["a"], r["b"]) for r in calibration.get("reviews", [])}
    reviews_per_score = defaultdict(int)
    for r in calibration.get("reviews", []):
        reviews_per_score[r["score"]] += 1

    candidates = {}
    for score, keys in by_score.items():
        if len(keys) < 2:
            continue
        open_pairs = [(a, b) for i, a in enumerate(sorted(keys)) for b in sorted(keys)[i + 1:]
                      if _pair_id(a, b) not in reviewed]
        if open_pairs:
            candidates[score] = open_pairs
    if not candidates:
        return None
    scores = sorted(candidates)
    weights = [1.0 / (1 + reviews_per_score[s]) for s in scores]
    score = rng.choices(scores, weights=weights, k=1)[0]
    a, b = rng.choice(candidates[score])
    if rng.random() < 0.5:
        a, b = b, a
    return {"score": score, "a": a, "b": b}


def pr_card(pr: dict) -> dict:
    """The fields the side-by-side view shows for one PR."""
    m = pr.get("metrics", {})
    return {
        "key": pr["key"], "title": pr["title"], "repo": pr["repo"], "url": pr["url"],
        "merged_at": pr.get("merged_at"), "summary": pr.get("summary"), "rationale": pr.get("rationale"),
        "confidence": pr.get("confidence"), "dimensions": pr.get("dimensions"), "score": pr.get("score"),
        "by_category": m.get("by_category"), "change_types": m.get("change_types"),
        "directories_touched": m.get("directories_touched"), "languages": m.get("languages"),
        "excluded_count": len(m.get("excluded_files", [])),
    }


# --- Verdicts -------------------------------------------------------------
def record_review(store: Store, rubric_version: int, a: str, b: str, score: int, verdict: str,
                  dispute_type: str | None = None, explanation: str = "",
                  user_scores: dict | None = None) -> dict:
    if verdict not in ("agree", "dispute"):
        raise ValueError("verdict must be 'agree' or 'dispute'")
    if verdict == "dispute":
        if dispute_type not in DISPUTE_TYPES:
            raise ValueError(f"dispute_type must be one of {sorted(DISPUTE_TYPES)}")
        if not explanation.strip():
            raise ValueError("An explanation is required when disputing")
    review = {
        "id": uuid.uuid4().hex[:10], "created_at": _now(), "rubric_version": rubric_version,
        "score": score, "a": a, "b": b, "verdict": verdict,
        "dispute_type": dispute_type if verdict == "dispute" else None,
        "explanation": explanation.strip(), "user_scores": user_scores or {},
        "status": "open" if verdict == "dispute" else "closed", "proposal_id": None,
    }
    with store.lock:
        cal = store.load_calibration()
        cal["reviews"].append(review)
        store.save_calibration(cal)
    return review


# --- Analysis -------------------------------------------------------------
def _impact_table(prs: dict[str, dict]) -> str:
    rows = [p for p in prs.values() if p.get("llm_score") is not None]
    rows.sort(key=lambda p: p.get("merged_at") or "", reverse=True)
    lines = ["key | score | scope/logic/novelty/risk | title | rationale"]
    for p in rows[:IMPACT_TABLE_LIMIT]:
        d = p.get("dimensions") or {}
        dims = "/".join(str(d.get(k, "?")) for k in DIMENSIONS)
        lines.append(f"{p['key']} | {p['llm_score']} | {dims} | {p['title'][:80]} | {(p.get('rationale') or '')[:160]}")
    if len(rows) > IMPACT_TABLE_LIMIT:
        lines.append(f"... {len(rows) - IMPACT_TABLE_LIMIT} older PRs not shown; scale your estimate accordingly")
    return "\n".join(lines)


def _history(cal: dict) -> str:
    done = [p for p in cal.get("proposals", []) if p["status"] in ("accepted", "rejected")]
    if not done:
        return "(none)"
    return "\n".join(f"- [{p['status']}] {'; '.join(p['changes'])}" for p in done[-15:])


def build_analysis_message(reviews: list[dict], prs: dict[str, dict], store: Store, cfg: Config) -> str:
    rubric = load_rubric(cfg.rubric_version)
    parts = [f"<current_rubric version=\"{cfg.rubric_version}\">\n{rubric}\n</current_rubric>"]
    for n, r in enumerate(reviews, 1):
        block = [f"<dispute n=\"{n}\">", f"Both PRs were scored {r['score']}.",
                 f"Developer verdict: {DISPUTE_TYPES[r['dispute_type']]}."]
        if r.get("user_scores"):
            us = r["user_scores"]
            block.append(f"Scores the developer would give: A={us.get('a', '-')}, B={us.get('b', '-')}.")
        block.append(f"Developer explanation: {r['explanation']}")
        for label, key in (("A", r["a"]), ("B", r["b"])):
            pr = prs[key]
            out = {d: (pr.get("dimensions") or {}).get(d) for d in DIMENSIONS}
            block.append(f"<pr_{label} key=\"{key}\">")
            block.append(build_user_message(pr, store.load_diff(key) or "", cfg.max_diff_chars // 2,
                                            cfg.max_lines_per_file).replace(
                "\n\nRate this pull request's complexity using the rubric.", ""))
            block.append(f"Scorer output: score={pr.get('llm_score')}, dimensions={out}, "
                         f"summary={pr.get('summary')!r}, rationale={pr.get('rationale')!r}")
            block.append(f"</pr_{label}>")
        block.append("</dispute>")
        parts.append("\n".join(block))
    parts.append(f"<earlier_proposals>\n{_history(store.load_calibration())}\n</earlier_proposals>")
    parts.append(f"<all_scored_prs>\n{_impact_table(prs)}\n</all_scored_prs>")
    parts.append("Diagnose the mismatch and propose the rubric edit.")
    return "\n\n".join(parts)


def rubric_diff(old: str, new: str, old_version: int, new_version: int) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"rubric_v{old_version}.md", tofile=f"rubric_v{new_version}.md",
    ))


def analyze(store: Store, cfg: Config, review_ids: list[str], llm=call_claude_json) -> dict:
    """Send one or more disputes to Claude and save the resulting rubric proposal."""
    cal = store.load_calibration()
    reviews = [r for r in cal["reviews"] if r["id"] in review_ids and r["verdict"] == "dispute"]
    if not reviews:
        raise ValueError("No disputes found for those ids")
    prs = store.load_prs()
    message = build_analysis_message(reviews, prs, store, cfg)
    result = llm(cfg, ANALYST_SYSTEM, message, PROPOSAL_SCHEMA, 16000)
    old = load_rubric(cfg.rubric_version)
    new_text = result["new_rubric"].rstrip() + "\n"
    proposal = {
        "id": uuid.uuid4().hex[:10], "created_at": _now(), "review_ids": [r["id"] for r in reviews],
        "base_version": cfg.rubric_version, "diagnosis": result["diagnosis"], "changes": result["changes"],
        "new_rubric": new_text, "diff": rubric_diff(old, new_text, cfg.rubric_version, cfg.rubric_version + 1),
        "predicted_scores": result["predicted_scores"], "affected_estimate": result["affected_estimate"],
        "affected_examples": result["affected_examples"], "status": "proposed",
    }
    with store.lock:
        cal = store.load_calibration()
        cal["proposals"].append(proposal)
        for r in cal["reviews"]:
            if r["id"] in proposal["review_ids"]:
                r["status"] = "analyzed"
                r["proposal_id"] = proposal["id"]
        store.save_calibration(cal)
    return proposal


# --- Decisions on proposals --------------------------------------------------
def _set_proposal(store: Store, proposal_id: str, **fields) -> dict:
    with store.lock:
        cal = store.load_calibration()
        prop = next((p for p in cal["proposals"] if p["id"] == proposal_id), None)
        if prop is None:
            raise ValueError(f"Unknown proposal {proposal_id}")
        prop.update(fields)
        status = fields.get("status")
        for r in cal["reviews"]:
            if r["id"] in prop["review_ids"]:
                # Rejected proposals send their disputes back to the pending list.
                r["status"] = {"accepted": "resolved", "rejected": "open"}.get(status, r["status"])
        store.save_calibration(cal)
        return prop


def accept_proposal(store: Store, cfg: Config, proposal_id: str, edited_rubric: str | None = None,
                    note: str = "") -> int:
    """Write rubric_v{N+1}.md, log it in CHANGELOG.md, make it the active version. Returns N+1."""
    cal = store.load_calibration()
    prop = next((p for p in cal["proposals"] if p["id"] == proposal_id), None)
    if prop is None:
        raise ValueError(f"Unknown proposal {proposal_id}")
    if prop["status"] not in ("proposed", "saved"):
        raise ValueError(f"Proposal is already {prop['status']}")
    if prop["base_version"] != cfg.rubric_version:
        raise ValueError(f"The rubric changed since this proposal (it was based on v{prop['base_version']}, "
                         f"current is v{cfg.rubric_version}). Analyze the dispute again.")
    new_version = cfg.rubric_version + 1
    text = (edited_rubric if edited_rubric is not None else prop["new_rubric"]).rstrip() + "\n"
    path = rubric_path(new_version)
    if path.exists():
        raise ValueError(f"{path.name} already exists")
    path.write_text(text)

    reviews = {r["id"]: r for r in cal["reviews"]}
    entry = [f"\n## v{new_version} ({_now()[:10]})", f"Diagnosis: {prop['diagnosis']}", "", "Changes:"]
    entry += [f"- {c}" for c in prop["changes"]]
    if edited_rubric is not None and edited_rubric.strip() != prop["new_rubric"].strip():
        entry.append("- (proposal edited by hand before accepting)")
    entry += ["", "Prompted by disputes:"]
    for rid in prop["review_ids"]:
        r = reviews.get(rid)
        if r:
            entry.append(f"- {r['a']} vs {r['b']} (both {r['score']}): {DISPUTE_TYPES[r['dispute_type']]}. "
                         f"\"{r['explanation']}\" [dispute {rid}]")
    if note.strip():
        entry += ["", f"Note: {note.strip()}"]
    with (RUBRIC_DIR / "CHANGELOG.md").open("a") as f:
        f.write("\n".join(entry) + "\n")

    set_rubric_version(new_version)
    cfg.rubric_version = new_version
    _set_proposal(store, proposal_id, status="accepted", result_version=new_version, decided_at=_now(),
                  edited=edited_rubric is not None)
    return new_version


def reject_proposal(store: Store, proposal_id: str) -> dict:
    return _set_proposal(store, proposal_id, status="rejected", decided_at=_now())


def save_proposal(store: Store, proposal_id: str) -> dict:
    return _set_proposal(store, proposal_id, status="saved", decided_at=_now())


# --- Stats ----------------------------------------------------------------
def stats(calibration: dict) -> dict:
    per_version = defaultdict(lambda: {"agree": 0, "dispute": 0})
    per_score = defaultdict(lambda: {"agree": 0, "dispute": 0})
    for r in calibration.get("reviews", []):
        per_version[r["rubric_version"]][r["verdict"]] += 1
        per_score[r["score"]][r["verdict"]] += 1

    def rate(d):
        total = d["agree"] + d["dispute"]
        return round(d["agree"] / total, 3) if total else None

    return {
        "by_version": [{"version": v, **d, "agreement": rate(d)} for v, d in sorted(per_version.items())],
        "by_score": [{"score": s, **d, "agreement": rate(d)} for s, d in sorted(per_score.items())],
        "open_disputes": sum(1 for r in calibration.get("reviews", []) if r["status"] == "open"),
    }
