import json
import random

import pytest

from velocity import calibrate as cal
from velocity.github import GitHub, label_score
from velocity.metrics import categorize, compute_metrics, split_diff
from velocity.pipeline import diff_report, pending_labels, run_scoring, select_targets, write_labels
from velocity.prompt import build_diff_section, build_user_message, truncate_file_diff
from velocity.scorer import normalize
from velocity.sync import apply_label_state

from .conftest import DIFF, FILES, FakeGh, FakeScorer, make_pr


# --- metrics ---------------------------------------------------------------
@pytest.mark.parametrize("path,cat", [
    ("src/app.py", "source"), ("tests/test_app.py", "tests"), ("web/foo.test.ts", "tests"),
    ("package-lock.json", "excluded"), ("node_modules/x/index.js", "excluded"), ("logo.png", "excluded"),
    ("dist/app.min.js", "excluded"), ("README.md", "docs"), ("pyproject.toml", "config"),
    (".github/workflows/ci.yml", "config"), ("app/models/user.rb", "source"),
])
def test_categorize(path, cat):
    assert categorize(path) == cat


def test_split_diff_and_metrics():
    parts = split_diff(DIFF)
    assert parts["tests/test_app.py"]["change"] == "new"
    assert parts["src/app.py"]["change"] == "modified"
    m = compute_metrics(FILES, DIFF)
    assert m["excluded_files"] == ["package-lock.json"]
    assert m["by_category"]["source"] == {"files": 1, "additions": 2, "deletions": 0}
    assert m["total_additions"] == 4 and m["files_counted"] == 2
    assert m["change_types"]["new"] == 1
    assert m["languages"] == ["Python"]


# --- prompt -------------------------------------------------------------------
def test_truncate_file_diff():
    text = "\n".join(f"l{i}" for i in range(10))
    out = truncate_file_diff(text, 4)
    assert out.endswith("[truncated: 6 more lines in this file]")
    assert truncate_file_diff(text, 50) == text


def test_diff_section_orders_source_first_and_skips_excluded():
    m = compute_metrics(FILES, DIFF)
    out = build_diff_section(DIFF, m["files"], 40000, 300)
    assert out.index("src/app.py") < out.index("tests/test_app.py")
    assert "package-lock.json" not in out


def test_diff_section_budget_lists_omitted_files():
    m = compute_metrics(FILES, DIFF)
    out = build_diff_section(DIFF, m["files"], 250, 300)
    assert "[omitted from diff due to size budget:" in out


def test_user_message_contains_sections():
    msg = build_user_message(make_pr("o/r#1", body="x" * 3000), DIFF)
    for part in ("## Title", "## Metrics", "## Files", "## Diff", "[truncated]", "package-lock.json"):
        assert part in msg


def test_normalize_clamps():
    r = normalize({"scope": 9, "logic_depth": 0, "novelty": 3, "risk": 3, "score": 14,
                   "summary": " s ", "rationale": "r", "confidence": "low"})
    assert r["scope"] == 5 and r["logic_depth"] == 1 and r["score"] == 10 and r["summary"] == "s"


# --- labels & manual overrides -----------------------------------------------
def test_label_score():
    assert label_score(["bug", "complexity:4"], "complexity:") == 4
    assert label_score(["complexity:x", "complexity:11"], "complexity:") is None
    assert label_score(["complexity:2", "complexity:7"], "complexity:") == 7


def test_manual_label_detected():
    pr = {"llm_score": 3, "llm_label_written": 3, "score": 3, "score_source": "llm"}
    apply_label_state(pr, ["complexity:6"], "complexity:")
    assert pr["score"] == 6 and pr["score_source"] == "manual"


def test_our_own_label_is_not_manual():
    pr = {"llm_score": 3, "llm_label_written": 3, "score": 3, "score_source": "llm"}
    apply_label_state(pr, ["complexity:3"], "complexity:")
    assert pr["score_source"] == "llm"


def test_label_before_any_scoring_is_manual():
    pr = {}
    apply_label_state(pr, ["complexity:5"], "complexity:")
    assert pr["score"] == 5 and pr["score_source"] == "manual"


def test_manual_label_removed_falls_back_to_llm():
    pr = {"llm_score": 3, "llm_label_written": 3, "score": 6, "score_source": "manual"}
    apply_label_state(pr, [], "complexity:")
    assert pr["score"] == 3 and pr["score_source"] == "llm" and pr["label_status"] == "pending"


def test_set_complexity_label_replaces_old_label():
    calls = []

    def runner(args):
        calls.append(args)
        return json.dumps([{"name": f"complexity:{n}"} for n in range(1, 11)]) if args[:2] == ["label", "list"] else ""

    GitHub(runner).set_complexity_label("o/r", 5, 7, "complexity:", ["complexity:3", "bug"])
    edit = calls[-1]
    assert edit[:2] == ["pr", "edit"] and "complexity:7" in edit and "complexity:3" in edit
    assert not any(c[:2] == ["label", "create"] for c in calls)


# --- scoring / rescoring --------------------------------------------------------
def seed(store, prs):
    store.save_prs({p["key"]: p for p in prs})
    for p in prs:
        store.save_diff(p["key"], DIFF)


def test_select_targets_modes():
    prs = {
        "o/r#1": make_pr("o/r#1", llm_score=3, rubric_version=1),
        "o/r#2": make_pr("o/r#2", llm_score=4, rubric_version=2),
        "o/r#3": make_pr("o/r#3"),
        "x/y#4": make_pr("x/y#4", merged="2025-01-01T00:00:00Z"),
    }
    assert set(select_targets(prs, "unscored", 2)) == {"o/r#3", "x/y#4"}
    assert set(select_targets(prs, "outdated", 2)) == {"o/r#1", "o/r#3", "x/y#4"}
    assert len(select_targets(prs, "all", 2)) == 4
    assert select_targets(prs, "all", 2, repo="x/y") == ["x/y#4"]
    assert "x/y#4" not in select_targets(prs, "all", 2, since="2026-01-01")


def test_rescore_keeps_history_and_manual_override(store):
    seed(store, [
        make_pr("o/r#1", llm_score=3, score=3, score_source="llm", rubric_version=1, llm_label_written=3),
        make_pr("o/r#2", llm_score=4, score=8, score_source="manual", rubric_version=1, llm_label_written=4),
    ])
    run = run_scoring(["o/r#1", "o/r#2"], FakeScorer({"o/r#1": 5, "o/r#2": 6}, version=2), store, "m", workers=1)
    prs = store.load_prs()
    assert prs["o/r#1"]["score"] == 5 and prs["o/r#1"]["rubric_version"] == 2
    assert prs["o/r#1"]["score_history"][0]["score"] == 3
    assert prs["o/r#1"]["label_status"] == "pending"
    assert prs["o/r#2"]["score"] == 8 and prs["o/r#2"]["llm_score"] == 6  # manual stays
    rep = diff_report(run)
    assert rep["changed"] == 2 and rep["up"] == 2
    assert pending_labels(prs) == ["o/r#1"]
    assert set(pending_labels(prs, include_manual=True)) == {"o/r#1", "o/r#2"}


def test_run_scoring_continues_after_error(store):
    seed(store, [make_pr("o/r#1"), make_pr("o/r#2")])
    run = run_scoring(["o/r#1", "o/r#2"], FakeScorer({"o/r#1": RuntimeError("boom"), "o/r#2": 4}), store, "m", workers=1)
    assert len(run["errors"]) == 1 and run["errors"][0]["key"] == "o/r#1"
    assert store.load_prs()["o/r#2"]["score"] == 4


def test_write_labels(store, cfg):
    seed(store, [make_pr("o/r#1", llm_score=5, score=5, score_source="llm", label_status="pending")])
    gh = FakeGh()
    write_labels(["o/r#1"], cfg, store, gh, log=lambda m: None)
    pr = store.load_prs()["o/r#1"]
    assert gh.calls == [("o/r", 1, 5)]
    assert pr["llm_label_written"] == 5 and pr["label_status"] == "written"
    # Next sync sees our own label: not manual.
    apply_label_state(pr, pr["labels"], cfg.label_prefix)
    assert pr["score_source"] == "llm"


def test_write_labels_without_permission(store, cfg):
    seed(store, [make_pr("o/r#1", llm_score=5, score=5, score_source="llm", label_status="pending")])
    write_labels(["o/r#1"], cfg, store, FakeGh(writable=False), log=lambda m: None)
    assert store.load_prs()["o/r#1"]["label_status"] == "no_permission"


# --- calibrate --------------------------------------------------------------
def scored(key, s, v=1, source="llm"):
    return make_pr(key, llm_score=s, score=s, score_source=source, rubric_version=v,
                   dimensions={"scope": 1, "logic_depth": 1, "novelty": 1, "risk": 1})


def test_pick_pair_same_score_and_unreviewed():
    prs = {k: scored(k, s) for k, s in [("o/r#1", 3), ("o/r#2", 3), ("o/r#3", 5), ("o/r#4", 7), ("o/r#5", 7)]}
    prs["o/r#6"] = scored("o/r#6", 3, source="manual")   # manual excluded
    prs["o/r#7"] = scored("o/r#7", 3, v=0)                # older rubric excluded
    calib = {"reviews": [{"a": "o/r#4", "b": "o/r#5", "score": 7}]}
    for seed_ in range(30):
        p = cal.pick_pair(prs, calib, 1, random.Random(seed_))
        assert p["score"] == 3 and {p["a"], p["b"]} == {"o/r#1", "o/r#2"}
    calib["reviews"].append({"a": "o/r#2", "b": "o/r#1", "score": 3})
    assert cal.pick_pair(prs, calib, 1) is None


def test_pick_pair_prefers_less_reviewed_scores():
    prs = {f"o/r#{i}": scored(f"o/r#{i}", 3 if i < 6 else 6) for i in range(1, 12)}
    calib = {"reviews": [{"a": f"x#{i}", "b": f"y#{i}", "score": 3} for i in range(20)]}
    picks = [cal.pick_pair(prs, calib, 1, random.Random(i))["score"] for i in range(200)]
    assert picks.count(6) > picks.count(3) * 5


def test_record_review_requires_explanation(store):
    with pytest.raises(ValueError):
        cal.record_review(store, 1, "a", "b", 3, "dispute", "a_more", "  ")
    with pytest.raises(ValueError):
        cal.record_review(store, 1, "a", "b", 3, "dispute", "nope", "x")
    r = cal.record_review(store, 1, "a", "b", 3, "agree")
    assert r["status"] == "closed"


def fake_llm(cfg, system, user, schema, max_tokens):
    assert "<current_rubric" in user and "Developer explanation: renames are trivial" in user
    return {"diagnosis": "Renames counted as scope.", "changes": ["Add rule: renames don't add scope"],
            "new_rubric": "Rubric one.\nRule A.\nRule B: renames add no scope.\n",
            "predicted_scores": [{"key": "o/r#1", "score": 2, "reason": "rename"}],
            "affected_estimate": 4, "affected_examples": ["o/r#9"]}


def test_dispute_analyze_accept_flow(store, cfg, rubric_dir):
    seed(store, [scored("o/r#1", 4), scored("o/r#2", 4)])
    r = cal.record_review(store, 1, "o/r#1", "o/r#2", 4, "dispute", "b_more", "renames are trivial", {"a": 2})
    prop = cal.analyze(store, cfg, [r["id"]], llm=fake_llm)
    assert "+Rule B: renames add no scope." in prop["diff"]
    assert store.load_calibration()["reviews"][0]["status"] == "analyzed"

    v = cal.accept_proposal(store, cfg, prop["id"], note="good catch")
    assert v == 2 and cfg.rubric_version == 2
    assert (rubric_dir / "rubric_v2.md").read_text().endswith("Rule B: renames add no scope.\n")
    log = (rubric_dir / "CHANGELOG.md").read_text()
    assert "## v2" in log and "renames are trivial" in log and "good catch" in log
    data = store.load_calibration()
    assert data["proposals"][0]["status"] == "accepted" and data["reviews"][0]["status"] == "resolved"
    with pytest.raises(ValueError):
        cal.accept_proposal(store, cfg, prop["id"])


def test_accept_with_edit_and_stale_proposal(store, cfg, rubric_dir):
    seed(store, [scored("o/r#1", 4), scored("o/r#2", 4)])
    r1 = cal.record_review(store, 1, "o/r#1", "o/r#2", 4, "dispute", "b_more", "renames are trivial")
    p1 = cal.analyze(store, cfg, [r1["id"]], llm=fake_llm)
    p2 = cal.analyze(store, cfg, [r1["id"]], llm=fake_llm)
    cal.accept_proposal(store, cfg, p1["id"], edited_rubric="Hand edited.\n")
    assert (rubric_dir / "rubric_v2.md").read_text() == "Hand edited.\n"
    assert "edited by hand" in (rubric_dir / "CHANGELOG.md").read_text()
    with pytest.raises(ValueError, match="rubric changed"):
        cal.accept_proposal(store, cfg, p2["id"])


def test_reject_reopens_dispute_and_stats(store, cfg, rubric_dir):
    seed(store, [scored("o/r#1", 4), scored("o/r#2", 4)])
    r = cal.record_review(store, 1, "o/r#1", "o/r#2", 4, "dispute", "both_high", "renames are trivial")
    cal.record_review(store, 1, "o/r#3", "o/r#4", 4, "agree")
    p = cal.analyze(store, cfg, [r["id"]], llm=fake_llm)
    cal.reject_proposal(store, p["id"])
    data = store.load_calibration()
    assert data["reviews"][0]["status"] == "open"
    s = cal.stats(data)
    assert s["by_version"][0]["agreement"] == 0.5 and s["open_disputes"] == 1


def test_make_client_prefers_configured_key_env(monkeypatch):
    from velocity.config import Config
    from velocity.scorer import make_client

    cfg = Config(api_key_env="ANTHROPIC_API_KEY_COMPLEXITY_SCORER")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "generic-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY_COMPLEXITY_SCORER", "scorer-key")
    assert make_client(cfg).api_key == "scorer-key"
    monkeypatch.delenv("ANTHROPIC_API_KEY_COMPLEXITY_SCORER")
    assert make_client(cfg).api_key == "generic-key"


# --- sync --------------------------------------------------------------------
class FakeSyncGh:
    """Stands in for GitHub during sync; `fail` holds PR numbers whose details fetch errors."""

    def __init__(self, n, fail=()):
        self.rows = [{"repository": {"nameWithOwner": "me/big"}, "number": i, "title": f"PR {i}",
                      "url": f"u{i}", "labels": []} for i in range(1, n + 1)]
        self.fail = set(fail)
        self.fetched = []

    def search_merged_prs(self, author, since):
        return self.rows

    def pr_details(self, repo, number):
        if number in self.fail:
            raise RuntimeError("timeout")
        self.fetched.append(number)
        return {"title": f"PR {number}", "url": f"u{number}", "mergedAt": "2025-01-01T00:00:00Z",
                "files": FILES, "commits": []}

    def pr_diff(self, repo, number):
        return DIFF

    def repo_info(self, repo):
        return {"description": "", "language": "Python", "permission": "WRITE"}


def test_sync_skips_failed_pr_and_retries_it_next_time(store, cfg):
    from velocity.sync import sync

    gh = FakeSyncGh(5, fail={3})
    result = sync(cfg, store, gh, log=lambda m: None, workers=3)
    assert result["new"] == 4 and result["failed"] == ["me/big#3"]
    assert "me/big#3" not in store.load_prs() and len(store.load_prs()) == 4

    gh.fail.clear(); gh.fetched.clear()
    result = sync(cfg, store, gh, log=lambda m: None)
    assert gh.fetched == [3]  # only the missing PR is downloaded again
    assert result["new"] == 1 and result["refreshed"] == 4 and len(store.load_prs()) == 5


def test_sync_saves_progress_before_finishing(store, cfg, monkeypatch):
    from velocity import sync as sync_mod

    monkeypatch.setattr(sync_mod, "SAVE_EVERY_PRS", 2)
    saved_sizes = []
    real_save = store.save_prs
    monkeypatch.setattr(store, "save_prs", lambda prs: (saved_sizes.append(len(prs)), real_save(prs)))
    sync_mod.sync(cfg, store, FakeSyncGh(5), log=lambda m: None, workers=1)
    assert 2 in saved_sizes and 4 in saved_sizes and saved_sizes[-1] == 5


def test_sync_keeps_fetched_prs_when_interrupted(store, cfg):
    from velocity.sync import sync

    class InterruptingGh(FakeSyncGh):
        def pr_details(self, repo, number):
            if number == 4:
                raise KeyboardInterrupt
            return super().pr_details(repo, number)

    with pytest.raises(KeyboardInterrupt):
        sync(cfg, store, InterruptingGh(6), log=lambda m: None, workers=1)
    assert sorted(p["number"] for p in store.load_prs().values()) == [1, 2, 3]
