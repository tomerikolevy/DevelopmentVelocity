#!/bin/zsh
# Double-click in Finder to sync your merged PRs and open the dashboard.
# Scoring is not run here (it costs API credits); run `velocity score` for that.

cd "$(dirname "$0")" || exit 1

if [[ ! -x .venv/bin/python ]]; then
  echo "Setting up the Python environment..."
  python3 -m venv .venv && .venv/bin/pip install -q anthropic pytest || { echo "Setup failed."; read -k1 "?Press any key to close."; exit 1; }
fi

if [[ -z "$ANTHROPIC_API_KEY_COMPLEXITY_SCORER" ]]; then
  echo "Note: ANTHROPIC_API_KEY_COMPLEXITY_SCORER is not set in this shell."
  echo "      Rescore and Calibrate analysis need it (export it in ~/.zshrc)."
  echo
fi

echo "Syncing merged PRs from GitHub..."
.venv/bin/python -m velocity sync || echo "Sync failed; showing the data already on disk."

echo
echo "Starting the dashboard (Ctrl+C to stop)..."
.venv/bin/python -m velocity serve
