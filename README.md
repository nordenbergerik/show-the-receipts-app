# Show the Receipts

**Kvitto på varje rad i journalen.**

AI-scribes och stressade läkare skriver journaler snabbt, men ingen kan snabbt bevisa var en mening kommer ifrån. Show the Receipts är ett **säkerhetslager** som granskar en journalanteckning mot det inspelade patientsamtalet, oavsett om läkaren, vår AI eller någon annans AI skrev den:

- **Kvittokontroll:** varje påstående länkas till den rad i samtalet där det sades. Det som motsägs av samtalet eller saknar grund flaggas.
- **Tillägg med ett klick:** viktiga saker som sades men aldrig skrevs ner föreslås med kvitto och motivering.
- **Missade frågor:** frågor som borde ha ställts eller följts upp.

> *Vi skriver inte journalen åt läkaren. Vi gör att läkaren kan skriva mindre och ändå lita på journalen.*

Byggt för Hacking Healthcare Hackathon (Track 1). Planer: [PLAN.md](PLAN.md) · Arkitektur: [ARCHITECTURE.md](ARCHITECTURE.md)

---

## Vad appen gör

| Steg | Vad AI:n gör |
|---|---|
| **Visit check**, direkt efter samtalet | Frågor som inte ställdes och en andra blick på besluten. Läkaren kryssar i det hen vill ta med. |
| **Note** | Ett journalutkast från samtalet, eller läkarens egen anteckning. Redigeras direkt i dokumentet. |
| **Note check** | Kvitto på varje mening, fel som går att rätta med ett klick, det som saknas, och en andra blick på besluten. |
| **Ask about this patient** | Söker belägg i journalen för en misstanke, med kvitton och en tydlig varning om att AI kan ha fel. |

Varje AI-punkt kan kvitteras (✓) eller avfärdas (×) med ett klick.

## Demo

Öppna http://localhost:8000/app/ och välj ett exempel under **Try an example**, eller ladda upp filerna i [demo/cases/chest-pain/](demo/cases/chest-pain/) under **New visit**:

- **Hela flödet:** ladda upp bara `transcript.txt` och kör Visit check → Draft → Check note.
- **Tillägget:** ladda upp `transcript.txt` och `note-wrong.txt` (eller `note-correct.txt`) och gå direkt till Check note.

Utan API-nyckel svarar appen med **inspelade AI-svar** på exakt de förfrågningar den skickar (märkt *Demo mode* uppe till höger). Svaren togs fram av Claude-agenter med samma promptar och scheman, och går genom samma kvittokontroll som riktiga svar. Med `ANTHROPIC_API_KEY` i `.env` används Claude-API:t i stället. Inspelningarna görs med `python -m demo.record` (se filen).

## Status

| Del | Status |
|---|---|
| Datalager, kvittokontroll, granskning, andra blick, besökskontroll, utkast, frågor till journalen | Klart och testat (falsk LLM och inspelade svar) |
| Landningssida, patientlista, uppladdning, stegflöde, redigerbar journal | Klart |
| Inloggning | Platshållare (`backend/auth.py`) |
| Riktiga API-anrop | Inte testat än (nyckel saknas) |
| Driftsättning (Render) | Pågår |

## Kom igång

Kräver Python 3.12 eller senare.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
# .venv/bin/python -m pip install -r backend/requirements.txt     # macOS/Linux
cp .env.example .env    # fyll i ANTHROPIC_API_KEY
```

Starta appen och öppna http://localhost:8000:

```bash
.venv/Scripts/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Bygg om de simulerade granskningarna efter ändringar i `demo/llm/` eller i kvittokontrollen:

```bash
.venv/Scripts/python -m demo.make_samples
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
