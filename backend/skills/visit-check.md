Task: the visit has just ended and no note has been written yet. Give the clinician quick feedback, like a colleague would, so nothing important is forgotten after a long day.

1. missed: 2 to 6 questions that were not asked or things that were not checked, given the reason for the visit and what came up. Prioritize red flags and patient safety, then history that changes management (risk factors, medications, allergies).
   - category: for example "Red flags", "Risk factors", "Medication", "History" or "Follow-up".
   - question: phrased the way the clinician would ask it, or the check they would do.
   - why: one short sentence on why it matters for this patient.
   - evidence: line_id with a verbatim quote for the transcript lines that make it relevant. It may be empty.
2. decision_flags: a second look at the clinician's decisions in the visit (diagnosis, treatment, medication, tests and follow-up). Flag only what a careful clinician would want to reconsider, 0 to 4 flags, most important first. Do not flag decisions that are clearly sound.
   - category: Diagnosis, Treatment, Medication, Tests or Follow-up.
   - severity: "important" if it could change management or patient safety soon, otherwise "consider".
   - title: a short, neutral question or phrase of at most 8 words.
   - concern: one or two sentences on what in the visit raises the question.
   - consider: one sentence on what the clinician might consider, phrased as a suggestion, never an order.
   - evidence: line_id with a verbatim quote for each transcript line the concern rests on. It may be empty when the concern is that something was never discussed.
