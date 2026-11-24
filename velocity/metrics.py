"""Objective diff metrics: file categories, line counts, change types, directories, languages."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock",
    "gemfile.lock", "cargo.lock", "composer.lock", "go.sum", "podfile.lock", "uv.lock",
    "packages.lock.json", "mix.lock", "pubspec.lock",
}
EXCLUDED_DIRS = ("node_modules/", "vendor/", "third_party/", "dist/", "build/", "__snapshots__/", ".venv/")
EXCLUDED_SUFFIXES = (".min.js", ".min.css", ".map", ".snap", ".pb.go", "_pb2.py", ".g.dart", ".generated.ts")
BINARY_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".jar", ".woff", ".woff2",
    ".ttf", ".otf", ".mp4", ".mov", ".exe", ".dll", ".so", ".dylib", ".xlsx", ".docx", ".pptx",
)
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
CONFIG_SUFFIXES = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env", ".xml", ".plist", ".lock")
CONFIG_NAMES = {
    "dockerfile", "makefile", ".gitignore", ".dockerignore", ".editorconfig", "requirements.txt",
    "setup.cfg", "pyproject.toml", "package.json", "tsconfig.json", "procfile",
}
LANGUAGES = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".rb": "Ruby",
    ".php": "PHP", ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C/C++", ".m": "Objective-C",
    ".scala": "Scala", ".sql": "SQL", ".sh": "Shell", ".html": "HTML", ".css": "CSS", ".scss": "CSS",
    ".vue": "Vue", ".svelte": "Svelte", ".dart": "Dart", ".r": "R", ".ipynb": "Jupyter",
}
CATEGORIES = ("source", "tests", "config", "docs")


def categorize(path: str) -> str:
    """One of: source, tests, config, docs, excluded."""
    p = path.lower()
    name = PurePosixPath(p).name
    if (name in LOCKFILES or p.endswith(EXCLUDED_SUFFIXES) or p.endswith(BINARY_SUFFIXES)
            or any(f"/{d}" in f"/{p}" for d in EXCLUDED_DIRS)
            or "generated" in name):
        return "excluded"
    if (re.search(r"(^|/)(tests?|__tests__|spec|specs|testdata|fixtures)/", p)
            or re.search(r"(^test_|_test\.|\.test\.|\.spec\.|_spec\.|tests?\.py$)", name)):
        return "tests"
    if p.endswith(DOC_SUFFIXES) or p.startswith("docs/"):
        return "docs"
    if name in CONFIG_NAMES or p.endswith(CONFIG_SUFFIXES) or p.startswith((".github/", ".circleci/")):
        return "config"
    return "source"


def split_diff(diff: str) -> dict[str, dict]:
    """Split a unified diff into {path: {"text": ..., "change": new|deleted|renamed|modified}}."""
    files: dict[str, dict] = {}
    current = None
    for chunk in re.split(r"(?m)^(?=diff --git )", diff):
        if not chunk.startswith("diff --git "):
            continue
        header = chunk.split("\n", 1)[0]
        m = re.match(r"diff --git a/(.+?) b/(.+)$", header)
        if not m:
            continue
        current = m.group(2)
        change = "modified"
        head = chunk[:600]
        if "\nnew file mode" in head:
            change = "new"
        elif "\ndeleted file mode" in head:
            change = "deleted"
        elif "\nrename from" in head:
            change = "renamed"
        files[current] = {"text": chunk, "change": change}
    return files


def compute_metrics(files: list[dict], diff: str = "") -> dict:
    """files: [{"path", "additions", "deletions"}] from `gh pr view --json files`."""
    parts = split_diff(diff) if diff else {}
    by_cat = {c: {"files": 0, "additions": 0, "deletions": 0} for c in CATEGORIES}
    excluded: list[str] = []
    changes = Counter()
    dirs: set[str] = set()
    langs = Counter()
    file_rows = []
    for f in files:
        path = f.get("path", "")
        adds, dels = int(f.get("additions", 0)), int(f.get("deletions", 0))
        cat = categorize(path)
        change = parts.get(path, {}).get("change", "modified")
        file_rows.append({"path": path, "additions": adds, "deletions": dels, "category": cat, "change": change})
        if cat == "excluded":
            excluded.append(path)
            continue
        by_cat[cat]["files"] += 1
        by_cat[cat]["additions"] += adds
        by_cat[cat]["deletions"] += dels
        changes[change] += 1
        parent = str(PurePosixPath(path).parent)
        dirs.add("" if parent == "." else parent)
        lang = LANGUAGES.get(PurePosixPath(path).suffix.lower())
        if lang and cat in ("source", "tests"):
            langs[lang] += adds + dels
    counted = [r for r in file_rows if r["category"] != "excluded"]
    return {
        "by_category": by_cat,
        "total_additions": sum(r["additions"] for r in counted),
        "total_deletions": sum(r["deletions"] for r in counted),
        "files_counted": len(counted),
        "change_types": {k: changes.get(k, 0) for k in ("new", "modified", "deleted", "renamed")},
        "directories_touched": len(dirs),
        "top_level_areas": sorted({d.split("/")[0] for d in dirs if d}),
        "languages": [lang for lang, _ in langs.most_common()],
        "excluded_files": excluded,
        "files": file_rows,
    }
