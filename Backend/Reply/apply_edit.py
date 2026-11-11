"""Apply edit: the write path, gated on an explicit faculty decision.

A verification case is a proposal. Nothing is written until a faculty member approves it.
When they do, this file:

  1. re-validates the edit against the taxonomy (field scope, exact match, valid answer),
  2. writes it to the live question store,
  3. records who approved what, when, and the before/after value,
  4. marks the case resolved so it cannot be applied twice.

The live store is a local JSON copy of data/questions.json. In production the same
`apply_edit_to_question()` call would write to Acadza's content service instead - the
approval gate and audit trail are the point, not the storage.

Input   Backend/work/verification_cases.json, data/questions.json, data/taxonomy.json
Output  Backend/work/live_questions.json, Backend/work/audit_log.jsonl,
        updated Backend/work/verification_cases.json

Usage
    python Backend/Reply/apply_edit.py --list
    python Backend/Reply/apply_edit.py --show 60f744b3f1b9bc7cc7b4a949
    python Backend/Reply/apply_edit.py --approve 60f744b3f1b9bc7cc7b4a949 \
        --reviewer "Dr Rao" --note "checked by hand"
    python Backend/Reply/apply_edit.py --reject 60f744b3f1b9bc7cc7b4a949 \
        --reviewer "Dr Rao" --note "student misread"
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

REPLY = Path(__file__).resolve().parent
BACKEND = REPLY.parent
PROJECT = BACKEND.parent
WORK = BACKEND / "work"

CASES = WORK / "verification_cases.json"
LIVE = WORK / "live_questions.json"
AUDIT = WORK / "audit_log.jsonl"
SOURCE = PROJECT / "data" / "questions.json"
TAXONOMY = PROJECT / "data" / "taxonomy.json"


def load_json(path: Path, hint: str = "") -> dict | list:
    if not path.exists():
        raise SystemExit(f"Missing {path}. {hint}".strip())
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict | list) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_cases() -> dict:
    return load_json(CASES, "Run: python Backend/Reply/verification.py")


def load_store() -> dict:
    """Live questions keyed by id, seeded from the source on first use."""
    if not LIVE.exists():
        questions = load_json(SOURCE)
        store = {q["_id"]: q for q in questions}
        WORK.mkdir(exist_ok=True)
        save_json(LIVE, store)
        return store
    return load_json(LIVE)


def append_audit(entry: dict) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def question_body(question: dict) -> dict:
    return question[question["questionType"]]


def validate_edit(edit: dict, repair: str, question: dict, taxonomy: dict) -> None:
    """Raise ValueError if the edit is out of scope, ambiguous, or invalid."""
    spec = taxonomy["repairs"].get(repair)
    if not spec:
        raise ValueError(f"unknown repair '{repair}'")
    field = edit.get("field")
    find = edit.get("find")
    replace = edit.get("replace")
    if not field or find is None or replace is None:
        raise ValueError("edit needs field, find and replace")
    if field not in spec["fields"]:
        raise ValueError(f"'{repair}' may only touch {spec['fields']}, not '{field}'")
    if find == "":
        raise ValueError("empty find would replace the whole field")
    if find == replace:
        raise ValueError("replace is identical to find")

    if field == "questionType":
        if find != question["questionType"]:
            raise ValueError(f"find '{find}' does not match stored type "
                             f"'{question['questionType']}'")
        return

    haystack = str(question_body(question).get(field, ""))
    count = haystack.count(find)
    if count != 1:
        raise ValueError(f"find string occurs {count} times in '{field}' (must be exactly 1)")

    if field == "answer":
        qtype = question["questionType"]
        if qtype == "scq" and not re.fullmatch(r"[A-D]", str(replace).strip().upper()):
            raise ValueError(f"scq answer must be A-D, got '{replace}'")
        if qtype == "integerQuestion" and not re.fullmatch(r"-?\d+(\.\d+)?", str(replace).strip()):
            raise ValueError(f"integer answer must be numeric, got '{replace}'")
        if qtype == "mcq" and not re.fullmatch(r"[A-D](,[A-D])*", str(replace).strip().upper()):
            raise ValueError(f"mcq answer must be letters, got '{replace}'")


def apply_edit_to_question(question: dict, edit: dict) -> None:
    """Mutate one stored question in place."""
    field, find, replace = edit["field"], edit["find"], edit["replace"]
    if field == "questionType":
        old = question["questionType"]
        question[str(replace)] = question.pop(old)
        question["questionType"] = replace
        return
    body = question_body(question)
    if field == "answer" and isinstance(body.get("answer"), list):
        body["answer"] = [str(replace) if str(item).strip() == find else item
                          for item in body["answer"]]
        return
    body[field] = str(body.get(field, "")).replace(find, str(replace), 1)


def find_case(cases: dict, case_id: str) -> dict:
    for case in cases["cases"]:
        if case["caseId"] == case_id:
            return case
    raise SystemExit(f"No case '{case_id}'. Use --list to see case ids.")


def approve_case(case_id: str, reviewer: str, note: str = "",
                 override: dict | None = None, cases: dict | None = None,
                 store: dict | None = None, taxonomy: dict | None = None) -> dict:
    cases = cases or load_cases()
    store = store or load_store()
    taxonomy = taxonomy or load_json(TAXONOMY)
    case = find_case(cases, case_id)

    if case["status"] != "pending_review":
        raise SystemExit(f"Case {case_id} is already '{case['status']}'.")
    if not reviewer.strip():
        raise SystemExit("A reviewer name is required to approve a change.")

    edit = override or case.get("suggestedEdit")
    if not edit:
        raise SystemExit(
            f"Case {case_id} needs an authored fix ({case['manualReason']}).\n"
            "Provide one with --field/--find/--replace.")

    question = store[case["questionId"]]
    try:
        validate_edit(edit, case["repair"], question, taxonomy)
    except ValueError as error:
        raise SystemExit(f"Edit rejected: {error}")

    before = (question.get("questionType") if edit["field"] == "questionType"
              else question_body(question).get(edit["field"]))
    apply_edit_to_question(question, edit)

    case.update({
        "status": "applied",
        "appliedEdit": edit,
        "appliedBy": reviewer,
        "appliedAt": now(),
        "reviewNote": note,
    })
    question.setdefault("errorHistory", []).append({
        "type": case["repair"],
        "actor": reviewer,
        "at": case["appliedAt"],
        "note": note or f"Fixed from student report {', '.join(r['reportId'] for r in case['reports'])}",
    })

    save_store(store)
    save_json(CASES, cases)
    append_audit({
        "at": case["appliedAt"], "caseId": case_id, "questionId": case["questionId"],
        "action": "approve", "reviewer": reviewer, "note": note, "repair": case["repair"],
        "field": edit["field"], "before": before, "after": edit["replace"],
    })
    return case


def reject_case(case_id: str, reviewer: str, note: str = "",
                cases: dict | None = None) -> dict:
    cases = cases or load_cases()
    case = find_case(cases, case_id)
    if case["status"] != "pending_review":
        raise SystemExit(f"Case {case_id} is already '{case['status']}'.")
    if not reviewer.strip():
        raise SystemExit("A reviewer name is required to reject a report.")

    case.update({"status": "rejected", "reviewedBy": reviewer, "reviewedAt": now(),
                 "reviewNote": note})
    save_json(CASES, cases)
    append_audit({
        "at": case["reviewedAt"], "caseId": case_id, "questionId": case["questionId"],
        "action": "reject", "reviewer": reviewer, "note": note, "repair": case["repair"],
    })
    return case


def save_store(store: dict) -> None:
    save_json(LIVE, store)


def describe_case(case: dict) -> str:
    diff = case.get("diff")
    change = f"  {diff['before']} -> {diff['after']}" if diff else "  (authored fix)"
    return (f"[{case['status']:<14}] {case['severity']:<8} {case['caseId']}  "
            f"{case['headline']}\n{change}")


def main() -> int:
    global CASES, LIVE, AUDIT
    parser = argparse.ArgumentParser(description="Review and apply verification cases.")
    parser.add_argument("--list", action="store_true", help="list cases")
    parser.add_argument("--show", help="show one case as JSON")
    parser.add_argument("--approve", help="case id to approve and apply")
    parser.add_argument("--reject", help="case id to reject")
    parser.add_argument("--reviewer", default="", help="faculty name recorded in the audit log")
    parser.add_argument("--note", default="", help="reason / comment recorded in the audit log")
    parser.add_argument("--field", help="override edit field (manual fixes)")
    parser.add_argument("--find", help="override edit find string")
    parser.add_argument("--replace", help="override edit replacement")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--store", type=Path, default=LIVE)
    parser.add_argument("--audit", type=Path, default=AUDIT)
    args = parser.parse_args()

    CASES, LIVE, AUDIT = args.cases, args.store, args.audit

    if args.list:
        cases = load_cases()["cases"]
        for case in cases:
            print(describe_case(case))
        pending = sum(1 for c in cases if c["status"] == "pending_review")
        print(f"\n{len(cases)} cases, {pending} pending review")
        return 0

    if args.show:
        print(json.dumps(find_case(load_cases(), args.show), indent=2, ensure_ascii=False))
        return 0

    if args.approve:
        override = None
        if args.field or args.find or args.replace:
            if not (args.field and args.find and args.replace):
                raise SystemExit("An override needs --field, --find and --replace together.")
            override = {"field": args.field, "find": args.find, "replace": args.replace}
        case = approve_case(args.approve, args.reviewer, args.note, override)
        print(f"applied {case['caseId']} by {case['appliedBy']}: "
              f"{case['appliedEdit']['field']} -> {case['appliedEdit']['replace']}")
        return 0

    if args.reject:
        case = reject_case(args.reject, args.reviewer, args.note)
        print(f"rejected {case['caseId']} by {case['reviewedBy']}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
