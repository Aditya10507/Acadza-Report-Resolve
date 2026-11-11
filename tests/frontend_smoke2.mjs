// Runtime smoke test for faculty-authored corrections.
import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../Frontend", import.meta.url));
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(ROOT, "app.js"), "utf8");
const serverData = JSON.parse(fs.readFileSync(fileURLToPath(new URL("../Backend/work/console.json", import.meta.url)), "utf8"));
const qid = "696a866665252c04fc1f81c2";
let reviewApiCalls = 0;
const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
window.fetch = (url, options = {}) => {
  if (String(url).includes("/api/auth/me")) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ username: "Faculty", role: "faculty" }) });
  }
  if (String(url).includes("/api/console")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(serverData))) });
  }
  if (String(url).includes("/api/cases/")) {
    reviewApiCalls += 1;
    const body = JSON.parse(options.body);
    serverData.audit.push({
      at: new Date().toISOString(), caseId: qid, questionId: qid,
      action: "approve", reviewer: body.reviewer, note: body.note,
      field: body.edit.field, before: body.edit.find, after: body.edit.replace,
    });
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ case: {} }) });
  }
  return Promise.resolve({ ok: false, json: () => Promise.resolve({ detail: "Not found" }) });
};
window.prompt = () => "Faculty";

const $ = (selector) => window.document.querySelector(selector);
const $$ = (selector) => [...window.document.querySelectorAll(selector)];
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
const assert = (name, condition) => {
  results.push([condition ? "PASS" : "FAIL", name]);
  if (!condition) process.exitCode = 1;
};

window.localStorage.clear();
window.eval(appJs);
await wait(80);

const row = $(`.issue-row[data-group="${qid}"]`);
assert("manual-fix defect appears in the faculty queue", !!row);
row?.click();
await wait(20);
$("details.question-preview").open = true;
assert("question and solution are rendered as MathML rather than source tags",
  $$(".question-preview math").length > 0 &&
  !$(".question-preview").textContent.includes("</mrow>"));
assert("manual correction requirement is explained", $("#detailPanel").textContent.includes("inserted into the stem by a human"));
$("#btnEditMode")?.click();
assert("manual edit form opens on request", !$("#editForm").classList.contains("hidden"));
assert("edit field is limited to the valid question field", $$("#editField option").map((option) => option.value).join(",") === "question");

$("#editFind").value = "10";
$("#editReplace").value = "12";
$("#editFind").dispatchEvent(new window.Event("input", { bubbles: true }));
await wait(220);
assert("ambiguous find text is rejected", $("#editCount").classList.contains("bad"));

$("#editFind").value = "solid copper cube";
$("#editReplace").value = "solid copper cube of edge length 10 cm";
$("#editFind").dispatchEvent(new window.Event("input", { bubbles: true }));
await wait(220);
assert("unique replacement is validated", $("#editCount").textContent.includes("1 occurrence"));

$("#btnApprove").click();
await wait(20);
assert("confirmation displays authored correction", $("#confirmDialog").open && $("#confirmBody").textContent.includes("authored human fix"));
$("#confirmGo").click();
await wait(60);
assert("faculty-authored approval is recorded in the console without a review API call", reviewApiCalls === 0);
assert("approved banner reports changes done and replies sent",
  $("#detailPanel").textContent.includes("changes are done") &&
  $("#detailPanel").textContent.includes("Replies sent"));
assert("approved correction is removed from the review queue", !$$(".issue-row").some((entry) => entry.dataset.group === qid));

console.log(results.map(([status, name]) => `${status}  ${name}`).join("\n"));
console.log(results.some(([status]) => status === "FAIL") ? "\nFAILURES" : "\nALL PASS");
