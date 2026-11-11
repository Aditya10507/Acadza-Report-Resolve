"""Export the triage engine's output to results.json in the brief's schema.

Source of truth is the pipeline output that the console serves
(Backend/work/console.json, built by build_console_data.py from the
classifier + independent solver + verification + student replies).
The hand-authored ground truth (work/labels.json) is never read here.

    python -m Backend.scripts.export_results

Schema per report (Part 3 of the brief):
    {reportId, questionId, verdict, repair, edit, route, confidence,
     evidence, student_reply}

  edit = {"field": ..., "find": ..., "replace": ...} or null when the engine
  proposes no surgical change (non-defects, and defects the engine refuses to
  auto-write). Repair is null unless verdict == "defect".

Validation runs before the file is written and exits 1 on any violation:
closed-list verdict/repair/route, edit field inside the repair's allowed
fields, no auto edit outside scope, confidence in [0, 1], non-empty reply.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent      # Backend/
PROJECT = BACKEND.parent                              # repo root
WORK = BACKEND / "work"
DATA = PROJECT / "data"
OUT = PROJECT / "results.json"

ROUTES = {"auto_apply", "faculty_review", "no_change"}
NON_DEFECT = {"no_error", "display_issue", "app_issue", "language_mode",
              "already_fixed", "not_actionable"}

# The one report the pipeline has no stored reply for (added to data/reports.json
# after the reply pass ran). Kept short and in the same voice as the generated
# replies: acknowledge, say what is true now, thank the student.
REPLY_FALLBACK = {
    "R045": "Thanks for flagging this. Our faculty already corrected this "
            "question before your report reached the queue, so the version you "
            "see now has the right answer key and solution. Nothing further to "
            "fix - keep reporting anything that looks off.",
}


def load(path: Path, hint: str) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing {path} - {hint}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"{path} is not valid JSON: {error}")


def build(console: dict, taxonomy: dict, reports: list[dict]) -> list[dict]:
    by_id = {r["reportId"]: r for r in console["reports"]}
    repairs = taxonomy["repairs"]
    verdicts = taxonomy["verdicts"]

    out, problems = [], []
    for report in reports:
        rid = report["reportId"]
        case = by_id.get(rid)
        if case is None:
            problems.append(f"{rid}: not present in the pipeline output")
            continue

        verdict = case.get("verdict")
        repair = case.get("repair") if verdict == "defect" else None
        raw_edit = case.get("suggestedEdit")
        edit = None
        if raw_edit and raw_edit.get("field"):
            edit = {
                "field": raw_edit["field"],
                "find": raw_edit.get("find"),
                "replace": raw_edit.get("replace"),
            }

        reply = (case.get("reply") or "").strip() or REPLY_FALLBACK.get(rid, "")

        entry = {
            "reportId": rid,
            "questionId": case.get("questionId") or report.get("questionId"),
            "verdict": verdict,
            "repair": repair,
            "edit": edit,
            "route": case.get("route"),
            "confidence": round(float(case.get("confidence") or 0.0), 2),
            "evidence": (case.get("evidence") or "").strip(),
            "student_reply": reply,
        }

        # ---- validation -------------------------------------------------
        if verdict not in verdicts:
            problems.append(f"{rid}: verdict {verdict!r} not in taxonomy")
        if entry["route"] not in ROUTES:
            problems.append(f"{rid}: route {entry['route']!r} not allowed")
        if not 0.0 <= entry["confidence"] <= 1.0:
            problems.append(f"{rid}: confidence {entry['confidence']} out of range")
        if not entry["evidence"]:
            problems.append(f"{rid}: empty evidence")
        if not entry["student_reply"]:
            problems.append(f"{rid}: empty student_reply")
        if verdict == "defect" and repair not in repairs:
            problems.append(f"{rid}: repair {repair!r} not in taxonomy")
        if verdict != "defect" and repair is not None:
            problems.append(f"{rid}: non-defect carries repair {repair!r}")
        if edit and repair in repairs:
            allowed = repairs[repair]["fields"]
            if allowed and edit["field"] not in allowed:
                problems.append(
                    f"{rid}: edit touches {edit['field']!r}, "
                    f"{repair} may only touch {allowed}")
            if repairs[repair]["auto"] and entry["route"] != "auto_apply":
                problems.append(
                    f"{rid}: {repair} is auto:true but routed {entry['route']}")
        if entry["route"] == "auto_apply" and edit and repair in repairs:
            if not repairs[repair]["auto"]:
                problems.append(
                    f"{rid}: auto_apply on a repair the taxonomy forbids auto-writing")
        out.append(entry)

    for rid in sorted(set(by_id) - {r["reportId"] for r in reports}):
        # a report the console knows but the assignment data does not

        problems.append(f"{rid}: in pipeline output but not in data/reports.json")

    if problems:
        print("results.json NOT written:", file=sys.stderr)
        for line in problems:
            print("  -", line, file=sys.stderr)
        raise SystemExit(1)
    return out


def main() -> int:
    console = load(WORK / "console.json", "run the pipeline first (python run.py)")
    taxonomy = load(DATA / "taxonomy.json", "restore the assignment data")
    reports = load(DATA / "reports.json", "restore the assignment data")

    results = build(console, taxonomy, reports)
    results.sort(key=lambda row: row["reportId"])
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")

    defects = [r for r in results if r["verdict"] == "defect"]
    with_edit = [r for r in defects if r["edit"]]
    print(f"wrote {OUT.relative_to(PROJECT)} - {len(results)} reports")
    print(f"  defects {len(defects)}: {len(with_edit)} with a structured edit, "
          f"{len(defects) - len(with_edit)} sent to a human with no auto edit")
    for route in sorted(ROUTES):
        print(f"  route {route}: {sum(1 for r in results if r['route'] == route)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
