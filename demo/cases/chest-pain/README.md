# Testfall: bröstsmärta (ACI-Bench D2N164, Elizabeth)

Filer att ladda upp i appen under **New visit**:

| Fil | Innehåll |
|---|---|
| `transcript.txt` | Samtalet (46 repliker), "Doctor:/Patient:"-format |
| `note-correct.txt` | Kort läkaranteckning som stämmer med samtalet |
| `note-wrong.txt` | Samma anteckning med sex inplanterade fel |

**Inplanterade fel i `note-wrong.txt`** (facit, visas inte för AI:n):

1. `x3 weeks`: samtalet säger tre månader (rad 06)
2. `radiating to L arm`: patienten säger att smärtan inte strålar ut (rad 12)
3. `Non-smoker`: rökning diskuteras aldrig
4. `metformin 1000 mg BID`: dosen är 500 mg (rad 37)
5. `No murmur`: läkaren hör ett 2/6 systoliskt blåsljud (rad 29)
6. `Start lisinopril`: läkaren sätter in hydroklortiazid (rad 39)

**Beslut som är värda en andra blick** (finns i båda anteckningarna, för att testa granskningen av beslut):

- *Unstable angina*: smärtan kommer vid ansträngning, har varit stabil i tre månader och går över i vila, vilket stämmer bättre med stabil angina. Om den verkligen är instabil kan "kardiologi nästa vecka" vara för långsamt.
- Diabetes och misstänkt kranskärlssjukdom, men inga blodfetter eller statin diskuteras.
- Hydroklortiazid: läkaren nämner korsreaktion med sulfa men frågar aldrig om allergier, och tiazider kan höja blodsockret hos en diabetiker.

Data: ACI-Bench (Yim m.fl., 2023, CC BY 4.0), rollspelade samtal.
