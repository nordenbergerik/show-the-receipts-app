Task: split the note into atomic claims and judge each one against the transcript.

- Cover every clinical statement in the note, in order. Skip headings and text with no clinical content.
- note_quote: copy the exact span of the note the claim comes from, character for character (same spelling, abbreviations, punctuation). Use the smallest span that carries the claim.
- text: the claim as a short plain sentence with abbreviations expanded.
- status:
  - supported: the transcript states it.
  - inferred: a reasonable clinical conclusion, assessment or plan drawn from what was said (for example a diagnosis matching the findings discussed). Cite the turns it rests on.
  - contradicted: the transcript says something incompatible (negation flipped, wrong side, wrong number, dose, duration or timing, or attributed to the wrong person).
  - unsupported: nothing in the transcript supports it, or it adds details the transcript does not support.
- evidence: for supported, inferred and contradicted, give each line_id with a quote copied verbatim from that line (a few words up to one sentence, never spanning two lines). For contradicted, cite the lines that contradict the claim. For unsupported, leave evidence empty.
- explanation: one short sentence on why.
- correction: for contradicted and unsupported claims, rewrite note_quote so that it says only what the transcript supports, in the note's own style and length (keep shorthand if the note uses it). When part of the statement is supported, keep that part and fix or drop the rest (for example "numbness and tingling" becomes "mild tingling"). Use an empty string only when nothing in the statement is supported and it should be removed. For supported and inferred claims, use an empty string.

A short note is not an error. Shorthand and abbreviations (for example "2d", "ua", "neg", "hx") are normal; interpret them. Be strict about details: numbers, laterality, negation, medication names and doses must match the transcript.
