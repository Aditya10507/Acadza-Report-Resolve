"""Fuse the pipeline artifacts into one file the faculty console reads.

Reads (all produced/verified by the six backend stages):
  data/questions.json, data/taxonomy.json
  Backend/work/questions.normalized.json, grouped_reports.json,
  classification.json, solved_answers.json, verification_cases.json,
  student_replies.json, audit_log.jsonl (optional)

Writes: Backend/work/console.json - every report with its decision, evidence
chain, diff, student reply and initial audit state, plus all questions (raw
HTML for native MathML rendering) and the taxonomy. The file lives outside the
web-served Frontend/ folder on purpose: the API reads it server-side and
requires a faculty session, so the raw dataset is never downloadable.

Ground truth (work/labels.json, work/audit.md) is deliberately NOT included:
it is the scoring key and must never reach the UI.

No network, no LLM, stdlib only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent      # Backend/
PROJECT = BACKEND.parent                              # repo root
DATA = PROJECT / "data"
WORK = BACKEND / "work"
OUT_DIR = WORK
OUT = OUT_DIR / "console.json"

CONTAINER = {"scq": "scq", "mcq": "mcq", "integerQuestion": "integerQuestion"}
QUESTION_FIELDS = {"question", "solution", "answer", "questionType"}


def load_json(path: Path, hint: str = "") -> dict:
    if not path.exists():
        raise SystemExit(f"Missing {path}. {hint}".strip())
    return json.loads(path.read_text(encoding="utf-8"))


def describe_answer(options: list[dict], value) -> str:
    """Option text for letters, raw string for integer answers."""
    if value is None or str(value).strip() == "":
        return ""
    needle = str(value).strip().upper()
    for option in options or []:
        if str(option.get("letter", "")).upper() == needle:
            return f"({needle}) {option.get('text', '')}".strip()
    return needle


def main() -> int:
    taxonomy = load_json(DATA / "taxonomy.json")
    grouped = load_json(WORK / "grouped_reports.json", "Run: python Backend/decision_layer.py")
    classification = load_json(WORK / "classification.json", "Run: python Backend/classifier.py")
    solved = load_json(WORK / "solved_answers.json", "Run: python Backend/llm_solver.py")
    cases_payload = load_json(WORK / "verification_cases.json", "Run: python Backend/Reply/verification.py")
    replies_payload = load_json(WORK / "student_replies.json", "Run: python Backend/Reply/student_reply.py")
    normalized = {q["questionId"]: q for q in load_json(WORK / "questions.normalized.json")}
    questions = load_json(DATA / "questions.json")
    live_path = WORK / "live_questions.json"
    if live_path.exists():
        live_questions = load_json(live_path)
        questions = [live_questions.get(q["_id"], q) for q in questions]

    audit_path = WORK / "audit_log.jsonl"
    audit = []
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                audit.append(json.loads(line))

    source_qs = {q["_id"]: q for q in questions}
    raw_reports = {r["reportId"]: r for r in load_json(DATA / "reports.json")}
    decisions = {d["questionId"]: d for d in classification["questions"]}
    groups = {g["questionId"]: g for g in grouped["groups"]}
    cases = {c["questionId"]: c for c in cases_payload["cases"]}
    replies = replies_payload.get("replies", {})

    repairs = taxonomy["repairs"]
    verdicts = taxonomy["verdicts"]

    # ---- report-level theme/cluster maps -----------------------------------
    theme_by_report: dict[str, str] = {}
    cluster_by_report: dict[str, dict] = {}
    for qid, group in groups.items():
        for cluster in group["clusters"]:
            for comment in cluster["comments"]:
                theme_by_report[comment["reportId"]] = cluster["theme"]
                cluster_by_report[comment["reportId"]] = {
                    "family": cluster["family"],
                    "count": cluster["count"],
                    "reportIds": cluster["reportIds"],
                }

    # ---- question blocks (all 43, raw HTML for native MathML) --------------
    questions_out = []
    for qid, norm in normalized.items():
        src = source_qs[qid]
        body = src.get(CONTAINER.get(src["questionType"], src["questionType"])) or {}
        decisions.get(qid)
        questions_out.append({
            "questionId": qid,
            "questionType": src.get("questionType"),
            "subject": src.get("subject"),
            "chapter": src.get("chapter"),
            "level": src.get("level"),
            "key": (",".join(str(value).strip() for value in body.get("answer", [])
                             if str(value).strip())
                    if isinstance(body.get("answer"), list)
                    else str(body.get("answer") or "").strip()),
            "answerRaw": body.get("answer"),
            "questionHtml": body.get("question") or "",
            "solutionHtml": body.get("solution") or "",
            "options": norm.get("options") or [],
            "stem": norm.get("stem") or "",
            "flags": norm.get("flags") or [],
            "questionImages": body.get("quesImages") or [],
            "solutionImages": body.get("solutionImages") or [],
            "errorHistory": src.get("errorHistory") or [],
            "reported": qid in decisions,
        })

    # ---- fused reports (one record per student report) ---------------------
    reports_out = []
    for row in classification["reports"]:
        rid, qid = row["reportId"], row["questionId"]
        decision = decisions[qid]
        case = cases.get(qid)
        norm = normalized.get(qid, {})
        solve = solved.get(qid, {})
        compare = decision.get("compare") or {}
        group = groups.get(qid, {})
        options = norm.get("options") or []
        repair = decision.get("repair")
        reply = replies.get(rid)

        payload_reports = {r["reportId"]: r for r in (case.get("reports") if case else [])}
        source_report = payload_reports.get(rid, {})
        raw_report = raw_reports.get(rid, {})
        comment = raw_report.get("comment", "") or source_report.get("comment", "")
        cluster = cluster_by_report.get(rid, {})

        live_question = source_qs[qid]
        live_body = live_question.get(
            CONTAINER.get(live_question["questionType"], live_question["questionType"])) or {}
        key = (",".join(str(value).strip() for value in live_body.get("answer", [])
                        if str(value).strip())
               if isinstance(live_body.get("answer"), list)
               else str(live_body.get("answer") or "").strip())
        solve_answer = compare.get("solve")

        reports_out.append({
            "reportId": rid,
            "questionId": qid,
            "subject": decision.get("subject") or norm.get("subject"),
            "chapter": decision.get("chapter") or norm.get("chapter"),
            "questionType": decision.get("questionType") or norm.get("questionType"),
            "verdict": decision["verdict"],
            "verdictLabel": verdicts.get(decision["verdict"], decision["verdict"]),
            "repair": repair,
            "repairLabel": repairs[repair]["label"] if repair in repairs else (repair or ""),
            "fieldsAllowed": repairs[repair]["fields"] if repair in repairs else [],
            "auto": repairs[repair].get("auto") if repair in repairs else None,
            "route": decision["route"],
            "confidence": decision["confidence"],
            "evidence": decision.get("evidence") or "",
            "theme": theme_by_report.get(rid),
            "clusterReportIds": cluster.get("reportIds", [rid]),
            "clusterCount": cluster.get("count", 1),

            "severity": case.get("severity") if case else None,
            "headline": case.get("headline") if case else None,
            "errorExplanation": case.get("errorExplanation") if case else None,

            "keyDisplay": describe_answer(options, key),
            "solveAnswer": solve_answer,
            "solveDisplay": describe_answer(options, solve_answer),
            "solveConfidence": solve.get("confidence"),
            "solveWorking": solve.get("working"),
            "solveInsufficient": bool(solve.get("insufficient_information")),
            "solutionSupportsSolve": compare.get("solutionSupportsSolve"),
            "solutionSupportsKey": compare.get("solutionSupportsKey"),

            "diff": case.get("diff") if case else None,
            "currentDisplay": (case.get("current") or {}).get("display") if case else describe_answer(options, key),
            "suggestedDisplay": (case.get("suggested") or {}).get("display") if case else None,
            "suggestedEdit": case.get("suggestedEdit") if case else None,
            "manualFixRequired": bool(case.get("manualFixRequired")) if case else False,
            "manualReason": case.get("manualReason", "") if case else "",
            "missingOptions": case.get("missingOptions") if case else [],
            "blankOptions": case.get("blankOptions") if case else [],

            "comment": comment,
            "studentId": raw_report.get("studentId") or source_report.get("studentId") or "",
            "source": raw_report.get("source") or source_report.get("source") or "",
            "reportedAt": raw_report.get("reportedAt") or source_report.get("reportedAt") or "",

            "reply": reply.get("reply") if reply else None,
            "replyCategory": reply.get("category") if reply else None,
            "replyLanguage": reply.get("language") if reply else None,
            "replyResolved": bool(reply.get("resolved")) if reply else None,
        })

    # ---- grouped queue view -------------------------------------------------
    groups_out = []
    for qid, group in groups.items():
        decision = decisions.get(qid, {})
        repair = decision.get("repair")
        groups_out.append({
            "questionId": qid,
            "subject": decision.get("subject"),
            "chapter": decision.get("chapter"),
            "questionType": decision.get("questionType"),
            "reportCount": group["reportCount"],
            "studentCount": group["studentCount"],
            "urgency": group["urgency"],
            "urgencyReason": group["urgencyReason"],
            "themes": group.get("themes"),
            "topTheme": group.get("topTheme"),
            "firstReportedAt": group["firstReportedAt"],
            "lastReportedAt": group["lastReportedAt"],
            "reportIds": group["reportIds"] if "reportIds" in group
                         else [c for cl in group["clusters"] for c in [r["reportId"] for r in cl["comments"]]],
            "verdict": decision.get("verdict"),
            "verdictLabel": verdicts.get(decision.get("verdict"), decision.get("verdict")),
            "repair": repair,
            "repairLabel": repairs[repair]["label"] if repair in repairs else (repair or ""),
            "route": decision.get("route"),
            "confidence": decision.get("confidence"),
            "severity": cases[qid].get("severity") if qid in cases else None,
        })

    confirmed = {a["caseId"] for a in audit if a.get("action") == "approve"}
    rejected = {a["caseId"] for a in audit if a.get("action") == "reject"}
    pending_reports = sum(1 for r in reports_out if r["questionId"] not in confirmed | rejected)

    summary = {
        "reports": len(reports_out),
        "reportedQuestions": len(groups_out),
        "questions": len(questions_out),
        "cases": len(cases),
        "byVerdict": classification["summary"]["byVerdict"],
        "byRoute": classification["summary"]["byRoute"],
        "repliesPresent": sum(1 for r in reports_out if r["reply"]),
        "repliesMissing": sum(1 for r in reports_out if not r["reply"]),
        "pendingReports": pending_reports,
        "resolvedReports": len(reports_out) - pending_reports,
    }

    payload = {
        "meta": {
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "solvePromptVersion": "solve-v1",
            "replyPromptVersion": replies_payload.get("promptVersion", "reply-v1"),
            "summary": summary,
        },
        "taxonomy": taxonomy,
        "questions": questions_out,
        "groups": groups_out,
        "reports": reports_out,
        "audit": audit,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"questions={summary['questions']} reportedQ={summary['reportedQuestions']} "
          f"reports={summary['reports']} cases={summary['cases']}")
    print(f"replies {summary['repliesPresent']}/{summary['reports']} "
          f"({summary['repliesMissing']} missing)")
    print(f"audit entries={len(audit)}")
    print(f"wrote {OUT.relative_to(PROJECT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
