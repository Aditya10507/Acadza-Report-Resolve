"""Verification: turn a classified defect into a reviewable case for the console.

A case is everything a faculty member needs to judge the report in one screen:

  * what is stored now              (current key / option / solution)
  * what the independent solve says (the LLM answer from llm_solver.py)
  * why the two disagree            (plain-language error explanation)
  * the exact before -> after       (the diff the console renders)
  * the student reports behind it   (who said what, clustered)

Only real defects become cases. Display issues, app bugs, language requests and
already-fixed reports are not content edits and are handled elsewhere.

Input   Backend/work/classification.json, solved_answers.json, questions.normalized.json,
        grouped_reports.json, data/taxonomy.json
Output  Backend/work/verification_cases.json

Usage
    python Backend/Reply/verification.py
    python Backend/Reply/verification.py --show 60f744b3f1b9bc7cc7b4a949
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REPLY = Path(__file__).resolve().parent
BACKEND = REPLY.parent
PROJECT = BACKEND.parent
WORK = BACKEND / "work"

CLASSIFICATION = WORK / "classification.json"
SOLVED = WORK / "solved_answers.json"
NORMALIZED = WORK / "questions.normalized.json"
GROUPED = WORK / "grouped_reports.json"
TAXONOMY = PROJECT / "data" / "taxonomy.json"
OUTPUT = WORK / "verification_cases.json"

SEVERITY = {
    "answer_key": "critical", "unsound": "critical", "wrong_question": "critical",
    "stem_data": "high", "option_repair": "high", "type_mismatch": "high",
    "solution_fix": "high", "diagram": "high",
    "identical_options": "medium", "wrong_chapter": "medium", "syllabus": "medium",
    "spelling": "low", "exam_reference": "low", "markup_leak": "low",
    "noise": "low", "answer_form": "low",
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

HEADLINES = {
    "answer_key": "Answer key is wrong",
    "solution_fix": "Stored solution is wrong or belongs to another question",
    "stem_data": "Question is missing a required value",
    "option_repair": "Option set is incomplete",
    "type_mismatch": "Question is stored as the wrong type",
    "wrong_chapter": "Question is filed under the wrong chapter",
    "diagram": "Required figure is missing",
    "unsound": "No valid option as written",
    "identical_options": "Two options are identical",
    "syllabus": "Reported as outside the syllabus",
    "spelling": "Spelling or grammar error",
    "exam_reference": "Exam-name tag left in the question",
    "markup_leak": "Raw LaTeX visible in the question",
    "answer_form": "Unit or answer form not stated",
}


def load_json(path: Path, hint: str = "") -> dict:
    if not path.exists():
        raise SystemExit(f"Missing {path}. {hint}".strip())
    return json.loads(path.read_text(encoding="utf-8"))


def option_text(question: dict, letter: str | None) -> str:
    if not letter:
        return ""
    for option in question.get("options") or []:
        if option["letter"].upper() == str(letter).strip().upper():
            return option["text"]
    return ""


def describe_answer(question: dict, letter: str | None) -> str:
    if not letter:
        return "not solved"
    text = option_text(question, letter)
    if text:
        return f"({letter}) {text}"
    try:
        float(letter)          # integer question: the answer is the value, not an option
        return str(letter)
    except (TypeError, ValueError):
        return f"({letter})"


def reports_for(group: dict | None) -> list[dict]:
    if not group:
        return []
    out = []
    for cluster in group.get("clusters") or []:
        for comment in cluster["comments"]:
            out.append({
                "reportId": comment["reportId"],
                "studentId": comment["studentId"],
                "source": comment["source"],
                "reportedAt": comment["reportedAt"],
                "comment": comment["comment"],
                "theme": cluster["theme"],
            })
    return sorted(out, key=lambda r: r["reportedAt"])


def build_explanation(decision: dict, question: dict, solve: dict | None) -> str:
    compare = decision.get("compare") or {}
    key = compare.get("key")
    solved = compare.get("solve")
    supports_solve = compare.get("solutionSupportsSolve")
    supports_key = compare.get("solutionSupportsKey")
    parts: list[str] = []

    if decision["repair"] == "answer_key" and solved:
        parts.append(
            f"The stored key is {describe_answer(question, key)}, but solving the question "
            f"independently gives {describe_answer(question, solved)}.")
        if supports_solve and not supports_key:
            parts.append("The stored solution also reaches the independent answer, "
                         "so the key is the field that is wrong.")
        elif supports_key and not supports_solve:
            parts.append("The stored solution agrees with the key, so this needs a human "
                         "check before the key is changed.")
        else:
            parts.append("The stored solution does not settle the disagreement, "
                         "so a faculty member must confirm.")
    elif decision["repair"] == "solution_fix":
        parts.append("The independent solve agrees with the key, but the stored solution "
                     "does not prove that answer.")
    elif decision["repair"] == "stem_data":
        parts.append("The question cannot be solved from the stem alone: a required value "
                     "is missing, and the independent solve reports insufficient information.")
    elif decision["repair"] == "option_repair":
        parts.append("The stored option list is incomplete, so the question cannot be "
                     "answered as printed.")
    elif decision["repair"] == "type_mismatch":
        parts.append("The item is stored as multiple-correct but only one option is correct.")
    elif decision["repair"] == "unsound":
        parts.append("Checking every option shows that none of them is correct as written.")
    elif decision["repair"] == "diagram":
        parts.append("The stem refers to a figure that is not stored with the question.")
    elif decision["repair"] == "identical_options":
        parts.append("Two options carry the same text, which makes the key ambiguous.")
    else:
        parts.append(decision.get("evidence") or "Classified as a content defect.")

    if solve and solve.get("working"):
        parts.append(f"Independent working: {solve['working']}")
    return " ".join(parts)


def build_case(decision: dict, question: dict, solve: dict | None, group: dict | None) -> dict:
    repair = decision["repair"]
    key = (decision.get("compare") or {}).get("key") or question.get("answer")
    solved = (solve or {}).get("answer")
    suggested_edit = None
    diff = None
    manual_reason = ""

    if repair == "answer_key" and solved:
        suggested_edit = {"field": "answer", "find": str(key), "replace": str(solved)}
        diff = {
            "field": "answer",
            "before": describe_answer(question, key),
            "after": describe_answer(question, solved),
        }
    else:
        manual_reason = {
            "solution_fix": "The replacement solution must be written by a human.",
            "stem_data": "The missing value has to be inserted into the stem by a human.",
            "option_repair": "The missing or blank option is authored content.",
            "type_mismatch": "Changing the type also changes the answer format.",
            "wrong_chapter": "Reclassification needs a curriculum decision.",
            "diagram": "A figure cannot be generated from text.",
            "unsound": "The item must be rewritten, not patched.",
            "identical_options": "A new distractor has to be authored.",
            "syllabus": "Needs a curriculum decision.",
        }.get(repair, "This repair touches authored content and needs a human edit.")

    severity = SEVERITY.get(repair, "medium")
    options = question.get("options") or []
    option_letters = {o["letter"] for o in options}
    missing_options = ([letter for letter in "ABCD" if letter not in option_letters]
                       if decision.get("questionType") in ("scq", "mcq") else [])
    blank_options = [o["letter"] for o in options if not (o.get("text") or "").strip()]
    return {
        "caseId": decision["questionId"],
        "questionId": decision["questionId"],
        "subject": decision.get("subject"),
        "chapter": decision.get("chapter"),
        "questionType": decision.get("questionType"),
        "verdict": decision["verdict"],
        "repair": repair,
        "route": decision["route"],
        "severity": severity,
        "confidence": decision["confidence"],
        "headline": HEADLINES.get(repair, f"Content defect: {repair}"),
        "errorExplanation": build_explanation(decision, question, solve),
        "evidence": decision.get("evidence"),
        "current": {"answer": key, "display": describe_answer(question, key)},
        "suggested": ({"answer": solved, "display": describe_answer(question, solved)}
                      if solved else None),
        "diff": diff,
        "suggestedEdit": suggested_edit,
        "manualFixRequired": suggested_edit is None,
        "manualReason": manual_reason,
        "missingOptions": missing_options,
        "blankOptions": blank_options,
        "reports": reports_for(group),
        "reportCount": len(reports_for(group)),
        "status": "pending_review",
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_cases(classification: dict, questions: dict, solved: dict,
                groups: dict) -> list[dict]:
    groups_by_id = {g["questionId"]: g for g in groups.get("groups", [])}
    cases = []
    for decision in classification["questions"]:
        if decision["verdict"] != "defect" or not decision.get("repair"):
            continue
        question = questions[decision["questionId"]]
        solve = solved.get(decision["questionId"])
        cases.append(build_case(decision, question, solve, groups_by_id.get(decision["questionId"])))
    return sorted(cases, key=lambda c: (SEVERITY_ORDER[c["severity"]],
                                        -c["confidence"], c["questionId"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build verification cases for the console.")
    parser.add_argument("--classification", type=Path, default=CLASSIFICATION)
    parser.add_argument("--solved", type=Path, default=SOLVED)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--show", help="print one case and exit")
    args = parser.parse_args()

    classification = load_json(args.classification, "Run: python Backend/classifier.py")
    solved = load_json(args.solved, "Run: python Backend/llm_solver.py (needs LLM_API_KEY)")
    questions = {q["questionId"]: q for q in load_json(NORMALIZED)}
    groups = load_json(GROUPED)
    taxonomy = load_json(TAXONOMY)

    cases = build_cases(classification, questions, solved, groups)

    if args.show:
        for case in cases:
            if case["questionId"] == args.show:
                print(json.dumps(case, indent=2, ensure_ascii=False))
                return 0
        raise SystemExit(f"No defect case for {args.show}")

    by_severity: dict[str, int] = {}
    by_repair: dict[str, int] = {}
    for case in cases:
        by_severity[case["severity"]] = by_severity.get(case["severity"], 0) + 1
        by_repair[case["repair"]] = by_repair.get(case["repair"], 0) + 1

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "cases": len(cases),
            "bySeverity": by_severity,
            "byRepair": by_repair,
            "autoApplicable": sum(1 for c in cases if c["suggestedEdit"]),
            "manualFixRequired": sum(1 for c in cases if c["manualFixRequired"]),
            "taxonomyRepairs": len(taxonomy["repairs"]),
        },
        "cases": cases,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"defect cases        : {len(cases)}")
    print(f"by severity         : {by_severity}")
    print(f"with a ready edit   : {payload['summary']['autoApplicable']}")
    print(f"need authored fix   : {payload['summary']['manualFixRequired']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
