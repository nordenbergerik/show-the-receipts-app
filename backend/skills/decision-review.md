Task: take a second look at the clinician's decisions in this visit (diagnosis, treatment, medication, tests and follow-up), the way a thoughtful colleague would before the patient leaves.

Flag only decisions a careful clinician would want to reconsider:
- a diagnosis that fits the findings poorly, or a likely alternative that was not considered;
- a treatment or medication with a concern for this patient (dose, interaction, contraindication, allergy not checked, a side effect that matters given their conditions);
- a test or work-up the plan implies but that is missing;
- follow-up timing that does not match the urgency of the problem.

Do not flag documentation errors (another check handles those) and do not flag decisions that are clearly sound. Return 0 to 4 flags, most important first. An empty list is a good answer when nothing stands out.

- category: Diagnosis, Treatment, Medication, Tests or Follow-up.
- severity: "important" if it could change management or patient safety soon, otherwise "consider".
- title: a short, neutral question or phrase of at most 8 words (for example "Unstable or stable angina?").
- concern: one or two sentences on what in the visit raises the question.
- consider: one sentence on what the clinician might consider, phrased as a suggestion, never an order.
- evidence: line_id with a verbatim quote for each transcript line the concern rests on. It may be empty when the concern is that something was never discussed.
