# Testpersoner

Tio patienter att ladda upp under **New visit**. Varje mapp har samtalet (`transcript.txt`) och en journal (`journal.txt`).

- **Hela flödet:** ladda upp bara `transcript.txt`, kör *Check the visit*, låt AI:n skriva utkastet och granska det.
- **Tillägget:** ladda upp `transcript.txt` och `journal.txt` och gå direkt till *Check note*.

| # | Patient | Besvär | Repliker | Filer |
|---|---|---|---|---|
| 01 | [Elizabeth Moore](01-elizabeth-moore-chest-pain-on-exertion/) | Chest pain on exertion | 46 | `transcript.txt`, `journal.txt`, `journal-med-fel.txt` |
| 02 | [Donna](02-donna-headache/) | Headache | 39 | `transcript.txt`, `journal.txt` |
| 03 | [Bobby](03-bobby-abdominal-pain/) | Abdominal pain | 54 | `transcript.txt`, `journal.txt` |
| 04 | [Bryan Brooks](04-bryan-brooks-low-back-pain/) | Low back pain | 43 | `transcript.txt`, `journal.txt` |
| 05 | [Kyle](05-kyle-cough/) | Cough | 32 | `transcript.txt`, `journal.txt` |
| 06 | [Dennis](06-dennis-shortness-of-breath-with-hemoptysis/) | Shortness of breath with hemoptysis | 45 | `transcript.txt`, `journal.txt` |
| 07 | [Philip](07-philip-right-knee-pain/) | Right knee pain | 41 | `transcript.txt`, `journal.txt` |
| 08 | [Diana Scott](08-diana-scott-right-wrist-injury/) | Right wrist injury | 81 | `transcript.txt`, `journal.txt` |
| 09 | [Brian White](09-brian-white-follow-up-of-chronic-problems/) | Follow-up of chronic problems | 74 | `transcript.txt`, `journal.txt` |
| 10 | [Theresa](10-theresa-headache-and-high-blood-pressure/) | Headache and high blood pressure | 31 | `transcript.txt`, `journal.txt` |

**Om journalerna:**
- **01 Elizabeth Moore** har en kort läkaranteckning som stämmer (`journal.txt`) och samma anteckning med sex inplanterade fel (`journal-med-fel.txt`). För dem finns inspelade AI-svar, så de fungerar även utan API-nyckel och som reserv om API:t krånglar. Facit finns i [demo/cases/chest-pain/README.md](../demo/cases/chest-pain/README.md).
- **Övriga** har expertjournalen ur datasetet och granskas med det riktiga API:t. Expertjournalerna brukar innehålla några påståenden utan stöd i samtalet.
- **09 Brian White** spelades in med en röstassistent ('dax'), så samtalet innehåller kommandon till den.

Data: ACI-Bench (Yim m.fl., 2023, CC BY 4.0). Rollspelade samtal, inga riktiga patienter.
