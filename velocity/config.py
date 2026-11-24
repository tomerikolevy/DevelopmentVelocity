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


@dataclass
class Config:
    author: str = "@me"
    since: str = ""
    exclude_repos: list[str] = field(default_factory=list)
    label_prefix: str = "complexity:"
    model: str = "claude-opus-5-5"
    effort: str = "medium"
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
        model=scoring.get("model", "claude-opus-5-5"),
        effort=scoring.get("effort", "medium"),
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
