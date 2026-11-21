# Testfall: hosta och lungknöl (ACI-Bench, Kyle)

Inbyggd demopatient. Alla AI-steg är förberäknade (`stages.json` och `demo/recordings/`), se `demo/record_live.py`.

| Fil | Innehåll |
|---|---|
| `transcript.txt` | Samtalet (32 repliker) |
| `note-doctor.txt` | Expertjournalen ur datasetet |
| `note-with-errors.txt` | Kort läkaranteckning med fem inplanterade fel |

**Inplanterade fel i `note-with-errors.txt`** (facit, visas inte för AI:n):

1. `x 2 weeks`: hostan har funnits i ungefär tre månader (rad 06)
2. `methotrexate 15 mg weekly`: dosen är 7,5 mg i veckan (rad 16)
3. `quit 2 years ago`: slutade röka för ungefär sju år sedan (rad 18)
4. `Mother with lung cancer`: mamman hade bröstcancer, ingen lungcancer i släkten (rad 24)
5. `left upper lobe`: knölen sitter i höger överlob (rad 27)

Data: ACI-Bench (Yim m.fl., 2023, CC BY 4.0), rollspelade samtal.
