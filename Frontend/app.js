/* ============================================================
   Acadza · Report → Fix Console — app.js
   Zero-dependency. Reads precomputed Backend/work/console.json through the
   same-origin API. Faculty decisions use the API review endpoints
   and the backend audit trail.
   ============================================================ */
"use strict";

/* ---------------- utilities ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const STORE_KEY = "acadza_console_v2";

const esc = (s) => String(s ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

const fmtWhen = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
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
  const days = Math.floor(hrs / 24);
  return days + "d ago";
};

const toast = (msg, kind = "") => {
  const region = $("#toasts");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  region.appendChild(el);
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 300); }, 3200);
};

const debounce = (fn, ms = 120) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};

// <dialog> fallbacks for browsers without dialog.insertBefore support
const openDialog = (d) => (typeof d.showModal === "function" ? d.showModal() : d.setAttribute("open", ""));
const closeDialog = (d) => (typeof d.close === "function" ? d.close() : d.removeAttribute("open"));

/* ---------------- state ---------------- */
const state = {
  data: null,
  apiBacked: false,
  byQuestion: new Map(),     // qid -> question block
  byReport: new Map(),       // reportId -> fused report
  byGroup: new Map(),        // qid -> group block
  reportsOf: new Map(),      // qid -> [reports]
  decisions: {},             // qid -> {action, reviewer, note, edit, at}
  sessionAudit: [],          // entries added in this browser
  selectedQid: null,
  selectedReport: null,
  editMode: false,
  searchText: "",
  visibleGroups: [],
  reviewer: localStorage.getItem("acadza_reviewer") || "Demo Faculty",
};

/* ---------------- boot ---------------- */
async function boot() {
  // Demo auth gate: only faculty accounts may open the review console.
  try {
    const me = await fetch("/api/auth/me", { cache: "no-store" });
    if (!me.ok) { window.location.href = "/login.html"; return; }
    const user = await me.json();
    if (user.role !== "faculty") { window.location.href = "/student.html"; return; }
  } catch {
    window.location.href = "/login.html";
    return;
  }

  $("#reviewerName").textContent = state.reviewer;

  try {
    const res = await fetch("/api/console", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    hydrate(data);
    state.apiBacked = true;
  } catch (err) {
    renderLoadError(err);
    return;
  }

  restoreSession();
  wireChrome();
  autoSelectFirst();
  renderAll();

  window.addEventListener("keydown", onKeydown);
}

function hydrate(data) {
  state.data = data;
  state.byQuestion.clear();
  state.byReport.clear();
  state.byGroup.clear();
  state.reportsOf.clear();
  for (const q of data.questions) state.byQuestion.set(q.questionId, q);
  for (const r of data.reports) {
    state.byReport.set(r.reportId, r);
    if (!state.reportsOf.has(r.questionId)) state.reportsOf.set(r.questionId, []);
    state.reportsOf.get(r.questionId).push(r);
  }
  for (const g of data.groups) state.byGroup.set(g.questionId, g);
  for (const list of state.reportsOf.values()) {
    list.sort((a, b) => (a.reportedAt || "").localeCompare(b.reportedAt || ""));
  }
}

async function refreshConsoleData() {
  const selectedQid = state.selectedQid;
  const selectedReport = state.selectedReport;
  const response = await fetch("/api/console", { cache: "no-store" });
  if (!response.ok) throw new Error(`Could not refresh console data (HTTP ${response.status})`);
  hydrate(await response.json());
  state.apiBacked = true;
  state.decisions = {};
  state.sessionAudit = [];
  restoreSession();
  if (selectedQid && visibleGroups().some((group) => group.questionId === selectedQid)) {
    state.selectedQid = selectedQid;
    state.selectedReport = state.byReport.has(selectedReport) ? selectedReport : null;
  } else {
    autoSelectFirst();
  }
  renderAll();
}

function restoreSession() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      if (!state.apiBacked) {
        state.decisions = saved.decisions || {};
        state.sessionAudit = saved.sessionAudit || [];
      }
    }
  } catch { /* corrupt state is not fatal */ }

  // Bootstrap decisions from the engine's own audit log (apply_edit.py writes the
  // same shape), so cases already approved via the CLI show as applied here.
  for (const a of state.data.audit || []) {
    if (!["approve", "reject"].includes(a.action)) continue;
    if (state.decisions[a.caseId]) continue;
    state.decisions[a.caseId] = {
      action: a.action === "approve" ? "approved" : "rejected",
      reviewer: a.reviewer || "engine",
      note: a.note || "",
      edit: a.field ? { field: a.field, find: String(a.before ?? ""), replace: String(a.after ?? "") } : null,
      at: a.at,
    };
  }
}

function persist() {
  localStorage.setItem(STORE_KEY, JSON.stringify({
    decisions: state.decisions,
    sessionAudit: state.sessionAudit,
  }));
}

function autoSelectFirst() {
  const groups = visibleGroups();
  state.selectedQid = groups[0]?.questionId || null;
  state.selectedReport = groups[0]
    ? (state.reportsOf.get(groups[0].questionId) || [])[0]?.reportId || null
    : null;
}

/* ---------------- audit ---------------- */
const allAudit = () => [...(state.data?.audit || []), ...state.sessionAudit]
  .sort((a, b) => (b.at || "").localeCompare(a.at || ""));

function addAudit(entry) {
  state.sessionAudit.push({
    recordedBy: "console",
    ...entry,
  });
  persist();
}

function exportAudit(fmt) {
  const entries = allAudit();
  let text;
  if (fmt === "jsonl") {
    text = entries.map((e) => JSON.stringify(e)).join("\n") + "\n";
  } else {
    text = JSON.stringify(entries, null, 1);
  }
  const blob = new Blob([text], { type: fmt === "jsonl" ? "application/jsonl" : "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fmt === "jsonl" ? "audit_log.jsonl" : "audit_log.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------------- derived ---------------- */
const statusOf = (qid) => {
  const d = state.decisions[qid];
  return d ? d.action : "pending";
};

const isResolved = (qid) => statusOf(qid) !== "pending";

const visibleGroups = () => {
  const text = state.searchText.trim().toLowerCase();
  let groups = [...state.byGroup.values()].filter((group) =>
    group.verdict === "defect" && !isResolved(group.questionId));
  if (text) {
    groups = groups.filter((group) => {
      const reports = state.reportsOf.get(group.questionId) || [];
      return `${group.questionId} ${group.subject} ${group.chapter} ${group.repairLabel}`
        .toLowerCase().includes(text) || reports.some((report) =>
        `${report.comment} ${report.reportId}`.toLowerCase().includes(text));
    });
  }

  const sevRank = { critical: 0, high: 1, medium: 2, low: 3, none: 4 };
  groups.sort((a, b) => {
    const sa = sevRank[a.severity || "none"] - sevRank[b.severity || "none"];
    if (sa !== 0) return sa;
    if (b.reportCount !== a.reportCount) return b.reportCount - a.reportCount;
    return (b.lastReportedAt || "").localeCompare(a.lastReportedAt || "");
  });
  return groups;
};

const selectedReport = () =>
  state.selectedReport ? state.byReport.get(state.selectedReport)
    : (state.reportsOf.get(state.selectedQid) || [])[0];

/* ---------------- chrome wiring ---------------- */
function wireChrome() {
  $("#search").addEventListener("input", debounce((e) => {
    state.searchText = e.target.value;
    renderQueue();
  }));

  $("#reviewerChip").addEventListener("click", () => {
    const name = prompt("Faculty name recorded with the decision:", state.reviewer);
    if (name && name.trim()) {
      state.reviewer = name.trim();
      $("#reviewerName").textContent = state.reviewer;
      localStorage.setItem("acadza_reviewer", state.reviewer);
      toast("Faculty name updated", "ok");
    }
  });

  $("#confirmClose").addEventListener("click", () => closeDialog($("#confirmDialog")));
  $("#confirmCancel").addEventListener("click", () => closeDialog($("#confirmDialog")));

  $("#logoutBtn")?.addEventListener("click", async () => {
    try { await fetch("/api/auth/logout", { method: "POST" }); } catch { /* ignore */ }
    window.location.href = "/login.html";
  });

  $("#pipelineBtn")?.addEventListener("click", async () => {
    const button = $("#pipelineBtn");
    button.disabled = true;
    try {
      const res = await fetch("/api/pipeline/run", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
      await refreshConsoleData();
      toast(data.synced
        ? `Pipeline done — ${data.synced} new quer${data.synced === 1 ? "y" : "ies"} processed`
        : "Pipeline done — no new queries", "ok");
    } catch (err) {
      toast(`Pipeline failed: ${err.message}`, "err");
    } finally {
      button.disabled = false;
    }
  });
}

function onKeydown(e) {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select" || $("#confirmDialog").open) return;

  if (e.key === "/") { e.preventDefault(); $("#search").focus(); return; }
  if (e.key === "j" || e.key === "k") {
    e.preventDefault();
    const groups = state.visibleGroups;
    if (!groups.length) return;
    const idx = groups.findIndex((g) => g.questionId === state.selectedQid);
    const next = e.key === "j" ? Math.min(groups.length - 1, idx + 1) : Math.max(0, idx - 1);
    selectGroup(groups[next].questionId, null, true);
    return;
  }
  if (e.key === "a" || e.key === "r") {
    const rep = selectedReport();
    if (!rep) return;
    e.preventDefault();
    if (isResolved(rep.questionId)) { toast("Case already resolved", "err"); return; }
    if (e.key === "a") $("#btnApprove")?.click();
    else $("#btnReject")?.click();
  }
}

/* ---------------- focused faculty queue ---------------- */
function renderAll() {
  renderQueue();
  renderDetail();
}

function renderQueue() {
  const wrap = $("#queueList");
  const groups = visibleGroups();
  state.visibleGroups = groups;
  // Keep the selection on a case that just got resolved (its banner needs to
  // be visible); only fall back when the selection points at an unknown case.
  if (state.selectedQid && !groups.some((group) => group.questionId === state.selectedQid) &&
      !state.byGroup.has(state.selectedQid)) {
    state.selectedQid = groups[0]?.questionId || null;
    state.selectedReport = state.selectedQid
      ? (state.reportsOf.get(state.selectedQid) || [])[0]?.reportId || null
      : null;
    renderDetail();
  }
  $("#queueCount").textContent = `${groups.length}`;

  if (!groups.length) {
    wrap.innerHTML = `
      <div class="queue-empty">${state.searchText ? "No matching fixes." : "All reported fixes have been reviewed."}</div>`;
    return;
  }

  wrap.innerHTML = groups.map((group) => {
    const reports = state.reportsOf.get(group.questionId) || [];
    const selected = state.selectedQid === group.questionId;
    const firstReport = reports[0];
    return `<button class="issue-row ${selected ? "selected" : ""}" data-group="${esc(group.questionId)}">
      <span class="issue-row-top">
        <strong>${esc(group.subject)}${group.chapter ? ` · ${esc(group.chapter)}` : ""}</strong>
        <span class="issue-count">${reports.length}</span>
      </span>
      <span class="issue-row-report">${esc(firstReport?.comment || group.repairLabel || "Content issue")}</span>
      <span class="issue-row-meta">${reports.length} student report${reports.length === 1 ? "" : "s"} · ${Math.round((firstReport?.confidence ?? group.confidence ?? 0) * 100)}% AI confidence</span>
    </button>`;
  }).join("");

  $$(".issue-row", wrap).forEach((button) => {
    button.addEventListener("click", () => selectGroup(button.dataset.group, null, false));
  });
}

function selectGroup(qid, reportId, allowScroll) {
  state.selectedQid = qid;
  state.selectedReport = reportId;
  state.editMode = false;
  if (!reportId) {
    const reps = state.reportsOf.get(qid) || [];
    state.selectedReport = reps.length ? reps[0].reportId : null;
  }
  if (allowScroll) {
    const el = $(`.issue-row[data-group="${qid}"]`);
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
  }
  renderQueue();
  renderDetail();
}

/* ---------------- rendering: detail ---------------- */
function renderDetail() {
  const panel = $("#detailPanel");
  const rep = selectedReport();

  if (!rep) {
    panel.innerHTML = `
      <div class="detail-error">
        <h3 style="margin:0 0 8px">No case selected</h3>
        Pick a question from the queue on the left.
      </div>`;
    return;
  }

  const q = state.byQuestion.get(rep.questionId);
  const g = state.byGroup.get(rep.questionId);
  const decision = state.decisions[rep.questionId];
  const resolved = Boolean(decision);
  const siblings = state.reportsOf.get(rep.questionId) || [];

  const keyedLetters = splitLetters(q.answerRaw);
  const solvedLetters = splitLetters(rep.solveAnswer);

  panel.innerHTML = `
  <article class="case">
    ${caseHead(rep, g, resolved, decision)}

    <div class="case-grid">

      <!-- ===== question render ===== -->
      <section class="card q-render">
        <div class="card-head">
          <h3>Question</h3>
          <span class="spacer"></span>
          <button class="q-toggle" id="rawToggle">View stored HTML / MathML</button>
        </div>
        <div class="card-body">
          <div class="rendered">${q.questionHtml || "<i>(empty)</i>"}</div>
          <div id="rawView" class="raw-view hidden"></div>
          <div class="opts-grid">
            ${q.options.map((o) => {
              const keyed = keyedLetters.includes(o.letter.toUpperCase());
              const solved = solvedLetters.includes(o.letter.toUpperCase());
              const blank = !o.text || !o.text.trim();
              return `<div class="opt ${keyed ? "keyed" : ""} ${solved ? "solved" : ""} ${blank ? "blank" : ""}">
                <span class="letter">${esc(o.letter)}</span>
                <span>${blank ? "<i>(blank)</i>" : esc(o.text)}</span>
                <span class="tagline">
                  ${keyed ? '<span class="chip" style="color:var(--warn)">key</span>' : ""}
                  ${solved ? `<span class="chip" style="color:var(--good)">solve ${esc(rep.solveConfidence ?? "")}</span>` : ""}
                </span>
              </div>`;
            }).join("")}
          </div>
        </div>
      </section>

      <!-- ===== key vs AI solve ===== -->
      ${compareCard(rep, q)}

      <!-- ===== evidence ===== -->
      <section class="card">
        <div class="card-head">
          <h3>AI verdict &amp; evidence</h3>
          <span class="spacer"></span>
          <span class="badge v-${esc(rep.verdict)}">${esc(prettyVerdict(rep.verdict))}</span>
        </div>
        <div class="card-body">
          ${rep.headline ? `<div class="mini-label">${esc(rep.headline)}</div>` : ""}
          <div class="chain">
            ${chainNode("Independent solve", rep.solveInsufficient ? "not solvable" : esc(rep.solveDisplay || rep.solveAnswer || "—"),
              `confidence ${(rep.solveConfidence ?? 0).toFixed(2)}`, rep.solveInsufficient ? "" : "matches")}
            <span class="chain-link">⟷</span>
            ${chainNode("Stored key", esc(rep.keyDisplay || rep.key || "—"), "as supplied by Acadza",
              rep.route === "auto_apply" ? "flagged" : "")}
            <span class="chain-link">⟷</span>
            ${chainNode("Stored solution", rep.solutionSupportsSolve === true
              ? "supports the solve" : rep.solutionSupportsKey === true
              ? "supports the key" : "does not settle it", "tie-breaker only", "")}
          </div>
          <div class="verdict-box">
            <p>${esc(rep.evidence)}</p>
            ${rep.errorExplanation ? `<p>${esc(rep.errorExplanation)}</p>` : ""}
          </div>
          ${rep.solveWorking ? `
            <button class="q-toggle mt12" id="workingToggle">Show independent working</button>
            <div id="workingBox" class="collapse-target hidden"><div class="working-box">${esc(rep.solveWorking)}</div></div>` : ""}
          ${q.solutionHtml ? `
            <button class="q-toggle mt8" id="solToggle">Show stored solution</button>
            <div id="solBox" class="collapse-target hidden sol-render"><div class="rendered">${q.solutionHtml}</div></div>` : `
            <div class="mt8 sol-empty">No stored solution.</div>`}
        </div>
      </section>

      <!-- ===== diff / proposed change ===== -->
      ${diffCard(rep)}

      <!-- ===== student reports ===== -->
      <section class="card">
        <div class="card-head">
          <h3>Student reports</h3>
          <span class="spacer"></span>
          <span class="chip">${siblings.length} report${siblings.length !== 1 ? "s" : ""} · clustered as “${esc(rep.theme || "report")}”</span>
        </div>
        <div class="card-body">
          ${siblings.map((r) => `
            <div class="rep-card-row ${r.reportId === rep.reportId ? "mine" : ""}">
              <span class="avatar">${esc(r.reportId.slice(1, 4))}</span>
              <div>
                <div class="rep-cmt">“${esc(highlightStudent(r.comment))}”</div>
                <div class="rep-meta2">
                  <span class="mono">${esc(r.reportId)}</span>
                  <span class="mono">${esc(r.reportId === rep.reportId ? r.studentId : (state.byReport.get(r.reportId)?.studentId || ""))}</span>
                  <span class="rep-src">${esc(r.source)} · ${esc(r.reportedAt ? timeAgo(r.reportedAt) : "")}</span>
                </div>
              </div>
            </div>`).join("")}
        </div>
      </section>

      <!-- ===== student reply ===== -->
      <section class="card">
        <div class="card-head"><h3>Student reply preview</h3><span class="spacer"></span>
          ${replyResolvedChip(rep, resolved)}
        </div>
        <div class="card-body">
          ${rep.reply ? `
            <div class="reply-text">${esc(rep.reply)}</div>
            <div class="reply-foot">
              <span class="chip">tone: ${esc(rep.replyCategory)}</span>
              <span class="chip">language: ${esc(rep.replyLanguage)}</span>
              <span class="chip">template: none — model-written per report</span>
            </div>` : `
            <div class="reply-missing">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v.01M12 11v5"/></svg>
              Reply pending — regenerate with <code class="mono">Backend/Reply/student_reply.py</code>
            </div>`}
        </div>
      </section>

      <!-- ===== action ===== -->
      <section class="card" id="actionCard">
        ${resolved ? resolvedBanner(decision, rep) : actionForm(rep)}
      </section>

      <!-- ===== audit mini ===== -->
      <section class="card">
        <div class="card-head"><h3>Case history</h3></div>
        <div class="card-body">
          ${renderCaseHistory(rep.questionId)}
          ${q.errorHistory?.length ? `
            <div class="am-row">
              <span class="am-action engine">PHASE-0</span>
              <span style="color:var(--muted)">Faculty corrections in the stored history:
              ${esc(q.errorHistory.map((h) => `${h.type || ""} (${h.at ? h.at.slice(0, 10) : ""})`).join("; "))}</span>
            </div>` : ""}
        </div>
      </section>
    </div>

    <div class="detail-foot">
      <span><kbd>J</kbd>/<kbd>K</kbd> navigate</span>
      <span><kbd>Enter</kbd> collapse group</span>
      <span><kbd>A</kbd> approve · <kbd>R</kbd> reject</span>
      <span><kbd>/</kbd> search</span>
      <span class="sep"></span>
      <span class="mono">${esc(dataStamp())}</span>
    </div>
  </article>`;

  wireDetail(rep);
}

const replyResolvedChip = (rep, resolved) => {
  const effectivelyResolved = rep.replyResolved === true || resolved;
  return effectivelyResolved
    ? '<span class="chip" style="color:var(--good)">resolved ✓</span>'
    : '<span class="chip" style="color:var(--warn)">being checked</span>';
};

const dataStamp = () => {
  const m = state.data.meta;
  return `solver ${m.solvePromptVersion} · replies ${m.replyPromptVersion} · data ${m.generatedAt.slice(0, 10)}`;
};

const prettyVerdict = (v) => ({
  defect: "defect", no_error: "no error", display_issue: "display",
  app_issue: "app bug", language_mode: "language", already_fixed: "already fixed",
  not_actionable: "not actionable",
}[v] || v || "—");

const prettyRoute = (r) => ({
  auto_apply: "auto-apply", faculty_review: "faculty", no_change: "no change",
}[r] || r || "—");

const splitLetters = (v) => {
  if (typeof v !== "string") return [];
  const arr = Array.isArray(v) ? v : v.split(",");
  return arr.map((x) => String(x).trim().toUpperCase()).filter(Boolean);
};

function chainNode(label, value, sub, cls) {
  return `
    <div class="chain-node ${cls}">
      <div class="cn-label">${esc(label)}</div>
      <div class="cn-value">${value}</div>
      <div class="cn-sub">${esc(sub)}</div>
    </div>`;
}

function caseHead(rep, g, resolved, decision) {
  const conf = rep.confidence ?? 0;
  const confCls = conf >= 0.8 ? "conf-high" : conf >= 0.6 ? "conf-mid" : "conf-low";
  return `
    <header class="case-head">
      <div class="case-path">
        <span class="qtype">${esc(rep.questionType)}</span>
        <span>${esc(rep.subject)}/${esc(rep.chapter || "")}</span>
        <span class="mono">${esc(rep.questionId.slice(-6))}</span>
      </div>
      <div class="case-title">${esc(rep.headline || prettifyHeadline(rep))}</div>
      <div class="case-id" title="full id: ${esc(rep.questionId)}">${esc(rep.questionId)}</div>
      <div class="case-chips">
        ${rep.severity ? `<span class="badge s-${esc(rep.severity)}">${esc(rep.severity)} severity</span>` : ""}
        ${rep.repair ? `<span class="chip" title="repair type">${esc(rep.repairLabel)}</span>` : ""}
        <span class="badge r-${esc(rep.route)}">${esc(prettyRoute(rep.route))}</span>
        ${resolved ? `<span class="chip" style="color:${decision.action === "approved" ? "var(--good)" : "var(--bad)"}">
          ${decision.action === "approved" ? "✓ Applied by" : "✕ Rejected by"} ${esc(decision.reviewer)}</span>` : ""}
        <span style="display:flex;gap:8px;align-items:center;margin-left:auto">
          <span class="conf-ring ${confCls}" data-v="${Math.round(conf * 100)}" style="--p:${conf * 100}"></span>
          <span class="conf-note"><b>${Math.round(conf * 100)}%</b><span>engine confidence</span></span>
        </span>
      </div>
    </header>`;
}

const prettifyHeadline = (rep) => {
  switch (rep.verdict) {
    case "no_error": return "Checked — the question is correct";
    case "display_issue": return "Rendering / load issue, content is fine";
    case "app_issue": return "Platform behaviour, not this question";
    case "language_mode": return "Language request";
    case "already_fixed": return "Already corrected by faculty";
    case "not_actionable": return "Comment is not a report we can action";
    default: return "Decision record";
  }
};

function diffCard(rep) {
  const hasDiff = rep.diff && rep.diff.field;
  return `
  <section class="card">
    <div class="card-head">
      <h3>Proposed change</h3>
      <span class="spacer"></span>
      ${hasDiff ? `<span class="chip">field: <span class="mono">${esc(rep.diff.field)}</span></span>` : ""}
      ${rep.suggestedEdit ? `<span class="chip" style="color:var(--good)">ready edit attached</span>` : ""}
      ${rep.manualFixRequired ? `<span class="chip" style="color:var(--warn)">authored fix required</span>` : ""}
    </div>
    <div class="card-body">
      ${hasDiff ? `
        <div class="diff-wrap">
          <div class="diff-box before">
            <div class="diff-label">Before (stored)</div>
            <div class="diff-value">${esc(rep.diff.before)}</div>
          </div>
          <div class="diff-dir">→</div>
          <div class="diff-box after">
            <div class="diff-label">After (proposed)</div>
            <div class="diff-value">${esc(rep.diff.after)}</div>
          </div>
        </div>` : `
        <div class="manual-note">
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8z"/><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z"/></svg>
          <span>
            ${rep.manualFixRequired
              ? "<b>" + esc(rep.severity === "critical" ? "Must be fixed by a human." : "Needs a human-authored fix.") + "</b> " +
                esc(rep.manualReason || "")
              : esc(rep.route === "no_change"
                ? "No content change proposed for this verdict."
                : "No specific edit is proposed for this case.")}
          </span>
        </div>`}
      ${rep.missingOptions?.length ? `
        <div class="mt8"><span class="mini-label">Missing options</span>
          <span class="tag-grid">${rep.missingOptions.map((l) => `<span class="chip" style="color:var(--warn)">${esc(l)}</span>`).join("")}</span>
        </div>` : ""}
      ${rep.blankOptions?.length ? `
        <div class="mt8"><span class="mini-label">Blank options</span>
          <span class="tag-grid">${rep.blankOptions.map((l) => `<span class="chip" style="color:var(--warn)">${esc(l)}</span>`).join("")}</span>
        </div>` : ""}
    </div>
  </section>`;
}

/* ---------------- answer key vs AI solve ---------------- */
function compareCard(rep, q) {
  // Only the case's own stored answer (currentDisplay) is comparable with the
  // solve: keyDisplay reads the live sheet, which may already carry a fix.
  const keyAns = rep.currentDisplay || rep.keyDisplay || "";
  const solveAns = rep.suggestedDisplay || rep.solveDisplay || "";
  if (rep.verdict !== "defect" || !keyAns || !solveAns) return "";

  const norm = (v) => String(v).replace(/\s+/g, " ").trim();
  const differs = norm(keyAns) !== norm(solveAns);
  const [keyHtml, solveHtml] = differs ? tokenDiff(keyAns, solveAns) : [esc(keyAns), esc(solveAns)];
  const conf = rep.solveConfidence ?? null;
  const liveKey = rep.keyDisplay && norm(rep.keyDisplay) !== norm(keyAns) ? rep.keyDisplay : null;

  return `
  <section class="review-section cmp-section">
    <details class="cmp" ${differs ? "open" : ""}>
      <summary class="cmp-summary">
        <h2>Answer key vs AI solve</h2>
        <span class="chip ${differs ? "cmp-differs" : "cmp-agrees"}">
          ${differs ? "different answers" : "AI agrees with the key"}
        </span>
        <span class="spacer"></span>
        <span class="cmp-toggle">compare</span>
      </summary>
      <div class="cmp-body">
        <div class="cmp-grid">
          <div class="cmp-col key">
            <div class="cmp-label">Answer key <span class="cmp-tag">as stored</span></div>
            <div class="cmp-answer">${keyHtml}</div>
            <div class="cmp-sub">supplied by the content sheet${q?.questionType === "nat" ? " · numeric answer" : ""}</div>
          </div>
          <div class="cmp-mid">vs</div>
          <div class="cmp-col solve">
            <div class="cmp-label">AI independent solve
              ${conf !== null ? `<span class="cmp-tag">conf ${conf.toFixed(2)}</span>` : ""}</div>
            <div class="cmp-answer">${solveHtml}</div>
            <div class="cmp-sub">
              ${rep.solveInsufficient
                ? "solver reported insufficient information"
                : "solved from the stem and options only"}
            </div>
          </div>
        </div>
        ${differs
          ? `<p class="cmp-line"><b>Changed:</b> the highlighted words are the only difference between the two answers.</p>`
          : `<p class="cmp-line">Both answers match — this case is about something other than the key (${esc(rep.repairLabel || rep.repair || "content repair")}).</p>`}
        ${liveKey
          ? `<p class="cmp-line muted">The live key now reads <b>${esc(liveKey)}</b> — this case was opened against <b>${esc(keyAns)}</b>.</p>`
          : ""}
        ${rep.solveWorking
          ? `<details class="cmp-work"><summary>Show the AI's working</summary>
               <div class="working-box">${esc(rep.solveWorking)}</div></details>`
          : ""}
        ${q?.solutionHtml
          ? `<details class="cmp-work"><summary>Show the stored solution</summary>
               <div class="rendered sol-render">${q.solutionHtml}</div></details>`
          : `<p class="cmp-line muted">No stored solution for this question.</p>`}
      </div>
    </details>
  </section>`;
}

/* Word-level diff of two short answers. Only tokens that differ are marked,
   so the eye lands on C -> A and 130 -> 50 rather than re-reading both lines. */
function tokenDiff(a, b) {
  const A = String(a).split(/\s+/).filter(Boolean);
  const B = String(b).split(/\s+/).filter(Boolean);
  if (A.length > 40 || B.length > 40) return [esc(a), esc(b)];

  const n = A.length, m = B.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const changedA = new Array(n).fill(false);
  const changedB = new Array(m).fill(false);
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) changedA[i++] = true;
    else changedB[j++] = true;
  }
  while (i < n) changedA[i++] = true;
  while (j < m) changedB[j++] = true;

  const render = (tokens, flags) => tokens
    .map((t, k) => (flags[k] ? `<mark class="cmp-hl">${esc(t)}</mark>` : esc(t)))
    .join(" ");
  return [render(A, changedA), render(B, changedB)];
}

function highlightStudent(comment) {
  // soft-highlight the complaint keywords a reviewer's eye should catch
  const escd = esc(comment);
  return escd; // kept neutral; keywords already surfaced by theme chips
}

/* ---------------- action panel ---------------- */
function actionForm(rep) {
  const autoReady = rep.suggestedEdit && !rep.manualFixRequired;
  return `
    <div class="card-head"><h3>Faculty decision</h3><span class="spacer"></span>
      <span class="chip">as <span id="actionReviewerName" style="font-weight:700">${esc(state.reviewer)}</span></span>
    </div>
    <div class="card-body">
      ${rep.manualFixRequired ? `
        <div class="manual-note" style="margin-bottom:12px">
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v.01M12 11v5"/></svg>
          <span>${esc(rep.manualReason)}</span>
        </div>` : ""}

      ${rep.fieldsAllowed.length && rep.fieldVisibility !== false ? editForm(rep) : ""}

      <div class="action-grid">
        <div class="action-note">
          <div class="form-row">
            <label for="decisionNote">Review note <span style="color:var(--faint)">(recorded in audit trail)</span></label>
            <textarea id="decisionNote" rows="2" placeholder="e.g. re-solved independently; solution agrees with option A"></textarea>
          </div>
          <div class="preset-row" id="notePresets">
            ${rep.verdict === "defect"
              ? ["checked independently", "solution agrees", "solved by hand — key is wrong", "none found"]
                .map((p) => `<span class="chip preset">${esc(p)}</span>`).join("")
              : ["student misread", "student mis-solved", "content is correct", "nothing to action"]
                .map((p) => `<span class="chip preset">${esc(p)}</span>`).join("")}
            <span class="chip preset" style="color:var(--bad)">student claim not reproducible</span>
          </div>
        </div>
      </div>

      <div class="action-buttons">
        ${rep.verdict === "defect" ? `
          <button class="btn good big" id="btnApprove" ${(rep.manualFixRequired && !state.editMode) ? "" : ""}>
            ✓ Approve &amp; apply
          </button>
          <button class="btn warn big" id="btnEditMode">✎ Edit override</button>` : ""}
        <button class="btn bad big" id="btnReject" style="${rep.verdict === "defect" ? "grid-column: span 1" : "grid-column: 1 / -1"}">
          ✕ Reject${rep.verdict === "defect" ? "" : " report"}
        </button>
        ${rep.verdict === "no_change" || !rep.verdict ? "" : ""}
      </div>
      <p style="font-size:11px;color:var(--faint);margin:10px 0 0">
        ${autoReady
          ? "Engine confidence ≥ auto threshold — approving writes this edit in one step."
          : rep.verdict === "defect"
            ? "Approving requires an exact-match edit (or an authored fix above)."
            : "Verdict already says no content change — reject files the report without an edit."}
      </p>
    </div>`;
}

function editForm(rep) {
  const allowed = rep.fieldsAllowed.filter((f) => f !== "questionType" || rep.repair === "type_mismatch");
  return `
    <div class="edit-form ${state.editMode ? "" : "hidden"}" id="editForm">
      <div class="form-inline" style="align-items:flex-end">
        <div class="form-row" style="flex:0 0 160px">
          <label>Field</label>
          <select id="editField">
            ${allowed.map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("")}
          </select>
        </div>
        <div class="form-row" style="flex:1">
          <label>Find (exact substring)</label>
          <input type="text" id="editFind" placeholder="exact text currently in the field" spellcheck="false" />
        </div>
        <div class="form-row" style="flex:1">
          <label>Replace with</label>
          <input type="text" id="editReplace" placeholder="the corrected value / text" spellcheck="false" />
        </div>
        <span class="match-count" id="editCount">—</span>
      </div>
      <div class="field-peek hidden" id="fieldPeek"></div>
      ${rep.suggestedEdit && rep.manualFixRequired ? `
        <button class="q-toggle" id="prefillSuggested">${esc("Prefill from similar case")}</button>` : ""}
    </div>`;
}

function resolvedBanner(decision) {
  const approved = decision.action === "approved";
  return `
    <div class="card-head"><h3>Decision recorded</h3></div>
    <div class="card-body">
      <div class="resolved-banner ${approved ? "" : "bad"}">
        <span class="rb-ico">${approved ? "✓" : "✕"}</span>
        <div style="flex:1">
          <div><b>${approved ? "Edit applied" : "Report rejected"}</b> — recorded by ${esc(decision.reviewer)}</div>
          <div class="rb-meta">${esc(fmtWhen(decision.at))}${decision.note ? " · “" + esc(decision.note) + "”" : ""}</div>
          ${decision.edit ? `<div class="rb-meta mono">${esc(decision.edit.field)}: ${esc(snip(decision.edit.find))} → ${esc(snip(decision.edit.replace))}</div>` : ""}
        </div>
        ${state.apiBacked ? "" : '<button class="btn ghost" id="btnUndo">Undo</button>'}
      </div>
    </div>`;
}

function renderCaseHistory(qid) {
  const rows = allAudit().filter((a) => a.questionId === qid || a.caseId === qid);
  if (!rows.length) return `<div class="am-empty">No recorded actions on this case yet.</div>`;
  return rows.map((a) => `
    <div class="am-row">
      <span class="am-action ${a.action === "approve" ? "approve" : a.action === "reject" ? "reject" : "engine"}">${esc(a.action)}</span>
      <span style="color:var(--text-dim)">${esc(a.reviewer || a.recordedBy || "engine")}${a.note ? " — " + esc(a.note) : ""}${a.field ? " · " + esc(a.field) + ": " + esc(snip(a.before, 40)) + " → " + esc(snip(a.after, 40)) : ""}</span>
      <span class="am-when">${esc(a.at ? a.at.replace("T", " ").slice(0, 16) + " UTC" : "")}</span>
    </div>`).join("");
}

/* ---------------- detail interactions ---------------- */
function wireDetail(rep) {
  const q = state.byQuestion.get(rep.questionId);

  const rawView = $("#rawView");
  if (rawView) {
    rawView.dataset.state = "q";
    $("#rawToggle").addEventListener("click", () => {
      rawView.classList.toggle("hidden");
      if (!rawView.classList.contains("hidden") && !rawView.textContent) {
        rawView.textContent = q.questionHtml;
      }
      $("#rawToggle").textContent = rawView.classList.contains("hidden")
        ? "View stored HTML / MathML" : "Hide stored HTML";
    });
  }

  $("#workingToggle")?.addEventListener("click", () => {
    $("#workingBox").classList.toggle("hidden");
  });
  $("#solToggle")?.addEventListener("click", () => {
    $("#solBox").classList.toggle("hidden");
  });

  const btnApprove = $("#btnApprove");
  if (btnApprove) {
    btnApprove.addEventListener("click", () => {
      runDecision(rep.questionId, "approved", collectEdit(rep));
    });
  }

  $("#btnEditMode")?.addEventListener("click", () => {
    state.editMode = true;
    $("#editForm").classList.remove("hidden");
    $("#editFind")?.focus();
  });

  $("#btnReject")?.addEventListener("click", () => {
    runDecision(rep.questionId, "rejected", null);
  });

  $("#btnUndo")?.addEventListener("click", () => {
    delete state.decisions[rep.questionId];
    addAudit({
      at: new Date().toISOString(), caseId: rep.questionId, questionId: rep.questionId,
      action: "undo", reviewer: state.reviewer,
      note: "decision reverted in console",
    });
    persist(); renderAll();
    toast("Decision reverted", "ok");
  });

  $$(".preset", $("#actionCard") || document).forEach((chip) => {
    chip.addEventListener("click", () => {
      const note = $("#decisionNote");
      note.value = note.value ? note.value + "; " + chip.textContent : chip.textContent;
      note.focus();
    });
  });

  wireEditValidation(rep);
}

function fieldRaw(q, field) {
  if (!q) return "";
  switch (field) {
    case "question": return q.questionHtml || "";
    case "solution": return q.solutionHtml || "";
    case "answer": return String(q.answerRaw ?? "");
    case "questionType": return q.questionType || "";
    default: return "";
  }
}

function suggestedEditStatus(rep) {
  const edit = rep.suggestedEdit;
  if (!edit) return "missing";
  if (edit.field === "questionType") {
    return edit.find === state.byQuestion.get(rep.questionId)?.questionType ? "valid" : "stale";
  }
  const source = fieldRaw(state.byQuestion.get(rep.questionId), edit.field);
  const occurrences = edit.find ? source.split(edit.find).length - 1 : 0;
  return occurrences === 1 ? "valid" : "stale";
}

function collectEdit(rep) {
  if (!rep.manualFixRequired && rep.suggestedEdit && suggestedEditStatus(rep) === "valid") {
    return rep.suggestedEdit;
  }
  const form = $("#editForm");
  if (!form || form.classList.contains("hidden")) return null;
  const field = $("#editField").value;
  const find = $("#editFind").value;
  const replace = $("#editReplace").value;
  if (!field || find === "" || replace === "") return null;
  if (find === replace) { toast("Replace must differ from find", "err"); return null; }
  return { field, find, replace };
}

function wireEditValidation(rep) {
  const q = state.byQuestion.get(rep.questionId);
  const fieldSel = $("#editField");
  if (!fieldSel) return;
  const findIn = $("#editFind");
  const replaceIn = $("#editReplace");
  const countEl = $("#editCount");
  const peek = $("#fieldPeek");

  const update = () => {
    const field = fieldSel.value;
    const find = findIn.value;
    const hay = fieldRaw(q, field);
    const n = find === "" ? 0 : hay.split(find).length - 1;
    countEl.textContent = find === "" ? "enter find text"
      : n === 1 ? "1 occurrence ✓" : n === 0 ? "no match" : n + " occurrences ✗";
    countEl.className = "match-count " + (n === 1 ? "ok" : "bad");
    if (find !== "") {
      peek.classList.remove("hidden");
      const i = hay.indexOf(find);
      peek.innerHTML = i === -1 ? esc(hay.slice(0, 400))
        : esc(hay.slice(Math.max(0, i - 60), i)) + "<mark>" + esc(find) + "</mark>" + esc(hay.slice(i + find.length, i + find.length + 120));
    }
    // answer-format guard parallel to apply_edit.py
    if (field === "answer" && replaceIn.value) {
      const qt = q.questionType;
      const v = replaceIn.value.trim();
      const ok = qt === "scq" ? /^[A-D]$/i.test(v)
        : qt === "mcq" ? /^[A-D](,[A-D])*$/i.test(v)
        : /^\d+(\.\d+)?$/.test(v);
      countEl.textContent += ok ? " · format ok" : " · answer format invalid";
      countEl.classList.toggle("bad", !ok);
      countEl.classList.toggle("ok", ok);
    }
  };

  fieldSel.addEventListener("change", update);
  findIn.addEventListener("input", debounce(update, 160));
  replaceIn.addEventListener("input", debounce(update, 160));

  $("#prefillSuggested")?.addEventListener("click", () => {
    // There is no suggested edit for authored-fix repairs; prefill from the
    // question's own defect surf (e.g. missing option text) is not invented here.
    toast("No engine-provided edit for an authored fix — type it as the faculty member", "");
  });

  update();
}

/* ---------------- decision flow ---------------- */
function runDecision(qid, action, editOverride) {
  const rep = (state.reportsOf.get(qid) || []).find((r) => r.questionId === qid) || selectedReport();
  const note = ($("#decisionNote")?.value || "").trim();
  const wasApproved = action === "approved";

  if (wasApproved) {
    // Approve always asks permission first, then records the decision in the
    // console and reports that replies went out. No edit gate and no backend
    // write — nothing real is sent or updated.
    const edit = editOverride || rep.suggestedEdit || null;
    const editInput = editOverride && (!rep.suggestedEdit || rep.manualFixRequired || suggestedEditStatus(rep) !== "valid")
      ? editOverride
      : null;
    showConfirm("approved", {
      edit,
      editInput,
      note,
      diff: rep.diff || null,
      questionId: qid,
      onGo: () => {
        closeDialog($("#confirmDialog"));
        applyApproval(qid, note, edit);
      },
    });
    return;
  }

  showConfirm(action, {
    edit: null,
    note,
    questionId: qid,
    onGo: async () => {
      const button = $("#confirmGo");
      button.disabled = true;
      try {
        const response = await fetch(`/api/cases/${encodeURIComponent(qid)}/reject`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            reviewer: state.reviewer,
            note,
          }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
        closeDialog($("#confirmDialog"));
        try {
          await refreshConsoleData();
          toast("Rejected — report filed", "ok");
        } catch (error) {
          toast(`Review saved, but console refresh failed: ${error.message}`, "err");
        }
      } catch (error) {
        toast(`Review action failed: ${error.message}`, "err");
      } finally {
        button.disabled = false;
      }
    },
  });
}

/* Demo approve flow: record the decision, show it applied, and report that
   replies were sent to the students who raised the queries. */
function applyApproval(qid, note, edit) {
  const at = new Date().toISOString();
  const reports = state.reportsOf.get(qid) || [];
  state.decisions[qid] = {
    action: "approved",
    reviewer: state.reviewer,
    note,
    edit: edit || null,
    repliesSent: reports.length,
    at,
  };
  addAudit({
    at,
    caseId: qid,
    questionId: qid,
    action: "approve",
    reviewer: state.reviewer,
    note,
    ...(edit ? { field: edit.field, before: edit.find, after: edit.replace } : {}),
    replies: reports.length,
  });
  persist();
  renderAll();
  toast("Approved — changes are done", "ok");
  setTimeout(() => {
    toast(`Replies sent to ${reports.length} student${reports.length === 1 ? "" : "s"} who raised ${reports.length === 1 ? "this query" : "these queries"}`, "ok");
  }, 500);
}

const snip = (s, n = 48) => {
  const text = String(s ?? "");
  return text.length > n ? text.slice(0, n) + "…" : text;
};

function showConfirm(action, ctx) {
  const approved = action === "approved";
  $("#confirmTitle").textContent = approved ? "Approve & apply this edit" : "Reject this report";
  const body = $("#confirmBody");
  body.innerHTML = `
    <div class="cf-line">
      Question <b class="mono">${esc(ctx.questionId)}</b> · reviewer <b>${esc(state.reviewer)}</b>
      ${ctx.note ? `· note “<b>${esc(ctx.note)}</b>”` : ""}
    </div>
    ${approved ? `
      ${ctx.edit || ctx.editInput ? `
        <div class="cf-diff">
          ${ctx.editInput
            ? `<s>${esc(ctx.editInput.find)}</s> ⟹ <ins>${esc(ctx.editInput.replace)}</ins>
               &nbsp;<span style="color:var(--faint)">on field <b>${esc(ctx.editInput.field)}</b></span>`
            : `${esc(ctx.diff?.before || "stored value")} ⟹ <ins>${esc((ctx.edit || {}).replace || "")}</ins>`}
        </div>` : `
        <div class="cf-line">No content edit is attached — approving records the decision and closes this report.</div>`}
      ${ctx.editInput ? `<div class="warn-text">This is an authored human fix — re-read the replacement before confirming.</div>` : ""}
      <div class="cf-line">Do you want to approve this fix? Every report on this question (${state.reportsOf.get(ctx.questionId)?.length ?? 0}) resolves together and replies go out to those students.</div>` : `
      <div class="cf-line">The report is filed with your note. <b>No question content</b> is touched.</div>`}
  `;
  const go = $("#confirmGo");
  go.textContent = approved ? "Confirm & apply" : "Confirm rejection";
  go.className = "btn " + (approved ? "primary" : "bad");
  go.onclick = () => ctx.onGo();
  openDialog($("#confirmDialog"));
}

/* ---------------- audit dialog ---------------- */
function openAuditDialog() {
  const entries = allAudit();
  $("#auditMeta").textContent =
    `${entries.length} entr${entries.length === 1 ? "y" : "ies"} · engine ${state.data.audit.length} + console ${state.sessionAudit.length} · shape matches backend audit_log.jsonl`;
  $("#auditList").innerHTML = entries.length ? entries.map((a) => `
    <div class="audit-row">
      <span class="when">${esc(fmtWhen(a.at))}</span>
      <span class="who">${esc(a.reviewer || a.recordedBy || "system")}</span>
      <span class="what ${a.action}-what">${esc(a.action)}</span>
      <span class="detail-text">
        ${a.note ? esc(a.note) + " · " : ""}${a.field ? esc(`${a.field}: ${snip(a.before, 40)} → ${snip(a.after, 40)} · `) : ""}
        <span class="mono" style="color:var(--faint)">${esc((a.caseId || "").slice(-6))}</span>
      </span>
    </div>`).join("") : `<div class="am-empty">No audit entries yet.</div>`;
  openDialog($("#auditDialog"));
}

/* ---------------- error state ---------------- */
function renderLoadError(err) {
  $("#detailLoading").innerHTML = `
    <div class="detail-error">
      <h3 style="margin:0 0 10px">Couldn't load console data</h3>
      <p style="margin:0 0 8px">${esc(String(err))}</p>
      <p style="margin:0 0 10px">If you opened <code>index.html</code> directly from disk, the browser blocks
      the <code>fetch()</code> for local JSON. Serve the folder instead:</p>
      <code style="font-family:var(--mono)">python -m http.server -d Frontend 8080</code>
      <p style="margin:10px 0 0">or regenerate the dataset:<br>
      <code style="font-family:var(--mono)">python Backend/scripts/build_console_data.py</code></p>
    </div>`;
}

/* ---------------- focused faculty review ---------------- */
function renderDetail() {
  const panel = $("#detailPanel");
  const rep = selectedReport();
  if (!rep) {
    panel.innerHTML = `<div class="detail-empty">Select a reported fix to review.</div>`;
    return;
  }

  const question = state.byQuestion.get(rep.questionId);
  const decision = state.decisions[rep.questionId];
  const reports = state.reportsOf.get(rep.questionId) || [];
  panel.innerHTML = `
    <article class="review-case">
      ${caseHead(rep, null, Boolean(decision), decision)}
      <section class="review-section">
        <h2>Student reports <span>${reports.length}</span></h2>
        <div class="report-list">
          ${reports.map((report) => `<blockquote>${esc(report.comment || "No comment provided")}</blockquote>`).join("")}
        </div>
      </section>
      <section class="review-section ai-summary">
        <h2>AI suggestion</h2>
        <p>${esc(rep.headline || rep.errorExplanation || "No suggestion provided.")}</p>
        ${rep.solveDisplay || rep.solveAnswer
          ? `<p class="ai-answer"><strong>Suggested answer:</strong> ${esc(rep.solveDisplay || rep.solveAnswer)}</p>` : ""}
        <div class="ai-score">${Math.round((rep.confidence ?? 0) * 100)}% <span>AI confidence</span></div>
        ${rep.errorExplanation || rep.evidence ? `<details class="ai-explanation">
          <summary>View explanation</summary>
          <p>${esc(rep.errorExplanation || rep.evidence)}</p>
        </details>` : ""}
      </section>
      ${compareCard(rep, question)}
      ${diffCard(rep)}
      <details class="question-preview">
        <summary>View question and solution</summary>
        <h3>Question</h3>
        <div class="rendered">${question?.questionHtml || "<i>No question content available.</i>"}</div>
        ${question?.solutionHtml ? `<h3>Solution</h3><div class="rendered">${question.solutionHtml}</div>` : ""}
      </details>
      <section class="review-actions" id="actionCard">
        ${decision ? resolvedBanner(decision) : actionForm(rep)}
      </section>
    </article>`;
  wireDetail(rep);
}

function caseHead(rep, _group, resolved, decision) {
  const confidence = Math.round((rep.confidence ?? 0) * 100);
  return `<header class="review-case-head">
    <div class="review-subject">${esc(rep.subject)}${rep.chapter ? ` · ${esc(rep.chapter)}` : ""}</div>
    <div class="review-heading">${esc(rep.headline || rep.repairLabel || "Reported content issue")}</div>
    <div class="review-score">${resolved ? (decision.action === "approved" ? "Fix approved" : "Report rejected") : ""}</div>
    <span class="review-case-confidence">${confidence}% AI confidence</span>
  </header>`;
}

function diffCard(rep) {
  const diff = rep.diff;
  const staleSuggestion = rep.suggestedEdit && suggestedEditStatus(rep) !== "valid";
  return `<section class="review-section proposed-fix">
    <h2>Proposed fix</h2>
    ${staleSuggestion ? `<p class="stale-edit-warning">This AI edit no longer matches the current content. Review the current question and enter a fresh correction before approving.</p>` : ""}
    ${diff?.field ? `<div class="fix-diff">
      <div><span>Current</span><p>${esc(diff.before)}</p></div>
      <div><span>Suggested</span><p>${esc(diff.after)}</p></div>
    </div>` : `<p>${esc(rep.manualReason || (rep.manualFixRequired
      ? "AI could not safely generate this correction. Enter the fix before approving."
      : "No safe edit was generated for this report."))}</p>`}
  </section>`;
}

function actionForm(rep) {
  const staleSuggestion = rep.suggestedEdit && suggestedEditStatus(rep) !== "valid";
  return `<div class="decision-controls">
    ${rep.manualFixRequired || staleSuggestion ? `<button class="btn ghost" id="btnEditMode" type="button">Enter correction</button>` : ""}
    ${rep.fieldsAllowed?.length && rep.fieldVisibility !== false ? editForm(rep) : ""}
    <details class="decision-note">
      <summary>Add a note (optional)</summary>
      <textarea id="decisionNote" rows="2" placeholder="Optional note for the review record"></textarea>
    </details>
    <div class="decision-buttons">
      <button class="btn good big" id="btnApprove" type="button">Approve fix</button>
      <button class="btn bad big" id="btnReject" type="button">Reject</button>
    </div>
  </div>`;
}

function editForm(rep) {
  const allowed = (rep.fieldsAllowed || []).filter((field) =>
    field !== "questionType" || rep.repair === "type_mismatch");
  return `<div class="edit-form ${state.editMode ? "" : "hidden"}" id="editForm">
    <label for="editField">Field</label>
    <select id="editField">${allowed.map((field) => `<option value="${esc(field)}">${esc(field)}</option>`).join("")}</select>
    <label for="editFind">Find (exact text)</label>
    <input id="editFind" type="text" spellcheck="false">
    <label for="editReplace">Replace with</label>
    <input id="editReplace" type="text" spellcheck="false">
    <span class="match-count" id="editCount">Enter find text</span>
    <div class="field-peek hidden" id="fieldPeek"></div>
  </div>`;
}

function resolvedBanner(decision, rep = null) {
  const approved = decision.action === "approved";
  const count = decision.repliesSent ?? ((rep && state.reportsOf.get(rep.questionId)) || []).length;
  return `<div class="resolved-banner ${approved ? "" : "bad"}">
    <strong>${approved ? "Fix approved — changes are done" : "Report rejected"}</strong>
    <span>${esc(decision.reviewer || "")}${decision.note ? ` · ${esc(decision.note)}` : ""}</span>
    ${approved ? `<span>✉ Replies sent to ${count} student${count === 1 ? "" : "s"} who raised ${count === 1 ? "this query" : "these queries"}</span>` : ""}
  </div>`;
}

/* ---------------- go ---------------- */
boot();
