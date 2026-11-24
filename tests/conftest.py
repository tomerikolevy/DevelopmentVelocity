import pytest

from velocity import calibrate, scorer
from velocity.config import Config
from velocity.store import Store

DIFF = """diff --git a/src/app.py b/src/app.py
index 1..2 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,5 @@
+import os
+x = 1
diff --git a/tests/test_app.py b/tests/test_app.py
new file mode 100644
--- /dev/null
+++ b/tests/test_app.py
@@ -0,0 +1,2 @@
+def test_x():
+    assert True
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-{}
+{"a": 1}
"""

FILES = [
    {"path": "src/app.py", "additions": 2, "deletions": 0},
    {"path": "tests/test_app.py", "additions": 2, "deletions": 0},
    {"path": "package-lock.json", "additions": 1, "deletions": 1},
]


def make_pr(key, merged="2026-05-01T00:00:00Z", **extra):
    from velocity.metrics import compute_metrics

    repo, num = key.split("#")
    pr = {"key": key, "repo": repo, "number": int(num), "title": f"PR {key}", "url": f"https://x/{num}",
          "merged_at": merged, "body": "body", "commits": ["c1"], "repo_description": "d",
          "repo_language": "Python", "metrics": compute_metrics(FILES, DIFF), "score_history": [], "labels": []}
    pr.update(extra)
    return pr


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "data")


@pytest.fixture
def cfg():
    return Config(rubric_version=1)


@pytest.fixture
def rubric_dir(tmp_path, monkeypatch):
    d = tmp_path / "rubric"
    d.mkdir()
    (d / "rubric_v1.md").write_text("Rubric one.\nRule A.\n")
    (d / "CHANGELOG.md").write_text("# Rubric changelog\n")
    monkeypatch.setattr(scorer, "RUBRIC_DIR", d)
    monkeypatch.setattr(calibrate, "RUBRIC_DIR", d)
    monkeypatch.setattr(calibrate, "set_rubric_version", lambda v: None)
    return d


class FakeScorer:
    def __init__(self, scores, version=1):
        self.scores = scores
        self.version = version

    def score(self, pr, diff):
        s = self.scores[pr["key"]]
        if isinstance(s, Exception):
            raise s
        return {"scope": 2, "logic_depth": 3, "novelty": 1, "risk": 2, "score": s,
                "summary": "sum", "rationale": "why", "confidence": "high"}


class FakeGh:
    def __init__(self, writable=True):
        self.writable = writable
        self.calls = []

    def can_label(self, repo):
        return self.writable

    def set_complexity_label(self, repo, number, score, prefix, current):
        self.calls.append((repo, number, score))
