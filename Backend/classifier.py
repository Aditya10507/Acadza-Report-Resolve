"""Classification: compare the independent solve with the stored key and solution.

For every reported question this file answers one question: *is the report pointing at a
real defect, and if so, what kind?* It combines three independent sources of evidence:

  1. the LLM's independent solve      (Backend/work/solved_answers.json)
  2. the stored answer key            (data/questions.json)
  3. the stored solution              (used as a tie-breaker, never as an input to the solve)
  4. the clustered student complaints (Backend/work/grouped_reports.json)

Input   Backend/work/grouped_reports.json, solved_answers.json, questions.normalized.json,
        data/taxonomy.json
Output  Backend/work/classification.json

Usage
    python Backend/classifier.py
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
PROJECT = BACKEND.parent
WORK = BACKEND / "work"
TAXONOMY = PROJECT / "data" / "taxonomy.json"
GROUPED = WORK / "grouped_reports.json"
NORMALIZED = WORK / "questions.normalized.json"
SOLVED = WORK / "solved_answers.json"
OUTPUT = WORK / "classification.json"

# Themes that describe a platform problem rather than broken content.
NON_CONTENT_THEMES = {"app", "display", "language"}
# Themes that point at a specific content repair when the solve agrees with the key.
CONTENT_THEMES = {
    "spelling", "exam_ref", "latex", "unit", "solution", "chapter", "syllabus",
    "diagram", "duplicate_option", "blank_option", "missing_data", "option",
}

MIN_AUTO_CONFIDENCE = 0.80


def load_json(path: Path, hint: str = "") -> dict:
    if not path.exists():
        raise SystemExit(f"Missing {path}. {hint}".strip())
    return json.loads(path.read_text(encoding="utf-8"))


def letters(value: str) -> set[str]:
    return {part.strip().upper() for part in re.split(r"[,;/]+", str(value)) if part.strip()}


def numeric(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def answers_match(question_type: str, solve: str | None, key: str | None) -> bool:
    if solve is None or key is None:
        return False
    if question_type == "integerQuestion":
        a, b = numeric(solve), numeric(key)
        if a is None or b is None:
            return False
        return abs(a - b) <= max(1e-9, abs(b) * 0.01)   # 1% absorbs rounding
    return letters(solve) == letters(key)


def same_number_in_text(number: str, text: str) -> bool:
    """Boundary-safe number search that ignores single digits (units, exponents)."""
    token = str(number).strip()
    if len(token.replace("-", "").replace(".", "")) < 2:
        return False
    pattern = r"(?<![\d.])" + re.escape(token) + r"(?![\d.])"
    return re.search(pattern, text) is not None


def integer_in_text(value: float, text: str) -> bool:
    """A single-digit integer only counts when the solution states it as a result."""
    token = f"{value:g}"
    if len(token.replace("-", "").replace(".", "")) >= 2:
        return same_number_in_text(token, text)
    pattern = r"(?:=|≈|equals)\s*" + re.escape(token) + r"(?![\d.])"
    return re.search(pattern, text) is not None


def solution_supports(question: dict, answer: str | None) -> bool:
    """Heuristic: does the stored solution end up at this answer?

    For options it looks for the option's own numbers or a distinctive word; for integer
    answers it looks for the number. Used only to break a solve-vs-key tie.
    """
    if not answer:
        return False
    text = question.get("solutionText") or ""
    if not text:
        return False
    if question["questionType"] == "integerQuestion":
        value = numeric(answer)
        return value is not None and integer_in_text(value, text)

    options = {o["letter"]: o["text"] for o in question.get("options") or []}
    for letter in letters(answer):
        option_text = options.get(letter, "")
        numbers = re.findall(r"\d+(?:\.\d+)?", option_text)
        if numbers:
            if any(same_number_in_text(n, text) for n in numbers):
                return True
        else:
            words = [w for w in re.findall(r"[a-zA-Z]{4,}", option_text.lower())
                     if w not in {"none", "these", "above", "correct"}]
            if words and any(word in text.lower() for word in words[:2]):
                return True
    return False


def structural_findings(question: dict) -> dict:
    """Cheap, objective checks on the stored record."""
    flags = set(question.get("flags") or [])
    options = question.get("options") or []
    texts = [re.sub(r"\s+", " ", (o.get("text") or "")).strip().lower() for o in options]
    filled = [t for t in texts if t]

    duplicate_options = len(filled) != len(set(filled)) and len(filled) >= 2
    blank_option = any(not t for t in texts) and bool(texts)
    missing_option = question["questionType"] in ("scq", "mcq") and len(options) < 4
    empty_answer_slot = "answer_array_has_empty_slot" in flags
    type_mismatch = (question["questionType"] == "mcq" and empty_answer_slot
                     and len(letters(question.get("answer") or "")) == 1)

    return {
        "latex_leak": "q:latex_leak" in flags,
        "exam_reference": "q:exam_reference" in flags,
        "noise": "q:noise" in flags,
        "duplicate_options": duplicate_options,
        "blank_option": blank_option,
        "missing_option": missing_option,
        "type_mismatch": type_mismatch,
        "image_expected": not question.get("questionImages"),
    }


def fixed_after_report(group: dict, question: dict) -> bool:
    """Was this content already corrected by faculty after the first student report?"""
    history = question.get("errorHistory") or []
    if not history:
        return False
    first_report = group.get("firstReportedAt") or ""
    for entry in history:
        if (entry.get("at") or "") > first_report:
            return True
    return False


def classify(group: dict, question: dict, solved: dict | None, taxonomy: dict) -> dict:
    themes = group.get("themes") or {}
    findings = structural_findings(question)
    key = question.get("answer")
    qtype = question["questionType"]
    theme_set = set(themes)

    def result(verdict: str, repair: str | None, confidence: float, evidence: str) -> dict:
        spec = taxonomy["repairs"].get(repair) if repair else None
        if verdict != "defect":
            route = "no_change"
        elif spec is None or spec.get("auto") is False:
            route = "faculty_review"
        else:
            route = "auto_apply" if confidence >= MIN_AUTO_CONFIDENCE else "faculty_review"
        return {
            "questionId": group["questionId"],
            "verdict": verdict,
            "repair": repair,
            "route": route,
            "confidence": round(confidence, 2),
            "evidence": evidence,
            "compare": {
                "solve": (solved or {}).get("answer"),
                "solveConfidence": (solved or {}).get("confidence"),
                "key": key,
                "solutionSupportsSolve": solution_supports(question, (solved or {}).get("answer")),
                "solutionSupportsKey": solution_supports(question, key),
            },
        }

    if not solved:
        return result("unresolved", None, 0.0,
                      "No independent solve yet - run Backend/llm_solver.py.")

    solve = solved.get("answer")
    solve_conf = float(solved.get("confidence") or 0.0)
    insufficient = bool(solved.get("insufficient_information"))
    solved_supports = solution_supports(question, solve)
    key_supports = solution_supports(question, key)
    prefix = f"Independent solve: {solve or 'insufficient'} (conf {solve_conf:.2f}). Key: {key}."

    # 1. Already corrected by faculty after the report -> stale report.
    if fixed_after_report(group, question) and (theme_set & {"spelling", "latex", "exam_ref", "noise"}):
        return result("already_fixed", None, 0.9,
                      prefix + " errorHistory shows a faculty correction after the report date.")

    # 2. Garbage or instruction-like comments are not content reports.
    if theme_set and theme_set <= {"injection", "garbage"}:
        return result("not_actionable", None, 0.9,
                      prefix + " Comment is not a content report (garbage or instruction-like); "
                               "treated as data and ignored.")

    # 3. The model could not solve it from the stem -> data or figure is missing.
    if insufficient:
        reason = str(solved.get("reason") or "").lower()
        if "option" in reason and re.search(r"\b(no|none|not|neither)\b", reason):
            return result("defect", "unsound", 0.85,
                          prefix + " The solve finds no correct printed option; the item has no "
                                   "valid answer as written.")
        if "diagram" in theme_set:
            return result("defect", "diagram", 0.7,
                          prefix + " Solve reports insufficient information; the stem relies on "
                                   "a figure that is not stored.")
        return result("defect", "stem_data", 0.8,
                      prefix + " Solve reports insufficient information: a required value is missing "
                               "from the stem.")

    matches = answers_match(qtype, solve, key)

    # 4. Content agrees with the key: the report is about presentation or behaviour, or the
    #    student is simply wrong. Structural defects still win over "no error".
    if matches:
        if findings["duplicate_options"]:
            return result("defect", "identical_options", 0.9,
                          prefix + " Two options have identical text.")
        if findings["type_mismatch"]:
            return result("defect", "type_mismatch", 0.9,
                          prefix + " Multiple-correct type but only one correct option stored.")
        if findings["blank_option"]:
            return result("defect", "option_repair", 0.85,
                          prefix + " An option is blank in the stored record.")
        if findings["missing_option"]:
            return result("defect", "option_repair", 0.72,
                          prefix + f" Only {len(question.get('options') or [])} options stored; "
                                   "restoring a missing distractor is authored content.")
        if findings["latex_leak"]:
            return result("defect", "markup_leak", 0.95,
                          prefix + " Raw LaTeX is visible in the stem.")
        if findings["exam_reference"]:
            return result("defect", "exam_reference", 0.95,
                          prefix + " Stem contains an exam-name source tag.")
        if theme_set & {"chapter"}:
            return result("defect", "wrong_chapter", 0.85,
                          prefix + " Student reports the chapter/subject is wrong.")
        if theme_set & {"syllabus"}:
            return result("defect", "syllabus", 0.6,
                          prefix + " Student claims it is outside the syllabus; needs a curriculum call.")
        if theme_set & {"diagram"}:
            return result("defect", "diagram", 0.85,
                          prefix + " Student reports a missing figure.")
        if theme_set & {"spelling"}:
            return result("defect", "spelling", 0.95,
                          prefix + " Spelling/grammar complaint; content otherwise correct.")
        if theme_set & {"solution"}:
            return result("defect", "solution_fix", 0.85,
                          prefix + " Solve agrees with the key, but the student reports the solution "
                                   "is wrong or from another question.")
        if theme_set & {"unit"}:
            return result("defect", "answer_form", 0.8,
                          prefix + " Unit/answer form is not stated.")
        if theme_set & {"missing_data"} and solve_conf < 0.7:
            return result("defect", "stem_data", 0.65,
                          prefix + " Student reports missing data and the solve is low-confidence.")
        if theme_set & NON_CONTENT_THEMES:
            if "app" in theme_set:
                return result("app_issue", None, 0.85, prefix + " Complaint describes app behaviour.")
            if "language" in theme_set:
                return result("language_mode", None, 0.9, prefix + " Request for another language.")
            return result("display_issue", None, 0.85, prefix + " Complaint describes rendering/load.")
        return result("no_error", None, 0.9,
                      prefix + " Independent solve matches the key; the student misread or mis-solved.")

    # 5. The solve disagrees with the key. Decide which side the solution supports.
    if qtype in ("scq", "mcq"):
        available = {o["letter"] for o in question.get("options") or []}
        if solve and not letters(solve).issubset(available):
            return result("defect", "unsound", 0.75,
                          prefix + f" Solve {solve} is not among the printed options.")
    if findings["duplicate_options"]:
        return result("defect", "identical_options", 0.75,
                      prefix + " Duplicate options make the key ambiguous.")
    if findings["blank_option"] and key and letters(key) & {
            o["letter"] for o in question.get("options") or [] if not (o.get("text") or "").strip()}:
        return result("defect", "option_repair", 0.8,
                      prefix + " The keyed option is blank.")

    # A student saying "data missing" plus a solve that had to assume a value is stronger
    # evidence for a missing stem value than a solve-vs-key disagreement.
    if theme_set & {"missing_data"} and solve_conf < 0.9:
        return result("defect", "stem_data", 0.8,
                      prefix + f" Student reports missing data and the solve had to assume a "
                               f"value (confidence {solve_conf:.2f}).")

    if solved_supports and not key_supports:
        return result("defect", "answer_key", min(0.95, 0.7 + solve_conf / 3),
                      prefix + " The stored solution supports the independent solve, not the key.")
    if key_supports and not solved_supports:
        return result("no_error", None, 0.55,
                      prefix + " The stored solution supports the key; the solve is likely wrong.")
    return result("defect", "answer_key", 0.6,
                  prefix + " Solve and key disagree and the solution does not settle it; "
                           "faculty must confirm.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify reported questions.")
    parser.add_argument("--answers", type=Path, default=SOLVED,
                        help="independent solves (default: Backend/work/solved_answers.json)")
    parser.add_argument("--out", type=Path, default=OUTPUT,
                        help="classification output path")
    args = parser.parse_args()

    taxonomy = load_json(TAXONOMY)
    grouped = load_json(GROUPED, "Run: python Backend/decision_layer.py")
    questions = {q["questionId"]: q for q in load_json(
        NORMALIZED, "Run: python Backend/scripts/normalize.py")}
    solved = load_json(args.answers, "Run: python Backend/llm_solver.py (needs LLM_API_KEY)")

    classified = []
    report_rows = []
    for group in grouped["groups"]:
        question = questions[group["questionId"]]
        decision = classify(group, question, solved.get(group["questionId"]), taxonomy)
        decision.update({
            "subject": question.get("subject"),
            "chapter": question.get("chapter"),
            "questionType": question["questionType"],
            "reportIds": [r["reportId"] for cluster in group["clusters"]
                          for r in cluster["comments"]],
            "themes": group.get("themes"),
            "reportCount": group["reportCount"],
        })
        classified.append(decision)
        for report_id in decision["reportIds"]:
            report_rows.append({
                "reportId": report_id,
                "questionId": group["questionId"],
                "verdict": decision["verdict"],
                "repair": decision["repair"],
                "route": decision["route"],
                "confidence": decision["confidence"],
            })

    summary = {
        "reportedQuestions": len(classified),
        "reports": len(report_rows),
        "byVerdict": dict(Counter(d["verdict"] for d in classified).most_common()),
        "byRepair": dict(Counter(d["repair"] for d in classified if d["repair"]).most_common()),
        "byRoute": dict(Counter(d["route"] for d in classified).most_common()),
    }
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "questions": sorted(classified, key=lambda d: (-d["confidence"], d["questionId"])),
        "reports": sorted(report_rows, key=lambda r: r["reportId"]),
    }
    WORK.mkdir(exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"questions classified : {summary['reportedQuestions']}")
    print(f"reports              : {summary['reports']}")
    print(f"verdicts             : {summary['byVerdict']}")
    print(f"repairs              : {summary['byRepair']}")
    print(f"routes               : {summary['byRoute']}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
