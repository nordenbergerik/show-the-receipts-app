# Show the Receipts

**Kvitto på varje rad i journalen.**

AI-scribes och stressade läkare skriver journaler snabbt, men ingen kan snabbt bevisa var en mening kommer ifrån. Show the Receipts är ett **säkerhetslager** som granskar en journalanteckning mot det inspelade patientsamtalet, oavsett om läkaren, vår AI eller någon annans AI skrev den:

- **Kvittokontroll:** varje påstående länkas till den rad i samtalet där det sades. Det som motsägs av samtalet eller saknar grund flaggas.
- **Tillägg med ett klick:** viktiga saker som sades men aldrig skrevs ner föreslås med kvitto och motivering.
- **Missade frågor:** frågor som borde ha ställts eller följts upp.

> *Vi skriver inte journalen åt läkaren. Vi gör att läkaren kan skriva mindre och ändå lita på journalen.*

Byggt för Hacking Healthcare Hackathon (Track 1). Planer: [PLAN.md](PLAN.md) · Arkitektur: [ARCHITECTURE.md](ARCHITECTURE.md)

---

## Status

| Del | Status |
|---|---|
| Datalager och radindexering (`backend/data.py`, `backend/transcript.py`) | Klart och testat |
| LLM-granskning och kvittokontroll | Pågår |
| API (`/api/audit`) | Planerat |
| Frontend | Planerat |
| Utvärdering och demosvar i cache | Planerat |
| Driftsättning (Render) | Planerat |

## Kom igång

Kräver Python 3.12 eller senare.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
# .venv/bin/python -m pip install -r backend/requirements.txt     # macOS/Linux
cp .env.example .env    # fyll i ANTHROPIC_API_KEY
```

Kör testerna:

```bash
.venv/Scripts/python -m pytest -q
```

## Data

`data/aci-bench-2023/` innehåller ACI-Bench: 207 rollspelade samtal mellan läkare och patient, med tillhörande journalanteckningar. Där finns också varianter av samma samtal som rå taligenkänning (`asr`), rättad taligenkänning (`asrcorr`) och manuell transkription (`humantrans`). Det är syntetisk data, inga riktiga patienter.

| Variant | Samtal |
|---|---|
| Transkription (`clean`) | 207 |
| Rå taligenkänning (`asr`) | 117 |
| Rättad taligenkänning (`asrcorr`) | 112 |
| Manuell transkription (`humantrans`) | 28 |

## Attribuering

Datan kommer från ACI-Bench och används under licensen CC BY 4.0:

> Yim, W., Fu, Y., Ben Abacha, A., Snider, N., Lin, T., & Yetisgen, M. (2023). *aci-bench: a Novel Ambient Clinical Intelligence Dataset for Benchmarking Automatic Visit Note Generation.* Scientific Data, 10, 586.
