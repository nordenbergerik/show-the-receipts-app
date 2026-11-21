"""Inställningar från miljövariabler (.env lokalt, miljövariabler på Render)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MAX_NOTE_CHARS = 10_000
MAX_TRANSCRIPT_CHARS = 100_000


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model: str
    effort: str
    use_fallbacks: bool
    demo_offline: bool
    access_code: str | None
    cache_dir: Path
    frontend_dir: Path
    replay: bool
    recordings_dir: Path
    effort_overrides: dict[str, str]

    def effort_for(self, task: str) -> str:
        """Effort för ett AI-steg: CLAUDE_EFFORT_<STEG> om satt, annars CLAUDE_EFFORT."""
        return self.effort_overrides.get(task, self.effort)

    @property
    def model_label(self) -> str:
        """Det som visas och ingår i cache-nyckeln: inspelade svar får aldrig
        förväxlas med (eller cachas som) riktiga svar från modellen."""
        return "recorded" if self.replay else self.model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        model=os.getenv("CLAUDE_MODEL", "claude-fable-5-2"),
        effort=os.getenv("CLAUDE_EFFORT", "high"),
        use_fallbacks=_flag("CLAUDE_FALLBACKS", "1"),
        demo_offline=_flag("DEMO_OFFLINE"),
        access_code=os.getenv("ACCESS_CODE") or None,
        cache_dir=ROOT / "cache",
        frontend_dir=ROOT / "frontend",
        replay=_replay_mode(),
        recordings_dir=ROOT / "demo" / "recordings",
        effort_overrides=_effort_overrides(),
    )


# AI-stegen och deras namn i miljövariabler, t.ex. CLAUDE_EFFORT_CLAIMS=medium
EFFORT_TASKS = {
    "ClaimsLLM": "CLAIMS",
    "CoverageLLM": "COVERAGE",
    "DecisionsLLM": "DECISIONS",
    "VisitCheckLLM": "VISIT_CHECK",
    "DraftNoteLLM": "DRAFT",
    "AskLLM": "ASK",
}
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


def _effort_overrides() -> dict[str, str]:
    out = {}
    for task, name in EFFORT_TASKS.items():
        value = os.getenv(f"CLAUDE_EFFORT_{name}", "").strip().lower()
        if value in EFFORT_LEVELS:
            out[task] = value
    return out


def _replay_mode() -> bool:
    """DEMO_REPLAY=1 tvingar fram inspelade svar, 0 stänger av dem. Standard
    (auto): inspelade svar används när ingen API-nyckel finns."""
    mode = os.getenv("DEMO_REPLAY", "auto").strip().lower()
    if mode in {"1", "true", "yes", "on"}:
        return True
    if mode in {"0", "false", "no", "off"}:
        return False
    return not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
