"""Validate work/labels.json against the data and the taxonomy.

Checks coverage, closed-list keys, repair field scope, route consistency,
and that every proposed edit actually targets its field. Exit 1 on failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent      # Backend/
PROJECT = BACKEND.parent                               # repo root
DATA, WORK = PROJECT / "data", BACKEND / "work"

CONTAINER = {"scq": "scq", "mcq": "mcq", "integerQuestion": "integerQuestion"}
ROUTES = {"auto_apply", "faculty_review", "no_change"}
NON_DEFECT = {"no_error", "display_issue", "app_issue", "language_mode",
              "already_fixed", "not_actionable"}


def main() -> int:
    qs = {q["_id"]: q for q in json.loads((DATA / "questions.json").read_text(encoding="utf-8"))}
    reports = json.loads((DATA / "reports.json").read_text(encoding="utf-8"))
    tax = json.loads((DATA / "taxonomy.json").read_text(encoding="utf-8"))
    labels = json.loads((WORK / "labels.json").read_text(encoding="utf-8"))

    problems: list[str] = []
    warnings: list[str] = []

    rep_by_id = {r["reportId"]: r for r in reports}
    seen: dict[str, int] = {}
    for lab in labels:
        rid = lab["reportId"]
        seen[rid] = seen.get(rid, 0) + 1
        rep = rep_by_id.get(rid)
        if rep is None:
            problems.append(f"{rid}: not a real report")
            continue
        if rep["questionId"] != lab["questionId"]:
            problems.append(f"{rid}: questionId mismatch with reports.json")

        verdict, repair, route = lab["verdict"], lab["repair"], lab["route"]
        if verdict not in tax["verdicts"]:
            problems.append(f"{rid}: verdict '{verdict}' not in taxonomy")
        if verdict == "defect":
            if not repair:
                problems.append(f"{rid}: defect without repair")
            elif repair not in tax["repairs"]:
                problems.append(f"{rid}: repair '{repair}' not in taxonomy")
        else:
            if repair is not None:
                problems.append(f"{rid}: non-defect verdict has repair '{repair}'")
            if route != "no_change":
                problems.append(f"{rid}: non-defect must route no_change, got '{route}'")
        if route not in ROUTES:
            problems.append(f"{rid}: bad route '{route}'")
        if not 0.0 <= float(lab["confidence"]) <= 1.0:
            problems.append(f"{rid}: confidence out of range")

        if repair and repair in tax["repairs"]:
            spec = tax["repairs"][repair]
            edit = lab.get("edit")
            if edit is None:
                if spec["auto"] is False:
                    pass  # human-only repair, suggestion may be empty
                else:
                    warnings.append(f"{rid}: auto repair '{repair}' has no edit")
            else:
                if edit["field"] not in spec["fields"]:
                    problems.append(
                        f"{rid}: edit field '{edit['field']}' outside '{repair}' scope {spec['fields']}")
                if spec["auto"] is False:
                    problems.append(f"{rid}: edit proposed for human-only repair '{repair}'")
                if route == "auto_apply" and spec["auto"] is False:
                    problems.append(f"{rid}: auto_apply on auto:false repair '{repair}'")
                if route == "no_change":
                    problems.append(f"{rid}: edit proposed but route no_change")
                # does the find string exist in the target field?
                q = qs[rep["questionId"]]
                body = q[CONTAINER[q["questionType"]]]
                field_name = edit["field"]
                if field_name == "questionType":
                    hay, needle = q["questionType"], edit["find"]
                else:
                    hay, needle = str(body.get(field_name, "")), edit["find"]
                if needle == "":
                    warnings.append(f"{rid}: whole-field replacement of '{field_name}'")
                else:
                    n = hay.count(needle)
                    if n == 0:
                        problems.append(f"{rid}: find string not present in '{field_name}': {needle!r}")
                    elif n > 1:
                        warnings.append(f"{rid}: find string occurs {n}x in '{field_name}'")

    for rid in rep_by_id:
        if seen.get(rid, 0) != 1:
            problems.append(f"{rid}: labelled {seen.get(rid, 0)} times (expected 1)")
    for rid in seen:
        if rid not in rep_by_id:
            problems.append(f"{rid}: label for unknown report")

    print(f"labels={len(labels)} reports={len(reports)} "
          f"defects={sum(1 for l in labels if l['verdict'] == 'defect')} "
          f"auto={sum(1 for l in labels if l['route'] == 'auto_apply')}")
    for w in warnings:
        print(f"WARN  {w}")
    for p in problems:
        print(f"FAIL  {p}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
