"use strict";

// Show the Receipts: patient list, new visit and the review view.
// The note is the main view; the source in the visit only appears in the
// drawer when something is clicked. All text from transcripts, notes and the
// model is untrusted input and is rendered with textContent, never innerHTML.

const STATUS = { supported: "Supported", inferred: "Inferred", contradicted: "Contradicted", unsupported: "No support", edited: "Edited" };
const SPEAKER = { doctor: "Doctor", patient: "Patient", patient_guest: "Companion", unknown: "Unknown" };
const VARIANT = { clean: "Transcript", asr: "Raw speech-to-text", asrcorr: "Corrected speech-to-text", humantrans: "Manual transcript" };
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const OWN_PREFIX = "own-";

const state = {
  health: null,
  user: null,
  encounters: [],
  encounter: null, // the open visit
  variant: "clean",
  noteId: null,
  noteText: "",
  editing: false,
  audit: null,
  isSample: false,
  selected: null, // { kind: "claim" | "addition", id }
  showFullTranscript: false,
  writingOwn: false,
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
    let message = `Something went wrong (${response.status}).`;
    if (body && typeof body.detail === "string") message = body.detail;
    else if (response.status === 422) message = "The request is not valid. Check the transcript and note and try again.";
    throw Object.assign(new Error(message), { status: response.status });
  }
  return body;
}

// ------------------------------------------------------------------ browser storage (no login yet)

const store = {
  read(key, fallback) {
    try {
      const raw = localStorage.getItem(`receipts.${key}`);
      return raw ? JSON.parse(raw) : fallback;
    } catch {
      return fallback;
    }
  },
  write(key, value) {
    try { localStorage.setItem(`receipts.${key}`, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};
const ownVisits = () => store.read("visits", []);
const reviews = () => store.read("reviews", {});

function saveReviewSummary(visitId, audit) {
  const all = reviews();
  const problems = audit.claims.filter((c) => c.status === "contradicted" || c.status === "unsupported").length;
  all[visitId] = { problems, missing: audit.suggested_additions.length, at: Date.now() };
  store.write("reviews", all);
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

// ------------------------------------------------------------------ start-up and routing

async function init() {
  $("banner-close").addEventListener("click", hideBanner);
  $("patient-search").addEventListener("input", renderPatients);
  $("new-form").addEventListener("submit", createVisit);
  setupUpload("transcript");
  setupUpload("note");
  $("variant-select").addEventListener("change", (e) => loadVisit(state.encounter.encounter_id, e.target.value));
  $("note-select").addEventListener("change", (e) => selectNote(e.target.value));
  $("audit-btn").addEventListener("click", runAudit);
  $("edit-btn").addEventListener("click", toggleEdit);
  $("sample-btn").addEventListener("click", showSample);
  $("delete-btn").addEventListener("click", deleteVisit);
  $("backdrop").addEventListener("click", () => select(null));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") select(null); });
  window.addEventListener("hashchange", route);

  try {
    [state.health, state.encounters, state.user] = await Promise.all([api("/api/health"), api("/api/encounters"), api("/api/me")]);
  } catch (error) {
    showBanner(`Could not reach the server: ${error.message}`);
    return;
  }
  const initials = state.user.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  $("user-chip").replaceChildren(el("span", { class: "avatar", text: initials }), el("span", { class: "user-name", text: state.user.name }));
  route();
}

function route() {
  const [view, id] = location.hash.replace(/^#\/?/, "").split("/");
  hideBanner();
  if (view === "visit" && id) return openView("visit", () => loadVisit(decodeURIComponent(id), "clean"));
  if (view === "new") return openView("new", resetNewVisitForm);
  return openView("patients", renderPatients);
}

function openView(name, setup) {
  for (const view of ["patients", "new", "visit"]) $(`view-${view}`).hidden = view !== name;
  $("nav-patients").classList.toggle("is-active", name === "patients");
  if (name !== "visit") {
    state.selected = null;
    $("backdrop").hidden = true;
  }
  window.scrollTo(0, 0);
  setup();
}

// ------------------------------------------------------------------ patients

function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

function patientRow(visit) {
  const review = reviews()[visit.id];
  let status = el("span", { class: "p-status", text: "Not checked" });
  if (review) {
    status = review.problems
      ? el("span", { class: "p-status is-bad", text: `${review.problems} ${review.problems === 1 ? "problem" : "problems"}` })
      : el("span", { class: "p-status is-good", text: "All backed" });
  }
  return el("li", {},
    el("a", { class: "patient-row", href: `#/visit/${encodeURIComponent(visit.id)}` },
      el("span", { class: "avatar", text: initials(visit.patient) }),
      el("span", { class: "p-main" },
        el("span", { class: "p-name", text: visit.patient || "Unknown patient" }),
        el("span", { class: "p-reason", text: visit.reason || "No reason given" })),
      status,
      el("span", { class: "p-chevron", "aria-hidden": "true", text: "›" })));
}

function renderPatients() {
  const query = $("patient-search").value.trim().toLowerCase();
  const matches = (v) => !query || [v.patient, v.reason, v.id].some((s) => (s || "").toLowerCase().includes(query));
  const own = ownVisits().map((v) => ({ id: v.id, patient: v.patient, reason: v.reason })).filter(matches);
  const fromData = state.encounters.map((e) => ({ id: e.encounter_id, patient: e.patient, reason: e.chief_complaint, featured: e.featured })).filter(matches);
  const demo = fromData.filter((v) => v.featured);
  const rest = fromData.filter((v) => !v.featured);

  $("group-own").hidden = !own.length;
  $("list-own").replaceChildren(...own.map(patientRow));
  $("group-demo").hidden = !demo.length;
  $("list-demo").replaceChildren(...demo.map(patientRow));
  $("all-title").textContent = `All visits (${rest.length})`;
  $("list-all").replaceChildren(...(rest.length ? rest.map(patientRow) : [el("li", { class: "empty-row", text: "No visits match your search." })]));
}

// ------------------------------------------------------------------ new visit

const UPLOAD_LIMITS = { transcript: 100_000, note: 10_000 };
const UPLOAD_HINTS = {
  transcript: "A .txt file from speech-to-text. Drop it here or click to choose.",
  note: "The clinician's note as a .txt file. Optional, you can also write it later.",
};

// "Doctor: ..." / "Patient: ..." lines become the "[doctor] ..." format the app uses.
function normalizeTranscript(text) {
  return text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/^\s*(doctor|dr\.?|physician|clinician)\s*:\s*/i, "[doctor] ").replace(/^\s*(patient|pt)\s*:\s*/i, "[patient] "))
    .join("\n")
    .trim();
}

function setupUpload(kind) {
  const zone = $(`drop-${kind}`);
  const input = $(`file-${kind}`);
  const target = $(`new-${kind}`);
  const load = async (file) => {
    if (!file) return;
    if (!/\.(txt|md)$/i.test(file.name) && file.type && !file.type.startsWith("text/")) {
      return showUploadError(kind, `${file.name} is not a text file. Upload a .txt file.`);
    }
    let text = await file.text();
    if (kind === "transcript") text = normalizeTranscript(text);
    if (text.length > UPLOAD_LIMITS[kind]) {
      return showUploadError(kind, `${file.name} is too long (max ${UPLOAD_LIMITS[kind].toLocaleString("en-US")} characters).`);
    }
    target.value = text;
    const turns = text.split("\n").filter((l) => l.trim()).length;
    $(`hint-${kind}`).textContent = kind === "transcript" ? `${file.name} · ${turns} speaker turns` : `${file.name} · ${text.length.toLocaleString("en-US")} characters`;
    zone.classList.add("is-loaded");
    $("new-error").hidden = true;
  };
  input.addEventListener("change", () => load(input.files[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("is-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("is-over");
    load(e.dataTransfer.files[0]);
  });
}

function showUploadError(kind, message) {
  $(`drop-${kind}`).classList.remove("is-loaded");
  $(`hint-${kind}`).textContent = UPLOAD_HINTS[kind];
  $("new-error").textContent = message;
  $("new-error").hidden = false;
}

function resetNewVisitForm() {
  $("new-form").reset();
  $("new-error").hidden = true;
  for (const kind of ["transcript", "note"]) {
    $(`drop-${kind}`).classList.remove("is-loaded", "is-over");
    $(`hint-${kind}`).textContent = UPLOAD_HINTS[kind];
  }
  $("new-patient").focus();
}

function createVisit(event) {
  event.preventDefault();
  const patient = $("new-patient").value.trim();
  const transcript = normalizeTranscript($("new-transcript").value);
  const error = !patient ? "Add the patient's name." : !transcript ? "Upload or paste the transcript of the visit." : "";
  if (error) {
    $("new-error").textContent = error;
    $("new-error").hidden = false;
    return;
  }
  const visit = {
    id: `${OWN_PREFIX}${Date.now().toString(36)}`,
    patient,
    reason: $("new-reason").value.trim(),
    transcript,
    note: $("new-note").value,
    created: Date.now(),
  };
  store.write("visits", [visit, ...ownVisits()]);
  location.hash = `#/visit/${visit.id}`;
}

function deleteVisit() {
  if (!state.encounter || !state.encounter.own) return;
  const button = $("delete-btn");
  if (button.dataset.confirm !== "1") {
    button.dataset.confirm = "1";
    button.textContent = "Click again to delete";
    return;
  }
  store.write("visits", ownVisits().filter((v) => v.id !== state.encounter.encounter_id));
  const all = reviews();
  delete all[state.encounter.encounter_id];
  store.write("reviews", all);
  location.hash = "#/patients";
}

// ------------------------------------------------------------------ open a visit

async function loadVisit(visitId, variant) {
  let encounter;
  if (visitId.startsWith(OWN_PREFIX)) {
    const own = ownVisits().find((v) => v.id === visitId);
    if (!own) {
      showBanner("This visit is not saved in this browser.");
      location.hash = "#/patients";
      return;
    }
    encounter = {
      encounter_id: own.id,
      own: true,
      patient: own.patient,
      chief_complaint: own.reason,
      transcript_text: own.transcript,
      variants: {},
      notes: [{ id: "own", label: "Note", note_text: own.note || "" }],
    };
  } else {
    try {
      encounter = await api(`/api/encounters/${encodeURIComponent(visitId)}?variant=${variant}`);
    } catch (error) {
      showBanner(error.message);
      return;
    }
  }
  const sameVisit = state.encounter && state.encounter.encounter_id === visitId;
  state.encounter = encounter;
  state.variant = variant;

  $("patient-name").textContent = encounter.patient || "Unknown patient";
  $("patient-meta").textContent = [encounter.chief_complaint, encounter.own ? "Your visit" : `Visit ${encounter.encounter_id}`].filter(Boolean).join(" · ");
  const variants = Object.keys(encounter.variants);
  $("variant-select").hidden = variants.length < 2;
  $("variant-select").replaceChildren(...variants.map((key) => el("option", { value: key, text: VARIANT[key] || key })));
  $("variant-select").value = variant;
  $("note-select").hidden = encounter.notes.length < 2;
  $("delete-btn").hidden = !encounter.own;
  $("delete-btn").dataset.confirm = "";
  $("delete-btn").textContent = "Delete visit";

  if (sameVisit && state.noteId) {
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
  render();
}

function toggleEdit() {
  if (state.editing) {
    state.noteText = $("note-input").value;
    state.editing = false;
    saveOwnNote();
  } else {
    state.editing = true;
    state.audit = null;
    state.selected = null;
  }
  render();
  if (state.editing) $("note-input").focus();
}

function saveOwnNote() {
  if (!state.encounter.own) return;
  store.write("visits", ownVisits().map((v) => (v.id === state.encounter.encounter_id ? { ...v, note: state.noteText } : v)));
  state.encounter.notes[0].note_text = state.noteText;
}

// ------------------------------------------------------------------ review

async function runAudit() {
  if (state.editing) state.noteText = $("note-input").value;
  const noteText = state.noteText.trim();
  if (!noteText) {
    showBanner("Write or paste a note first.");
    return;
  }
  saveOwnNote();
  hideBanner();
  state.editing = false;
  $("audit-btn").disabled = true;
  $("status-text").textContent = "Checking the note against the visit…";
  render();
  const body = state.encounter.own
    ? { transcript_text: state.encounter.transcript_text, note_text: noteText }
    : { encounter_id: state.encounter.encounter_id, variant: state.variant, note_text: noteText };
  try {
    showAudit(await api("/api/audit", { method: "POST", body: JSON.stringify(body) }), false);
  } catch (error) {
    const note = currentNote();
    const canShowSample = note && note.sample && noteText === note.note_text.trim();
    showBanner(error.message, canShowSample ? { label: "Show simulated review", run: showSample } : null);
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
    showAudit(sample, true);
  } catch (error) {
    showBanner(`Could not load the simulated review: ${error.message}`);
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
  saveReviewSummary(state.encounter.encounter_id, state.audit);
  render();
}

// ------------------------------------------------------------------ rendering the visit

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

function sampleNote(model) {
  if (model.startsWith("simulated")) {
    return "Simulated review: a Claude agent answered the exact request the app sends to the API, and the result went through the same receipt checks.";
  }
  return "Example review, written by hand to show the interface. Not generated by Claude.";
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
  const missing = state.audit.suggested_additions.filter((a) => !state.additionState[a.addition_id]).length;

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
    state.isSample ? el("p", { class: "stats-note", text: sampleNote(state.audit.meta.model) }) : null,
  );
  box.hidden = false;
}

function nextIssue() {
  const list = issues();
  if (!list.length) return;
  state.issueCursor = (state.issueCursor + 1) % list.length;
  const id = list[state.issueCursor].claim_id;
  select({ kind: "claim", id });
  const node = document.querySelector(`.claim[data-claim="${id}"]`);
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
  if (sections.length > 1 && sections[0].label === "Note") sections[0].label = "History";
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
    body.replaceChildren(el("p", { class: "status-text", text: "No note yet. Choose Edit to write one." }));
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
    if (claim.added || claim.fixed === "corrected") classes.push("is-added");
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
  const additions = state.audit ? state.audit.suggested_additions : [];
  $("additions-section").hidden = !additions.length;
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
  audit.claims.push({
    claim_id: `T${addition.addition_id}`,
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
  saveOwnNote();
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
  state.writingOwn = false;
  if (!$("view-visit").hidden) render();
}

// ------------------------------------------------------------------ fixing a statement in the note

function renderFixActions(claim) {
  const box = el("div", { class: "fix" });
  if (state.writingOwn) {
    const input = el("textarea", { id: "own-fix", class: "fix-input", rows: "2", "aria-label": "Your wording" });
    input.value = claim.note_quote;
    box.append(
      input,
      el("div", { class: "drawer-actions" },
        el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Save", onclick: () => applyFix(claim, input.value.trim(), "edited") }),
        el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Cancel", onclick: () => { state.writingOwn = false; renderDrawer(); } })),
    );
    setTimeout(() => input.focus(), 0);
    return box;
  }
  const actions = el("div", { class: "drawer-actions" });
  if (claim.correction) {
    actions.append(el("button", { type: "button", class: "btn btn-primary btn-sm fix-change", onclick: () => applyFix(claim, claim.correction, "corrected") },
      "Change to ", el("span", { class: "fix-text", text: `“${claim.correction}”` })));
  }
  if (!claim.correction || claim.status === "unsupported") {
    actions.append(el("button", { type: "button", class: `btn ${claim.correction ? "btn-ghost" : "btn-primary"} btn-sm`, text: "Remove from note", onclick: () => applyFix(claim, "", "removed") }));
  }
  actions.append(el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Write my own", onclick: () => { state.writingOwn = true; renderDrawer(); } }));
  box.append(actions);
  return box;
}

// Replaces (or removes) the statement's text in the note and shifts the
// positions of every statement after it.
function applyFix(claim, newText, kind) {
  const audit = state.audit;
  const text = audit.note_text;
  const start = claim.note_span.start;
  let end = claim.note_span.end;
  if (!newText) {
    while (end < text.length && /[.,;]/.test(text[end])) end += 1;
    while (end < text.length && text[end] === " ") end += 1;
  }
  audit.note_text = text.slice(0, start) + newText + text.slice(end);
  const delta = newText.length - (end - start);
  for (const other of audit.claims) {
    if (other === claim || !other.note_span || other.note_span.start < end) continue;
    other.note_span = { start: other.note_span.start + delta, end: other.note_span.end + delta };
  }
  if (!newText) {
    audit.claims = audit.claims.filter((c) => c !== claim);
    state.selected = null;
  } else {
    claim.note_span = { start, end: start + newText.length };
    claim.note_quote = newText;
    claim.fixed = kind;
    if (kind === "corrected" && claim.evidence.length) {
      claim.status = "supported";
      claim.explanation = "Corrected to match the visit.";
    } else {
      claim.status = "edited";
      claim.explanation = "Changed by you. Check the note again to verify it.";
    }
    claim.correction = "";
  }
  state.noteText = audit.note_text;
  state.writingOwn = false;
  state.issueCursor = -1;
  saveOwnNote();
  saveReviewSummary(state.encounter.encounter_id, audit);
  render();
}

function renderDrawer() {
  const item = selectedItem();
  const open = Boolean(item);
  $("drawer").hidden = !open;
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
  if (isClaim && (item.status === "contradicted" || item.status === "unsupported") && item.note_span) {
    parts.push(renderFixActions(item));
  }

  parts.push(el("p", { class: "drawer-section", text: "In the visit" }));
  if (item.evidence.length) parts.push(renderExcerpt(item.evidence, status));
  else parts.push(el("p", { class: "drawer-text", text: "Nothing in the visit supports this." }));

  if (!isClaim && !state.additionState[item.addition_id]) {
    parts.push(el("div", { class: "drawer-actions" },
      el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Add to note", onclick: () => addAddition(item) }),
      el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Dismiss", onclick: () => { state.additionState[item.addition_id] = "dismissed"; select(null); } })));
  }

  // The toggle sits below the full transcript once it is open.
  if (state.showFullTranscript) parts.push(renderFullTranscript(item.evidence, status));
  parts.push(el("button", {
    type: "button",
    class: "link-btn",
    text: state.showFullTranscript ? "Hide full transcript" : "Show full transcript",
    onclick: () => { state.showFullTranscript = !state.showFullTranscript; renderDrawer(); },
  }));

  $("drawer-content").replaceChildren(el("div", { class: "drawer-content" }, parts));
  if (state.showFullTranscript) {
    const box = document.querySelector(".full-transcript");
    const first = box && box.querySelector(".is-cited");
    if (first) box.scrollTop = first.offsetTop - 40;
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
  const lines = state.audit.transcript;
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
  const lines = state.audit.transcript;
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
