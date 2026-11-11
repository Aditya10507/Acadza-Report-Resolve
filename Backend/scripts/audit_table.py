"""Render work/question_audit.md: one row per question, with its reports and my label."""
from __future__ import annotations

import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent      # Backend/
WORK = BACKEND / "work"


def main() -> None:
    qs = json.loads((WORK / "questions.normalized.json").read_text(encoding="utf-8"))
    labels = json.loads((WORK / "labels.json").read_text(encoding="utf-8"))
    by_q: dict[str, list] = {}
    for lab in labels:
        by_q.setdefault(lab["questionId"], []).append(lab)

    lines = [
        "# Question audit (generated from work/labels.json)",
        "",
        "`finding` is my independent judgement; `route` is what the engine should do.",
        "`-` means the question was never reported (audit surface).",
        "",
        "| # | id | type | chapter | key | flags | reports | finding | route |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, q in enumerate(qs, 1):
        labs = by_q.get(q["questionId"], [])
        if not labs:
            finding, route = "never reported", "-"
        else:
            verdicts = {l["verdict"] for l in labs}
            repairs = sorted({l["repair"] for l in labs if l["repair"]})
            routes = sorted({l["route"] for l in labs})
            finding = "+".join(sorted(verdicts))
            if repairs:
                finding += ": " + ", ".join(repairs)
            route = ", ".join(routes)
        flags = ",".join(q["flags"]) or "-"
        chapter = (q["chapter"] or "")[:34]
        reports = ", ".join(l["reportId"] for l in labs) or "-"
        lines.append(
            f"| {i:02d} | {q['questionId'][-6:]} | {q['questionType']} | {chapter} | "
            f"`{q['answer']}` | {flags} | {reports} | {finding} | {route} |")

    (WORK / "question_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {WORK / 'question_audit.md'} ({len(qs)} questions)")


if __name__ == "__main__":
    main()
