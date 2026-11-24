"""Builds the per-PR scoring input sent to Claude (the rubric is the system prompt)."""
from __future__ import annotations

from .metrics import split_diff

BODY_LIMIT = 2000
COMMIT_LIMIT = 30
DIFF_ORDER = {"source": 0, "config": 1, "tests": 2, "docs": 3}


def truncate_file_diff(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.rstrip("\n")
    return "\n".join(lines[:max_lines]) + f"\n[truncated: {len(lines) - max_lines} more lines in this file]"


def build_diff_section(diff: str, files: list[dict], max_chars: int, max_lines: int) -> str:
    """Diff limited to counted files, ordered source -> config -> tests -> docs, within a char budget."""
    if diff.startswith("[diff unavailable"):
        return diff + "\nJudge from the file list and metrics."
    parts = split_diff(diff)
    ordered = sorted(
        (f for f in files if f["category"] != "excluded" and f["path"] in parts),
        key=lambda f: (DIFF_ORDER.get(f["category"], 9), -(f["additions"] + f["deletions"])),
    )
    out, used, omitted = [], 0, []
    for f in ordered:
        chunk = truncate_file_diff(parts[f["path"]]["text"], max_lines)
        if used + len(chunk) > max_chars:
            remaining = max_chars - used
            if remaining > 2000 and not omitted:
                chunk = chunk[:remaining] + "\n[truncated: diff budget reached]"
                out.append(chunk)
                used += len(chunk)
            else:
                omitted.append(f)
            continue
        out.append(chunk)
        used += len(chunk)
    if omitted:
        out.append("[omitted from diff due to size budget: " + ", ".join(
            f"{f['path']} (+{f['additions']}/-{f['deletions']})" for f in omitted) + "]")
    return "\n".join(out) if out else "[no diff content for counted files]"


def build_user_message(pr: dict, diff: str, max_chars: int = 40000, max_lines: int = 300) -> str:
    m = pr["metrics"]
    body = (pr.get("body") or "").strip()
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT] + " [truncated]"
    commits = pr.get("commits", [])[:COMMIT_LIMIT]

    cat_lines = "\n".join(
        f"- {cat}: {v['files']} files, +{v['additions']} / -{v['deletions']}"
        for cat, v in m["by_category"].items()
    )
    ct = m["change_types"]
    excluded = ", ".join(m["excluded_files"]) or "none"
    file_lines = "\n".join(
        f"- {f['path']}  +{f['additions']}/-{f['deletions']}  [{f['category']}, {f['change']}]"
        for f in m["files"]
    ) or "- (no files)"

    return f"""<pr>
## Repository
{pr['repo']} — {pr.get('repo_description') or 'no description'} (primary language: {pr.get('repo_language') or 'unknown'})

## Title
{pr['title']}

## Description
{body or '(empty)'}

## Commit headlines
{chr(10).join('- ' + c for c in commits) or '- (none)'}

## Metrics (excluded files not counted)
{cat_lines}
- change types: {ct['new']} new, {ct['modified']} modified, {ct['deleted']} deleted, {ct['renamed']} renamed
- directories touched: {m['directories_touched']}; top-level areas: {', '.join(m['top_level_areas']) or 'root'}
- languages: {', '.join(m['languages']) or 'n/a'}
- excluded (generated/lockfile/vendored/binary): {excluded}

## Files
{file_lines}

## Diff
{build_diff_section(diff, m['files'], max_chars, max_lines)}
</pr>

Rate this pull request's complexity using the rubric."""
