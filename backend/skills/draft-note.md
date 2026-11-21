Task: write the clinical note for this visit from the transcript, in the format given in <format>.

Formats:
- "soap": the SOAP structure. Four sections, each on its own line, starting with the label and a colon:
  - "Subjective:" the reason for the visit, history of the complaint, relevant history, medications, allergies and the answers to red-flag questions.
  - "Objective:" exam findings and results (vitals, tests, imaging) found or reviewed during the visit.
  - "Assessment:" the clinician's assessment or diagnoses, numbered if there are several.
  - "Plan:" treatment, prescriptions with doses, tests ordered, referrals, counselling and safety advice, and follow-up.
  Leave out a section only when the visit has nothing for it.
- "plain": one or a few short paragraphs of plain prose, in the order the clinic would read it (reason for visit, history, findings, assessment, plan). No headings or labels.

For both formats:
- Write it the way a busy clinician would: short, factual sentences, common abbreviations (for example "53F", "x3 months", "SOB", "BID", "prn").
- Be brief, but complete: the note will be checked against the visit for anything clinically important that is missing. Include every key symptom characteristic, red flag asked about (and the answer), relevant history, medications and allergies, exam finding, result, the assessment, the agreed plan, counselling and safety advice given, and follow-up. Leave out small talk and logistics.
- Include only what was said or found in the visit. Never add facts, vitals, doses or diagnoses that are not in the transcript. Keep the clinician's own assessment and plan, even if you would have chosen differently.
- If <accepted_points> is given, the clinician has asked to keep these points in mind. Add a final line "To follow up:" that lists them briefly, separated by semicolons. Do not present them as facts about the patient.
- note_text: the note as plain text. In "soap", sections are separated by line breaks. No markdown, no bullet characters.
