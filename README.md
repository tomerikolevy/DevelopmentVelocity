# Development Velocity

Tracks development velocity as **complexity units delivered over time**. Each of your merged GitHub PRs gets a complexity score from 1 to 10, rated by Claude against a versioned rubric. The score is written to the PR as a `complexity:N` label.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install anthropic pytest
export ANTHROPIC_API_KEY_COMPLEXITY_SCORER=...   # or: ant auth login
gh auth status                      # gh must be logged in
```

Settings live in `config.toml`: author, start date, excluded repos, model, effort, the API key variable (`api_key_env`), and the active rubric version.

The key is read from `ANTHROPIC_API_KEY_COMPLEXITY_SCORER`. If that variable is unset, the SDK's usual credentials apply (`ANTHROPIC_API_KEY`, an `ant auth login` sign-in, or workload identity federation).

## Commands

```bash
.venv/bin/python -m velocity sync           # fetch merged PRs, diffs and current labels (read-only on GitHub)
.venv/bin/python -m velocity score          # score never-scored PRs, then ask before writing labels
.venv/bin/python -m velocity score --dry-run --limit 5   # try scoring on 5 PRs; no labels
.venv/bin/python -m velocity rescore        # re-score PRs scored with an older rubric
.venv/bin/python -m velocity rescore --all --dry-run     # re-score everything, report only
.venv/bin/python -m velocity labels         # write pending labels to GitHub
.venv/bin/python -m velocity serve          # dashboard + calibrate at http://localhost:8787
.venv/bin/python -m velocity report         # static read-only dashboard.html
.venv/bin/python -m velocity backup         # push data/ to its backup repo (also automatic)
```

## How scores work

- The rubric is in `rubric/rubric_vN.md`. It's the system prompt Claude scores with, and you can edit it by hand.
- Claude rates four dimensions (scope, logic depth, novelty, risk; each 1–5), then gives a 1–10 score, a summary and a rationale.
- **Manual override:** change a PR's `complexity:N` label in GitHub. The next `sync` marks that PR as `manual`, and its label is never overwritten unless you pass `--include-manual`.
- **Rescore** keeps earlier scores in `score_history`. It prints how scores and monthly totals moved before any label changes.

## Calibrate

On the dashboard's Calibrate page you see two PRs that got the same score.

- **Agree** records the pair as correctly scored.
- **Dispute** asks what's wrong and why. Claude then proposes a rubric edit, shown as a diff, with predicted scores and an estimated number of affected PRs.
- **Accept** creates `rubric_v{N+1}.md`, logs the reason in `rubric/CHANGELOG.md` and makes it the active rubric. The dashboard then offers to rescore.

## Data

`data/prs.json` holds PR records and scores, `data/calibration.json` holds reviews and proposals, and `data/diffs/` caches diffs. The code repository ignores `data/`; it's backed up separately (see Backup).

## Backup

`data/` is its own private git repository (`DevelopmentVelocity-data` on GitHub), separate from the code. It's committed and pushed automatically:

- after `sync`, `score`, `rescore`, `labels` and `all`;
- after each dashboard job (Sync & score, Rescore, writing labels) and each Calibrate action.

`python -m velocity backup` runs a backup by hand. A failed push is reported but doesn't stop anything; the next backup retries it.

**Diffs aren't backed up.** They're large (hundreds of KB per PR) and can be downloaded again, so `data/.gitignore` excludes `diffs/`. When a diff is missing, scoring downloads it again from GitHub on first use.

**Restore** on a new machine, or after losing `data/`:

```bash
gh repo clone <you>/DevelopmentVelocity-data data
```

**Set up** backup for a fresh `data/`:

```bash
cd data && printf 'diffs/\n*.tmp\n.DS_Store\n' > .gitignore && git init -b main && git add -A && git commit -m "Initial data backup"
gh repo create DevelopmentVelocity-data --private --source=. --push
```

If `data/` isn't a git repo with a remote, backup does nothing.

`scripts/demo_server.py .demo/data` serves the dashboard on a copy of the data with made-up scores, for trying the UI.

## Tests

```bash
.venv/bin/python -m pytest -q
```
