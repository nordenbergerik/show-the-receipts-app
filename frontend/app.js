"use strict";

// The note is the main view. The source in the visit is only shown in the
// drawer when something is clicked. All text from transcripts, notes and the
// model is untrusted input and is rendered with textContent, never innerHTML.

const STATUS = { supported: "Supported", inferred: "Inferred", contradicted: "Contradicted", unsupported: "No support" };
const SPEAKER = { doctor: "Doctor", patient: "Patient", patient_guest: "Companion", unknown: "Unknown" };
const VARIANT = { clean: "Transcript", asr: "Raw speech-to-text", asrcorr: "Corrected speech-to-text", humantrans: "Manual transcript" };
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  health: null,
  encounters: [],
  encounter: null,
  variant: "clean",
  noteId: null,
  noteText: "",
  editing: false,
  audit: null,
  isSample: false,
  selected: null, // { kind: "claim" | "addition", id }
  showFullTranscript: false,
  additionState: {}, // addition_id -> "added" | "dismissed"
  issueCursor: -1,
};

const $ = (id) => document.getElementById(id);
const pad = (n, width = 2) => String(n).padStart(width, "0");

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  let body = null;
  try { body = await response.json(); } catch { /* empty body */ }
  if (!response.ok) {
    const message = body && typeof body.detail === "string" ? body.detail : `Something went wrong (${response.status}).`;
    throw Object.assign(new Error(message), { status: response.status });
  }
  return body;
}

// ------------------------------------------------------------------ banner

function showBanner(message, action) {
  $("banner-text").textContent = message;
  const button = $("banner-action");
  button.hidden = !action;
  if (action) {
    button.textContent = action.label;
    button.onclick = () => { hideBanner(); action.run(); };
  }
  $("banner").hidden = false;
}
function hideBanner() { $("banner").hidden = true; }

// ------------------------------------------------------------------ start-up

async function init() {
  $("banner-close").addEventListener("click", hideBanner);
  $("encounter-select").addEventListener("change", (e) => loadEncounter(e.target.value, "clean"));
  $("variant-select").addEventListener("change", (e) => loadEncounter(state.encounter.encounter_id, e.target.value));
  $("note-select").addEventListener("change", (e) => selectNote(e.target.value));
  $("audit-btn").addEventListener("click", runAudit);
  $("edit-btn").addEventListener("click", toggleEdit);
  $("sample-btn").addEventListener("click", showSample);
  $("backdrop").addEventListener("click", () => select(null));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") select(null); });

  try {
    [state.health, state.encounters] = await Promise.all([api("/api/health"), api("/api/encounters")]);
  } catch (error) {
    showBanner(`Could not reach the server: ${error.message}`);
    return;
  }
  renderEncounterSelect();
  const first = state.encounters.find((e) => e.featured) || state.encounters[0];
  await loadEncounter(first.encounter_id, "clean");
}

function renderEncounterSelect() {
  const label = (e) => `${e.patient || "Unknown patient"} · ${e.chief_complaint || e.dataset}`;
  const option = (e) => el("option", { value: e.encounter_id, text: label(e) });
  const featured = state.encounters.filter((e) => e.featured);
  $("encounter-select").replaceChildren(
    featured.length ? el("optgroup", { label: "Demo cases" }, featured.map(option)) : null,
    el("optgroup", { label: `All visits (${state.encounters.length})` }, state.encounters.filter((e) => !e.featured).map(option)),
  );
}

async function loadEncounter(encounterId, variant) {
  let encounter;
  try {
    encounter = await api(`/api/encounters/${encodeURIComponent(encounterId)}?variant=${variant}`);
  } catch (error) {
    showBanner(error.message);
    return;
  }
  const sameEncounter = state.encounter && state.encounter.encounter_id === encounterId;
  state.encounter = encounter;
  state.variant = variant;
  $("encounter-select").value = encounterId;
  $("variant-select").replaceChildren(
    ...Object.keys(encounter.variants).map((key) => el("option", { value: key, text: VARIANT[key] || key })),
  );
  $("variant-select").value = variant;
  $("patient-name").textContent = encounter.patient || "Unknown patient";
  $("patient-meta").textContent = [encounter.chief_complaint, `Visit ${encounter.encounter_id}`].filter(Boolean).join(" · ");
  if (sameEncounter) {
    resetAudit();
  } else {
    $("note-select").replaceChildren(...encounter.notes.map((n) => el("option", { value: n.id, text: n.label })));
    const preferred = encounter.notes.find((n) => n.sample) || encounter.notes[0];
    selectNote(preferred.id);
  }
}

function currentNote() {
  return state.encounter.notes.find((n) => n.id === state.noteId);
}

function selectNote(noteId) {
  state.noteId = noteId;
  $("note-select").value = noteId;
  state.noteText = currentNote().note_text;
  state.editing = !state.noteText;
  resetAudit();
}

function resetAudit() {
  state.audit = null;
  state.isSample = false;
  state.selected = null;
  state.additionState = {};
  state.issueCursor = -1;
  hideBanner();
  render();
}

function toggleEdit() {
  if (state.editing) {
    state.noteText = $("note-input").value;
    state.editing = false;
  } else {
    state.editing = true;
    state.audit = null;
    state.selected = null;
  }
  render();
  if (state.editing) $("note-input").focus();
}

// ------------------------------------------------------------------ review

async function runAudit() {
  if (state.editing) state.noteText = $("note-input").value;
  const noteText = state.noteText.trim();
  if (!noteText) {
    showBanner("Type or paste a note first.");
    return;
  }
  hideBanner();
  state.editing = false;
  $("audit-btn").disabled = true;
  $("status-text").textContent = "Checking the note against the visit…";
  render();
  try {
    const result = await api("/api/audit", {
      method: "POST",
      body: JSON.stringify({ encounter_id: state.encounter.encounter_id, variant: state.variant, note_text: noteText }),
    });
    showAudit(result, false);
  } catch (error) {
    const note = currentNote();
    const canShowSample = note && note.sample && noteText === note.note_text.trim();
    showBanner(error.message, canShowSample ? { label: "Show example", run: showSample } : null);
  } finally {
    $("audit-btn").disabled = false;
    $("status-text").textContent = "";
  }
}

async function showSample() {
  const note = currentNote();
  if (!note || !note.sample) return;
  try {
    const sample = await api(`/samples/${encodeURIComponent(note.sample)}.json`);
    if (state.variant !== (sample.variant || "clean")) await loadEncounter(state.encounter.encounter_id, sample.variant || "clean");
    showAudit(sample, true);
  } catch (error) {
    showBanner(`Could not load the example: ${error.message}`);
  }
}

function showAudit(result, isSample) {
  state.audit = structuredClone(result);
  state.noteText = state.audit.note_text;
  state.isSample = isSample;
  state.editing = false;
  state.selected = null;
  state.additionState = {};
  state.issueCursor = -1;
  render();
}

// ------------------------------------------------------------------ rendering

function render() {
  const audited = Boolean(state.audit);
  $("journal-body").hidden = state.editing;
  $("note-input").hidden = !state.editing;
  if (state.editing) $("note-input").value = state.noteText;
  $("edit-btn").textContent = state.editing ? "Done" : "Edit";
  $("audit-btn").hidden = audited;
  const note = currentNote();
  $("sample-btn").hidden = audited || !(note && note.sample && state.noteText === note.note_text);

  renderJournal();
  renderStats();
  renderAdditions();
  renderFollowUps();
  renderDrawer();
}

function issues() {
  if (!state.audit) return [];
  return state.audit.claims
    .filter((c) => c.status === "contradicted" || c.status === "unsupported")
    .sort((a, b) => (a.note_span ? a.note_span.start : 1e9) - (b.note_span ? b.note_span.start : 1e9));
}

function openAdditions() {
  if (!state.audit) return [];
  return state.audit.suggested_additions.filter((a) => !state.additionState[a.addition_id]);
}

function renderStats() {
  const box = $("stats");
  if (!state.audit) {
    box.hidden = true;
    return;
  }
  const claims = state.audit.claims;
  const problems = issues();
  const contradicted = problems.filter((c) => c.status === "contradicted").length;
  const backed = claims.filter((c) => c.status === "supported" || c.status === "inferred").length;
  const pct = claims.length ? Math.round((100 * backed) / claims.length) : 0;
  const missing = openAdditions().length;

  const stat = (value, label, cls, onclick, title) =>
    el("button", { type: "button", class: `stat ${cls}`, onclick, title },
      el("span", { class: "stat-value", text: value }),
      el("span", { class: "stat-label", text: label }));

  box.replaceChildren(
    stat(problems.length, problems.length === 1 ? "problem in the note" : "problems in the note",
      problems.length ? "is-bad" : "is-good", nextIssue,
      `${contradicted} contradicted by the visit, ${problems.length - contradicted} with no support`),
    stat(missing, missing === 1 ? "thing missing" : "things missing", "", () =>
      $("additions-section").scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" })),
    stat(`${pct}%`, "backed by the visit", "is-good", null, `${backed} of ${claims.length} statements have a receipt`),
    state.isSample ? el("p", { class: "stats-note", text: "Example review, written by hand to show the interface. Not generated by Claude." }) : null,
  );
  box.hidden = false;
}

function nextIssue() {
  const list = issues();
  if (!list.length) return;
  state.issueCursor = (state.issueCursor + 1) % list.length;
  select({ kind: "claim", id: list[state.issueCursor].claim_id });
  const node = document.querySelector(`.claim[data-claim="${list[state.issueCursor].claim_id}"]`);
  if (node) node.scrollIntoView({ block: "center", behavior: REDUCED_MOTION ? "auto" : "smooth" });
}

// ---------------------------------------------- the note, as a structured record

const HEADING_RE = /^[A-Z][A-Z0-9 &/()\-]{2,40}$/;
const LABEL_RE = /^\s*([A-Za-z][A-Za-z0-9 &/\-]{0,28}):\s*/;

function titleCase(text) {
  const lower = text.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

// Splits the note into sections ("Plan: ..." or an ALL CAPS heading) and rows
// (one sentence each), keeping character offsets so statements line up.
function parseNote(text) {
  const sections = [];
  let current = null;
  let offset = 0;
  for (const line of text.split("\n")) {
    const lineStart = offset;
    const lineEnd = offset + line.length;
    offset = lineEnd + 1;
    const trimmed = line.trim();
    if (!trimmed) continue;

    if (HEADING_RE.test(trimmed) && /[A-Z]{3}/.test(trimmed)) {
      current = { label: titleCase(trimmed), rows: [], fromHeading: true };
      sections.push(current);
      continue;
    }
    const match = LABEL_RE.exec(line);
    let contentStart = lineStart;
    let sublabel = null;
    if (match && !(current && current.fromHeading)) {
      const label = match[1].trim();
      contentStart = lineStart + match[0].length;
      if (!current || current.label.toLowerCase() !== label.toLowerCase()) {
        current = { label, rows: [], fromHeading: false };
        sections.push(current);
      }
    } else if (match) {
      sublabel = match[1].trim();
      contentStart = lineStart + match[0].length;
    } else if (!current) {
      current = { label: "Note", rows: [], fromHeading: false };
      sections.push(current);
    }
    sentenceRanges(text, contentStart, lineEnd).forEach(([s, e], i) => current.rows.push({ start: s, end: e, sublabel: i === 0 ? sublabel : null }));
  }
  if (sections.length && sections[0].label === "Note" && sections.length > 1) sections[0].label = "History";
  return sections;
}

function sentenceRanges(text, start, end) {
  const ranges = [];
  const re = /[.!?](?=\s+[A-Z0-9(])/g;
  const chunk = text.slice(start, end);
  let last = 0;
  let m;
  while ((m = re.exec(chunk))) {
    ranges.push([last, m.index + 1]);
    last = m.index + 1;
  }
  ranges.push([last, chunk.length]);
  return ranges
    .map(([s, e]) => {
      let a = start + s;
      while (a < start + e && /\s/.test(text[a])) a += 1;
      return [a, start + e];
    })
    .filter(([a, b]) => b > a);
}

function renderJournal() {
  const text = state.noteText;
  const sections = parseNote(text);
  const claims = state.audit ? state.audit.claims.filter((c) => c.note_span).sort((a, b) => a.note_span.start - b.note_span.start) : [];
  const body = $("journal-body");
  if (!sections.length) {
    body.replaceChildren(el("p", { class: "status-text", text: "This note is empty. Choose Edit to write one." }));
    return;
  }
  body.replaceChildren(
    ...sections.map((section) =>
      el("section", { class: "jsection" },
        el("h2", { class: "jlabel", text: section.label }),
        el("div", { class: "jrows" },
          section.rows.map((row) => {
            const p = el("p", { class: "jrow" });
            if (row.sublabel) p.append(el("span", { class: "sublabel", text: `${row.sublabel}: ` }));
            appendWithClaims(p, text, row.start, row.end, claims);
            return p;
          })))),
  );
}

function appendWithClaims(parent, text, start, end, claims) {
  let pos = start;
  for (const claim of claims) {
    const s = Math.max(claim.note_span.start, start);
    const e = Math.min(claim.note_span.end, end);
    if (e <= s || s < pos) continue;
    if (s > pos) parent.append(text.slice(pos, s));
    const open = () => select({ kind: "claim", id: claim.claim_id });
    const classes = ["claim", `st-${claim.status}`];
    if (claim.added) classes.push("is-added");
    if (isSelected("claim", claim.claim_id)) classes.push("is-selected");
    parent.append(el("span", {
      class: classes.join(" "),
      "data-claim": claim.claim_id,
      tabindex: "0",
      role: "button",
      "aria-label": `${STATUS[claim.status]}: ${text.slice(s, e)}`,
      onclick: open,
      onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); } },
    }, text.slice(s, e)));
    pos = e;
  }
  if (pos < end) parent.append(text.slice(pos, end));
}

// ------------------------------------------------------------------ additions and follow-ups

function renderAdditions() {
  const section = $("additions-section");
  const additions = state.audit ? state.audit.suggested_additions : [];
  section.hidden = !additions.length;
  $("additions").replaceChildren(
    ...additions.map((a) => {
      const status = state.additionState[a.addition_id];
      const classes = ["row"];
      if (status) classes.push("is-done");
      if (isSelected("addition", a.addition_id)) classes.push("is-selected");
      return el("li", { class: classes.join(" ") },
        el("button", { type: "button", class: "row-main", onclick: () => select({ kind: "addition", id: a.addition_id }) },
          el("span", { class: `prio prio-${a.priority}`, title: `${a.priority} priority` }),
          el("span", { class: "row-text", text: a.text })),
        status === "added" ? el("span", { class: "row-done", text: "Added" })
          : status === "dismissed" ? el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Undo", onclick: () => setAdditionState(a.addition_id, undefined) })
          : el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Add", onclick: () => addAddition(a) }));
    }),
  );
}

function renderFollowUps() {
  const items = state.audit ? state.audit.follow_up_questions : [];
  $("followups-section").hidden = !items.length;
  $("followups-title").textContent = `Questions to follow up (${items.length})`;
  $("followups").replaceChildren(
    ...items.map((f) => el("li", { class: "followup" }, el("span", { text: f.question }), el("span", { class: "followup-why", text: f.why }))),
  );
}

function setAdditionState(id, value) {
  if (value) state.additionState[id] = value;
  else delete state.additionState[id];
  render();
}

// A suggestion already has verified receipts, so it goes straight into the
// note as "supported" without another call to the model.
function addAddition(addition) {
  const audit = state.audit;
  const prefix = "Added: ";
  const separator = audit.note_text === "" || audit.note_text.endsWith("\n") ? "" : "\n";
  const start = audit.note_text.length + separator.length + prefix.length;
  audit.note_text = `${audit.note_text}${separator}${prefix}${addition.text}`;
  state.noteText = audit.note_text;
  const claimId = `T${addition.addition_id}`;
  audit.claims.push({
    claim_id: claimId,
    text: addition.text,
    note_quote: addition.text,
    note_span: { start, end: start + addition.text.length },
    status: "supported",
    evidence: addition.evidence,
    explanation: "Added from the visit.",
    receipt_failed: false,
    added: true,
  });
  state.additionState[addition.addition_id] = "added";
  state.selected = null;
  render();
}

// ------------------------------------------------------------------ selection and the source drawer

function isSelected(kind, id) {
  return Boolean(state.selected && state.selected.kind === kind && state.selected.id === id);
}

function selectedItem() {
  if (!state.selected || !state.audit) return null;
  if (state.selected.kind === "claim") return state.audit.claims.find((c) => c.claim_id === state.selected.id) || null;
  return state.audit.suggested_additions.find((a) => a.addition_id === state.selected.id) || null;
}

function select(selection) {
  state.selected = selection;
  state.showFullTranscript = false;
  render();
}

function renderDrawer() {
  const item = selectedItem();
  const drawer = $("drawer");
  const open = Boolean(item);
  drawer.hidden = !open;
  $("page").classList.toggle("has-drawer", open);
  $("backdrop").hidden = !(open && window.matchMedia("(max-width: 1099px)").matches);
  if (!open) return;

  const isClaim = state.selected.kind === "claim";
  const status = isClaim ? item.status : "supported";
  const parts = [
    el("div", { class: "drawer-head" },
      isClaim ? el("span", { class: `pill st-${item.status}`, text: STATUS[item.status] }) : el("span", { class: "pill neutral", text: "Missing from the note" }),
      el("button", { type: "button", class: "icon-btn", "aria-label": "Close", text: "×", onclick: () => select(null) })),
    el("p", { class: "drawer-title", text: isClaim ? (item.added ? item.text : item.note_quote) : item.text }),
  ];
  const why = isClaim ? (item.receipt_failed ? "The cited evidence could not be found in the visit." : item.explanation) : item.why;
  if (why) parts.push(el("p", { class: "drawer-text", text: why }));

  parts.push(el("p", { class: "drawer-section", text: "In the visit" }));
  if (item.evidence.length) parts.push(renderExcerpt(item.evidence, status));
  else parts.push(el("p", { class: "drawer-text", text: "Nothing in the visit supports this." }));

  if (!isClaim && !state.additionState[item.addition_id]) {
    parts.push(el("div", { class: "drawer-actions" },
      el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Add to note", onclick: () => addAddition(item) }),
      el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Dismiss", onclick: () => { setAdditionState(item.addition_id, "dismissed"); select(null); } })));
  }

  parts.push(el("button", {
    type: "button",
    class: "link-btn",
    text: state.showFullTranscript ? "Hide full transcript" : "Show full transcript",
    onclick: () => { state.showFullTranscript = !state.showFullTranscript; renderDrawer(); },
  }));
  if (state.showFullTranscript) parts.push(renderFullTranscript(item.evidence, status));

  $("drawer-content").replaceChildren(el("div", { class: "drawer-content" }, parts));
  if (state.showFullTranscript) {
    const first = document.querySelector(".full-transcript .is-cited");
    if (first) first.parentElement.parentElement.scrollTop = first.offsetTop - 40;
  }
}

function quotesByLine(evidence) {
  const map = new Map();
  for (const ev of evidence) {
    if (!map.has(ev.line_id)) map.set(ev.line_id, []);
    map.get(ev.line_id).push(ev.quote);
  }
  return map;
}

function lineItem(line, quotes, status, width, compact = false) {
  let content;
  if (quotes) content = markQuotes(line.text, quotes, compact ? 90 : Infinity);
  else content = compact && line.text.length > 140 ? `${line.text.slice(0, 130).trimEnd()} …` : line.text;
  return el("li", { class: quotes ? `line is-cited st-${status}` : "line" },
    el("span", { class: "ln", text: pad(line.line_id, width) }),
    el("span", { class: `spk spk-${line.speaker}`, text: SPEAKER[line.speaker] || line.speaker }),
    el("p", { class: "txt" }, content));
}

// The cited lines with one line of context on each side.
function renderExcerpt(evidence, status) {
  const lines = state.encounter.transcript;
  const width = Math.max(2, String(lines.length).length);
  const quotes = quotesByLine(evidence);
  const show = new Set();
  for (const id of quotes.keys()) for (const n of [id - 1, id, id + 1]) if (n >= 1 && n <= lines.length) show.add(n);
  const ids = [...show].sort((a, b) => a - b);
  const items = [];
  ids.forEach((id, i) => {
    if (i > 0 && id !== ids[i - 1] + 1) items.push(el("li", { class: "gap", text: "···", "aria-hidden": "true" }));
    items.push(lineItem(lines[id - 1], quotes.get(id), status, width, true));
  });
  return el("ol", { class: "excerpt" }, items);
}

function renderFullTranscript(evidence, status) {
  const lines = state.encounter.transcript;
  const width = Math.max(2, String(lines.length).length);
  const quotes = quotesByLine(evidence);
  return el("div", { class: "full-transcript" },
    el("ol", { class: "excerpt" }, lines.map((line) => lineItem(line, quotes.get(line.line_id), status, width))));
}

// Highlights the quotes inside a line, word by word, so that "hi, brian"
// matches ACI-Bench's tokenized "hi , brian". With a finite `context`, long
// lines are cut down to the quotes plus that many characters around them.
function markQuotes(text, quotes, context = Infinity) {
  const ranges = [];
  for (const quote of quotes) {
    const words = quote.toLowerCase().match(/[\p{L}\p{N}']+/gu);
    if (!words) continue;
    const pattern = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[^\\p{L}\\p{N}']+");
    const match = new RegExp(pattern, "iu").exec(text);
    if (match) ranges.push([match.index, match.index + match[0].length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  let from = 0;
  let to = text.length;
  if (ranges.length && Number.isFinite(context) && text.length > 2 * context + 60) {
    from = Math.max(0, ranges[0][0] - context);
    to = Math.min(text.length, ranges[ranges.length - 1][1] + context);
  }
  const out = from > 0 ? ["… "] : [];
  let pos = from;
  for (const [start, end] of ranges) {
    if (start < pos) continue;
    if (start > pos) out.push(text.slice(pos, start));
    out.push(el("mark", { text: text.slice(start, end) }));
    pos = end;
  }
  if (pos < to) out.push(text.slice(pos, to));
  if (to < text.length) out.push(" …");
  return out;
}

init();
