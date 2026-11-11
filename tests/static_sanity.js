// Static contract test: ids referenced by app.js exist in index.html,
// and console.json carries every key app.js renders.
const fs = require("fs");
const js = fs.readFileSync("Frontend/app.js", "utf8");
const html = fs.readFileSync("Frontend/index.html", "utf8");
const ids = new Set([...html.matchAll(/id="([\w-]+)"/g)].map((m) => m[1]));
const refs = new Set([...js.matchAll(/\$\("#([\w-]+)"\)/g)].map((m) => m[1]));
const dynamicIds = new Set([
  "btnApprove", "btnReject", "rawView", "rawToggle", "workingToggle", "workingBox",
  "solToggle", "solBox", "editForm", "editFind", "btnEditMode", "btnUndo",
  "actionCard", "decisionNote", "editField", "editReplace", "editCount", "fieldPeek",
  "prefillSuggested",
]);
const missing = [...refs].filter((r) => !ids.has(r) && !dynamicIds.has(r));
console.log("ids in html:", ids.size, "| id refs in js:", refs.size);
console.log("missing:", missing.length ? missing : "none");
if (missing.length) process.exitCode = 1;

const data = JSON.parse(fs.readFileSync("Backend/work/console.json", "utf8"));
const rKeys = ["reportId","questionId","verdict","verdictLabel","repair","repairLabel","fieldsAllowed","route","confidence","evidence","theme","clusterReportIds","severity","headline","errorExplanation","keyDisplay","solveAnswer","solveDisplay","solveConfidence","solveWorking","solutionSupportsSolve","solutionSupportsKey","diff","currentDisplay","suggestedDisplay","suggestedEdit","manualFixRequired","manualReason","missingOptions","blankOptions","comment","studentId","source","reportedAt","reply","replyCategory","replyLanguage","replyResolved","subject","chapter","questionType","auto"];
const qKeys = ["questionId","questionType","subject","chapter","level","key","answerRaw","questionHtml","solutionHtml","options","stem","flags","questionImages","solutionImages","errorHistory","reported"];
const gKeys = ["questionId","subject","chapter","questionType","reportCount","studentCount","urgency","themes","topTheme","firstReportedAt","lastReportedAt","verdict","verdictLabel","repair","repairLabel","route","confidence","severity","reportIds"];
let bad = 0;
for (const r of data.reports) for (const k of rKeys) if (!(k in r)) { console.log("report missing", k, r.reportId); bad++; }
for (const q of data.questions) for (const k of qKeys) if (!(k in q)) { console.log("question missing", k, q.questionId); bad++; }
for (const g of data.groups) for (const k of gKeys) if (!(k in g)) { console.log("group missing", k, g.questionId); bad++; }
console.log(bad ? bad + " problems" : "console.json contract: OK");
console.log("reports:", data.reports.length, "| questions:", data.questions.length, "| groups:", data.groups.length, "| audit:", data.audit.length);
