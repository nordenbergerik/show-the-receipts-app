"use strict";

// Show the Receipts: patients, new visit and the visit view.
//
// A visit moves through three steps: (1) a check right after the visit, before
// a note exists; (2) the note, drafted by AI or written by the clinician, edited
// like a document; (3) a check of the note: receipts, what is missing and a
// second look at decisions. The source in the visit only appears in the drawer
// when something is clicked. All text from transcripts, notes and the model is
// untrusted input and is rendered with textContent, never innerHTML.

const STATUS = { supported: "Supported", inferred: "Inferred", contradicted: "Contradicted", unsupported: "No support", edited: "Changed" };
const SPEAKER = { doctor: "Doctor", patient: "Patient", patient_guest: "Companion", unknown: "Unknown", note: "Note" };
const VARIANT = { clean: "Transcript", asr: "Raw speech-to-text", asrcorr: "Corrected speech-to-text", humantrans: "Manual transcript" };
const VERDICT = { evidence_found: "Evidence found", some_evidence: "Some evidence", no_evidence_found: "No evidence found" };
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const OWN_PREFIX = "own-";
const REQUEST_TIMEOUT_MS = 330_000;

const state = {
  health: null,
  user: null,
  encounters: [],
  cases: [],
  encounter: null, // the open visit
  lines: [], // the visit transcript, indexed
  variant: "clean",
  noteId: null,
  noteText: "",
  editingText: false, // "Edit as text" mode
  visitCheck: null,
  audit: null,
  isSample: false,
  selected: null, // { kind, id }
  showFullTranscript: false,
  additionState: {}, // addition_id -> "added" | "dismissed"
  flagState: {}, // flag_id -> "noted" | "dismissed"
  followState: {}, // follow-up index -> "seen" | "dismissed"
  visitState: {}, // visit-check item id -> "dismissed"
  issueCursor: -1,
  ask: { loading: false, result: null, suggestions: [] },
  busy: "", // which AI step is running
};

const $ = (id) => document.getElementById(id);
const pad = (n, width = 2) => String(n).padStart(width, "0");

// replaceChildren() prints null and undefined as text, so skipped parts are removed first.
function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

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

// ------------------------------------------------------------------ API

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    const message = error && error.name === "TimeoutError"
      ? "The server took too long to answer. Try again."
      : "Could not reach the server. Check your connection and try again.";
    throw Object.assign(new Error(message), { status: 0 });
  }
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    let message = `Something went wrong (${response.status}). Try again.`;
    if (body && typeof body.detail === "string") message = body.detail;
    else if (response.status === 422) message = "The request is not valid. Check the transcript and note and try again.";
    throw Object.assign(new Error(message), { status: response.status });
  }
  if (body === null) throw Object.assign(new Error("The server sent an unexpected answer. Try again."), { status: response.status });
  return body;
}

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });

function isValidAudit(result) {
  return Boolean(result && typeof result.note_text === "string" && Array.isArray(result.claims) && Array.isArray(result.transcript));
}

// The visit as the API wants it: from the dataset, or the user's own transcript.
function visitSource() {
  const enc = state.encounter;
  return enc.own
    ? { transcript_text: enc.transcript_text, chief_complaint: enc.chief_complaint || "" }
    : { encounter_id: enc.encounter_id, variant: state.variant };
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
const NOTE_FORMATS = { soap: "SOAP", plain: "Plain text" };
const noteFormat = () => (store.read("noteFormat", "soap") in NOTE_FORMATS ? store.read("noteFormat", "soap") : "soap");
const reviews = () => store.read("reviews", {});

function saveReviewSummary() {
  if (!state.audit) return;
  const all = reviews();
  all[state.encounter.encounter_id] = { problems: issues().length, missing: state.audit.suggested_additions.length, at: Date.now() };
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
  $("edit-btn").addEventListener("click", toggleTextEdit);
  $("sample-btn").addEventListener("click", showSample);
  $("delete-btn").addEventListener("click", deleteVisit);
  $("ask-form").addEventListener("submit", (e) => { e.preventDefault(); runAsk($("ask-input").value); });
  $("backdrop").addEventListener("click", () => select(null));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") select(null); });
  window.addEventListener("hashchange", route);

  try {
    [state.health, state.encounters, state.user, state.cases] = await Promise.all([
      api("/api/health"), api("/api/encounters"), api("/api/me"), api("/api/cases").catch(() => []),
    ]);
  } catch (error) {
    showBanner(`Could not reach the server: ${error.message}`);
    return;
  }
  const initialsOf = state.user.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  $("user-chip").replaceChildren(el("span", { class: "avatar", text: initialsOf }), el("span", { class: "user-name", text: state.user.name }));
  $("replay-badge").hidden = !state.health.replay;
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
  let status = el("span", { class: "p-status", text: visit.hasNote === false ? "No note yet" : "Not checked" });
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
  const own = ownVisits().map((v) => ({ id: v.id, patient: v.patient, reason: v.reason, hasNote: Boolean((v.note || "").trim()) })).filter(matches);
  const fromData = state.encounters.map((e) => ({ id: e.encounter_id, patient: e.patient, reason: e.chief_complaint, featured: e.featured })).filter(matches);
  const demo = fromData.filter((v) => v.featured);
  const rest = fromData.filter((v) => !v.featured);

  renderCaseCards(!query);
  $("group-own").hidden = !own.length;
  $("list-own").replaceChildren(...own.map(patientRow));
  $("group-demo").hidden = !demo.length;
  $("list-demo").replaceChildren(...demo.map(patientRow));
  $("all-title").textContent = `All visits (${rest.length})`;
  $("list-all").replaceChildren(...(rest.length ? rest.map(patientRow) : [el("li", { class: "empty-row", text: "No visits match your search." })]));
}

// Example cases start a visit with one click, as if the files had been uploaded.
function renderCaseCards(show) {
  const cards = [];
  for (const c of state.cases) {
    const wrong = Object.keys(c.notes).find((n) => n.includes("wrong"));
    cards.push(caseCard("Full flow", `${c.title}: upload only the transcript, check the visit, draft the note, then check it.`,
      () => startCase(c, null)));
    if (wrong) {
      cards.push(caseCard("Add-on", `${c.title}: transcript and an existing note with errors, straight to the check.`,
        () => startCase(c, wrong)));
    }
  }
  $("group-cases").hidden = !show || !cards.length;
  $("case-cards").replaceChildren(...cards);
}

function caseCard(label, text, run) {
  return el("button", { type: "button", class: "case-card", onclick: run },
    el("span", { class: "case-label", text: label }),
    el("span", { class: "case-text", text }),
    el("span", { class: "case-go", text: "Start →" }));
}

function startCase(c, noteFile) {
  const visit = {
    id: `${OWN_PREFIX}${Date.now().toString(36)}`,
    patient: c.patient || "Example patient",
    reason: c.reason || c.title,
    transcript: normalizeTranscript(c.transcript),
    note: noteFile ? c.notes[noteFile] : "",
    created: Date.now(),
  };
  store.write("visits", [visit, ...ownVisits()]);
  location.hash = `#/visit/${visit.id}`;
}

// ------------------------------------------------------------------ new visit

const UPLOAD_LIMITS = { transcript: 100_000, note: 10_000 };
const UPLOAD_HINTS = {
  transcript: "A .txt file from speech-to-text. Drop it here or click to choose.",
  note: "Have a note already? Upload it to go straight to the check. Leave it out to get a draft.",
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

// The same indexing as the server (backend/transcript.py), for showing the
// transcript before any AI step has run.
function indexTranscript(text) {
  const lines = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const m = /^\[([A-Za-z_]+)\]\s*(.*)$/.exec(line);
    const known = m && ["doctor", "patient", "patient_guest"].includes(m[1].toLowerCase());
    const speaker = known ? m[1].toLowerCase() : "unknown";
    const body = known ? m[2].trim() : line;
    if (body) lines.push({ line_id: lines.length + 1, speaker, text: body });
  }
  return lines;
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
    let text;
    try {
      text = await file.text();
    } catch {
      return showUploadError(kind, `${file.name} could not be read. Try saving it again as a .txt file.`);
    }
    text = text.replace(/^﻿/, "").replace(/\r\n?/g, "\n");
    if (text.includes("\u0000")) return showUploadError(kind, `${file.name} does not look like a text file. Upload a .txt file.`);
    if (!text.trim()) return showUploadError(kind, `${file.name} is empty.`);
    if (kind === "transcript") text = normalizeTranscript(text);
    else text = text.trim();
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
    note: $("new-note").value.replace(/\r\n?/g, "\n").trim(),
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
      transcript: indexTranscript(own.transcript),
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
  state.lines = encounter.transcript;
  state.variant = variant;
  if (!sameVisit) {
    state.visitCheck = null;
    state.visitState = {};
    state.ask = { loading: false, result: null, suggestions: [] };
    $("ask-input").value = "";
  }

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
  loadSuggestions();
}

async function loadSuggestions() {
  try {
    const { questions } = await post("/api/ask/suggestions", visitSource());
    state.ask.suggestions = questions;
  } catch {
    state.ask.suggestions = [];
  }
  renderAsk();
}

function currentNote() {
  return state.encounter.notes.find((n) => n.id === state.noteId);
}

function selectNote(noteId) {
  state.noteId = noteId;
  $("note-select").value = noteId;
  state.noteText = currentNote().note_text;
  state.editingText = false;
  resetAudit();
}

function resetAudit() {
  state.audit = null;
  state.isSample = false;
  state.selected = null;
  state.additionState = {};
  state.flagState = {};
  state.followState = {};
  state.issueCursor = -1;
  render();
}

function saveOwnNote() {
  if (!state.encounter.own) return;
  store.write("visits", ownVisits().map((v) => (v.id === state.encounter.encounter_id ? { ...v, note: state.noteText } : v)));
  state.encounter.notes[0].note_text = state.noteText;
}

function toggleTextEdit() {
  if (state.editingText) {
    const text = $("note-input").value.replace(/\r\n?/g, "\n");
    if (text !== state.noteText) {
      state.noteText = text;
      markAuditStale();
    }
    state.editingText = false;
    saveOwnNote();
  } else {
    state.editingText = true;
  }
  render();
  if (state.editingText) $("note-input").focus();
}

// After a big edit the old positions no longer hold: every statement is
// re-located by its text, and the ones that are gone count as changed.
function markAuditStale() {
  if (!state.audit) return;
  state.audit.note_text = state.noteText;
  let cursor = 0;
  for (const claim of state.audit.claims) {
    const idx = claim.note_quote ? state.noteText.indexOf(claim.note_quote, cursor) : -1;
    const found = idx >= 0 ? idx : state.noteText.indexOf(claim.note_quote);
    if (claim.note_quote && found >= 0) {
      claim.note_span = { start: found, end: found + claim.note_quote.length };
      cursor = claim.note_span.end;
    } else {
      claim.note_span = null;
      markChanged(claim);
    }
  }
}

function markChanged(claim) {
  if (claim.status === "edited") return;
  claim.status = "edited";
  claim.explanation = "Changed by you. Check the note again to verify it.";
  claim.correction = "";
  state.audit.changed = (state.audit.changed || 0) + 1;
}

// ------------------------------------------------------------------ AI steps

function setBusy(step, text) {
  state.busy = step;
  $("status-text").textContent = text || "";
  render();
}

async function runVisitCheck() {
  setBusy("visit-check", "");
  try {
    state.visitCheck = await post("/api/visit-check", visitSource());
    state.lines = state.visitCheck.transcript;
    state.visitState = {};
  } catch (error) {
    showBanner(error.message);
  }
  setBusy("");
}

async function runDraft() {
  setBusy("draft", "");
  try {
    const result = await post("/api/draft-note", { ...visitSource(), note_format: noteFormat() });
    state.noteText = result.note_text;
    currentNote().note_text = result.note_text;
    saveOwnNote();
    resetAudit();
  } catch (error) {
    showBanner(error.message);
  }
  setBusy("");
}

function writeOwnNote() {
  state.editingText = true;
  render();
  $("note-input").focus();
}

async function runAudit() {
  if (state.editingText) {
    state.noteText = $("note-input").value.replace(/\r\n?/g, "\n");
    state.editingText = false;
  }
  const noteText = state.noteText.trim();
  if (!noteText) {
    showBanner("Write or paste a note first.");
    return;
  }
  saveOwnNote();
  hideBanner();
  setBusy("audit", "Checking the note against the visit…");
  try {
    showAudit(await post("/api/audit", { ...visitSource(), note_text: noteText }), false);
  } catch (error) {
    const note = currentNote();
    const canShowSample = note && note.sample && noteText === note.note_text.trim();
    showBanner(error.message, canShowSample ? { label: "Show simulated review", run: showSample } : null);
  }
  setBusy("");
}

async function showSample() {
  const note = currentNote();
  if (!note || !note.sample) return;
  try {
    showAudit(await api(`/samples/${encodeURIComponent(note.sample)}.json`), true);
  } catch (error) {
    showBanner(`Could not load the simulated review: ${error.message}`);
  }
}

function showAudit(result, isSample) {
  if (!isValidAudit(result)) {
    showBanner("The review came back incomplete and could not be shown. Try again.");
    return;
  }
  state.audit = structuredClone(result);
  state.audit.suggested_additions = state.audit.suggested_additions || [];
  state.audit.follow_up_questions = state.audit.follow_up_questions || [];
  state.audit.decision_flags = state.audit.decision_flags || [];
  state.audit.claims = state.audit.claims.filter((c) => c && typeof c.claim_id === "string" && STATUS[c.status]);
  state.audit.changed = 0;
  state.lines = state.audit.transcript;
  state.noteText = state.audit.note_text;
  state.isSample = isSample;
  state.editingText = false;
  state.selected = null;
  state.additionState = {};
  state.flagState = {};
  state.followState = {};
  state.issueCursor = -1;
  saveReviewSummary();
  render();
}

async function runAsk(question) {
  question = (question || "").trim();
  if (question.length < 3 || state.ask.loading) return;
  $("ask-input").value = question;
  state.ask.loading = true;
  state.ask.result = null;
  renderAsk();
  try {
    state.ask.result = await post("/api/ask", { ...visitSource(), question, note_text: state.noteText.slice(0, 10_000) });
  } catch (error) {
    state.ask.result = { error: error.message };
  }
  state.ask.loading = false;
  renderAsk();
}

// ------------------------------------------------------------------ rendering the visit

function stage() {
  if (state.noteText.trim() || state.editingText) return state.audit ? "checked" : "note";
  return "visit";
}

function render() {
  const current = stage();
  $("visit-card").hidden = current !== "visit";
  $("note-card").hidden = current === "visit";
  renderStepper(current);
  renderVisitCard();

  $("journal-body").hidden = state.editingText;
  $("note-input").hidden = !state.editingText;
  if (state.editingText && document.activeElement !== $("note-input")) $("note-input").value = state.noteText;
  $("edit-btn").textContent = state.editingText ? "Done" : "Edit as text";
  $("audit-btn").textContent = state.audit ? "Check again" : "Check note";
  $("audit-btn").hidden = Boolean(state.audit) && !(state.audit.changed > 0);
  $("audit-btn").disabled = state.busy === "audit";
  const note = currentNote();
  $("sample-btn").hidden = Boolean(state.audit) || !(note && note.sample && state.noteText === note.note_text);

  renderJournal();
  renderStats();
  renderFlags();
  renderAdditions();
  renderFollowUps();
  renderAsk();
  renderDrawer();
}

function renderStepper(current) {
  const steps = [
    { label: "Visit check", done: Boolean(state.visitCheck), active: current === "visit" },
    { label: "Note", done: current !== "visit", active: current === "note" },
    { label: "Note check", done: current === "checked", active: current === "checked" },
  ];
  $("stepper").replaceChildren(...steps.map((s, i) =>
    el("li", { class: `step${s.done ? " is-done" : ""}${s.active ? " is-active" : ""}`, "aria-current": s.active ? "step" : null },
      el("span", { class: "step-dot", text: s.done ? "✓" : String(i + 1), "aria-hidden": "true" }),
      el("span", { class: "step-label", text: s.label }))));
}

// Step 1: right after the visit, before any note exists. The visit check is
// feedback for the clinician (what may have been forgotten, which decisions
// deserve a second look). Nothing from it is written into the note.
function renderVisitCard() {
  if (stage() !== "visit") return;
  const turns = state.lines.length;
  const checked = Boolean(state.visitCheck);
  $("visit-card-title").textContent = checked ? "Visit check" : "The visit is recorded";
  $("visit-card-text").textContent = checked
    ? "Feedback for you before you write the note. Nothing here is added to the note."
    : `${turns} speaker turns. Get a quick check for forgotten questions and decisions worth a second look, then write the note.`;

  const box = $("visit-check");
  box.hidden = !checked;
  if (checked) {
    const vc = state.visitCheck;
    const group = (title, items) => items.length
      ? el("section", { class: "check-group" }, el("h3", { class: "check-title" }, el("span", { text: title }), el("span", { class: "count", text: String(items.length) })), el("ul", { class: "rows" }, items))
      : null;
    fill(box,
      group("Questions you may have missed", vc.missed.map((m) =>
        feedbackRow(m.item_id, m.title || m.question, m.title ? m.question : m.why, m.category, null, () => select({ kind: "missed", id: m.item_id })))),
      group("Decisions worth a second look", vc.decision_flags.map((f) =>
        feedbackRow(f.flag_id, f.title, f.concern, f.category, f.severity === "important" ? "is-important" : "is-consider", () => select({ kind: "vflag", id: f.flag_id })))),
      !vc.missed.length && !vc.decision_flags.length ? el("p", { class: "all-clear", text: "Nothing stood out. Go ahead and write the note." }) : null,
    );
  }

  const busy = state.busy;
  const actions = [];
  if (!checked) {
    actions.push(el("button", { type: "button", class: "btn btn-primary", disabled: Boolean(busy), onclick: runVisitCheck, text: busy === "visit-check" ? "Checking the visit…" : "Check the visit" }));
    actions.push(el("button", { type: "button", class: "btn btn-ghost", disabled: Boolean(busy), onclick: runDraft, text: busy === "draft" ? "Writing…" : "Skip and draft the note" }));
  } else {
    actions.push(el("button", { type: "button", class: "btn btn-primary", disabled: Boolean(busy), onclick: runDraft, text: busy === "draft" ? "Writing the note…" : "Draft the note" }));
  }
  actions.push(el("button", { type: "button", class: "btn btn-quiet", disabled: Boolean(busy), onclick: writeOwnNote, text: "Write it myself" }));
  actions.push(el("button", { type: "button", class: "btn btn-quiet", onclick: () => select({ kind: "transcript", id: "all" }), text: "Show transcript" }));
  actions.push(formatSwitch());
  $("visit-actions").replaceChildren(...actions);
}

// SOAP or plain text, whichever the clinic uses. Remembered in this browser.
function formatSwitch() {
  const current = noteFormat();
  return el("span", { class: "format-switch" },
    el("span", { class: "format-label", id: "format-label", text: "Note format" }),
    el("span", { class: "segmented", role: "radiogroup", "aria-labelledby": "format-label" },
      Object.entries(NOTE_FORMATS).map(([key, label]) => el("button", {
        type: "button",
        role: "radio",
        "aria-checked": String(current === key),
        text: label,
        onclick: () => { store.write("noteFormat", key); renderVisitCard(); },
      }))));
}

// One row layout for every AI point: a short heading, the text under it, and
// the category as a quiet label. A dot marks how important it is.
function rowBody(title, body, meta, dot, open) {
  return el(open ? "button" : "span", { type: open ? "button" : null, class: `row-main${open ? "" : " static"}`, onclick: open },
    dot ? el("span", { class: `dot-mark ${dot}`, "aria-hidden": "true" }) : null,
    el("span", { class: "row-body" },
      el("span", { class: "row-head" },
        el("span", { class: "row-text", text: title }),
        meta ? el("span", { class: "row-meta", text: meta }) : null),
      body ? el("span", { class: "row-why", text: body }) : null));
}

function feedbackRow(id, title, body, meta, dot, open) {
  const status = state.visitState[id];
  const set = (value) => { if (value) state.visitState[id] = value; else delete state.visitState[id]; renderVisitCard(); renderStats(); };
  return el("li", { class: `row${status ? " is-done" : ""}${isSelected("missed", id) || isSelected("vflag", id) ? " is-selected" : ""}` },
    rowBody(title, body, meta, dot, open),
    rowActions(status
      ? { doneLabel: status === "seen" ? "Seen" : "Not relevant", undo: () => set(null) }
      : { seen: () => set("seen"), dismiss: () => set("dismissed") }));
}

function issues() {
  if (!state.audit) return [];
  return state.audit.claims
    .filter((c) => (c.status === "contradicted" || c.status === "unsupported") && !c.kept)
    .sort((a, b) => (a.note_span ? a.note_span.start : 1e9) - (b.note_span ? b.note_span.start : 1e9));
}

function openFlags(list) {
  return (list || []).filter((f) => state.flagState[f.flag_id] !== "dismissed");
}

function renderStats() {
  const box = $("stats");
  let tiles = [];
  const notes = [];
  if (state.audit) {
    const claims = state.audit.claims;
    const problems = issues();
    const backed = claims.filter((c) => c.status === "supported" || c.status === "inferred").length;
    const pct = claims.length ? Math.round((100 * backed) / claims.length) : 0;
    const missing = state.audit.suggested_additions.filter((a) => !state.additionState[a.addition_id]).length;
    const flags = openFlags(state.audit.decision_flags).length;
    tiles = [
      stat(problems.length, problems.length === 1 ? "problem in the note" : "problems in the note", problems.length ? "is-bad" : "is-good", nextIssue),
      stat(missing, missing === 1 ? "thing missing" : "things missing", "", () => scrollToSection("additions-section")),
      stat(flags, flags === 1 ? "decision worth a second look" : "decisions worth a second look", flags ? "is-warn" : "", () => scrollToSection("flags-section")),
    ];
    notes.push(`${pct}% of ${claims.length} statements are backed by the visit.`);
    if (state.isSample) notes.push(sampleNote(state.audit.meta.model));
    if (state.audit.changed) notes.push(`${state.audit.changed} ${state.audit.changed === 1 ? "statement has" : "statements have"} changed since the check. Choose Check again to verify.`);
    for (const w of state.audit.warnings || []) notes.push(w);
  } else if (state.visitCheck && stage() === "visit") {
    const vc = state.visitCheck;
    tiles = [
      stat(vc.missed.filter((m) => !state.visitState[m.item_id]).length, "questions you may have missed", vc.missed.length ? "is-warn" : "is-good", null),
      stat(vc.decision_flags.filter((f) => !state.visitState[f.flag_id]).length, "decisions worth a second look", vc.decision_flags.length ? "is-warn" : "is-good", null),
    ];
  }
  box.hidden = !tiles.length;
  box.className = `stats cols-${tiles.length || 3}`;
  box.replaceChildren(...tiles, ...notes.map((n) => el("p", { class: "stats-note", text: n })));
}

function stat(value, label, cls, onclick) {
  return el(onclick ? "button" : "div", { type: onclick ? "button" : null, class: `stat ${cls}${onclick ? " is-clickable" : ""}`, onclick },
    el("span", { class: "stat-value", text: value }),
    el("span", { class: "stat-label", text: label }));
}

function scrollToSection(id) {
  const node = $(id);
  if (node && !node.hidden) node.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" });
}

function sampleNote(model) {
  if ((model || "").startsWith("simulated")) {
    return "Simulated review: a Claude agent answered the exact request the app sends to the API, and the result went through the same receipt checks.";
  }
  return "Example review, written by hand to show the interface. Not generated by Claude.";
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

// ---------------------------------------------- the note, as an editable document

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
  if (state.editingText || stage() === "visit") return;
  const text = state.noteText;
  const sections = parseNote(text);
  const claims = state.audit ? state.audit.claims.filter((c) => c.note_span).sort((a, b) => a.note_span.start - b.note_span.start) : [];
  const body = $("journal-body");
  if (!sections.length) {
    body.replaceChildren(el("p", { class: "status-text", text: "The note is empty. Choose Edit as text to write it." }));
    return;
  }
  const plain = sections.length === 1 && sections[0].label === "Note";
  body.replaceChildren(
    ...sections.map((section) =>
      el("section", { class: plain ? "jsection is-plain" : "jsection" },
        el("h2", { class: "jlabel", text: section.label }),
        el("div", { class: "jrows" },
          section.rows.map((row) => {
            const editable = el("span", {
              class: "jtext",
              contenteditable: "true",
              spellcheck: "false",
              "data-start": row.start,
              "data-end": row.end,
              oninput: onRowInput,
              onkeydown: onRowKeydown,
              onpaste: onRowPaste,
              onblur: onRowBlur,
            });
            appendWithClaims(editable, text, row.start, row.end, claims);
            return el("p", { class: "jrow" }, row.sublabel ? el("span", { class: "sublabel", text: `${row.sublabel}: ` }) : null, editable);
          })))),
  );
}

// Editing a sentence in place: the note text is updated as you type; the
// positions of statements after it shift; statements in the sentence that no
// longer read the same are marked as changed.
function onRowInput(event) {
  const node = event.currentTarget;
  const start = Number(node.dataset.start);
  const end = Number(node.dataset.end);
  const newText = node.textContent.replace(/\n/g, " ");
  const delta = newText.length - (end - start);
  state.noteText = state.noteText.slice(0, start) + newText + state.noteText.slice(end);
  node.dataset.end = String(start + newText.length);
  for (const other of document.querySelectorAll(".jtext")) {
    if (other !== node && Number(other.dataset.start) >= end) {
      other.dataset.start = String(Number(other.dataset.start) + delta);
      other.dataset.end = String(Number(other.dataset.end) + delta);
    }
  }
  if (state.audit) {
    state.audit.note_text = state.noteText;
    for (const claim of state.audit.claims) {
      if (!claim.note_span) continue;
      if (claim.note_span.start >= end) {
        claim.note_span = { start: claim.note_span.start + delta, end: claim.note_span.end + delta };
      } else if (claim.note_span.end > start && claim.note_span.start < end) {
        const idx = newText.indexOf(claim.note_quote);
        if (idx >= 0) claim.note_span = { start: start + idx, end: start + idx + claim.note_quote.length };
        else { claim.note_span = null; markChanged(claim); }
      }
    }
  }
  state.dirty = true;
  // Keep the summary and "Check again" current while typing, without
  // re-rendering the note (that would move the cursor).
  renderStats();
  $("audit-btn").hidden = Boolean(state.audit) && !(state.audit.changed > 0);
}

function onRowKeydown(event) {
  if (event.key === "Enter") {
    event.preventDefault();
    event.currentTarget.blur();
  }
}

function onRowPaste(event) {
  event.preventDefault();
  const text = (event.clipboardData.getData("text/plain") || "").replace(/\s*\n\s*/g, " ");
  document.execCommand("insertText", false, text);
}

function onRowBlur() {
  if (!state.dirty) return;
  state.dirty = false;
  saveOwnNote();
  saveReviewSummary();
  setTimeout(render, 0); // let a click on another sentence land first
}

function appendWithClaims(parent, text, start, end, claims) {
  let pos = start;
  for (const claim of claims) {
    const s = Math.max(claim.note_span.start, start);
    const e = Math.min(claim.note_span.end, end);
    if (e <= s || s < pos) continue;
    if (s > pos) parent.append(text.slice(pos, s));
    const classes = ["claim", `st-${claim.status}`];
    if (claim.added || claim.fixed === "corrected") classes.push("is-added");
    if (claim.kept) classes.push("is-kept");
    if (isSelected("claim", claim.claim_id)) classes.push("is-selected");
    parent.append(el("span", {
      class: classes.join(" "),
      "data-claim": claim.claim_id,
      title: claim.status === "contradicted" || claim.status === "unsupported" ? `${STATUS[claim.status]}: click to see why` : null,
      onclick: () => select({ kind: "claim", id: claim.claim_id }),
    }, text.slice(s, e)));
    pos = e;
  }
  if (pos < end) parent.append(text.slice(pos, end));
}

// ------------------------------------------------------------------ second look, additions, follow-ups

// Every AI point can be acknowledged or dismissed with one click, right on its row.
function rowActions({ seen, dismiss, undo, doneLabel }) {
  if (doneLabel) {
    return el("span", { class: "row-actions" },
      el("span", { class: "row-done", text: doneLabel }),
      undo ? el("button", { type: "button", class: "icon-action", title: "Undo", "aria-label": "Undo", text: "\u21ba", onclick: undo }) : null);
  }
  return el("span", { class: "row-actions" },
    seen ? el("button", { type: "button", class: "icon-action is-seen", title: "Seen", "aria-label": "Mark as seen", text: "\u2713", onclick: seen }) : null,
    dismiss ? el("button", { type: "button", class: "icon-action is-dismiss", title: "Dismiss", "aria-label": "Dismiss", text: "\u00d7", onclick: dismiss }) : null);
}

function sectionTitle(id, text, onAll) {
  fill($(id), el("span", { text }), onAll ? el("button", { type: "button", class: "link-btn quiet small", text: "Mark all as seen", onclick: onAll }) : null);
}

function flagRow(flag, kind) {
  const status = state.flagState[flag.flag_id];
  return el("li", { class: `row${status ? " is-done" : ""}${isSelected(kind, flag.flag_id) ? " is-selected" : ""}` },
    rowBody(flag.title, flag.concern, flag.category, flag.severity === "important" ? "is-important" : "is-consider", () => select({ kind, id: flag.flag_id })),
    kind === "flag"
      ? rowActions(status
        ? { doneLabel: status === "noted" ? "Seen" : "Dismissed", undo: () => { delete state.flagState[flag.flag_id]; render(); } }
        : { seen: () => { state.flagState[flag.flag_id] = "noted"; render(); }, dismiss: () => { state.flagState[flag.flag_id] = "dismissed"; render(); } })
      : el("span", { class: "p-chevron", "aria-hidden": "true", text: "›" }));
}

function renderFlags() {
  const flags = state.audit ? state.audit.decision_flags : [];
  $("flags-section").hidden = !flags.length;
  const open = flags.filter((f) => !state.flagState[f.flag_id]);
  sectionTitle("flags-title", "Decisions worth a second look", open.length > 1 ? () => { for (const f of open) state.flagState[f.flag_id] = "noted"; render(); } : null);
  $("flags").replaceChildren(...flags.map((f) => flagRow(f, "flag")));
}

function renderAdditions() {
  const additions = state.audit ? state.audit.suggested_additions : [];
  $("additions-section").hidden = !additions.length;
  $("additions").replaceChildren(
    ...additions.map((a) => {
      const status = state.additionState[a.addition_id];
      return el("li", { class: `row${status ? " is-done" : ""}${isSelected("addition", a.addition_id) ? " is-selected" : ""}` },
        rowBody(a.text, null, null, `prio-${a.priority}`, () => select({ kind: "addition", id: a.addition_id })),
        status === "added" ? el("span", { class: "row-done", text: "Added" })
          : status === "dismissed" ? rowActions({ doneLabel: "Dismissed", undo: () => { delete state.additionState[a.addition_id]; render(); } })
          : el("span", { class: "row-actions" },
            el("button", { type: "button", class: "icon-action is-add", title: "Add to note", "aria-label": "Add to note", text: "+", onclick: () => addAddition(a) }),
            el("button", { type: "button", class: "icon-action is-dismiss", title: "Dismiss", "aria-label": "Dismiss", text: "\u00d7", onclick: () => { state.additionState[a.addition_id] = "dismissed"; render(); } })));
    }),
  );
}

function renderFollowUps() {
  const items = state.audit ? state.audit.follow_up_questions : [];
  $("followups-section").hidden = !items.length;
  const open = items.filter((_, i) => !state.followState[i]);
  fill($("followups-title"), el("span", { text: "Questions you may have missed" }), el("span", { class: "count", text: open.length ? String(open.length) : "All seen" }));
  fill($("followups"),
    ...items.map((f, i) => {
      const status = state.followState[i];
      return el("li", { class: `row${status ? " is-done" : ""}` },
        rowBody(f.title || f.question, f.title ? f.question : f.why, f.category, null, null),
        rowActions(status
          ? { doneLabel: status === "seen" ? "Seen" : "Dismissed", undo: () => { delete state.followState[i]; render(); } }
          : { seen: () => { state.followState[i] = "seen"; render(); }, dismiss: () => { state.followState[i] = "dismissed"; render(); } }));
    }),
    open.length > 1 ? el("li", { class: "row-footer" }, el("button", { type: "button", class: "link-btn quiet small", text: "Mark all as seen",
      onclick: () => { items.forEach((_, i) => { if (!state.followState[i]) state.followState[i] = "seen"; }); render(); } })) : null,
  );
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

function addFlagToNote(flag) {
  const line = `To follow up: ${flag.consider || flag.title}`;
  const separator = state.noteText === "" || state.noteText.endsWith("\n") ? "" : "\n";
  state.noteText = `${state.noteText}${separator}${line}`;
  if (state.audit) state.audit.note_text = state.noteText;
  state.flagState[flag.flag_id] = "noted";
  state.selected = null;
  saveOwnNote();
  render();
}

// ------------------------------------------------------------------ ask the record

function renderAsk() {
  const section = $("ask-section");
  section.hidden = !state.encounter;
  if (!state.encounter) return;
  $("ask-btn").disabled = state.ask.loading;
  $("ask-btn").textContent = state.ask.loading ? "Searching…" : "Ask";
  $("ask-chips").replaceChildren(...state.ask.suggestions.map((q) =>
    el("button", { type: "button", class: "chip-btn", text: q, disabled: state.ask.loading, onclick: () => runAsk(q) })));

  const box = $("ask-answer");
  const result = state.ask.result;
  box.hidden = !result;
  if (!result) return;
  if (result.error) {
    box.replaceChildren(el("p", { class: "ask-error", text: result.error }));
    return;
  }
  const supports = result.findings.filter((f) => f.stance === "supports");
  const against = result.findings.filter((f) => f.stance === "against");
  const findingList = (title, list) => list.length ? el("div", {},
    el("h3", { class: "subhead", text: title }),
    el("ul", { class: "rows" }, list.map((f) => el("li", { class: `row${isSelected("finding", f.finding_id) ? " is-selected" : ""}` },
      el("button", { type: "button", class: "row-main", onclick: () => select({ kind: "finding", id: f.finding_id }) },
        el("span", { class: `stance stance-${f.stance}`, "aria-hidden": "true", text: f.stance === "supports" ? "+" : "\u2212" }),
        el("span", { class: "row-body" }, el("span", { class: "row-text", text: f.text }))),
      el("span", { class: "p-chevron", "aria-hidden": "true", text: "›" }))))) : null;

  fill(box,
    el("div", { class: "ask-head" },
      el("span", { class: `verdict verdict-${result.verdict}`, text: VERDICT[result.verdict] }),
      el("span", { class: "ask-question", text: result.question })),
    el("p", { class: "ask-summary", text: result.summary }),
    findingList("Points in favour", supports),
    findingList("Points against", against),
    result.where_to_look.length ? el("div", {}, el("h3", { class: "subhead", text: "Check yourself" }),
      el("ul", { class: "plain-list" }, result.where_to_look.map((w) => el("li", { text: w })))) : null,
    ...(result.warnings || []).map((w) => el("p", { class: "stats-note is-warning", text: w })),
    el("div", { class: "disclaimer", role: "note" },
      el("strong", { text: "AI can be wrong. " }),
      result.disclaimer),
  );
}

// ------------------------------------------------------------------ selection and the drawer

function isSelected(kind, id) {
  return Boolean(state.selected && state.selected.kind === kind && state.selected.id === id);
}

function selectedItem() {
  const s = state.selected;
  if (!s) return null;
  if (s.kind === "transcript") return { evidence: [] };
  if (s.kind === "claim") return state.audit && state.audit.claims.find((c) => c.claim_id === s.id);
  if (s.kind === "addition") return state.audit && state.audit.suggested_additions.find((a) => a.addition_id === s.id);
  if (s.kind === "flag") return state.audit && state.audit.decision_flags.find((f) => f.flag_id === s.id);
  if (s.kind === "missed") return state.visitCheck && state.visitCheck.missed.find((m) => m.item_id === s.id);
  if (s.kind === "vflag") return state.visitCheck && state.visitCheck.decision_flags.find((f) => f.flag_id === s.id);
  if (s.kind === "finding") return state.ask.result && state.ask.result.findings && state.ask.result.findings.find((f) => f.finding_id === s.id);
  return null;
}

function linesFor(kind) {
  if (kind === "finding" && state.ask.result && state.ask.result.record) return state.ask.result.record;
  return state.lines;
}

function select(selection) {
  if (document.activeElement && document.activeElement.classList && document.activeElement.classList.contains("jtext")) {
    // keep typing undisturbed: open the drawer without re-rendering the note
    state.selected = selection;
    state.showFullTranscript = selection && selection.kind === "transcript";
    renderDrawer();
    return;
  }
  state.selected = selection;
  state.showFullTranscript = Boolean(selection && selection.kind === "transcript");
  if (!$("view-visit").hidden) render();
}

function renderDrawer() {
  const item = selectedItem();
  const open = Boolean(item);
  $("drawer").hidden = !open;
  $("page").classList.toggle("has-drawer", open);
  $("backdrop").hidden = !(open && window.matchMedia("(max-width: 1099px)").matches);
  if (!open) return;

  const kind = state.selected.kind;
  const lines = linesFor(kind);
  const tone = kind === "claim" ? item.status : kind === "flag" || kind === "vflag" ? "flag" : "supported";
  const close = el("button", { type: "button", class: "icon-btn", "aria-label": "Close", text: "×", onclick: () => select(null) });
  const parts = [];

  if (kind === "transcript") {
    parts.push(el("div", { class: "drawer-head" }, el("span", { class: "pill neutral", text: "Transcript" }), close));
    parts.push(renderTranscriptList(lines, [], "supported", false));
    $("drawer-content").replaceChildren(el("div", { class: "drawer-content" }, parts));
    return;
  }

  const pill = {
    claim: () => el("span", { class: `pill st-${item.status}`, text: STATUS[item.status] }),
    addition: () => el("span", { class: "pill neutral", text: "Missing from the note" }),
    flag: () => el("span", { class: `pill ${item.severity === "important" ? "st-important" : "st-consider"}`, text: `Second look · ${item.category}` }),
    vflag: () => el("span", { class: `pill ${item.severity === "important" ? "st-important" : "st-consider"}`, text: `Second look · ${item.category}` }),
    missed: () => el("span", { class: "pill st-consider", text: `You may have missed · ${item.category}` }),
    finding: () => el("span", { class: `pill ${item.stance === "supports" ? "st-supported" : "st-consider"}`, text: item.stance === "supports" ? "Point in favour" : "Point against" }),
  }[kind]();
  parts.push(el("div", { class: "drawer-head" }, pill, close));

  if (kind === "claim") {
    parts.push(el("p", { class: "drawer-title", text: item.added ? item.text : item.note_quote }));
    parts.push(el("p", { class: "drawer-text", text: item.receipt_failed ? "The cited evidence could not be found in the visit." : item.explanation }));
    if ((item.status === "contradicted" || item.status === "unsupported") && item.note_span) {
      if (item.kept) {
        parts.push(el("p", { class: "drawer-fine" }, "You chose to keep this as written. ",
          el("button", { type: "button", class: "link-btn quiet small", text: "Undo", onclick: () => { item.kept = false; saveReviewSummary(); render(); } })));
      } else {
        parts.push(renderFixBox(item));
      }
    }
  } else if (kind === "addition") {
    parts.push(el("p", { class: "drawer-title", text: item.text }), el("p", { class: "drawer-text", text: item.why }));
    if (!state.additionState[item.addition_id]) {
      parts.push(el("div", { class: "drawer-actions" },
        el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Add to note", onclick: () => addAddition(item) }),
        el("button", { type: "button", class: "link-btn quiet", text: "Dismiss", onclick: () => { state.additionState[item.addition_id] = "dismissed"; select(null); } })));
    }
  } else if (kind === "flag" || kind === "vflag") {
    parts.push(el("p", { class: "drawer-title", text: item.title }), el("p", { class: "drawer-text", text: item.concern }));
    if (item.consider) parts.push(el("p", { class: "consider" }, el("strong", { text: "Consider: " }), item.consider));
    parts.push(el("p", { class: "drawer-fine", text: "A prompt to think again, not a verdict. You know the patient; you decide." }));
    if (kind === "flag" && !state.flagState[item.flag_id]) {
      parts.push(el("div", { class: "drawer-actions" },
        el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Add to follow-up", onclick: () => addFlagToNote(item) }),
        el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Noted", onclick: () => { state.flagState[item.flag_id] = "noted"; select(null); } }),
        el("button", { type: "button", class: "link-btn quiet", text: "Dismiss", onclick: () => { state.flagState[item.flag_id] = "dismissed"; select(null); } })));
    }
  } else if (kind === "missed") {
    parts.push(el("p", { class: "drawer-title", text: item.title || item.question }));
    if (item.title) parts.push(el("p", { class: "consider", text: item.question }));
    parts.push(el("p", { class: "drawer-text", text: item.why }));
  } else if (kind === "finding") {
    parts.push(el("p", { class: "drawer-title", text: item.text }));
  }

  parts.push(el("p", { class: "drawer-section", text: kind === "finding" ? "In the record" : "In the visit" }));
  if (item.evidence.length) parts.push(renderExcerpt(lines, item.evidence, tone));
  else parts.push(el("p", { class: "drawer-text", text: kind === "claim" ? "Nothing in the visit supports this." : "This is about something the visit did not cover." }));

  if (state.showFullTranscript) parts.push(el("div", { class: "full-transcript" }, renderTranscriptList(lines, item.evidence, tone, false)));
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

// ------------------------------------------------------------------ fixing a statement

// A replacement box already filled in with the suggested correction: edit it
// directly and choose Replace, or remove the statement.
function renderFixBox(claim) {
  const suggestion = claim.correction || "";
  const input = el("textarea", { class: "fix-input", rows: "2", id: "fix-input", "aria-label": "Replacement text",
    placeholder: "Nothing in the visit supports this. Remove it, or write what was actually said." });
  input.value = suggestion;
  const replace = el("button", { type: "button", class: `btn ${suggestion ? "btn-primary" : "btn-ghost"} btn-sm`, text: "Replace",
    onclick: () => {
      const text = input.value.trim();
      if (text) applyFix(claim, text, text === suggestion ? "corrected" : "edited");
    } });
  const remove = el("button", { type: "button", class: suggestion ? "link-btn quiet" : "btn btn-primary btn-sm", text: "Remove from note",
    onclick: () => applyFix(claim, "", "removed") });
  const sync = () => { replace.disabled = !input.value.trim(); };
  input.addEventListener("input", sync);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); replace.click(); } });
  sync();
  return el("div", { class: "fix" },
    el("label", { class: "fix-label", for: "fix-input", text: suggestion ? "Suggested wording" : "Replace with" }),
    input,
    el("div", { class: "drawer-actions" }, ...(suggestion ? [replace, remove] : [remove, replace]),
      el("button", { type: "button", class: "link-btn quiet", text: "Keep as written",
        onclick: () => { claim.kept = true; saveReviewSummary(); select(null); } })));
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
      audit.changed = (audit.changed || 0) + 1;
    }
    claim.correction = "";
  }
  state.noteText = audit.note_text;
  state.issueCursor = -1;
  saveOwnNote();
  saveReviewSummary();
  render();
}

// ------------------------------------------------------------------ transcript excerpts

function quotesByLine(evidence) {
  const map = new Map();
  for (const ev of evidence) {
    if (!map.has(ev.line_id)) map.set(ev.line_id, []);
    map.get(ev.line_id).push(ev.quote);
  }
  return map;
}

function lineItem(line, quotes, tone, width, compact = false) {
  let content;
  if (quotes) content = markQuotes(line.text, quotes, compact ? 90 : Infinity);
  else content = compact && line.text.length > 140 ? `${line.text.slice(0, 130).trimEnd()} …` : line.text;
  return el("li", { class: quotes ? `line is-cited st-${tone}` : "line" },
    el("span", { class: "ln", text: pad(line.line_id, width) }),
    el("span", { class: `spk spk-${line.speaker}`, text: SPEAKER[line.speaker] || line.speaker }),
    el("p", { class: "txt" }, content));
}

// The cited lines with one line of context on each side.
function renderExcerpt(lines, evidence, tone) {
  const width = Math.max(2, String(lines.length).length);
  const quotes = quotesByLine(evidence);
  const show = new Set();
  for (const id of quotes.keys()) for (const n of [id - 1, id, id + 1]) if (n >= 1 && n <= lines.length) show.add(n);
  const ids = [...show].sort((a, b) => a - b);
  const items = [];
  ids.forEach((id, i) => {
    if (i > 0 && id !== ids[i - 1] + 1) items.push(el("li", { class: "gap", text: "···", "aria-hidden": "true" }));
    items.push(lineItem(lines[id - 1], quotes.get(id), tone, width, true));
  });
  return el("ol", { class: "excerpt" }, items);
}

function renderTranscriptList(lines, evidence, tone) {
  const width = Math.max(2, String(lines.length).length);
  const quotes = quotesByLine(evidence);
  return el("ol", { class: "excerpt" }, lines.map((line) => lineItem(line, quotes.get(line.line_id), tone, width)));
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
