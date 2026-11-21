"use strict";

// All text from transcripts, notes and the model is untrusted input and is
// rendered with textContent, never innerHTML.

const STATUS = {
  supported: "Supported",
  inferred: "Inferred",
  contradicted: "Contradicted",
  unsupported: "Unsupported",
};
const STATUS_ORDER = ["supported", "inferred", "contradicted", "unsupported"];
const PRIORITY = { high: "High priority", medium: "Medium priority", low: "Low priority" };
const SPEAKER = { doctor: "Doctor", patient: "Patient", patient_guest: "Companion", unknown: "Unknown" };
const VARIANT_SHORT = { clean: "Transcript", asr: "Raw speech-to-text", asrcorr: "Corrected speech-to-text", humantrans: "Manual" };
const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  health: null,
  encounters: [],
  encounter: null, // response from /api/encounters/{id}
  variant: "clean",
  noteId: null,
  audit: null, // AuditResult (or the example)
  isSample: false,
  selected: null, // { kind: "claim" | "addition", id }
  additionState: {}, // addition_id -> "added" | "dismissed"
};

const $ = (id) => document.getElementById(id);

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

const pad = (n, width = 2) => String(n).padStart(width, "0");

// ------------------------------------------------------------------ API

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let body = null;
  try {
    body = await response.json();
  } catch {
    /* empty body */
  }
  if (!response.ok) {
    let message = `Something went wrong (${response.status}).`;
    if (body && typeof body.detail === "string") message = body.detail;
    else if (response.status === 422) message = "The note or request is not valid. Check the text and try again.";
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

// ------------------------------------------------------------------ messages

function showBanner(message, kind = "warn") {
  $("banner-text").textContent = message;
  $("banner").className = `banner is-${kind}`;
  $("banner").hidden = false;
}
function hideBanner() {
  $("banner").hidden = true;
}

// ------------------------------------------------------------------ start-up

async function init() {
  $("banner-close").addEventListener("click", hideBanner);
  $("encounter-select").addEventListener("change", (e) => loadEncounter(e.target.value, "clean"));
  $("note-select").addEventListener("change", (e) => selectNote(e.target.value));
  $("audit-btn").addEventListener("click", runAudit);
  $("sample-btn").addEventListener("click", showSample);
  $("edit-btn").addEventListener("click", enterEditMode);
  $("note-input").addEventListener("input", () => {
    if (state.audit) clearAudit();
    else updateSampleButton();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") select(null);
  });

  try {
    const [health, encounters] = await Promise.all([api("/api/health"), api("/api/encounters")]);
    state.health = health;
    state.encounters = encounters;
  } catch (error) {
    showBanner(`Could not reach the server: ${error.message}`, "error");
    return;
  }
  renderModeBadge();
  renderEncounterSelect();
  const first = state.encounters.find((e) => e.featured) || state.encounters[0];
  await loadEncounter(first.encounter_id, "clean");
}

function renderModeBadge() {
  const badge = $("mode-badge");
  const h = state.health;
  if (h.offline) badge.textContent = "Offline demo";
  else if (h.access_code_required) badge.textContent = "Live review needs an access code";
  else badge.textContent = "Live";
  badge.title = h.offline ? "Only precomputed reviews are available." : `Model: ${h.model}`;
  badge.hidden = false;
  $("access-field").hidden = !(h.access_code_required && !h.offline);
}

function renderEncounterSelect() {
  const select = $("encounter-select");
  select.replaceChildren();
  const label = (e) => `${e.patient || "Unknown patient"} · ${e.chief_complaint || e.dataset} · ${e.encounter_id}`;
  const featured = state.encounters.filter((e) => e.featured);
  if (featured.length) {
    select.append(el("optgroup", { label: "Demo cases" }, featured.map((e) => el("option", { value: e.encounter_id, text: label(e) }))));
  }
  select.append(
    el("optgroup", { label: `All visits (${state.encounters.length})` },
      state.encounters.filter((e) => !e.featured).map((e) => el("option", { value: e.encounter_id, text: label(e) }))),
  );
}

// ------------------------------------------------------------------ visit and note

async function loadEncounter(encounterId, variant) {
  let encounter;
  try {
    encounter = await api(`/api/encounters/${encodeURIComponent(encounterId)}?variant=${variant}`);
  } catch (error) {
    showBanner(error.message, "error");
    return;
  }
  const sameEncounter = state.encounter && state.encounter.encounter_id === encounterId;
  state.encounter = encounter;
  state.variant = variant;
  $("encounter-select").value = encounterId;
  renderVariantSwitch();
  renderTranscript();
  clearAudit();
  if (!sameEncounter) {
    const preferred = encounter.notes.find((n) => n.sample) || encounter.notes[0];
    renderNoteSelect();
    selectNote(preferred.id);
  }
}

function renderVariantSwitch() {
  const wrap = $("variant-switch");
  wrap.replaceChildren();
  for (const [key, label] of Object.entries(VARIANT_SHORT)) {
    const available = key in state.encounter.variants;
    if (!available && key === "humantrans") continue;
    wrap.append(
      el("button", {
        type: "button",
        role: "radio",
        "aria-checked": String(state.variant === key),
        disabled: !available,
        title: available ? state.encounter.variants[key] : "Not available for this visit",
        text: label,
        onclick: () => {
          if (state.variant !== key) loadEncounter(state.encounter.encounter_id, key);
        },
      }),
    );
  }
}

function renderNoteSelect() {
  $("note-select").replaceChildren(...state.encounter.notes.map((n) => el("option", { value: n.id, text: n.label })));
}

function currentNote() {
  return state.encounter.notes.find((n) => n.id === state.noteId);
}

function selectNote(noteId) {
  state.noteId = noteId;
  $("note-select").value = noteId;
  const note = currentNote();
  $("note-description").textContent = note.description || "";
  $("note-input").value = note.note_text;
  clearAudit();
}

function clearAudit() {
  state.audit = null;
  state.isSample = false;
  state.selected = null;
  state.additionState = {};
  $("note-edit").hidden = false;
  $("note-view").hidden = true;
  $("suggest-content").hidden = true;
  $("suggest-empty").hidden = false;
  $("receipt").hidden = true;
  updateSampleButton();
  renderTranscriptHighlights();
}

// The example only applies to the unchanged example note.
function updateSampleButton() {
  const note = currentNote();
  $("sample-btn").hidden = !(note && note.sample && $("note-input").value === note.note_text);
}

function enterEditMode() {
  const text = state.audit ? state.audit.note_text : $("note-input").value;
  clearAudit();
  $("note-input").value = text;
  updateSampleButton();
  $("note-input").focus();
}

// ------------------------------------------------------------------ review

async function runAudit() {
  const noteText = $("note-input").value.trim();
  if (!noteText) {
    showBanner("Type or paste a note first.", "warn");
    $("note-input").focus();
    return;
  }
  hideBanner();
  const headers = {};
  const code = $("access-code").value.trim();
  if (code) headers["X-Access-Code"] = code;

  $("audit-btn").disabled = true;
  $("loading").hidden = false;
  try {
    const result = await api("/api/audit", {
      method: "POST",
      headers,
      body: JSON.stringify({ encounter_id: state.encounter.encounter_id, variant: state.variant, note_text: noteText }),
    });
    showAudit(result, false);
  } catch (error) {
    const note = currentNote();
    const hint = note && note.sample ? " You can open the example review instead." : "";
    showBanner(`${error.message}${hint}`, "error");
  } finally {
    $("audit-btn").disabled = false;
    $("loading").hidden = true;
  }
}

async function showSample() {
  const note = currentNote();
  if (!note || !note.sample) return;
  let sample;
  try {
    sample = await api(`/samples/${encodeURIComponent(note.sample)}.json`);
  } catch (error) {
    showBanner(`Could not load the example: ${error.message}`, "error");
    return;
  }
  const variant = sample.variant || "clean";
  if (state.variant !== variant) await loadEncounter(state.encounter.encounter_id, variant);
  showAudit(sample, true);
  showBanner(
    "Example review, written by hand to show the interface. It went through the same receipt checks as live reviews, but it was not produced by Claude.",
    "info",
  );
}

function showAudit(result, isSample) {
  state.audit = structuredClone(result);
  state.isSample = isSample;
  state.selected = null;
  state.additionState = {};
  $("note-edit").hidden = true;
  $("note-view").hidden = false;
  $("suggest-empty").hidden = true;
  $("suggest-content").hidden = false;
  if (!isSample) hideBanner();
  renderAudit();
  const firstIssue = sortedIssues()[0];
  if (firstIssue) select({ kind: "claim", id: firstIssue.claim_id }, { scroll: true });
}

function renderAudit() {
  renderSummary();
  renderNote();
  renderIssues();
  renderAdditions();
  renderFollowUps();
  renderTranscriptHighlights();
  renderReceipt();
}

function summarize(claims) {
  const counts = { supported: 0, inferred: 0, contradicted: 0, unsupported: 0 };
  for (const c of claims) counts[c.status] += 1;
  const total = claims.length;
  const pct = total ? Math.round((100 * (counts.supported + counts.inferred)) / total) : 0;
  return { counts, total, pct };
}

function renderSummary() {
  const { counts, total, pct } = summarize(state.audit.claims);
  const bar = el("div", { class: "summary-bar", role: "img", "aria-label": STATUS_ORDER.map((s) => `${counts[s]} ${STATUS[s].toLowerCase()}`).join(", ") },
    STATUS_ORDER.filter((s) => counts[s]).map((s) => el("span", { class: `bg-${s}`, style: `width:${(100 * counts[s]) / total}%` })));
  const legend = el("div", { class: "summary-legend" },
    STATUS_ORDER.map((s) => el("span", {}, el("span", { class: `dot bg-${s}` }), el("b", { text: counts[s] }), ` ${STATUS[s].toLowerCase()}`)));
  $("summary").replaceChildren(
    el("div", { class: "summary-top" },
      el("span", { class: "summary-score" }, `${pct}%`, el("small", { text: `of ${total} statements have a receipt` })),
      el("span", { class: "meta", text: metaText() })),
    bar,
    legend,
  );
}

function metaText() {
  const m = state.audit.meta;
  if (state.isSample) return "Example, not produced by Claude";
  const source = VARIANT_SHORT[state.audit.variant] || "Custom transcript";
  return m.cached ? `${source} · cached` : `${source} · ${(m.latency_ms / 1000).toFixed(1)} s`;
}

function renderNote() {
  const text = state.audit.note_text;
  const spans = state.audit.claims.filter((c) => c.note_span).sort((a, b) => a.note_span.start - b.note_span.start);
  const container = $("note-rendered");
  container.replaceChildren();
  let pos = 0;
  for (const claim of spans) {
    const { start, end } = claim.note_span;
    if (start < pos) continue; // skip overlapping spans
    if (start > pos) container.append(text.slice(pos, start));
    const open = () => select({ kind: "claim", id: claim.claim_id }, { scroll: true });
    container.append(
      el("span", {
        class: `claim st-${claim.status}${claim.added ? " is-added" : ""}`,
        "data-claim": claim.claim_id,
        tabindex: "0",
        role: "button",
        "aria-label": `${STATUS[claim.status]}: ${text.slice(start, end)}`,
        onclick: open,
        onkeydown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        },
      }, text.slice(start, end)),
    );
    pos = end;
  }
  if (pos < text.length) container.append(text.slice(pos));
}

function sortedIssues() {
  if (!state.audit) return [];
  const rank = { contradicted: 0, unsupported: 1 };
  return state.audit.claims.filter((c) => c.status in rank).sort((a, b) => rank[a.status] - rank[b.status]);
}

function renderIssues() {
  const list = $("issues");
  const issues = sortedIssues();
  if (!issues.length) {
    list.replaceChildren(el("li", {}, el("p", { class: "all-clear", text: "Nothing to fix. Every statement is backed by the visit." })));
    return;
  }
  list.replaceChildren(
    ...issues.map((c) =>
      el("li", {},
        el("button", {
          type: "button",
          class: `issue${isSelected("claim", c.claim_id) ? " is-selected" : ""}`,
          onclick: () => select({ kind: "claim", id: c.claim_id }, { scroll: true }),
        },
          el("span", { class: `pill st-${c.status}`, text: STATUS[c.status] }),
          el("span", { class: "issue-quote", text: c.note_quote }),
          el("span", { class: "issue-why", text: c.receipt_failed ? "The cited evidence could not be found in the visit." : c.explanation }),
        ),
      ),
    ),
  );
}

function renderAdditions() {
  const list = $("additions");
  const additions = state.audit.suggested_additions;
  if (!additions.length) {
    list.replaceChildren(el("li", {}, el("p", { class: "empty", text: "Nothing important is missing from the note." })));
    return;
  }
  list.replaceChildren(
    ...additions.map((a) => {
      const status = state.additionState[a.addition_id];
      const card = el("li", { class: `card${status ? " is-done" : ""}${isSelected("addition", a.addition_id) ? " is-selected" : ""}` },
        el("div", { class: "card-head" },
          el("span", { class: `pill pr-${a.priority}`, text: PRIORITY[a.priority] }),
          receiptChips(a.evidence, { kind: "addition", id: a.addition_id })),
        el("p", { class: "card-text", text: a.text }),
        el("p", { class: "card-why", text: a.why }),
      );
      if (status === "added") {
        card.append(el("span", { class: "card-status", text: "Added to the note." }));
      } else if (status === "dismissed") {
        card.append(el("div", { class: "actions" },
          el("span", { class: "card-status", text: "Dismissed." }),
          el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Undo", onclick: () => setAdditionState(a.addition_id, undefined) })));
      } else {
        card.append(el("div", { class: "actions" },
          el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Add to note", onclick: () => addAddition(a) }),
          el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Dismiss", onclick: () => setAdditionState(a.addition_id, "dismissed") })));
      }
      return card;
    }),
  );
}

function renderFollowUps() {
  const list = $("followups");
  const items = state.audit.follow_up_questions;
  if (!items.length) {
    list.replaceChildren(el("li", {}, el("p", { class: "empty", text: "No follow-up questions." })));
    return;
  }
  list.replaceChildren(
    ...items.map((f) =>
      el("li", { class: "card" },
        el("span", { class: "category", text: f.category }),
        el("p", { class: "card-text", text: f.question }),
        el("p", { class: "card-why", text: f.why })),
    ),
  );
}

function setAdditionState(id, value) {
  if (value) state.additionState[id] = value;
  else delete state.additionState[id];
  renderAdditions();
}

// A suggestion already has verified receipts, so it goes straight into the
// note as "supported" without another call to the model.
function addAddition(addition) {
  const audit = state.audit;
  const separator = audit.note_text.endsWith("\n") || audit.note_text === "" ? "" : "\n";
  const start = audit.note_text.length + separator.length;
  audit.note_text = `${audit.note_text}${separator}${addition.text}`;
  const claimId = `T${addition.addition_id}`;
  audit.claims.push({
    claim_id: claimId,
    text: addition.text,
    note_quote: addition.text,
    note_span: { start, end: start + addition.text.length },
    status: "supported",
    evidence: addition.evidence,
    explanation: "Added from the visit, with a receipt.",
    receipt_failed: false,
    added: true,
  });
  state.additionState[addition.addition_id] = "added";
  state.selected = { kind: "claim", id: claimId };
  renderAudit();
}

// ------------------------------------------------------------------ selection and receipts

function isSelected(kind, id) {
  return Boolean(state.selected && state.selected.kind === kind && state.selected.id === id);
}

function findSelected() {
  if (!state.selected || !state.audit) return null;
  if (state.selected.kind === "claim") return state.audit.claims.find((c) => c.claim_id === state.selected.id) || null;
  return state.audit.suggested_additions.find((a) => a.addition_id === state.selected.id) || null;
}

function select(selection, { scroll = false } = {}) {
  state.selected = selection;
  if (!state.audit) return;
  document.querySelectorAll(".claim").forEach((node) => {
    node.classList.toggle("is-selected", Boolean(selection && selection.kind === "claim" && node.dataset.claim === selection.id));
    node.classList.remove("is-linked");
  });
  renderIssues();
  renderAdditions();
  renderTranscriptHighlights();
  renderReceipt();
  const item = findSelected();
  if (scroll && item && item.evidence.length) scrollToLine(item.evidence[0].line_id);
}

function renderReceipt() {
  const box = $("receipt");
  const item = findSelected();
  if (!item) {
    box.hidden = true;
    return;
  }
  const isClaim = state.selected.kind === "claim";
  const pill = isClaim
    ? el("span", { class: `pill st-${item.status}`, text: STATUS[item.status] })
    : el("span", { class: "pill neutral", text: "Suggested addition" });
  const parts = [
    el("div", { class: "receipt-head" },
      pill,
      el("button", { type: "button", class: "icon-btn", "aria-label": "Close receipt", text: "×", onclick: () => select(null) })),
    el("span", { class: "receipt-title", text: item.text }),
    el("p", { class: "receipt-text", text: isClaim ? item.explanation : item.why }),
  ];
  if (isClaim && item.receipt_failed) {
    parts.push(el("p", { class: "receipt-warning", text: "The model cited evidence that is not in the visit, so this statement counts as unsupported." }));
  }
  if (item.evidence.length) parts.push(receiptChips(item.evidence, state.selected));
  else parts.push(el("p", { class: "receipt-text", text: "Nothing in the visit supports this." }));
  box.replaceChildren(...parts);
  box.hidden = false;
}

function receiptChips(evidence, selection) {
  const lineIds = [...new Set(evidence.map((e) => e.line_id))];
  return el("div", { class: "chips" },
    lineIds.map((id) =>
      el("button", {
        type: "button",
        class: "chip",
        title: "Show this line in the transcript",
        text: `Line ${pad(id)}`,
        onclick: () => {
          if (!isSelected(selection.kind, selection.id)) select(selection);
          scrollToLine(id);
        },
      }),
    ));
}

// ------------------------------------------------------------------ transcript

function renderTranscript() {
  const lines = state.encounter.transcript;
  $("transcript-meta").textContent = `${lines.length} turns · ${state.encounter.variants[state.variant]}`;
  const width = Math.max(2, String(lines.length).length);
  $("transcript").replaceChildren(
    ...lines.map((line) =>
      el("li", { class: "line", id: `line-${line.line_id}`, "data-line": line.line_id, onclick: () => showClaimsForLine(line.line_id) },
        el("span", { class: "ln", text: pad(line.line_id, width) }),
        el("span", { class: `spk spk-${line.speaker}`, text: SPEAKER[line.speaker] || line.speaker }),
        el("p", { class: "txt", text: line.text })),
    ),
  );
  $("transcript").scrollTop = 0;
}

function renderTranscriptHighlights() {
  const item = findSelected();
  const status = item && state.selected.kind === "claim" ? item.status : "supported";
  const quotesByLine = new Map();
  if (item) {
    for (const ev of item.evidence) {
      if (!quotesByLine.has(ev.line_id)) quotesByLine.set(ev.line_id, []);
      quotesByLine.get(ev.line_id).push(ev.quote);
    }
  }
  for (const line of state.encounter.transcript) {
    const node = document.getElementById(`line-${line.line_id}`);
    if (!node) continue;
    const quotes = quotesByLine.get(line.line_id);
    node.className = quotes ? `line is-cited st-${status}` : "line";
    node.querySelector(".txt").replaceChildren(...(quotes ? markQuotes(line.text, quotes) : [line.text]));
  }
}

// Highlights the quotes inside a line. Matches word by word so that
// "hi, brian" finds ACI-Bench's tokenized "hi , brian".
function markQuotes(text, quotes) {
  const ranges = [];
  for (const quote of quotes) {
    const words = quote.toLowerCase().match(/[\p{L}\p{N}']+/gu);
    if (!words) continue;
    const pattern = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[^\\p{L}\\p{N}']+");
    const match = new RegExp(pattern, "iu").exec(text);
    if (match) ranges.push([match.index, match.index + match[0].length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const out = [];
  let pos = 0;
  for (const [start, end] of ranges) {
    if (start < pos) continue;
    if (start > pos) out.push(text.slice(pos, start));
    out.push(el("mark", { text: text.slice(start, end) }));
    pos = end;
  }
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}

// Scrolls only the transcript, not the whole page.
function scrollToLine(lineId) {
  const node = document.getElementById(`line-${lineId}`);
  const box = $("transcript");
  if (!node) return;
  const top = node.offsetTop - box.clientHeight / 2 + node.offsetHeight / 2;
  box.scrollTo({ top: Math.max(0, top), behavior: REDUCED_MOTION ? "auto" : "smooth" });
}

// Clicking a line highlights the statements in the note that cite it.
function showClaimsForLine(lineId) {
  if (!state.audit) return;
  const linked = state.audit.claims.filter((c) => c.evidence.some((e) => e.line_id === lineId));
  if (linked.length === 1) {
    select({ kind: "claim", id: linked[0].claim_id });
    return;
  }
  document.querySelectorAll(".claim").forEach((node) => {
    node.classList.toggle("is-linked", linked.some((c) => c.claim_id === node.dataset.claim));
  });
  const first = document.querySelector(".claim.is-linked");
  if (first) first.scrollIntoView({ block: "nearest", behavior: REDUCED_MOTION ? "auto" : "smooth" });
}

init();
