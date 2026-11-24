"""Command line: python -m velocity <sync|score|rescore|labels|serve|report|all>."""
from __future__ import annotations

import argparse
import sys

from .config import ROOT, load_config
from .github import GitHub
from .pipeline import diff_report, format_report, pending_labels, run_scoring, select_targets, write_labels
from .store import Store


def _progress(i: int, total: int, key: str, msg: str) -> None:
    print(f"  [{i}/{total}] {key}: {msg}", flush=True)


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"{question} -> no (non-interactive; pass --yes to apply)")
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _score(args, cfg, store, mode: str) -> None:
    from .scorer import ClaudeScorer, ScoringError

    prs = store.load_prs()
    if not prs:
        print("No PRs in the local store. Run `python -m velocity sync` first.")
        return
    keys = select_targets(prs, mode, cfg.rubric_version, repo=args.repo,
                          since=getattr(args, "since", None), limit=args.limit)
    if not keys:
        print(f"Nothing to score: every PR is already at rubric v{cfg.rubric_version}.")
    else:
        print(f"Scoring {len(keys)} PRs with rubric v{cfg.rubric_version} ({cfg.model})...")
        try:
            scorer = ClaudeScorer(cfg)
        except ScoringError as e:
            sys.exit(f"Error: {e}")
        run = run_scoring(keys, scorer, store, cfg.model, workers=args.workers, progress=_progress)
        print()
        print(format_report(diff_report(run)))
    if args.dry_run:
        print("\nDry run: GitHub labels were not changed.")
        return
    include_manual = getattr(args, "include_manual", False)
    todo = pending_labels(store.load_prs(), include_manual)
    if not todo:
        print("\nNo labels to update.")
        return
    if _confirm(f"\nWrite complexity labels to {len(todo)} PRs on GitHub?", args.yes):
        write_labels(todo, cfg, store, GitHub(), include_manual)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="velocity", description="Complexity-weighted development velocity")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync", help="fetch merged PRs and current labels from GitHub")

    for name, help_text in (("score", "score PRs that were never scored"),
                            ("rescore", "re-score PRs scored with an older rubric")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--repo", help="only this repo (owner/name)")
        sp.add_argument("--limit", type=int, help="at most N PRs (newest first)")
        sp.add_argument("--dry-run", action="store_true", help="score but do not write GitHub labels")
        sp.add_argument("--yes", action="store_true", help="write labels without asking")
        sp.add_argument("--workers", type=int, default=4, help="parallel Claude requests")
        if name == "rescore":
            sp.add_argument("--all", action="store_true", help="re-score every PR, not only outdated ones")
            sp.add_argument("--since", help="only PRs merged on/after YYYY-MM-DD")
            sp.add_argument("--include-manual", action="store_true",
                            help="also overwrite labels you set by hand")

    lp = sub.add_parser("labels", help="write pending complexity labels to GitHub")
    lp.add_argument("--include-manual", action="store_true")
    lp.add_argument("--yes", action="store_true")

    sv = sub.add_parser("serve", help="run the dashboard at http://localhost:<port>")
    sv.add_argument("--port", type=int)

    rp = sub.add_parser("report", help="export a static read-only dashboard.html")
    rp.add_argument("-o", "--output", default=str(ROOT / "dashboard.html"))

    sub.add_parser("all", help="sync, score new PRs (asks before labelling), export report")

    args = p.parse_args(argv)
    cfg = load_config()
    store = Store()

    if args.cmd in ("sync", "all"):
        from .sync import sync
        sync(cfg, store, GitHub())
    if args.cmd == "score":
        _score(args, cfg, store, "unscored")
    elif args.cmd == "rescore":
        _score(args, cfg, store, "all" if args.all else "outdated")
    elif args.cmd == "all":
        args.repo = args.limit = None
        args.dry_run = args.yes = False
        args.workers = 4
        _score(args, cfg, store, "unscored")
    if args.cmd == "labels":
        todo = pending_labels(store.load_prs(), args.include_manual)
        if todo and _confirm(f"Write complexity labels to {len(todo)} PRs on GitHub?", args.yes):
            write_labels(todo, cfg, store, GitHub(), args.include_manual)
        elif not todo:
            print("No labels to update.")
    if args.cmd == "serve":
        from .server import serve
        serve(cfg, store, args.port or cfg.port)
    if args.cmd in ("report", "all"):
        from .report import export_static
        out = export_static(store, cfg, getattr(args, "output", str(ROOT / "dashboard.html")))
        print(f"Wrote {out}")


if __name__ == "__main__":
    main()
