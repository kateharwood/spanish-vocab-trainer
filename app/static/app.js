const GROUP_COLORS = {
  "0a": "var(--g-0a)",
  "0b": "var(--g-0b)",
  n: "var(--g-n)",
  1: "var(--g-1)",
  2: "var(--g-2)",
  3: "var(--g-3)",
  4: "var(--g-4)",
  v: "var(--g-v)",
};

const KEY_TO_GROUP = { 0: "0a", 1: "1", 2: "2", 3: "3", 4: "4", b: "n", d: "0b", v: "v" };

const HINTS = {
  study: [
    ["space", "reveal"],
    ["f", "related forms"],
    ["k", "keep new word"],
    ["a", "archive it"],
    ["1", "again"],
    ["2", "hard"],
    ["3", "good"],
    ["4", "easy"],
  ],
  progress: [],
  cards: [
    ["space", "reveal"],
    ["f", "related forms"],
    ["0–4", "levels"],
    ["b", "bad entry"],
    ["d", "don't care"],
    ["v", "verb practice"],
    ["k", "keep new word"],
    ["a", "archive"],
    ["→", "skip"],
    ["←", "back"],
    ["⌘Z", "undo"],
  ],
  list: [
    ["drag", "select a run"],
    ["shift+click", "extend"],
    ["⌘+click", "toggle"],
    ["0–4", "levels"],
    ["b", "bad"],
    ["d", "don't care"],
    ["v", "verb practice"],
    ["u", "unsort"],
    ["a", "archive"],
    ["⌘Z", "undo"],
    ["esc", "clear"],
  ],
};

// An entry is one word as you study it: either a single word, or a family of
// variants (singular/plural, masculine/feminine) that share a `key`.
const state = {
  words: [],
  byKey: new Map(),
  entryByRank: new Map(),
  groups: [],
  mode: "study",
  filter: "unsorted",
  // List shows one section at a time: "active" (unarchived) or "archived".
  section: "active",
  search: "",
  visible: [],
  selected: new Set(),
  anchor: null,
  cursor: null,
  dragging: false,
  dragBase: null,
  undoStack: [],
  cardQueue: [],
  cardPos: 0,
  revealed: false,
  study: null,
  studyRevealed: false,
  studyEditing: false,
  // Claude's suggested definitions for the current study card.
  suggestion: { key: null, data: null, timer: null, polls: 0, decided: false },
  overallProgress: null,
  // Subfamilies you've opened, by card ("study:<queue id>", "sort:<key>",
  // "list:<key>"). Closed unless you open them.
  openForms: new Set(),
  // Levels expanded on the progress page, kept open across refreshes.
  openLevels: new Set(),
};

const els = {
  toolbar: document.getElementById("toolbar"),
  studyView: document.getElementById("study-view"),
  studyCard: document.getElementById("study-card"),
  studySummary: document.getElementById("study-summary"),
  studySource: document.getElementById("study-source"),
  studyPrompt: document.getElementById("study-prompt"),
  studyReveal: document.getElementById("study-reveal"),
  studyAnswer: document.getElementById("study-answer"),
  studyAnswerVariants: document.getElementById("study-answer-variants"),
  studyForms: document.getElementById("study-forms"),
  cardForms: document.getElementById("card-forms"),
  studySuggestion: document.getElementById("study-suggestion"),
  cardSuggestion: document.getElementById("card-suggestion"),
  studyGroup: document.getElementById("study-group"),
  studyGroupLabel: document.getElementById("study-group-label"),
  studyEdit: document.getElementById("study-edit"),
  studySave: document.getElementById("study-save"),
  studyCancel: document.getElementById("study-cancel"),
  ratingButtons: document.getElementById("rating-buttons"),
  studyMessage: document.getElementById("study-message"),
  studyMessageTitle: document.getElementById("study-message-title"),
  studyMessageBody: document.getElementById("study-message-body"),
  studyEnd: document.getElementById("study-end"),
  studyProgressView: document.getElementById("study-progress-view"),
  overallProgressCards: document.getElementById("overall-progress-cards"),
  overallProgressGroups: document.getElementById("overall-progress-groups"),
  stageBar: document.getElementById("stage-bar"),
  stageLegend: document.getElementById("stage-legend"),
  showProgress: document.getElementById("show-progress"),
  progressRefresh: document.getElementById("progress-refresh"),
  progressBack: document.getElementById("progress-back"),
  list: document.getElementById("list"),
  listView: document.getElementById("list-view"),
  cardView: document.getElementById("card-view"),
  empty: document.getElementById("empty"),
  filters: document.getElementById("filters"),
  sections: document.getElementById("sections"),
  archive: document.getElementById("archive"),
  studyNew: document.getElementById("study-new"),
  cardNew: document.getElementById("card-new"),
  cardChoices: document.getElementById("card-choices"),
  groupButtons: document.getElementById("group-buttons"),
  assignBar: document.getElementById("assign-bar"),
  selectionCount: document.getElementById("selection-count"),
  search: document.getElementById("search"),
  undo: document.getElementById("undo"),
  cardUndo: document.getElementById("card-undo"),
  merge: document.getElementById("merge"),
  unmerge: document.getElementById("unmerge"),
  progressFill: document.getElementById("progress-fill"),
  progressLabel: document.getElementById("progress-label"),
  modeToggle: document.getElementById("mode-toggle"),
  card: document.getElementById("card"),
  cardMeta: document.getElementById("card-meta"),
  cardWord: document.getElementById("card-word"),
  cardEnglish: document.getElementById("card-english"),
  revealHint: document.getElementById("reveal-hint"),
  cardPosition: document.getElementById("card-position"),
  cardEmpty: document.getElementById("card-empty"),
  cardPrev: document.getElementById("card-prev"),
  cardSkip: document.getElementById("card-skip"),
  hints: document.getElementById("hints"),
  mergeModal: document.getElementById("merge-modal"),
  mergeModalWords: document.getElementById("merge-modal-words"),
  mergeModalChoices: document.getElementById("merge-modal-choices"),
  mergeModalCancel: document.getElementById("merge-modal-cancel"),
  showInfo: document.getElementById("show-info"),
  infoModal: document.getElementById("info-modal"),
  infoClose: document.getElementById("info-close"),
  infoWeights: document.getElementById("info-weights"),
};

// Mirrors NEW_WEIGHTS in app/study.py.
const NEW_WEIGHTS = [
  ["3", 40],
  ["4", 40],
  ["2", 10],
  ["1", 5],
  ["v", 5],
];
const MAX_WEIGHT = Math.max(...NEW_WEIGHTS.map(([, weight]) => weight));

const rowEls = new Map();

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

async function refreshWords() {
  const data = await api("/api/words");
  state.words = data.words;
  state.byKey = new Map(data.words.map((w) => [w.key, w]));
  state.entryByRank = new Map();
  data.words.forEach((w) => w.ranks.forEach((rank) => state.entryByRank.set(rank, w)));
  state.groups = data.groups;
}

async function load() {
  await refreshWords();
  buildCardQueue();
  setMode(state.mode);
}

/* ---------- main word, alt definition, subfamily ---------- */

// Every family lists its main word first; the rest are its subfamily.
function subfamilyOf(item) {
  return item.variants.slice(1);
}

function altHtml(alt, className = "alt-def") {
  return alt ? `<div class="${className}">${escapeHtml(alt)}</div>` : "";
}

function formsToggleHtml(id, count, compact = false) {
  const open = state.openForms.has(id);
  const label = compact
    ? `${open ? "−" : "+"}${count}`
    : `${open ? "Hide" : "Show"} ${count} related form${count === 1 ? "" : "s"}`;
  return `<button class="forms-toggle${compact ? " compact" : ""}${open ? " open" : ""}"
    data-forms="${id}" aria-expanded="${open}"
    title="${open ? "Hide" : "Show"} ${count} related form${count === 1 ? "" : "s"}">${label}</button>`;
}

function toggleForms(id) {
  if (state.openForms.has(id)) state.openForms.delete(id);
  else state.openForms.add(id);
  if (state.mode === "study") renderStudy();
  else if (state.mode === "cards") renderCard();
  else if (state.mode === "list") renderList();
}

// Keep List entries in step with an edit made elsewhere (study card, suggestion).
function applyVariantToList(rank, fields) {
  const listed = state.entryByRank.get(rank);
  if (!listed) return;
  const variant = listed.variants.find((item) => item.rank === rank);
  if (variant) Object.assign(variant, fields);
  const main = listed.variants[0];
  listed.spanish = main.spanish;
  listed.english = main.english;
  listed.alt_english = main.alt_english;
}

function inSection(word, section = state.section) {
  return (section === "archived") === Boolean(word.archived);
}

function counts(section = state.section) {
  const result = { unsorted: 0, total: 0 };
  state.groups.forEach((g) => (result[g.code] = 0));
  state.words.forEach((w) => {
    if (!inSection(w, section)) return;
    result.total += 1;
    if (w.group_code) result[w.group_code] += 1;
    else result.unsorted += 1;
  });
  return result;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
  });
}

function uses(frequency) {
  return frequency ? frequency.toLocaleString() : "";
}

// Words Claude added aren't in the frequency list, so they carry no count.
const FROM_CLAUDE = "from Claude";
function usesLabel(frequency) {
  return frequency ? `${uses(frequency)} uses` : FROM_CLAUDE;
}

// Inline CSS variables that color a chip, button, or bar for one group.
function groupStyle(code) {
  if (!code) return "";
  return `--chip: var(--g-${code}); --chip-bg: var(--g-${code}-bg); --chip-fg: var(--g-${code}-fg)`;
}

function groupLabel(code) {
  if (!code) return "Unsorted";
  const group = state.groups.find((g) => g.code === code);
  return group ? group.label : code;
}

let toastTimer = null;
function notify(message, { error = true } = {}) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.hidden = true), 4000);
}

function groupShortcut(code) {
  if (code === "0a") return "0";
  if (code === "0b") return "d";
  if (code === "n") return "b";
  return code;
}

/* ---------- shared chrome ---------- */

function renderProgress() {
  const c = counts("active");
  const sorted = c.total - c.unsorted;
  const pct = c.total ? (sorted / c.total) * 100 : 0;
  els.progressFill.style.width = `${pct}%`;
  els.progressLabel.textContent = `${sorted} of ${c.total} sorted`;
}

function renderFilters() {
  const c = counts();
  const compactLabel = (group) => ({
    n: "Bad",
    "0b": "Don't care",
    v: "Verbs",
  }[group.code] || group.label);
  const options = [
    { key: "unsorted", label: "Unsorted", count: c.unsorted },
    { key: "all", label: "All", count: c.total },
    ...state.groups.map((g) => ({
      key: g.code,
      label: compactLabel(g),
      count: c[g.code],
    })),
  ];
  els.filters.innerHTML = options
    .map(
      (o) =>
        `<button data-filter="${o.key}" class="${
          state.filter === o.key ? "active" : ""
        }">${o.label}<span class="count">${o.count.toLocaleString()}</span></button>`
    )
    .join("");
  const archivedCount = state.words.filter((w) => w.archived).length;
  els.sections.innerHTML = [
    ["active", "Unarchived", state.words.length - archivedCount],
    ["archived", "Archived", archivedCount],
  ]
    .map(
      ([key, label, count]) =>
        `<button data-section="${key}" class="${
          state.section === key ? "active" : ""
        }">${label}<span class="count">${count.toLocaleString()}</span></button>`
    )
    .join("");
}

function renderGroupButtons() {
  const compactLabel = (group) => ({
    "0b": "Don't care",
    v: "Verbs",
  }[group.code] || group.label);
  const buttons = state.groups.map(
    (g) => `
      <button data-group="${g.code}" title="${g.label}" style="${groupStyle(g.code)}">
        <span class="group-dot"></span>
        <kbd>${groupShortcut(g.code)}</kbd>
        <span>${compactLabel(g)}</span>
      </button>`
  );
  buttons.push(
    `<button data-group="" title="Back to unsorted"><span class="group-dot"></span><kbd>u</kbd><span>Unsort</span></button>`
  );
  els.groupButtons.innerHTML = buttons.join("");
  updateAssignBar();
}

function renderHints() {
  const hints = HINTS[state.mode];
  els.hints.hidden = hints.length === 0;
  els.hints.innerHTML = hints
    .map(([key, label]) => `<span><kbd>${key}</kbd> ${label}</span>`)
    .join("");
}

function updateAssignBar() {
  const inCards = state.mode === "cards";
  const n = state.selected.size;
  const card = currentCard();

  if (inCards) {
    els.selectionCount.textContent = card
      ? `Sort “${card.spanish}”`
      : "Nothing left to sort";
  } else {
    els.selectionCount.textContent =
      n === 0 ? "No words selected" : `${n} word${n === 1 ? "" : "s"} selected`;
  }

  els.assignBar.classList.toggle("has-selection", inCards ? Boolean(card) : n > 0);
  const disabled = inCards ? !card : n === 0;
  els.groupButtons.querySelectorAll("button").forEach((b) => (b.disabled = disabled));
  els.undo.disabled = state.undoStack.length === 0;
  els.cardUndo.disabled = state.undoStack.length === 0;
  els.archive.innerHTML = `${state.section === "archived" ? "Unarchive" : "Archive"} <kbd>a</kbd>`;
  els.archive.disabled = inCards || n === 0;

  // Merging is a list-mode action: you pick the words that belong together.
  els.merge.disabled = inCards || n < 2;
  els.unmerge.disabled =
    inCards || n === 0 || ![...state.selected].some((k) => state.byKey.get(k).merged);
}

function setMode(mode) {
  state.mode = mode;
  document.body.dataset.mode = mode;
  els.modeToggle.querySelectorAll("button").forEach((b) => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
  els.studyView.hidden = mode !== "study";
  els.studyProgressView.hidden = mode !== "progress";
  els.cardView.hidden = mode !== "cards";
  els.listView.hidden = mode !== "list";
  // The toolbar (filters, search, Undo) is for the List; Sort keeps its Undo
  // next to back and skip.
  els.toolbar.hidden = mode !== "list";
  // Sort puts its choices on the card itself; the bar is for the List.
  els.assignBar.hidden = mode !== "list";

  if (mode === "study") {
    loadStudy();
  } else if (mode === "progress") {
    loadOverallProgress();
  } else if (mode === "cards") {
    buildCardQueue();
    renderCard();
  } else {
    applyFilter();
  }
  if (mode !== "study" && mode !== "progress") {
    renderProgress();
    renderGroupButtons();
  }
  renderHints();
}

/* ---------- study mode ---------- */

async function loadStudy() {
  els.studySummary.textContent = "Loading your session…";
  try {
    state.study = await api("/api/study/session", { method: "POST" });
    state.studyRevealed = false;
    state.studyEditing = false;
    renderStudy();
  } catch (error) {
    els.studyCard.hidden = true;
    els.studyMessage.hidden = false;
    els.studyMessageTitle.textContent = "Study setup needed";
    els.studyMessageBody.textContent =
      "The study tables are not available yet. Run the Supabase migration, then reload.";
    els.studySummary.textContent = "";
  }
}

function renderStudyProgress() {
  const progress = state.study && state.study.progress;
  if (!progress) {
    els.progressFill.style.width = "0";
    els.progressLabel.textContent = "No active session";
    return;
  }
  const pct = progress.total ? (progress.completed / progress.total) * 100 : 100;
  els.progressFill.style.width = `${pct}%`;
  if (state.study.status === "complete" || state.study.status === "done_today") {
    els.progressLabel.textContent = `${progress.completed} reviewed today · next session tomorrow`;
    els.studySummary.textContent = "";
    return;
  }
  els.progressLabel.textContent =
    `${progress.completed} reviewed · ${progress.remaining} remaining`;
  els.studySummary.textContent =
    `${progress.left.due} due · ${progress.left.new} new · ${progress.left.repeat} repeat${
      progress.left.repeat === 1 ? "" : "s"
    }`;
}

function renderStudyGroup() {
  const current = state.groups.find((group) => group.code === state.study.group_code);
  els.studyGroup.innerHTML = state.groups
    .map(
      (group) =>
        `<option value="${group.code}"${
          group.code === state.study.group_code ? " selected" : ""
        }>${group.label}</option>`
    )
    .join("");
  els.studyGroup.hidden = !state.studyEditing;
  els.studyGroupLabel.hidden = state.studyEditing;
  els.studyGroupLabel.textContent = current ? current.label : "Unsorted";
  els.studyEdit.hidden = state.studyEditing;
  els.studySave.hidden = !state.studyEditing;
  els.studyCancel.hidden = !state.studyEditing;
}

function renderStudy() {
  if (state.mode !== "study" || !state.study) return;
  renderStudyProgress();
  els.studyCard.hidden = state.study.status !== "active";
  els.studyMessage.hidden = state.study.status === "active";
  const finished = state.study.status === "complete" || state.study.status === "done_today";
  els.studyEnd.hidden = finished;
  els.ratingButtons.hidden = true;

  if (finished) {
    els.studyMessageTitle.textContent = state.study.ended
      ? "Session ended"
      : "You're done for today";
    els.studyMessageBody.textContent = state.study.ended
      ? "Your progress was saved. Your next session opens tomorrow."
      : "Today's session is finished. Your next session opens tomorrow.";
    return;
  }

  const sourceLabel = {
    due: "Due review",
    new: "New this session",
    repeat: "Short repeat",
  }[state.study.source];
  els.studySource.textContent = sourceLabel;
  els.studyCard.classList.toggle("first-seen", Boolean(state.study.needs_decision));
  els.studyNew.hidden = !state.study.needs_decision || state.studyEditing;
  els.studyNew.innerHTML = newWordBarHtml(false);
  renderStudyGroup();
  const [main, ...forms] = state.study.variants;
  els.studyPrompt.innerHTML =
    `<div class="study-prompt-line">${escapeHtml(main.english)}</div>${altHtml(main.alt_english)}`;
  els.studyAnswerVariants.innerHTML = state.studyEditing
    ? state.study.variants.map((variant) => `
        <div class="study-answer-line editing" data-rank="${variant.rank}">
          <label class="edit-main">
            <input type="radio" name="main-word" value="${variant.rank}"${
              variant.rank === main.rank ? " checked" : ""
            }>
            ${variant.rank === main.rank ? "Main word" : "Make main word"}
          </label>
          <div class="study-edit-fields">
            <label>Spanish<input class="edit-spanish" value="${escapeHtml(variant.spanish)}"></label>
            <label>English<input class="edit-english" value="${escapeHtml(variant.english)}"></label>
            <label class="edit-alt-field">Alt definition <small>(optional, shown smaller)</small><input class="edit-alt" value="${escapeHtml(
              variant.alt_english || ""
            )}"></label>
          </div>
        </div>`).join("")
    : `
        <div class="study-answer-line" data-rank="${main.rank}">
          <div>
            <b>${escapeHtml(main.spanish)}</b>
            <small>#${main.rank} · ${usesLabel(main.frequency)}</small>
          </div>
        </div>`;
  renderStudyForms(forms);
  els.studyAnswer.hidden = !state.studyRevealed;
  els.studyReveal.hidden = state.studyRevealed;
  // Before you reveal, the rating buttons keep their space (just invisible),
  // so the centered card doesn't jump when they appear.
  els.ratingButtons.hidden = state.studyEditing;
  els.ratingButtons.classList.toggle("waiting", !state.studyRevealed);
  els.studyCard.classList.toggle("revealed", state.studyRevealed);
  els.ratingButtons.querySelectorAll("button").forEach((button) => {
    button.querySelector("span").textContent = state.study.intervals[button.dataset.rating];
  });
  loadSuggestion();
  renderSuggestion();
}

// The subfamily on a study card: closed until you open it. Before you reveal
// the Spanish it shows only the forms' English, after it the Spanish too.
function renderStudyForms(forms) {
  const id = `study:${state.study.queue_id}`;
  els.studyForms.hidden = !forms.length || state.studyEditing;
  if (els.studyForms.hidden) return;
  const open = state.openForms.has(id);
  els.studyForms.innerHTML = formsToggleHtml(id, forms.length) + (open
    ? `<div class="forms-list">${forms.map((v) => `
        <div class="form-row">
          ${state.studyRevealed
            ? `<b>${escapeHtml(v.spanish)}</b><small>#${v.rank} · ${usesLabel(v.frequency)}</small>`
            : ""}
          <span class="form-english">${escapeHtml(v.english)}</span>
          ${altHtml(v.alt_english, "alt-def small")}
        </div>`).join("")}</div>`
    : "");
}

/* ---------- Claude definition suggestions ---------- */

// The card a suggestion box belongs to right now: the study card or the sort
// card, whichever is on screen.
function suggestionContext() {
  if (state.mode === "study" && state.study && state.study.status === "active") {
    return {
      key: `study:${state.study.queue_id}`,
      ranks: state.study.ranks,
      variants: state.study.variants,
      el: els.studySuggestion,
      visible: state.studyRevealed && !state.studyEditing,
    };
  }
  if (state.mode === "cards") {
    const card = currentCard();
    if (card) {
      return {
        key: `sort:${card.key}`,
        ranks: card.ranks,
        variants: card.variants,
        el: els.cardSuggestion,
        visible: state.revealed,
      };
    }
  }
  return null;
}

async function loadSuggestion() {
  const ctx = suggestionContext();
  if (!ctx || state.suggestion.key === ctx.key) return;
  clearTimeout(state.suggestion.timer);
  state.suggestion = { key: ctx.key, data: null, timer: null, polls: 0, decided: false };
  await fetchSuggestion(ctx.key);
}

async function fetchSuggestion(key) {
  const ctx = suggestionContext();
  if (!ctx || ctx.key !== key) return;
  let data;
  try {
    data = await api(`/api/definitions?${ctx.ranks.map((r) => `ranks=${r}`).join("&")}`);
  } catch (error) {
    return;
  }
  // The card may have changed, or you may have already decided on this
  // suggestion, while the request was out. A late answer must not bring a
  // decided suggestion back.
  if (state.suggestion.key !== key || state.suggestion.decided) return;
  const s = state.suggestion;
  s.data = data;
  // Claude is still working on this card: check back a few times.
  if (data.working && s.polls < 15) {
    s.polls += 1;
    s.timer = setTimeout(() => fetchSuggestion(key), 3000);
  }
  renderSuggestion();
}

// While sorting, ask Claude about the current card and the next few, so a
// suggestion is usually ready by the time you reveal the English.
const requestedSortKeys = new Set();
async function requestSortSuggestions() {
  const upcoming = state.cardQueue
    .slice(state.cardPos, state.cardPos + 4)
    .map((key) => state.byKey.get(key))
    .filter((entry) => entry && !entry.group_code && !requestedSortKeys.has(entry.key));
  if (upcoming.length) {
    upcoming.forEach((entry) => requestedSortKeys.add(entry.key));
    try {
      await api("/api/definitions/request", {
        method: "POST",
        body: JSON.stringify({ families: upcoming.map((entry) => entry.ranks) }),
      });
    } catch (error) {
      upcoming.forEach((entry) => requestedSortKeys.delete(entry.key));
    }
  }
  await loadSuggestion();
}

function renderSuggestion() {
  els.studySuggestion.hidden = true;
  els.cardSuggestion.hidden = true;
  const ctx = suggestionContext();
  const data = state.suggestion.data;
  const show = ctx
    && ctx.key === state.suggestion.key
    && ctx.visible
    && data
    && (data.suggestions.length > 0 || data.working);
  if (!show) return;
  const el = ctx.el;
  el.hidden = false;

  if (!data.suggestions.length) {
    el.innerHTML = '<p class="suggestion-working">Claude is looking for a clearer definition…</p>';
    return;
  }
  const spanish = new Map(ctx.variants.map((v) => [v.rank, v.spanish]));
  const note = data.suggestions.find((item) => item.note)?.note;
  el.innerHTML = `
    <div class="suggestion-title">Suggested definition</div>
    ${data.suggestions
      .map(
        (item) => `
        <div class="suggestion-line">
          ${data.suggestions.length > 1 || ctx.variants.length > 1
            ? `<span class="suggestion-word">${escapeHtml(spanish.get(item.rank) || "")}</span>`
            : ""}
          <b>${escapeHtml(item.suggested_english)}</b>
          ${item.suggested_alt
            ? `<span class="suggestion-alt">${escapeHtml(item.suggested_alt)}</span>`
            : ""}
          <small>was: ${escapeHtml(item.original_english)}${
            item.original_alt ? ` · ${escapeHtml(item.original_alt)}` : ""
          }</small>
        </div>`
      )
      .join("")}
    ${note ? `<p class="suggestion-note">${escapeHtml(note)}</p>` : ""}
    <div class="suggestion-actions">
      <button class="suggestion-accept" data-decision="accept">Use this</button>
      <button class="ghost" data-decision="dismiss">Keep mine</button>
    </div>`;
}

async function decideSuggestion(decision, event) {
  if (event) event.stopPropagation();
  const data = state.suggestion.data;
  if (!data || !data.suggestions.length) return;
  const ranks = data.suggestions.map((item) => item.rank);
  // Stop checking back for this card; the decision is final.
  clearTimeout(state.suggestion.timer);
  state.suggestion.decided = true;
  [els.studySuggestion, els.cardSuggestion].forEach((el) =>
    el.querySelectorAll("button").forEach((b) => (b.disabled = true))
  );
  try {
    const result = await api(`/api/definitions/${decision}`, {
      method: "POST",
      body: JSON.stringify({ ranks }),
    });
    (result.accepted || []).forEach(({ rank, english, alt_english }) => {
      const studied = state.study && state.study.variants
        ? state.study.variants.find((item) => item.rank === rank)
        : null;
      if (studied) Object.assign(studied, { english, alt_english });
      applyVariantToList(rank, { english, alt_english });
    });
    data.suggestions = [];
    if (decision === "accept") notify("Definition updated", { error: false });
    if (state.mode === "cards") renderCard();
    else renderStudy();
  } catch (error) {
    state.suggestion.decided = false;
    notify("Couldn't save that. Try again.");
    renderSuggestion();
  }
}

function revealStudy() {
  if (!state.study || state.study.status !== "active") return;
  state.studyRevealed = true;
  renderStudy();
}

async function rateStudy(rating) {
  if (
    !state.studyRevealed
    || state.studyEditing
    || !state.study
    || state.study.status !== "active"
  ) return;
  els.ratingButtons.querySelectorAll("button").forEach((button) => (button.disabled = true));
  try {
    state.study = await api(`/api/study/session/${state.study.session_id}/review`, {
      method: "POST",
      body: JSON.stringify({ queue_id: state.study.queue_id, rating }),
    });
    state.studyRevealed = false;
    state.studyEditing = false;
    renderStudy();
  } catch (error) {
    notify("Couldn't save that review. Try again.");
  } finally {
    els.ratingButtons.querySelectorAll("button").forEach((button) => (button.disabled = false));
  }
}

function editStudyCard() {
  state.studyEditing = true;
  renderStudy();
  els.studyAnswerVariants.querySelector("input")?.focus();
}

function cancelStudyEdit() {
  state.studyEditing = false;
  renderStudy();
}

async function saveStudyCard() {
  const rows = [...els.studyAnswerVariants.querySelectorAll(".study-answer-line")];
  const changes = rows.map((row) => ({
    rank: Number(row.dataset.rank),
    spanish: row.querySelector(".edit-spanish").value.trim(),
    english: row.querySelector(".edit-english").value.trim(),
    alt_english: row.querySelector(".edit-alt").value.trim(),
  }));
  const chosenMain = Number(
    els.studyAnswerVariants.querySelector('input[name="main-word"]:checked')?.value
  );
  if (changes.some((change) => !change.spanish || !change.english)) {
    notify("Spanish and English are both required.");
    return;
  }

  const groupCode = els.studyGroup.value;
  const ranks = [...state.study.ranks];
  const sameCard = (card) =>
    card && [...card.ranks].sort().join(",") === [...ranks].sort().join(",");
  els.studySave.disabled = true;
  try {
    await Promise.all(
      changes.map((change) =>
        api(`/api/words/${change.rank}`, {
          method: "PATCH",
          body: JSON.stringify({
            spanish: change.spanish,
            english: change.english,
            alt_english: change.alt_english,
          }),
        })
      )
    );
    changes.forEach(({ rank, spanish, english, alt_english }) => {
      const variant = state.study.variants.find((item) => item.rank === rank);
      Object.assign(variant, { spanish, english, alt_english });
      applyVariantToList(rank, { spanish, english, alt_english });
    });

    if (chosenMain && chosenMain !== state.study.variants[0].rank) {
      await api(`/api/words/${chosenMain}/main`, { method: "POST" });
      state.study.main_rank = chosenMain;
      state.study.variants.sort(
        (a, b) => (a.rank !== chosenMain) - (b.rank !== chosenMain) || a.rank - b.rank
      );
      // The family's entry in List is keyed by its main word, so rebuild them.
      await refreshWords();
    }

    if (groupCode !== state.study.group_code) {
      const nextCard = await api(`/api/study/session/${state.study.session_id}/group`, {
        method: "POST",
        body: JSON.stringify({ ranks, group_code: groupCode }),
      });
      ranks.forEach((rank) => {
        const entry = state.entryByRank.get(rank);
        if (entry) entry.group_code = groupCode;
      });
      state.study = nextCard;
      state.studyRevealed = sameCard(nextCard);
    }
    state.studyEditing = false;
    renderStudy();
  } catch (error) {
    notify("Couldn't save those changes. Try again.");
  } finally {
    els.studySave.disabled = false;
  }
}

async function loadOverallProgress() {
  els.overallProgressCards.innerHTML = '<div class="progress-loading">Loading progress…</div>';
  els.overallProgressGroups.innerHTML = "";
  try {
    state.overallProgress = await api("/api/study/progress");
    renderOverallProgress();
  } catch (error) {
    els.overallProgressCards.innerHTML =
      '<div class="progress-loading">Could not load your progress.</div>';
  }
}

function renderOverallProgress() {
  const progress = state.overallProgress;
  if (!progress || state.mode !== "progress") return;
  const n = (value) => value.toLocaleString();
  // Counted here rather than on the server so "tomorrow" follows your clock.
  const endOfTomorrow = new Date();
  endOfTomorrow.setHours(24 * 2, 0, 0, 0);
  const now = new Date();
  const dueTomorrow = progress.groups
    .flatMap((group) => group.cards)
    .filter((card) => {
      const due = new Date(card.next_due);
      return due > now && due < endOfTomorrow;
    }).length;
  const metrics = [
    ["Started", `${n(progress.started)} <small>of ${n(progress.total)}</small>`],
    ["Due now", n(progress.due)],
    ["Due tomorrow", n(dueTomorrow)],
    ["Due this week", n(progress.due_week)],
    ["Total reviews", n(progress.reviews)],
  ];
  els.overallProgressCards.innerHTML = metrics
    .map(
      ([label, value]) => `
        <div class="overall-progress-card">
          <strong>${value}</strong>
          <span>${label}</span>
        </div>`
    )
    .join("");

  const stages = [
    ["learning", "Learning", progress.learning, "on a short repeat this session"],
    ["young", "Young", progress.young, "in review, under 3 weeks"],
    ["mature", "Mature", progress.mature, "remembered 3+ weeks out"],
  ];
  els.stageBar.innerHTML = progress.started
    ? stages
        .filter(([, , count]) => count)
        .map(
          ([key, label, count]) =>
            `<div class="stage-${key}" style="flex: ${count}" title="${label}: ${count}"></div>`
        )
        .join("")
    : "";
  els.stageLegend.innerHTML = stages
    .map(
      ([key, label, count, hint]) => `
        <div class="stage-item">
          <span class="stage-dot stage-${key}"></span>
          <b>${n(count)}</b> ${label}
          <small>${hint}</small>
        </div>`
    )
    .join("");
  els.overallProgressGroups.innerHTML = progress.groups
    .map((group) => {
      const percent = group.total ? Math.round((group.started * 100) / group.total) : 0;
      const stageCounts = ["learning", "young", "mature"]
        .map((stage) => [stage, group.cards.filter((card) => card.stage === stage).length])
        .filter(([, count]) => count)
        .map(
          ([stage, count]) =>
            `<span class="stage-count"><span class="stage-dot stage-${stage}"></span>${count} ${STAGE_LABELS[stage]}</span>`
        )
        .join("");
      const rows = group.cards
        .map(
          (card) => `
          <div class="level-card">
            <span class="level-card-word">
              <b>${escapeHtml(card.spanish)}</b>${
                card.forms ? `<span class="level-card-forms">+${card.forms}</span>` : ""
              }
              <small>${escapeHtml(card.english)}</small>
            </span>
            <span class="stage-pill stage-pill-${card.stage}">${STAGE_LABELS[card.stage]}</span>
            <span class="level-card-due">${dueLabel(card.next_due)}</span>
            <span class="level-card-reviews">${card.reviews} review${card.reviews === 1 ? "" : "s"}</span>
          </div>`
        )
        .join("");
      return `
        <details class="group-progress" data-code="${group.code}"${
          state.openLevels.has(group.code) ? " open" : ""
        }>
          <summary class="group-progress-row">
            <div class="group-progress-label">
              <span>${escapeHtml(group.label)}</span>
              <small>${group.started} of ${group.total} started</small>
            </div>
            <div class="group-progress-detail">
              <div class="group-progress-track" style="${groupStyle(group.code)}">
                <div style="width: ${percent}%"></div>
              </div>
              <div class="stage-counts">${stageCounts}</div>
            </div>
          </summary>
          <div class="level-cards">
            ${rows || '<p class="level-empty">No words started in this level yet.</p>'}
          </div>
        </details>`;
    })
    .join("");
  els.progressFill.style.width = `${progress.percent_started}%`;
  els.progressLabel.textContent =
    `${progress.percent_started}% started · ${progress.unseen} unseen`;
}

const STAGE_LABELS = { learning: "Learning", young: "Young", mature: "Mature" };

// "due now", "in 3h", "in 5d", "in 4 mo" from an ISO timestamp.
function dueLabel(isoTime) {
  const minutes = (new Date(isoTime) - new Date()) / 60000;
  if (minutes <= 0) return "due now";
  if (minutes < 60) return `in ${Math.ceil(minutes)}m`;
  if (minutes < 60 * 24) return `in ${Math.round(minutes / 60)}h`;
  const days = Math.round(minutes / (60 * 24));
  if (days < 60) return `in ${days}d`;
  return `in ${Math.round(days / 30)} mo`;
}

async function openInfo() {
  els.infoModal.hidden = false;
  els.infoClose.focus();
  renderInfoWeights(null);
  try {
    renderInfoWeights(await api("/api/study/progress"));
  } catch (error) {
    // The static explanation is still useful without the live counts.
  }
}

function closeInfo() {
  els.infoModal.hidden = true;
  els.showInfo.focus();
}

function renderInfoWeights(progress) {
  const byCode = new Map((progress ? progress.groups : []).map((g) => [g.code, g]));
  els.infoWeights.innerHTML = NEW_WEIGHTS.map(([code, weight]) => {
    const group = state.groups.find((g) => g.code === code);
    const counts = byCode.get(code);
    const unseen = counts ? counts.total - counts.started : null;
    return `
      <div class="info-weight" style="--chip: ${GROUP_COLORS[code]}">
        <span>${escapeHtml(group ? group.label : code)}${
          unseen === null ? "" : `<small>${unseen.toLocaleString()} unseen</small>`
        }</span>
        <div class="info-weight-track"><div style="width: ${(weight / MAX_WEIGHT) * 100}%"></div></div>
        <b>${weight}%</b>
      </div>`;
  }).join("");
}

/* ---------- card mode ---------- */

function buildCardQueue() {
  // Words Claude added (no frequency count) come first, then the frequency
  // list in rank order.
  const unsorted = state.words.filter((w) => !w.group_code && !w.archived);
  state.cardQueue = [
    ...unsorted.filter((w) => !w.frequency),
    ...unsorted.filter((w) => w.frequency),
  ].map((w) => w.key);
  state.cardPos = 0;
  state.revealed = false;
}

function currentCard() {
  if (state.mode !== "cards") return null;
  const key = state.cardQueue[state.cardPos];
  return key === undefined ? null : state.byKey.get(key);
}

function renderCard() {
  const card = currentCard();
  const remaining = state.cardQueue.filter((k) => {
    const entry = state.byKey.get(k);
    return !entry.group_code && !entry.archived;
  }).length;
  const done = !card || remaining === 0;

  els.card.hidden = done;
  els.cardEmpty.hidden = !done;
  document.querySelector(".card-nav").hidden = done;

  if (done) {
    updateAssignBar();
    return;
  }

  const [main, ...forms] = card.variants;
  els.cardMeta.textContent = `#${main.rank} · ${usesLabel(main.frequency)}`;
  els.cardWord.textContent = main.spanish;
  els.cardEnglish.innerHTML = escapeHtml(main.english) + altHtml(main.alt_english);
  els.cardEnglish.hidden = !state.revealed;

  // The subfamily stays closed unless you open it; its English shows once the
  // card is revealed.
  const formsId = `sort:${card.key}`;
  els.cardForms.hidden = !forms.length;
  els.cardForms.innerHTML = forms.length
    ? formsToggleHtml(formsId, forms.length) + (state.openForms.has(formsId)
      ? `<div class="forms-list">${forms.map((v) => `
          <div class="form-row">
            <b>${escapeHtml(v.spanish)}</b>
            <small>#${v.rank} · ${usesLabel(v.frequency)}</small>
            ${state.revealed
              ? `<span class="form-english">${escapeHtml(v.english)}</span>${altHtml(v.alt_english, "alt-def small")}`
              : ""}
          </div>`).join("")}</div>`
      : "")
    : "";

  els.revealHint.hidden = state.revealed;
  els.card.classList.toggle("revealed", state.revealed);

  // Until you choose to keep or archive a word, it comes up with a halo and
  // both options, here and in Study.
  const undecided = !card.decided && !card.archived;
  els.card.classList.toggle("first-seen", undecided);
  // The question waits until you've seen the English.
  els.cardNew.hidden = card.archived ? false : !(undecided && state.revealed);
  els.cardNew.innerHTML = newWordBarHtml(card.archived);

  // Two steps: keep or archive first, and only a kept word gets a level.
  const showChoices = state.revealed && !undecided;
  els.cardChoices.hidden = !showChoices;
  els.cardChoices.innerHTML = showChoices ? cardChoicesHtml(card) : "";

  els.cardPosition.textContent = `${remaining} left to sort`;
  els.cardPrev.disabled = state.cardPos === 0;
  els.cardSkip.disabled = state.cardPos >= state.cardQueue.length - 1;
  updateAssignBar();
  renderSuggestion();
  requestSortSuggestions();
}

// Where a word can go, grouped by whether you'll study it. Same keys as before.
const SORT_CHOICES = [
  {
    title: "Study it",
    choices: [
      ["1", "Level 1"],
      ["2", "Level 2"],
      ["3", "Level 3"],
      ["4", "Level 4"],
      ["v", "Verbs"],
    ],
  },
  {
    title: "Don't study",
    choices: [
      ["0a", "Know it", "Level 0"],
      ["0b", "Don't care"],
      ["n", "Bad entry"],
    ],
  },
];

function cardChoicesHtml(card) {
  const groups = SORT_CHOICES.map(({ title, choices }) => `
    <div class="choice-group">
      <div class="choice-title">${title}</div>
      <div class="choice-buttons">
        ${choices.map(([code, label, sub]) => `
          <button data-group="${code}" style="${groupStyle(code)}"
            class="${card.group_code === code ? "current" : ""}">
            <kbd>${groupShortcut(code)}</kbd>
            <span>${label}${sub ? `<small>${sub}</small>` : ""}</span>
          </button>`).join("")}
      </div>
    </div>`).join("");
  // Only a word you've come back to has a group to clear.
  const unsort = card.group_code
    ? `<button class="choice-unsort" data-group="">Back to unsorted <kbd>u</kbd></button>`
    : "";
  return groups + unsort;
}

function reveal() {
  state.revealed = true;
  renderCard();
}

function moveCard(step) {
  const next = state.cardPos + step;
  if (next < 0 || next >= state.cardQueue.length) return;
  state.cardPos = next;
  state.revealed = false;
  renderCard();
}

/* ---------- list mode ---------- */

function matchesFilter(word) {
  if (!inSection(word)) return false;
  if (state.filter === "all") return true;
  if (state.filter === "unsorted") return !word.group_code;
  return word.group_code === state.filter;
}

function matchesSearch(word) {
  if (!state.search) return true;
  const q = state.search.toLowerCase();
  return word.variants.some(
    (v) => v.spanish.toLowerCase().includes(q)
      || v.english.toLowerCase().includes(q)
      || (v.alt_english || "").toLowerCase().includes(q)
  );
}

function applyFilter() {
  state.visible = state.words.filter((w) => matchesFilter(w) && matchesSearch(w));
  renderList();
  renderFilters();
  renderGroupButtons();
  renderProgress();
}

function chipHtml(word) {
  if (!word.group_code) return `<span class="chip none"><span class="dot"></span>Unsorted</span>`;
  return `<span class="chip" style="${groupStyle(word.group_code)}">
      <span class="dot"></span>${escapeHtml(groupLabel(word.group_code))}
    </span>`;
}

// A family's subfamily as rows under its main word, when opened.
function rowFormsHtml(word) {
  const id = `list:${word.key}`;
  if (!word.merged || !state.openForms.has(id)) return "";
  return `<div class="row-forms">${subfamilyOf(word).map((v) => `
    <div class="row-form">
      <span class="col-rank">${v.rank}</span>
      <span class="col-word">${escapeHtml(v.spanish)}</span>
      <span class="col-english">${escapeHtml(v.english)}${
        v.alt_english ? `<span class="row-sub">${escapeHtml(v.alt_english)}</span>` : ""
      }</span>
      <span class="col-freq">${v.frequency ? uses(v.frequency) : FROM_CLAUDE}</span>
      <span></span>
    </div>`).join("")}</div>`;
}

function renderList() {
  rowEls.clear();
  els.empty.hidden = state.visible.length > 0;
  els.list.innerHTML = state.visible
    .map(
      (w) => `
      <div class="row${w.merged ? " merged" : ""}" data-key="${w.key}">
        <span class="col-rank">${w.main_rank}</span>
        <span class="col-word">${escapeHtml(w.spanish)}${
          w.merged ? formsToggleHtml(`list:${w.key}`, w.variants.length - 1, true) : ""
        }</span>
        <span class="col-english">${escapeHtml(w.english)}${
          w.alt_english ? `<span class="row-sub">${escapeHtml(w.alt_english)}</span>` : ""
        }</span>
        <span class="col-freq">${w.variants[0].frequency ? uses(w.variants[0].frequency) : FROM_CLAUDE}</span>
        <span class="col-group">${chipHtml(w)}</span>
        ${rowFormsHtml(w)}
      </div>`
    )
    .join("");
  els.list.querySelectorAll(".row").forEach((el) => {
    rowEls.set(Number(el.dataset.key), el);
  });
  paintSelection();
}

function paintSelection() {
  rowEls.forEach((el, key) => {
    el.classList.toggle("selected", state.selected.has(key));
    el.classList.toggle("cursor", state.cursor === key);
  });
  updateAssignBar();
}

function visibleIndex(key) {
  return state.visible.findIndex((w) => w.key === key);
}

function rangeKeys(fromKey, toKey) {
  const a = visibleIndex(fromKey);
  const b = visibleIndex(toKey);
  if (a === -1 || b === -1) return [];
  const [start, end] = a <= b ? [a, b] : [b, a];
  return state.visible.slice(start, end + 1).map((w) => w.key);
}

function selectOnly(keys) {
  state.selected = new Set(keys);
  paintSelection();
}

els.list.addEventListener("mousedown", (event) => {
  const toggle = event.target.closest(".forms-toggle");
  if (toggle) {
    event.preventDefault();
    toggleForms(toggle.dataset.forms);
    return;
  }
  const row = event.target.closest(".row");
  if (!row) return;
  event.preventDefault();
  const key = Number(row.dataset.key);

  if (event.shiftKey && state.anchor !== null) {
    selectOnly(rangeKeys(state.anchor, key));
  } else if (event.metaKey || event.ctrlKey) {
    if (state.selected.has(key)) state.selected.delete(key);
    else state.selected.add(key);
    state.anchor = key;
  } else {
    state.anchor = key;
    selectOnly([key]);
  }

  state.cursor = key;
  state.dragging = true;
  state.dragBase = event.metaKey || event.ctrlKey ? new Set(state.selected) : new Set();
  document.body.classList.add("dragging");
  paintSelection();
});

els.list.addEventListener("mouseover", (event) => {
  if (!state.dragging) return;
  const row = event.target.closest(".row");
  if (!row) return;
  const key = Number(row.dataset.key);
  state.selected = new Set([...state.dragBase, ...rangeKeys(state.anchor, key)]);
  state.cursor = key;
  paintSelection();
});

window.addEventListener("mouseup", () => {
  if (!state.dragging) return;
  state.dragging = false;
  state.dragBase = null;
  document.body.classList.remove("dragging");
});

let autoScrollTimer = null;
window.addEventListener("mousemove", (event) => {
  if (autoScrollTimer) {
    clearInterval(autoScrollTimer);
    autoScrollTimer = null;
  }
  if (!state.dragging) return;

  const margin = 90;
  const fromTop = event.clientY;
  const fromBottom = window.innerHeight - event.clientY;
  let delta = 0;
  if (fromTop < margin) delta = -Math.ceil((margin - fromTop) / 5);
  else if (fromBottom < margin) delta = Math.ceil((margin - fromBottom) / 5);
  if (delta !== 0) autoScrollTimer = setInterval(() => window.scrollBy(0, delta), 16);
});

function moveCursor(step, extend) {
  if (!state.visible.length) return;
  const current = state.cursor === null ? -1 : visibleIndex(state.cursor);
  const nextIndex = Math.min(
    state.visible.length - 1,
    Math.max(0, current === -1 ? 0 : current + step)
  );
  const key = state.visible[nextIndex].key;
  state.cursor = key;
  if (extend && state.anchor !== null) {
    selectOnly(rangeKeys(state.anchor, key));
  } else {
    state.anchor = key;
    selectOnly([key]);
  }
  const el = rowEls.get(key);
  if (el) el.scrollIntoView({ block: "nearest" });
}

/* ---------- assigning ---------- */

async function assignEntries(keys, groupCode) {
  if (!keys.length) return;

  // Groups live on individual words, so assigning a family touches every variant.
  // Giving a word a level also counts as choosing to keep studying it.
  const keep = groupCode !== null;
  const previous = {};
  const ranks = [];
  const undecide = [];
  const newlyKept = [];
  keys.forEach((key) => {
    const entry = state.byKey.get(key);
    entry.ranks.forEach((rank) => {
      previous[rank] = entry.group_code || null;
      ranks.push(rank);
    });
    entry.group_code = groupCode;
    if (keep && !entry.decided) {
      entry.decided = true;
      newlyKept.push(key);
      undecide.push(...entry.ranks);
    }
  });
  state.undoStack.push({ previous, undecide, mode: state.mode, cardPos: state.cardPos });

  try {
    await api("/api/assign", {
      method: "POST",
      body: JSON.stringify({ ranks, group_code: groupCode, keep }),
    });
  } catch (error) {
    keys.forEach((key) => {
      const entry = state.byKey.get(key);
      entry.group_code = previous[entry.ranks[0]];
    });
    newlyKept.forEach((key) => (state.byKey.get(key).decided = false));
    state.undoStack.pop();
    if (state.mode === "cards") renderCard();
    else applyFilter();
    notify("Couldn't save that change, so it was undone.");
  }
}

async function assignCard(groupCode) {
  const card = currentCard();
  if (!card) return;
  const promise = assignEntries([card.key], groupCode);
  if (state.cardPos < state.cardQueue.length - 1) {
    state.cardPos += 1;
  }
  state.revealed = false;
  renderCard();
  renderProgress();
  renderGroupButtons();
  await promise;
}

async function assignSelection(groupCode) {
  const keys = [...state.selected];
  if (!keys.length) return;
  const promise = assignEntries(keys, groupCode);
  const next = nextVisibleAfter(keys);
  state.selected.clear();
  state.anchor = next;
  state.cursor = next;
  applyFilter();
  const el = next === null ? null : rowEls.get(next);
  if (el) el.scrollIntoView({ block: "nearest" });
  await promise;
}

function nextVisibleAfter(keys) {
  const lastIndex = Math.max(...keys.map(visibleIndex));
  const candidate = state.visible.slice(lastIndex + 1).find((w) => !keys.includes(w.key));
  if (candidate) return candidate.key;
  const remaining = state.visible.filter((w) => !keys.includes(w.key));
  return remaining.length ? remaining[remaining.length - 1].key : null;
}

/* ---------- merging ---------- */

function chooseGroup(entries) {
  const groups = [...new Set(entries.map((e) => e.group_code || ""))];
  if (groups.length < 2) return Promise.resolve({ ok: true, group: groups[0] || null });

  els.mergeModalWords.textContent = entries
    .map((e) => `${e.spanish} (${groupLabel(e.group_code)})`)
    .join(" · ");
  els.mergeModalChoices.innerHTML = groups
    .map(
      (code) =>
        `<button data-group="${code}" style="${groupStyle(code)}">${
          code ? escapeHtml(groupLabel(code)) : "Leave unsorted"
        }</button>`
    )
    .join("");
  els.mergeModal.hidden = false;

  return new Promise((resolve) => {
    const finish = (result) => {
      els.mergeModal.hidden = true;
      els.mergeModalChoices.onclick = null;
      els.mergeModalCancel.onclick = null;
      resolve(result);
    };
    els.mergeModalChoices.onclick = (event) => {
      const button = event.target.closest("button");
      if (button) finish({ ok: true, group: button.dataset.group || null });
    };
    els.mergeModalCancel.onclick = () => finish({ ok: false });
  });
}

async function mergeSelection() {
  const entries = [...state.selected].map((k) => state.byKey.get(k));
  if (entries.length < 2) return;

  const choice = await chooseGroup(entries);
  if (!choice.ok) return;

  const ranks = entries.flatMap((e) => e.ranks);
  const previous = {};
  const families = {};
  entries.forEach((e) =>
    e.ranks.forEach((rank) => {
      previous[rank] = e.group_code || null;
      families[rank] = e.merged ? e.key : null;
    })
  );

  try {
    await api("/api/merge", {
      method: "POST",
      body: JSON.stringify({ ranks, group_code: choice.group }),
    });
  } catch (error) {
    notify("Couldn't merge those words. Try again.");
    return;
  }

  state.undoStack.push({ previous, families, mode: state.mode, cardPos: state.cardPos });
  await load();
  setMode("list");
  selectOnly([Math.min(...ranks)]);
}

async function unmergeSelection() {
  const entries = [...state.selected].map((k) => state.byKey.get(k)).filter((e) => e.merged);
  if (!entries.length) return;

  const ranks = entries.flatMap((e) => e.ranks);
  const previous = {};
  const families = {};
  entries.forEach((e) =>
    e.ranks.forEach((rank) => {
      previous[rank] = e.group_code || null;
      families[rank] = e.key;
    })
  );

  try {
    await api("/api/unmerge", { method: "POST", body: JSON.stringify({ ranks }) });
  } catch (error) {
    notify("Couldn't unmerge those words. Try again.");
    return;
  }

  state.undoStack.push({ previous, families, mode: state.mode, cardPos: state.cardPos });
  await load();
  setMode("list");
  selectOnly(ranks);
}

/* ---------- archive ---------- */

// The keep-or-archive bar. A new word shows it every time it comes up until
// you choose, like a suggested definition. Archived words keep their level
// but never come up in study; they live under List → Archived.
function newWordBarHtml(archived) {
  return archived
    ? `<span class="new-word-label">Archived</span>
       <span class="new-word-actions">
         <button class="ghost" data-archive="false">Unarchive <kbd>a</kbd></button>
       </span>`
    : `<span class="new-word-label">New word: keep studying it?</span>
       <span class="new-word-actions">
         <button class="new-word-keep" data-keep>Keep <kbd>k</kbd></button>
         <button class="ghost" data-archive="true">Archive <kbd>a</kbd></button>
       </span>`;
}

// `resetDecision` is for undo: unarchiving a word you'd never decided on
// brings its keep-or-archive prompt back.
async function saveArchived(keys, archived, resetDecision = false) {
  const ranks = keys.flatMap((key) => state.byKey.get(key).ranks);
  await api("/api/archive", {
    method: "POST",
    body: JSON.stringify({ ranks, archived, reset_decision: resetDecision }),
  });
}

// Archive or unarchive entries from Sort or List, with undo.
async function setArchived(keys, archived) {
  if (!keys.length) return;
  const previous = {};
  const decided = {};
  keys.forEach((key) => {
    const entry = state.byKey.get(key);
    previous[key] = Boolean(entry.archived);
    decided[key] = Boolean(entry.decided);
    entry.archived = archived;
    if (archived) entry.decided = true;
  });
  state.undoStack.push({ archived: previous, decided, mode: state.mode, cardPos: state.cardPos });
  try {
    await saveArchived(keys, archived);
  } catch (error) {
    keys.forEach((key) => {
      state.byKey.get(key).archived = previous[key];
      state.byKey.get(key).decided = decided[key];
    });
    state.undoStack.pop();
    if (state.mode === "cards") renderCard();
    else applyFilter();
    notify("Couldn't save that change, so it was undone.");
  }
}

async function archiveCard() {
  const card = currentCard();
  if (!card) return;
  const archived = !card.archived;
  const promise = setArchived([card.key], archived);
  if (archived) {
    notify(`Archived “${card.spanish}”`, { error: false });
    if (state.cardPos < state.cardQueue.length - 1) state.cardPos += 1;
    state.revealed = false;
  }
  renderCard();
  renderProgress();
  await promise;
}

async function archiveSelection() {
  const keys = [...state.selected];
  if (!keys.length) return;
  const archived = state.section !== "archived";
  const promise = setArchived(keys, archived);
  const next = nextVisibleAfter(keys);
  state.selected = new Set(next === null ? [] : [next]);
  state.anchor = next;
  state.cursor = next;
  applyFilter();
  notify(`${archived ? "Archived" : "Unarchived"} ${keys.length} word${keys.length === 1 ? "" : "s"}`, {
    error: false,
  });
  await promise;
}

// Draw the eye to the keep-or-archive question when a level key is pressed
// too early.
function nudgeKeepOrArchive() {
  els.cardNew.classList.remove("nudge");
  void els.cardNew.offsetWidth;
  els.cardNew.classList.add("nudge");
}

// Keep studying the sort card: its halo and prompt go away for good, and the
// level choices appear.
async function keepCard() {
  const card = currentCard();
  if (!card || card.decided || card.archived || !state.revealed) return;
  card.decided = true;
  renderCard();
  try {
    await api("/api/keep", { method: "POST", body: JSON.stringify({ ranks: card.ranks }) });
  } catch (error) {
    card.decided = false;
    renderCard();
    notify("Couldn't save that. Try again.");
  }
}

async function keepStudyCard() {
  if (!state.study || state.study.status !== "active" || !state.study.needs_decision) return;
  const { ranks } = state.study;
  state.study.needs_decision = false;
  renderStudy();
  try {
    await api("/api/keep", { method: "POST", body: JSON.stringify({ ranks }) });
    const entry = state.entryByRank.get(ranks[0]);
    if (entry) entry.decided = true;
  } catch (error) {
    if (state.study.ranks === ranks) state.study.needs_decision = true;
    renderStudy();
    notify("Couldn't save that. Try again.");
  }
}

// In study, a word still waiting on keep-or-archive offers archiving.
async function archiveStudyCard() {
  if (!state.study || state.study.status !== "active" || !state.study.needs_decision) return;
  const { ranks, variants } = state.study;
  els.studyNew.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    await api("/api/archive", { method: "POST", body: JSON.stringify({ ranks, archived: true }) });
    const entry = state.entryByRank.get(ranks[0]);
    if (entry) Object.assign(entry, { archived: true, decided: true });
    notify(`Archived “${variants[0].spanish}”. It's under List → Archived.`, { error: false });
    state.study = await api("/api/study/session", { method: "POST" });
    state.studyRevealed = false;
    renderStudy();
  } catch (error) {
    notify("Couldn't archive that. Try again.");
    renderStudy();
  }
}

/* ---------- undo ---------- */

async function undo() {
  const entry = state.undoStack.pop();
  if (!entry) return;
  const { previous, families, mode, cardPos } = entry;

  if (entry.archived) {
    // Group by what to restore: archived or not, and whether the
    // keep-or-archive choice had been made.
    const groups = new Map();
    Object.entries(entry.archived).forEach(([key, was]) => {
      const reset = !entry.decided[key];
      const id = `${was}:${reset}`;
      if (!groups.has(id)) groups.set(id, { was, reset, keys: [] });
      groups.get(id).keys.push(Number(key));
    });
    try {
      for (const { was, reset, keys } of groups.values()) await saveArchived(keys, was, reset);
    } catch (error) {
      state.undoStack.push(entry);
      notify("Couldn't save the undo. Try again.");
      return;
    }
    Object.entries(entry.archived).forEach(([key, was]) => {
      state.byKey.get(Number(key)).archived = was;
      state.byKey.get(Number(key)).decided = entry.decided[key];
    });
    if (mode === "cards" && state.mode === "cards") {
      state.cardPos = cardPos;
      renderCard();
      renderProgress();
    } else {
      applyFilter();
      const keys = Object.keys(entry.archived).map(Number);
      state.selected = new Set(keys.filter((k) => visibleIndex(k) !== -1));
      paintSelection();
    }
    return;
  }

  try {
    await api("/api/restore", {
      method: "POST",
      body: JSON.stringify({
        updates: previous,
        families: families || null,
        undecide: entry.undecide || null,
      }),
    });
  } catch (error) {
    state.undoStack.push(entry);
    notify("Couldn't save the undo. Try again.");
    return;
  }

  // Undoing a merge changes which entries exist, so the list has to be rebuilt.
  if (families) {
    await load();
    setMode("list");
    const keys = [...state.byKey.keys()].filter((k) =>
      state.byKey.get(k).ranks.some((rank) => rank in previous)
    );
    state.selected = new Set(keys.filter((k) => visibleIndex(k) !== -1));
    paintSelection();
    return;
  }

  Object.entries(previous).forEach(([rank, group]) => {
    state.entryByRank.get(Number(rank)).group_code = group;
  });
  (entry.undecide || []).forEach((rank) => {
    const restored = state.entryByRank.get(rank);
    if (restored) restored.decided = false;
  });

  if (mode === "cards" && state.mode === "cards") {
    state.cardPos = cardPos;
    state.revealed = true;
    renderCard();
    renderProgress();
    renderGroupButtons();
  } else {
    applyFilter();
    const keys = [...new Set(Object.keys(previous).map((r) => state.entryByRank.get(Number(r)).key))];
    state.selected = new Set(keys.filter((k) => visibleIndex(k) !== -1));
    paintSelection();
  }
}

/* ---------- events ---------- */

els.modeToggle.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button) setMode(button.dataset.mode);
});

els.filters.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.filter = button.dataset.filter;
  state.selected.clear();
  state.anchor = null;
  state.cursor = null;
  applyFilter();
});

els.sections.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.section = button.dataset.section;
  state.selected.clear();
  state.anchor = null;
  state.cursor = null;
  applyFilter();
});

els.archive.addEventListener("click", archiveSelection);
els.studyNew.addEventListener("click", (event) => {
  event.stopPropagation();
  if (event.target.closest("button[data-keep]")) keepStudyCard();
  else if (event.target.closest("button[data-archive]")) archiveStudyCard();
});
els.cardNew.addEventListener("click", (event) => {
  event.stopPropagation();
  if (event.target.closest("button[data-keep]")) keepCard();
  else if (event.target.closest("button[data-archive]")) archiveCard();
});

els.search.addEventListener("input", () => {
  state.search = els.search.value.trim();
  applyFilter();
});

els.groupButtons.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const group = button.dataset.group || null;
  if (state.mode === "cards") assignCard(group);
  else assignSelection(group);
});

els.undo.addEventListener("click", undo);
els.cardUndo.addEventListener("click", undo);
els.merge.addEventListener("click", mergeSelection);
els.unmerge.addEventListener("click", unmergeSelection);
els.studyReveal.addEventListener("click", revealStudy);
els.studyCard.addEventListener("click", (event) => {
  if (
    !state.studyRevealed
    && !event.target.closest("button, select, input, textarea")
  ) {
    revealStudy();
  }
});
els.ratingButtons.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-rating]");
  if (button) rateStudy(button.dataset.rating);
});
els.studyEdit.addEventListener("click", editStudyCard);
[els.studySuggestion, els.cardSuggestion].forEach((el) =>
  el.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-decision]");
    if (button) decideSuggestion(button.dataset.decision, event);
  })
);
els.studySave.addEventListener("click", saveStudyCard);
els.studyCancel.addEventListener("click", cancelStudyEdit);
els.showProgress.addEventListener("click", () => setMode("progress"));
els.showInfo.addEventListener("click", openInfo);
els.infoClose.addEventListener("click", closeInfo);
els.infoModal.addEventListener("click", (event) => {
  if (event.target === els.infoModal) closeInfo();
});
els.progressRefresh.addEventListener("click", loadOverallProgress);
els.overallProgressGroups.addEventListener(
  "toggle",
  (event) => {
    const code = event.target.dataset.code;
    if (!code) return;
    if (event.target.open) state.openLevels.add(code);
    else state.openLevels.delete(code);
  },
  true
);
els.progressBack.addEventListener("click", () => setMode("study"));
els.studyEnd.addEventListener("click", async () => {
  if (!state.study || !state.study.session_id) return;
  await api(`/api/study/session/${state.study.session_id}/close`, {
    method: "POST",
    body: JSON.stringify({ status: "abandoned" }),
  });
  state.study.status = "complete";
  state.study.ended = true;
  renderStudy();
});
els.revealHint.addEventListener("click", reveal);
els.card.addEventListener("click", (event) => {
  if (!state.revealed && !event.target.closest("button")) reveal();
});
[els.studyForms, els.cardForms].forEach((el) =>
  el.addEventListener("click", (event) => {
    const toggle = event.target.closest(".forms-toggle");
    if (!toggle) return;
    event.stopPropagation();
    toggleForms(toggle.dataset.forms);
  })
);
els.cardChoices.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-group]");
  if (!button) return;
  event.stopPropagation();
  assignCard(button.dataset.group || null);
});
els.cardPrev.addEventListener("click", () => moveCard(-1));
els.cardSkip.addEventListener("click", () => moveCard(1));

document.addEventListener("keydown", (event) => {
  if (!els.infoModal.hidden) {
    if (event.key === "Escape") closeInfo();
    return;
  }
  if (["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) {
    if (event.key === "Escape") event.target.blur();
    return;
  }

  const meta = event.metaKey || event.ctrlKey;

  if (state.mode !== "study" && meta && event.key.toLowerCase() === "z") {
    event.preventDefault();
    undo();
    return;
  }

  if (state.mode === "study") {
    if (state.studyEditing) return;
    if (event.key === " ") {
      event.preventDefault();
      revealStudy();
      return;
    }
    if (!meta && state.studyRevealed && ["1", "2", "3", "4"].includes(event.key)) {
      event.preventDefault();
      rateStudy(["again", "hard", "good", "easy"][Number(event.key) - 1]);
      return;
    }
    if (!meta && ["a", "k"].includes(event.key) && state.study && state.study.needs_decision) {
      event.preventDefault();
      if (event.key === "k") keepStudyCard();
      else archiveStudyCard();
      return;
    }
    if (!meta && event.key === "f" && state.study && state.study.status === "active"
        && state.study.variants.length > 1) {
      event.preventDefault();
      toggleForms(`study:${state.study.queue_id}`);
    }
    return;
  }

  if (state.mode === "cards") {
    if (event.key === " ") {
      event.preventDefault();
      reveal();
      return;
    }
    const sorting = currentCard();
    if (!meta && event.key === "f" && sorting && sorting.variants.length > 1) {
      event.preventDefault();
      toggleForms(`sort:${sorting.key}`);
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      moveCard(1);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      moveCard(-1);
      return;
    }
    if (meta) return;
    const key = event.key.toLowerCase();
    const waiting = sorting && !sorting.decided && !sorting.archived;
    if (waiting && (key in KEY_TO_GROUP || key === "u")) {
      // A level comes after keep-or-archive.
      event.preventDefault();
      if (!state.revealed) reveal();
      nudgeKeepOrArchive();
      return;
    }
    if (key in KEY_TO_GROUP) {
      event.preventDefault();
      assignCard(KEY_TO_GROUP[key]);
    } else if (key === "u") {
      event.preventDefault();
      assignCard(null);
    } else if (key === "a") {
      event.preventDefault();
      archiveCard();
    } else if (key === "k") {
      event.preventDefault();
      keepCard();
    }
    return;
  }

  if (meta && event.key.toLowerCase() === "a") {
    event.preventDefault();
    selectOnly(state.visible.map((w) => w.key));
    return;
  }
  if (event.key === "Escape") {
    state.selected.clear();
    paintSelection();
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    moveCursor(event.key === "ArrowDown" ? 1 : -1, event.shiftKey);
    return;
  }
  if (meta) return;

  const key = event.key.toLowerCase();
  if (key in KEY_TO_GROUP) {
    event.preventDefault();
    assignSelection(KEY_TO_GROUP[key]);
  } else if (key === "u") {
    event.preventDefault();
    assignSelection(null);
  } else if (key === "a") {
    event.preventDefault();
    archiveSelection();
  }
});

new ResizeObserver(() => {
  document.documentElement.style.setProperty("--toolbar-h", `${els.toolbar.offsetHeight}px`);
}).observe(els.toolbar);

// The fixed bars at the bottom, so Study and Sort can center the card in the
// space that's actually left.
const bottomBars = new ResizeObserver(() => {
  const root = document.documentElement.style;
  root.setProperty("--hints-real-h", `${els.hints.offsetHeight}px`);
  root.setProperty("--assign-h", `${els.assignBar.offsetHeight}px`);
});
bottomBars.observe(els.hints);
bottomBars.observe(els.assignBar);

load().catch((error) => {
  els.empty.hidden = false;
  els.empty.textContent = `Could not load words: ${error.message}`;
});
