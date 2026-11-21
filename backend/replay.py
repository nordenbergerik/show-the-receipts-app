"""Inspelade svar: demon fungerar utan API-nyckel.

En inspelning är ett svar på exakt den förfrågan appen skickar till Claude-API:t
(samma innehåll, samma schema), framtaget i förväg. När uppspelning är aktiv
(ingen API-nyckel, eller DEMO_REPLAY=1) svarar ReplayLLM med inspelningen om
en sådan finns, och annars med ett tydligt fel. Svaren går därefter genom
samma kvittokontroll som riktiga svar.

Nyckeln är en hash av svarsformatet och innehållet, utom delar som inte ska
påverka träffen:
- rubriken "Chief complaint: ..." (användaren skriver den själv),
- <accepted_points> (vilka punkter läkaren valt i besökskontrollen),
- för frågor till journalen även <note> (anteckningen ändras under demon).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.llm import LLMError

T = TypeVar("T", bound=BaseModel)

_CC_RE = re.compile(r"^Chief complaint:.*?\n\n", re.S)
NOT_RECORDED = (
    "This demo only has recorded AI answers for the example files. "
    "Add an API key to review other visits and notes."
)


def replay_key(format_name: str, content: list[dict[str, Any]]) -> str:
    parts = [format_name]
    for block in content:
        text = block.get("text", "")
        if text.startswith("<accepted_points>"):
            continue
        if format_name == "AskLLM" and text.startswith("<note>"):
            continue
        parts.append(_CC_RE.sub("", text, count=1).strip())
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:24]


class ReplayLLM:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, format_name: str, content: list[dict[str, Any]]) -> Path:
        return self.directory / f"{replay_key(format_name, content)}.json"

    async def parse(self, *, system: str, content: list[dict[str, Any]], output_format: type[T]) -> T:
        path = self.path_for(output_format.__name__, content)
        if not path.exists():
            raise LLMError(NOT_RECORDED, 503)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return output_format.model_validate(data["response"])
        except (json.JSONDecodeError, KeyError, ValidationError) as exc:
            raise LLMError("A recorded answer is damaged and could not be used.", 502) from exc

    def save(self, format_name: str, content: list[dict[str, Any]], response: dict, source: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(format_name, content)
        path.write_text(json.dumps({"format": format_name, "source": source, "response": response}, ensure_ascii=False, indent=1), encoding="utf-8")
        return path
