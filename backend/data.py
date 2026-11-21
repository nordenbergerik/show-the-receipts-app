"""Inläsning av ACI-Bench (Yim m.fl., 2023, CC BY 4.0).

Struktur på disk (data/aci-bench-2023/aci-bench-corpus/):
  challenge_data/{split}.csv            dataset, encounter_id, dialogue, note
  challenge_data/{split}_metadata.csv   encounter_id, id, patient_*, cc, ...
  src_experiment_data/{kort split}_{aci|virtscribe}_{asr|asrcorr|humantrans}.csv
                                        dataset, id, dialogue, note
ASR-filerna har nyckeln `id` (t.ex. ACI110) i stället för `encounter_id`.
Kopplingen sker via metadata-filerna i challenge_data.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent.parent / "data" / "aci-bench-2023" / "aci-bench-corpus"

# Splits i challenge_data och deras korta namn i src_experiment_data.
SPLITS: dict[str, str] = {
    "train": "train",
    "valid": "valid",
    "clinicalnlp_taskB_test1": "test1",
    "clinicalnlp_taskC_test2": "test2",
    "clef_taskC_test3": "test3",
}

# Samtalsvarianter. "clean" är den transkription som finns i challenge_data.
VARIANTS: dict[str, str] = {
    "clean": "Transcript",
    "asr": "Raw speech-to-text",
    "asrcorr": "Corrected speech-to-text",
    "humantrans": "Manual transcript",
}

_SRC_FILE_RE = re.compile(r"^(?P<split>[a-z0-9]+)_(?P<dataset>aci|virtscribe)_(?P<variant>asr|asrcorr|humantrans)\.csv$")


@dataclass
class Encounter:
    encounter_id: str
    source_id: str
    split: str
    dataset: str
    reference_note: str
    metadata: dict[str, str]
    variants: dict[str, str] = field(default_factory=dict)  # variant -> dialogue

    @property
    def chief_complaint(self) -> str:
        return self.metadata.get("cc", "").strip()

    @property
    def patient_name(self) -> str:
        first = self.metadata.get("patient_firstname", "").strip()
        last = self.metadata.get("patient_familyname", "").strip()
        return f"{first} {last}".strip().title()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


class DataStore:
    def __init__(self, corpus_dir: Path = CORPUS_DIR) -> None:
        self.corpus_dir = corpus_dir
        self.encounters: dict[str, Encounter] = {}
        self._by_source_id: dict[str, str] = {}
        self._load_challenge_data()
        self._load_src_experiment_data()

    def _load_challenge_data(self) -> None:
        challenge = self.corpus_dir / "challenge_data"
        for split in SPLITS:
            rows = _read_csv(challenge / f"{split}.csv")
            meta = {m["encounter_id"]: m for m in _read_csv(challenge / f"{split}_metadata.csv")}
            for row in rows:
                eid = row["encounter_id"]
                if eid in self.encounters:
                    raise ValueError(f"Dubblett av encounter_id {eid}")
                m = meta.get(eid, {})
                enc = Encounter(
                    encounter_id=eid,
                    source_id=m.get("id", ""),
                    split=split,
                    dataset=row["dataset"],
                    reference_note=row["note"],
                    metadata=m,
                    variants={"clean": row["dialogue"]},
                )
                self.encounters[eid] = enc
                if enc.source_id:
                    self._by_source_id[enc.source_id] = eid

    def _load_src_experiment_data(self) -> None:
        src = self.corpus_dir / "src_experiment_data"
        for path in sorted(src.glob("*.csv")):
            match = _SRC_FILE_RE.match(path.name)
            if not match:
                continue  # metadata-filer m.m.
            for row in _read_csv(path):
                eid = self._by_source_id.get(row["id"])
                if eid is None:
                    raise ValueError(f"{path.name}: id {row['id']} saknar motsvarighet i challenge_data")
                self.encounters[eid].variants[match["variant"]] = row["dialogue"]

    def get(self, encounter_id: str) -> Encounter | None:
        return self.encounters.get(encounter_id)

    def list(self) -> list[Encounter]:
        return sorted(self.encounters.values(), key=lambda e: e.encounter_id)


@lru_cache(maxsize=1)
def get_store() -> DataStore:
    return DataStore()
