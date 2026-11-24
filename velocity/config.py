"""Loads config.toml and exposes project paths."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
DATA_DIR = ROOT / "data"
RUBRIC_DIR = ROOT / "rubric"
WEB_DIR = Path(__file__).resolve().parent / "web"

# Choices offered in the dashboard. Default: Opus 5.5 at medium effort.
MODELS = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5"},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5"},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5"},
]
EFFORTS = ["low", "medium", "high", "max"]
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"


@dataclass
class Config:
    author: str = "@me"
    since: str = ""
    exclude_repos: list[str] = field(default_factory=list)
    label_prefix: str = "complexity:"
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    api_key_env: str = "ANTHROPIC_API_KEY_COMPLEXITY_SCORER"
    rubric_version: int = 1
    max_diff_chars: int = 40000
    max_lines_per_file: int = 300
    port: int = 8787


def load_config(path: Path = CONFIG_PATH) -> Config:
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    scoring = raw.get("scoring", {})
    server = raw.get("server", {})
    return Config(
        author=raw.get("author", "@me"),
        since=raw.get("since", ""),
        exclude_repos=list(raw.get("exclude_repos", [])),
        label_prefix=raw.get("label_prefix", "complexity:"),
        model=scoring.get("model", DEFAULT_MODEL),
        effort=scoring.get("effort", DEFAULT_EFFORT),
        api_key_env=scoring.get("api_key_env", "ANTHROPIC_API_KEY_COMPLEXITY_SCORER"),
        rubric_version=int(scoring.get("rubric_version", 1)),
        max_diff_chars=int(scoring.get("max_diff_chars", 40000)),
        max_lines_per_file=int(scoring.get("max_lines_per_file", 300)),
        port=int(server.get("port", 8787)),
    )


def set_rubric_version(version: int, path: Path = CONFIG_PATH) -> None:
    """Rewrite the rubric_version line in config.toml, keeping comments intact."""
    text = path.read_text()
    new, count = re.subn(r"(?m)^rubric_version\s*=\s*\d+", f"rubric_version = {version}", text)
    if count != 1:
        raise RuntimeError("config.toml must contain exactly one 'rubric_version = N' line")
    path.write_text(new)


def set_scoring(model: str, effort: str, path: Path = CONFIG_PATH) -> None:
    """Rewrite the model and effort lines under [scoring] in config.toml, keeping comments intact."""
    if model not in {m["id"] for m in MODELS}:
        raise ValueError(f"Unknown model: {model}")
    if effort not in EFFORTS:
        raise ValueError(f"Unknown effort: {effort}")
    text = path.read_text()
    for name, value in (("model", model), ("effort", effort)):
        text, count = re.subn(rf'(?m)^{name}\s*=\s*"[^"]*"', f'{name} = "{value}"', text)
        if count != 1:
            raise RuntimeError(f"config.toml must contain exactly one '{name} = \"...\"' line")
    path.write_text(text)
