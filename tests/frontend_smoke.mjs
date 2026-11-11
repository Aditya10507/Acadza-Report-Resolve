// Runtime smoke test for the focused faculty defect-review flow.
import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../Frontend", import.meta.url));
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(ROOT, "app.js"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "styles.css"), "utf8");
const originalData = JSON.parse(fs.readFileSync(fileURLToPath(new URL("../Backend/work/console.json", import.meta.url)), "utf8"));
const serverData = JSON.parse(JSON.stringify(originalData));
const approvedBefore = new Set(serverData.audit.filter((entry) => ["approve", "reject"].includes(entry.action)).map((entry) => entry.caseId));
const staleCandidate = serverData.groups.find((group) =>
  group.verdict === "defect" && !approvedBefore.has(group.questionId) &&
  serverData.questions.find((question) => question.questionId === group.questionId)?.solutionHtml);
if (staleCandidate) {
  for (const report of serverData.reports.filter((item) => item.questionId === staleCandidate.questionId)) {
    report.fieldsAllowed = ["solution"];
    report.manualFixRequired = false;
    report.suggestedEdit = { field: "solution", find: "__stale_ai_find__", replace: "updated solution" };
  }
}

let reviewApiCalls = 0;
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
window.fetch = (url, options = {}) => {
  if (String(url).includes("/api/auth/me")) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ username: "Faculty", role: "faculty" }) });
  }
  if (String(url).includes("/api/console")) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(JSON.parse(JSON.stringify(serverData))) });
  }
  if (String(url).includes("/api/cases/")) {
    reviewApiCalls += 1;
    const [, caseId, action] = String(url).match(/\/api\/cases\/([^/]+)\/(approve|reject)$/) || [];
    const body = JSON.parse(options.body);
    serverData.audit.push({
      at: new Date().toISOString(), caseId, questionId: caseId,
      action, reviewer: body.reviewer, note: body.note,
      ...(body.edit ? { field: body.edit.field, before: body.edit.find, after: body.edit.replace } : {}),
    });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ case: {} }) });
  }
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ detail: "Not found" }) });
};
window.prompt = (_label, value) => value || "Faculty";

const $ = (selector) => window.document.querySelector(selector);
const $$ = (selector) => [...window.document.querySelectorAll(selector)];
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
const assert = (name, condition) => {
  results.push([condition ? "PASS" : "FAIL", name]);
  if (!condition) process.exitCode = 1;
};

window.eval(appJs);
await wait(80);

const defectIds = new Set(originalData.groups.filter((group) => group.verdict === "defect").map((group) => group.questionId));
const pendingReject = originalData.groups.find((group) =>
  group.verdict === "defect" && !approvedBefore.has(group.questionId) &&
  group.questionId !== staleCandidate?.questionId);

assert("only unresolved content defects are listed", $$(".issue-row").every((row) => defectIds.has(row.dataset.group)));
assert("non-defect report groups stay out of faculty queue", $$(".issue-row").length < originalData.groups.length);
assert("first actionable fix selected", !!$(".issue-row.selected") && !!$(".review-case"));
assert("student reports and AI suggestion are visible", !!$(".report-list blockquote") && !!$(".ai-summary"));
assert("AI confidence score is visible", !!$(".ai-score"));
assert("question and solution context is available but collapsed", $("details.question-preview") && !$("details.question-preview").open);
assert("filters and audit controls are hidden from the workflow", css.includes("#filtersToggle") && css.includes("#auditBtn"));
assert("review actions are limited to approve or reject", !!$("#btnApprove") && !!$("#btnReject"));
assert("manual corrections expose an edit action", !!originalData.reports.find((report) =>
  report.verdict === "defect" && report.manualFixRequired));

if (staleCandidate) {
  $(`.issue-row[data-group="${staleCandidate.questionId}"]`).click();
  await wait(20);
  assert("stale suggestion is called out before approval", !!$(".stale-edit-warning"));
  $("#btnApprove").click();
  await wait(20);
  assert("approve always asks permission first", $("#confirmDialog").open);
  $("#confirmCancel").click();

  $("#btnEditMode").click();
  const solution = serverData.questions.find((question) => question.questionId === staleCandidate.questionId).solutionHtml;
  const find = solution.slice(0, 40);
  $("#editFind").value = find;
  $("#editReplace").value = `${find} corrected`;
  $("#editFind").dispatchEvent(new window.Event("input", { bubbles: true }));
  await wait(200);
  $("#btnApprove").click();
  await wait(20);
  assert("updated exact-match correction can be confirmed", $("#confirmDialog").open && $("#confirmBody").textContent.includes(find));
  $("#confirmGo").click();
  await wait(60);
  assert("approval is recorded in the console without a review API call", reviewApiCalls === 0 &&
    !$$(".issue-row").some((entry) => entry.dataset.group === staleCandidate.questionId));
  assert("approved banner reports changes done and replies sent",
    $("#detailPanel").textContent.includes("changes are done") &&
    $("#detailPanel").textContent.includes("Replies sent"));
} else {
  assert("stale-suggestion regression case exists", false);
}

if (pendingReject) {
  const row = $(`.issue-row[data-group="${pendingReject.questionId}"]`);
  row?.click();
  await wait(20);
  $("#btnReject")?.click();
  await wait(20);
  assert("rejection does not require an unnecessary note", $("#confirmDialog").open);
  $("#confirmGo").click();
  await wait(40);
  assert("rejection is recorded through the API", serverData.audit.some((entry) =>
    entry.caseId === pendingReject.questionId && entry.action === "reject"));
  assert("rejected case leaves the open queue", !$$(".issue-row").some((entry) => entry.dataset.group === pendingReject.questionId));
} else {
  assert("rejection candidate exists", false);
}

const search = $("#search");
search.value = "asdfgh";
search.dispatchEvent(new window.Event("input", { bubbles: true }));
await wait(150);
assert("search narrows the defect queue", $$(".issue-row").length <= 1);

window.document.activeElement?.blur?.();
window.document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "j", bubbles: true }));
assert("keyboard navigation remains available", true);

console.log(results.map(([status, name]) => `${status}  ${name}`).join("\n"));
console.log(results.some(([status]) => status === "FAIL") ? "\nFAILURES" : "\nALL PASS");
