"""Filbaserad cache för granskningar. Demosvaren committas så att demon fungerar offline."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from backend.schemas import AuditResult


def audit_key(model: str, prompt_version: str, rendered_transcript: str, note_text: str) -> str:
    h = hashlib.sha256()
    for part in (model, prompt_version, rendered_transcript, note_text.strip()):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class AuditCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory / "audits"

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> AuditResult | None:
        path = self._path(key)
        if not path.exists():
            return None
        return AuditResult.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, key: str, result: AuditResult) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        data = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=1)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, self._path(key))
