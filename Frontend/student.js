/* ============================================================
   Acadza demo · student practice panel
   Zero-dependency. Shows the existing 43 questions, lets the
   student practice and raise queries through the API.
   ============================================================ */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) => String(s ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

/* Plain-text preview of question HTML for the list, for questions whose
   stem extraction came back empty (the practice card renders the full HTML). */
const previewText = (html, max = 130) => {
  const text = String(html || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? text.slice(0, max) + "…" : text;
};

const state = {
  me: null,
  subjects: [],
  questions: [],
  visible: [],
  selectedId: null,
  subject: "All",
  search: "",
  queries: [],
};

const toast = (msg, kind = "") => {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 300); }, 3200);
};

const timeAgo = (iso) => {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return iso;
  if (ms < 0) return "just now";
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + "m ago";
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + "h ago";
  return Math.floor(hrs / 24) + "d ago";
};

const STATUS_LABEL = {
  submitted: "⏳ AI is checking your query",
  sent_to_faculty: "✓ Genuine catch — sent to the faculty queue",
  closed_by_ai: "✓ Checked — this question is correct as stored",
};

const VERDICT_LABEL = {
  no_error: "no error found",
  already_fixed: "already corrected earlier",
  display_issue: "display matter",
  app_issue: "app behaviour",
  language_mode: "language request",
  not_actionable: "not actionable",
  unresolved: "inconclusive check",
};

/* ---------------- boot ---------------- */
async function boot() {
  try {
    const res = await fetch("/api/auth/me", { cache: "no-store" });
    if (!res.ok) { window.location.href = "/login.html"; return; }
    state.me = await res.json();
    if (state.me.role === "faculty") { window.location.href = "/"; return; }
  } catch {
    window.location.href = "/login.html";
    return;
  }
  $("#whoName").textContent = state.me.username;

  try {
    const res = await fetch("/api/practice/questions", { cache: "no-store" });
    if (!res.ok) throw new Error((await res.json()).detail || "HTTP " + res.status);
    const data = await res.json();
    state.subjects = data.subjects;
    state.questions = data.questions;
  } catch (err) {
    $("#practiceLoading").innerHTML = `
      <div class="detail-error"><h3 style="margin:0 0 8px">Couldn't load practice questions</h3>
      <p style="margin:0">${esc(String(err))}</p></div>`;
    return;
  }

  renderSubjectFilters();
  wireChrome();
  applyFilters();
  loadMyQueries();
}

/* ---------------- filters + list ---------------- */
function renderSubjectFilters() {
  const wrap = $("#subjectFilters");
  const subjects = ["All", ...state.subjects];
  wrap.innerHTML = subjects.map((s) => `
    <label class="chip f-chip ${state.subject === s ? "active" : ""}">
      <input type="radio" name="subject" value="${esc(s)}" ${state.subject === s ? "checked" : ""} />
      ${esc(s)}
    </label>`).join("");
  $$("input", wrap).forEach((input) =>
    input.addEventListener("change", () => {
      state.subject = input.value;
      renderSubjectFilters();
      applyFilters();
    }));
}

function applyFilters() {
  const text = state.search.trim().toLowerCase();
  state.visible = state.questions.filter((q) => {
    if (state.subject !== "All" && q.subject !== state.subject) return false;
    if (!text) return true;
    return `${q.subject} ${q.chapter} ${q.stem} ${q.questionId}`
      .toLowerCase().includes(text);
  });
  $("#questionCount").textContent = `${state.visible.length}`;
  renderQuestionList();
}

function renderQuestionList() {
  const wrap = $("#questionList");
  if (!state.visible.length) {
    wrap.innerHTML = `<div class="queue-empty">${state.search ? "No matching questions." : "No questions."}</div>`;
    return;
  }
  wrap.innerHTML = state.visible.map((q) => `
    <button class="q-row ${state.selectedId === q.questionId ? "selected" : ""}" data-qid="${esc(q.questionId)}">
      <span class="q-row-top">
        <span>${esc(q.subject)}${q.chapter ? ` · ${esc(q.chapter)}` : ""}</span>
        <span>${esc(q.questionType)}</span>
      </span>
      <span class="q-row-stem">${esc(q.stem || previewText(q.questionHtml) || "(no text)")}</span>
    </button>`).join("");
  $$(".q-row", wrap).forEach((btn) =>
    btn.addEventListener("click", () => selectQuestion(btn.dataset.qid)));
}

function selectQuestion(qid) {
  state.selectedId = qid;
  renderQuestionList();
  showPractice();
  renderPractice();
}

/* ---------------- practice view ---------------- */
function showPractice() {
  $("#queriesPanel").classList.add("hidden");
  $("#practicePanel").classList.remove("hidden");
}

function selectedQuestion() {
  return state.questions.find((q) => q.questionId === state.selectedId) || null;
}

function renderPractice() {
  const panel = $("#practicePanel");
  const q = selectedQuestion();
  if (!q) {
    panel.innerHTML = `
      <div class="panel-card">
        <h2 style="margin:0 0 6px;font-size:17px">Pick a question</h2>
        <p class="panel-note" style="margin:0">Choose a subject on the left and start practising. Use “Report question” if you think a question is wrong — the AI checks every query before faculty see it.</p>
      </div>`;
    return;
  }

  panel.innerHTML = `
    <div class="panel-card practice-card" data-qid="${esc(q.questionId)}">
      <div class="practice-meta">
        <span class="chip">${esc(q.subject)}${q.chapter ? ` · ${esc(q.chapter)}` : ""}</span>
        <span class="chip">${esc(q.questionType)}</span>
        ${q.level ? `<span class="chip">${esc(q.level)}</span>` : ""}
      </div>
      <div class="rendered">${q.questionHtml || "<i>(no text)</i>"}</div>
      <div class="practice-options">
        ${q.options.map((o) => `
          <div class="practice-opt" data-letter="${esc(o.letter)}">
            <span class="letter">${esc(o.letter)}</span>
            <span>${o.text ? esc(o.text) : "<i>(blank)</i>"}</span>
          </div>`).join("")}
      </div>
      <div class="practice-feedback" id="practiceFeedback"></div>
      <div class="practice-actions">
        <button class="btn primary" id="checkBtn" type="button">Check answer</button>
        <button class="btn ghost" id="solutionBtn" type="button">Show solution</button>
        <button class="btn warn" id="reportBtn" type="button">⚑ Report question</button>
      </div>
      <div class="sol-box hidden" id="solutionBox"></div>
    </div>`;

  const letters = String(q.key || "").split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
  let picked = null;

  $$(".practice-opt", panel).forEach((opt) => {
    opt.addEventListener("click", () => {
      if (opt.classList.contains("locked")) return;
      picked = opt.dataset.letter;
      $$(".practice-opt", panel).forEach((o) => o.classList.remove("picked"));
      opt.classList.add("picked");
    });
  });

  $("#checkBtn").addEventListener("click", () => {
    const feedback = $("#practiceFeedback");
    if (!picked) {
      feedback.textContent = "Pick an option first.";
      feedback.className = "practice-feedback";
      return;
    }
    const correct = letters.includes(picked);
    $$(".practice-opt", panel).forEach((o) => {
      o.classList.add("locked");
      if (letters.includes(o.dataset.letter)) o.classList.add("correct");
      else if (o.dataset.letter === picked) o.classList.add("wrong");
    });
    feedback.textContent = correct
      ? "Correct! ✓"
      : `Not quite — the key says (${letters.join(", ") || "—"}). If you still think it is wrong, report it.`;
    feedback.className = "practice-feedback " + (correct ? "good" : "bad");
  });

  $("#solutionBtn").addEventListener("click", () => {
    const box = $("#solutionBox");
    box.classList.toggle("hidden");
    if (!box.classList.contains("hidden") && !box.innerHTML) {
      box.innerHTML = q.solutionHtml
        ? `<strong>Solution</strong><div class="rendered">${q.solutionHtml}</div>`
        : "<i>No stored solution.</i>";
      $("#solutionBtn").textContent = box.classList.contains("hidden")
        ? "Show solution" : "Hide solution";
    }
  });

  $("#reportBtn").addEventListener("click", () => openReportDialog(q));
}

/* ---------------- report a question ---------------- */
function openReportDialog(q) {
  $("#reportQuestionRef").textContent =
    `Question ${q.questionId.slice(-6)} · ${q.subject}${q.chapter ? " · " + q.chapter : ""}`;
  $("#reportComment").value = "";
  openDialog($("#reportDialog"));
  $("#reportComment").focus();
}

const openDialog = (d) => (typeof d.showModal === "function" ? d.showModal() : d.setAttribute("open", ""));
const closeDialog = (d) => (typeof d.close === "function" ? d.close() : d.removeAttribute("open"));

async function sendReport() {
  const q = selectedQuestion();
  const button = $("#reportSend");
  button.disabled = true;
  try {
    const res = await fetch("/api/queries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        questionId: q.questionId,
        comment: $("#reportComment").value.trim(),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not send the query.");
    closeDialog($("#reportDialog"));
    toast(`Query ${data.reportId} sent. The AI is checking it — the verdict appears here once it is done.`, "ok");
    loadMyQueries();
  } catch (err) {
    toast(err.message, "err");
  } finally {
    button.disabled = false;
  }
}

/* ---------------- my queries ---------------- */
async function loadMyQueries() {
  try {
    const res = await fetch("/api/queries/mine", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    state.queries = data.queries;
    const badge = $("#myQueriesCount");
    badge.hidden = !state.queries.length;
    badge.textContent = state.queries.length;
    renderQueries();
    schedulePoll();
  } catch { /* badge is best-effort */ }
}

/* The AI check runs after the submit response, so poll while anything is still
   being checked. Polling stops by itself once every query has a verdict. */
let pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!state.queries.some((q) => q.status === "submitted")) return;
  pollTimer = setTimeout(loadMyQueries, 3000);
}

function renderQueries() {
  const wrap = $("#queriesList");
  if (!state.queries.length) {
    wrap.innerHTML = `<p class="panel-note" style="margin:0">No queries yet. Report a question from the practice view.</p>`;
    return;
  }
  wrap.innerHTML = state.queries.map((query) => `
    <div class="query-row">
      <span>${esc(STATUS_LABEL[query.status] || query.status)}
        ${query.verdict ? `<span class="chip">${esc(VERDICT_LABEL[query.verdict] || query.verdict)}</span>` : ""}</span>
      <span class="mono" style="font-size:12px">${esc(query.report_id)} · question ${esc(query.question_id.slice(-6))}</span>
      <span>“${esc(query.comment)}”</span>
      ${query.reply ? `<span class="query-reply">💬 ${esc(query.reply)}</span>` : ""}
      <span class="when">${esc(timeAgo(query.reported_at))}</span>
    </div>`).join("");
}

/* ---------------- chrome ---------------- */
function wireChrome() {
  $("#search").addEventListener("input", (e) => {
    state.search = e.target.value;
    applyFilters();
  });
  $("#myQueriesBtn").addEventListener("click", () => {
    $("#practicePanel").classList.toggle("hidden");
    $("#queriesPanel").classList.toggle("hidden");
    loadMyQueries();
  });
  $("#queriesClose").addEventListener("click", showPractice);
  $("#logoutBtn").addEventListener("click", async () => {
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    window.location.href = "/login.html";
  });
  $("#reportClose").addEventListener("click", () => closeDialog($("#reportDialog")));
  $("#reportCancel").addEventListener("click", () => closeDialog($("#reportDialog")));
  $("#reportSend").addEventListener("click", sendReport);
}

boot();
