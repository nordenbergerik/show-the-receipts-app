"""Filbaserad cache för granskningar: samma journal mot samma besök ger samma
svar direkt, utan nya API-anrop. Cachen ligger i cache/audits/ (gitignorerad)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import pydantic

from backend.schemas import AuditResult

log = logging.getLogger("receipts.cache")


def audit_key(*, model: str, efforts: dict[str, str], prompt_version: str, content: list[dict[str, Any]]) -> str:
    """Nyckeln bygger på exakt det som skickas till modellen (samtal, huvudbesvär
    och journal), plus modell, effort per steg och promptversion. Ändras något
    av dem blir det ett nytt svar i stället för ett gammalt ur cachen."""
    payload = json.dumps({"model": model, "efforts": efforts, "prompt_version": prompt_version, "content": content},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory / "audits"

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> AuditResult | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return AuditResult.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, pydantic.ValidationError):
            # trasig eller gammal fil: räkna som en miss och gör en ny granskning
            log.warning("ignoring unreadable cache entry %s", path.name)
            return None

    def put(self, key: str, result: AuditResult) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        data = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, self._path(key))
