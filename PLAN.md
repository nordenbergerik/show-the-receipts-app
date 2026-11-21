# Show the Receipts: utvecklingsplan (revisorkonceptet)

> **Kärnidé:** vi är en *revisor*, ingen scribe. Indata är **ett samtal + en journalanteckning från vilken källa som helst** (vår AI, en annan AI eller läkaren själv). Utdata är en granskning där varje påstående har ett kvitto, felen flaggas och tillägg föreslås med motivering.

---

## 1. Huvudanvändningsfall

1. Läkaren spelar in patientsamtalet som ett röstmemo.
2. Efter besöket skriver läkaren **korta anteckningar**, ofta med förkortningar (t.ex. `bröstsmärta 2d, ej utstrålning, EKG ua`).
3. Tjänsten granskar anteckningen mot samtalet och
   - **länkar** varje påstående till det ställe i samtalet där det sades,
   - **flaggar fel**: påståenden som motsägs av samtalet eller saknar grund,
   - **föreslår tillägg**: viktiga saker som sades men inte kom med, och *varför* de bör med,
   - **föreslår uppföljning**: frågor som borde ha ställts eller saker som bör kollas upp.

Att anteckningen är kort är **inget fel**. Tillägg är förslag rangordnade efter klinisk vikt, inte en lista över allt som sades.

---

## 2. Moduler och prioritet

| Modul | Vad | Prioritet |
|---|---|---|
| **A. Kvittokontroll** | Delar upp anteckningen i påståenden, bedömer varje påstående mot samtalet och länkar till källraderna | **Måste (kärnan)** |
| **B. Tillägg** | Kliniskt viktiga uppgifter i samtalet som saknas i anteckningen, med motivering och kvitto | **Måste** |
| **C. Missade frågor** | Frågor som borde ha ställts eller följts upp, utifrån huvudbesvär och samtal | Bör |
| **D. Journalutkast** | Genererar ett utkast från samtalet och kör det genom samma revisor (vi granskar vår egen AI) | Bör |
| **E. Röst till text** | Spela in ljud och transkribera till samtalstext | Kan (se §6) |

---

## 3. Pipeline (granskning)

```
samtal (text) ──► [1] radindexering ─┐
                                     ├─► [3] bedömning per påstående ──► [4] deterministisk kvittokontroll ─┐
anteckning ─────► [2] påståenden ────┘                                                                      ├─► svar till UI
                                      └─► [5] tillägg + missade frågor (parallellt) ──► [4] ──────────────────┘
```

1. **Radindexering (deterministisk):** samtalet delas per talartur och numreras: `[01] [doctor] ...`.
2. **Påståenden:** LLM:en delar anteckningen i atomära påståenden. Varje påstående återger *exakt* den textbit i anteckningen det kommer från, så att backend kan hitta positionen (`str.find`) och markera den i UI:t.
3. **Bedömning:** för varje påstående returnerar LLM:en
   - `status`, en av fyra:
     - `supported`: sades i samtalet
     - `inferred`: en rimlig klinisk slutsats av det som sades
     - `contradicted`: samtalet säger något annat
     - `unsupported`: grund saknas
   - `evidence`: rad-ID **plus ett exakt citat** ur raden
   - `explanation`: en kort motivering
4. **Deterministisk kvittokontroll (vår guardrail):** rad-ID:t måste finnas, och citatet måste finnas i den citerade raden (exakt matchning, eller fuzzy ≥ 90 för att tåla skiljetecken). Klarar kvittot inte kontrollen sänks statusen till `unsupported`, med anteckningen "kvittot kunde inte bekräftas". **Modellen kan alltså inte fejka ett kvitto.**
5. **Tillägg och missade frågor:** raderna som inget påstående citerar blir kandidater. LLM:en väljer ut det kliniskt viktiga, med kvitto och ett "varför". Missade frågor utgår från huvudbesväret.

**Varför LLM-bedömning och inte bara rapidfuzz?** Läkarens korta anteckningar med förkortningar matchar dåligt lexikalt, och fuzzy-matchning ser inte negation: "har bröstsmärta" mot "nej, ingen bröstsmärta" ger nästan full poäng. Fuzzy-matchningen används därför bara i steg 4 för att kontrollera att *citaten* är äkta.

### LLM
- Modell: `claude-opus-5-5` med **structured outputs** (`client.messages.parse()` med Pydantic-scheman). Guidens `claude-3-5-sonnet-latest` och tvingade `tool_choice` fungerar inte längre.
- Två anrop parallellt: (påståenden + bedömning) och (tillägg + missade frågor).
- Demosvaren förberäknas och cachas, så att demon inte är beroende av nätverk och latens.

---

## 4. API-kontrakt (låses vid lunch)

`POST /audit`, body: `{ "encounter_id": "D2N042" }` eller `{ "transcript_text": "...", "note_text": "..." }`

```json
{
  "transcript": [{ "line_id": 4, "speaker": "patient", "text": "..." }],
  "note_text": "bröstsmärta 2d, ej utstrålning ...",
  "claims": [{
    "claim_id": "C1",
    "note_span": { "start": 0, "end": 15 },
    "text": "Bröstsmärta i två dagar",
    "status": "supported | inferred | contradicted | unsupported",
    "evidence": [{ "line_id": 4, "quote": "it started two days ago" }],
    "explanation": "..."
  }],
  "suggested_additions": [{
    "text": "Smärtan strålar ut i vänster arm",
    "why": "Utstrålning är ett varningstecken vid bröstsmärta",
    "priority": "high | medium | low",
    "evidence": [{ "line_id": 12, "quote": "..." }]
  }],
  "follow_up_questions": [{ "category": "Red flags", "question": "...", "why": "..." }],
  "summary": { "supported": 6, "inferred": 2, "contradicted": 1, "unsupported": 1 }
}
```

Övriga endpoints: `GET /encounters`, `GET /encounters/{id}` (samtal + referensanteckning), `POST /generate` (modul D), `POST /transcribe` (modul E, valfri).

---

## 5. UI

- **Vänster panel:** anteckningen. Läkaren kan klistra in eller skriva en egen. Påståendena färgas grönt (stöds), blått (slutsats), rött (motsägs) och gult (saknar grund).
- **Höger panel:** samtalet med radnummer.
- **Klick på ett påstående** markerar källraderna och scrollar dit. Klick på en rad visar vilka påståenden som använder den.
- **Sidopanel:** "Förslag på tillägg" och "Följ upp", var och en med *varför* och kvitto.
- **Lägesväxling:** "Granska min anteckning" eller "Generera utkast" (det senare granskas också).
- **Demoknepet:** fyll i en anteckning med ett inplanterat fel och visa hur det fångas.

---

## 6. Ljud: hur röstmemot passar in

Claude-API:t tar inte emot ljud, så röstmemot kräver ett **separat steg för taligenkänning** före granskningen. Vi bygger det som ett utbytbart steg (`asr.py`):
- **Hackathon:** huvuddemon körs på ACI-Bench-transkriptionerna, som redan är text. ACI-Bench har också råa ASR-versioner, så vi kan visa att granskningen tål taligenkänningsfel.
- **Om tid finns:** spela in i webbläsaren och transkribera lokalt med en öppen Whisper-modell, eller med AWS Transcribe via partnerkrediterna.
- **Riktig produkt:** svensk taligenkänning (t.ex. en svensktränad Whisper) och talaridentifiering (läkare/patient). ACI-Bench är på engelska, så demon blir på engelska.

---

## 7. Utvärdering (det som gör pitchen trovärdig)

1. **Test med inplanterade fel:** vi tar referensanteckningar från `valid`, planterar kända fel och mäter om revisorn hittar dem:
   - vänd en negation
   - ändra en siffra, dos eller sida (höger/vänster)
   - lägg till ett påhittat fynd
   - stryk ett viktigt fynd (ska dyka upp som tillägg)

   Mått: **upptäckta fel (recall)** och **falsklarm** på oförändrade anteckningar.
2. **Korta läkaranteckningar:** vi låter LLM:en korta ned referensanteckningar till stödord med förkortningar och kontrollerar att korthet inte ger falsklarm.
3. **Pitchsiffran:** vi kör revisorn på experternas egna anteckningar och redovisar andelen meningar utan stöd i samtalet.

---

## 8. Repostruktur

```
show-the-receipts/
├── backend/
│   ├── app.py          # FastAPI-endpoints
│   ├── transcript.py   # inläsning och radindexering
│   ├── audit.py        # påståenden, bedömning, kvittokontroll (modul A)
│   ├── coverage.py     # tillägg och missade frågor (B, C)
│   ├── generate.py     # journalutkast (D)
│   ├── asr.py          # taligenkänning, valfri (E)
│   ├── llm.py          # Claude-klient och promptar
│   ├── schemas.py      # Pydantic-kontrakt
│   └── requirements.txt
├── frontend/           # index.html, app.js, style.css
├── eval/               # felinjicering, körning av utvärdering, granskning av referensanteckningar
├── cache/              # förberäknade demosvar
├── data/               # ACI-Bench (gitignorerad)
├── .env.example
└── README.md           # körinstruktioner + attribuering (Yim m.fl., 2023, CC BY 4.0)
```

---

## 9. Tidsplan (09:00–18:00)

| Tid | Block | Klart när |
|---|---|---|
| Före start | Data lokalt, API-nyckel i `.env`, välj 3–4 demo-encounters | Ett testanrop fungerar |
| 09–10 | Grundstruktur, `transcript.py`, `schemas.py`, `llm.py` | Indexerat samtal och svar från Claude |
| 10–13 | Modul A + B, `/audit`, kvittokontroll | Korrekt granskning av en anteckning, end-to-end |
| 13:00 | **Kontraktet låses** | Frontend bygger mot exempel-JSON |
| 13:45–16 | UI (paneler, klick-markering, färger, sidopanel), modul C | Demoflödet fungerar i webbläsaren |
| 16–17:30 | Utvärdering med inplanterade fel, pitchsiffran, modul D, cache, eventuellt E | Siffror till pitchen och en demo som fungerar offline |
| 17:30–18 | Inlämning, attribuering, pitchrepetition | Inlämnat |

---

## 10. Säkerhet och integritet

- **Hackathon:** endast syntetisk data (ACI-Bench). API-nyckeln ligger i `.env`, som gitignoreras. Inga samtal eller anteckningar loggas. CORS begränsas till den lokala frontenden. Indata valideras (storleksgränser).
- **Promptinjektion:** samtals- och anteckningstext behandlas som data i promptarna och aldrig som instruktioner. Kvittokontrollen i steg 4 begränsar dessutom vad modellen kan hävda.
- **Riktig produkt:** GDPR och patientdatalagen, ljud raderas efter transkribering, behandling inom EU, åtkomstloggning och revisionsspår. Modul C kan räknas som kliniskt beslutsstöd och därmed bli en medicinteknisk produkt enligt MDR, vilket måste utredas.
