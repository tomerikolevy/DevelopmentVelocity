"""Scores a PR with Claude against the active rubric and applies results to PR records."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import RUBRIC_DIR, Config
from .prompt import build_user_message

DIMENSIONS = ("scope", "logic_depth", "novelty", "risk")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {"type": "integer"},
        "logic_depth": {"type": "integer"},
        "novelty": {"type": "integer"},
        "risk": {"type": "integer"},
        "score": {"type": "integer"},
        "summary": {"type": "string"},
        "rationale": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["scope", "logic_depth", "novelty", "risk", "score", "summary", "rationale", "confidence"],
    "additionalProperties": False,
}


class ScoringError(RuntimeError):
    pass


def rubric_path(version: int):
    return RUBRIC_DIR / f"rubric_v{version}.md"


def load_rubric(version: int) -> str:
    path = rubric_path(version)
    if not path.exists():
        raise ScoringError(f"Rubric file missing: {path}")
    return path.read_text()


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def normalize(result: dict) -> dict:
    out = {d: _clamp(result[d], 1, 5) for d in DIMENSIONS}
    out["score"] = _clamp(result["score"], 1, 10)
    out["summary"] = result["summary"].strip()
    out["rationale"] = result["rationale"].strip()
    out["confidence"] = result["confidence"]
    return out


def call_claude_json(cfg: Config, system: str, user: str, schema: dict, max_tokens: int = 16000) -> dict:
    """One structured-output request. Refusals fall back server-side to another model."""
    import anthropic  # imported lazily so tests and sync work without the SDK configured

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=cfg.model,
        max_tokens=max_tokens,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise ScoringError("Claude declined to score this request")
    if response.stop_reason == "max_tokens":
        raise ScoringError("Response hit max_tokens before finishing")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ScoringError("No text block in response")
    return json.loads(text)


def preflight(cfg: Config) -> None:
    """Fail fast (before scoring dozens of PRs) if credentials or the model name are wrong."""
    import anthropic

    try:
        anthropic.Anthropic().models.retrieve(cfg.model)
    except anthropic.AuthenticationError as e:
        raise ScoringError(f"Anthropic API rejected the credentials: {e}") from e
    except anthropic.NotFoundError as e:
        raise ScoringError(f"Model '{cfg.model}' not found; check [scoring].model in config.toml") from e
    except TypeError as e:  # raised by the SDK when no credentials are configured at all
        raise ScoringError(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY (or log in with `ant auth login`)."
        ) from e


class ClaudeScorer:
    def __init__(self, cfg: Config, rubric_version: int | None = None, check: bool = True):
        self.cfg = cfg
        self.version = rubric_version or cfg.rubric_version
        self.rubric = load_rubric(self.version)
        if check:
            preflight(cfg)

    def score(self, pr: dict, diff: str) -> dict:
        user = build_user_message(pr, diff, self.cfg.max_diff_chars, self.cfg.max_lines_per_file)
        return normalize(call_claude_json(self.cfg, self.rubric, user, SCORE_SCHEMA))


def apply_result(pr: dict, result: dict, rubric_version: int, model: str) -> None:
    """Record an LLM score on a PR, keeping the previous LLM score in score_history."""
    if pr.get("llm_score") is not None:
        pr.setdefault("score_history", []).append({
            "score": pr["llm_score"], "rubric_version": pr.get("rubric_version"),
            "model": pr.get("model"), "dimensions": pr.get("dimensions"),
            "rationale": pr.get("rationale"), "scored_at": pr.get("scored_at"),
        })
    pr["llm_score"] = result["score"]
    pr["dimensions"] = {d: result[d] for d in DIMENSIONS}
    pr["summary"] = result["summary"]
    pr["rationale"] = result["rationale"]
    pr["confidence"] = result["confidence"]
    pr["rubric_version"] = rubric_version
    pr["model"] = model
    pr["scored_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if pr.get("score_source") != "manual":
        pr["score"] = result["score"]
        pr["score_source"] = "llm"
        if pr.get("llm_label_written") != result["score"]:
            pr["label_status"] = "pending"
