"""Decision layer: group every report by question and collapse the common complaints.

Input   Backend/work/questions.normalized.json  (run scripts/normalize.py first)
Output  Backend/work/grouped_reports.json

Deterministic, offline, no LLM. The output is the work queue: one group per reported
question, ordered by urgency, where complaints of the same kind are collapsed into one
cluster. A reviewer reads "3 students report the answer key" instead of three threads.

Clustering is theme-based rather than fuzzy: each comment is mapped to one complaint
theme by an ordered rule list, and comments sharing a theme on the same question form a
cluster. That is deliberately simple - it is explainable to a faculty member, stable
across runs, and cannot silently merge unrelated complaints.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
WORK = BACKEND / "work"
NORMALIZED = WORK / "questions.normalized.json"
OUTPUT = WORK / "grouped_reports.json"

# Ordered: the first pattern that matches a comment becomes its theme. Specific themes
# come before general ones, so "option D is missing" is an option problem, not "answer".
THEME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("injection", re.compile(
        r"(ignore\s+(all\s+)?(previous|prior)\s+instructions|you\s+are\s+(now\s+)?"
        r"(the\s+)?(admin|system)|system\s+prompt|change\s+the\s+answer|mark\s+this\s+report)",
        re.I)),
    ("garbage", re.compile(r"^(asdf\w*|qwer\w*|zxcv\w*|test|hi+|hello+|ok+|nice)[.!?]*$", re.I)),
    ("language", re.compile(r"(hindi|marathi|tamil|telugu|bengali|kannada|language|translate)", re.I)),
    ("syllabus", re.compile(r"(syllabus|out\s+of\s+(the\s+)?(course|syllabus)|not\s+in\s+(the\s+)?syllabus)", re.I)),
    ("chapter", re.compile(r"(chapter|belongs\s+to|filed\s+under|wrong\s+subject|why\s+is\s+it\s+in)", re.I)),
    ("duplicate_option", re.compile(
        r"(two\s+options|options?\s+(are\s+)?same|same\s+options?|repeat(ed)?\s+options?)", re.I)),
    ("diagram", re.compile(r"\b(figure|diagram|image|picture)\b", re.I)),
    ("blank_option", re.compile(
        r"(option\s+[a-d]\s+(is\s+)?(blank|empty|missing)|only\s+\d\s+options|"
        r"options?\s+(is|are)\s+missing|option\s+[a-d]\s+nahi)", re.I)),
    ("spelling", re.compile(r"(spelling|typo|misspell|spelt|grammar)", re.I)),
    ("latex", re.compile(r"(\\frac|\\sqrt|latex|code\s+is\s+showing|formula\s+showing)", re.I)),
    ("exam_ref", re.compile(r"(neet\s+20|jee\s+20|why\s+neet|exam\s+name|year\s+written)", re.I)),
    ("display", re.compile(r"(cut|screen|not\s+showing|render|zoom|load|slow|hang|takes\s+forever)", re.I)),
    ("app", re.compile(
        r"(clicked|saved|recorded|marked\s+.*still|keyboard|lag|negative\s+marking|got\s+it\s+wrong|"
        r"it\s+saved)", re.I)),
    ("unit", re.compile(r"\b(unit|si\s+unit|dimension)\b", re.I)),
    ("solution", re.compile(r"(solution|explanation)", re.I)),
    ("missing_data", re.compile(
        r"(data\s+missing|value\s+(is\s+)?not\s+given|not\s+given|missing\s+data|edge\s+length|"
        r"nahi\s+di|nahi\s+diya|nahi\s+bataya|kuch\s+aur\s+pucha)", re.I)),
    ("option", re.compile(r"(option\s+[a-d]|options?\s+(are|is)\s+(wrong|incorrect))", re.I)),
    ("answer", re.compile(
        r"(answer|ans\b|key|galat|wrong|correct|should\s+be|assertion|then\s+why)", re.I)),
]

# Complaints that are really the same disagreement share a family, so they merge on the
# same question (e.g. "key is wrong" and "solution says 50 but key is C").
CLUSTER_FAMILY = {"answer": "answer_solution", "solution": "answer_solution"}

# How much weight a theme adds to urgency, beyond report and student counts.
THEME_WEIGHT = {
    "answer": 3, "blank_option": 3, "duplicate_option": 2, "missing_data": 3,
    "solution": 2, "diagram": 2, "option": 2, "app": 2, "display": 2,
    "syllabus": 1, "chapter": 1, "spelling": 1, "exam_ref": 1, "latex": 1, "unit": 1,
    "injection": 0, "garbage": 0, "language": 1,
}


def load_questions() -> list[dict]:
    if not NORMALIZED.exists():
        raise SystemExit(f"Missing {NORMALIZED}. Run: python Backend/scripts/normalize.py")
    return json.loads(NORMALIZED.read_text(encoding="utf-8"))


def detect_theme(comment: str) -> str:
    for theme, pattern in THEME_PATTERNS:
        if pattern.search(comment or ""):
            return theme
    return "other"


def cluster_comments(reports: list[dict]) -> list[dict]:
    """One cluster per complaint family on this question, oldest comment first."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for report in sorted(reports, key=lambda r: r.get("reportedAt", "")):
        theme = detect_theme(report["comment"])
        buckets[CLUSTER_FAMILY.get(theme, theme)].append(report)

    clusters = []
    for family, members in buckets.items():
        themes = Counter(detect_theme(r["comment"]) for r in members)
        clusters.append({
            "family": family,
            "theme": themes.most_common(1)[0][0],
            "count": len(members),
            "studentCount": len({r["studentId"] for r in members}),
            "reportIds": [r["reportId"] for r in members],
            "comments": [
                {"reportId": r["reportId"], "studentId": r["studentId"],
                 "source": r["source"], "reportedAt": r["reportedAt"], "comment": r["comment"]}
                for r in members
            ],
        })
    return sorted(clusters, key=lambda c: (-c["count"], c["theme"]))


def group_reports_by_question(questions: list[dict]) -> list[dict]:
    groups = []
    for question in questions:
        reports = question.get("reports") or []
        if not reports:
            continue
        clusters = cluster_comments(reports)
        themes = Counter()
        for cluster in clusters:
            themes[cluster["theme"]] += cluster["count"]

        students = len({r["studentId"] for r in reports})
        urgency = len(reports) + students + sum(THEME_WEIGHT.get(t, 0) for t in themes)
        top_theme = themes.most_common(1)[0][0] if themes else "other"

        groups.append({
            "questionId": question["questionId"],
            "subject": question.get("subject"),
            "chapter": question.get("chapter"),
            "questionType": question.get("questionType"),
            "key": question.get("answer"),
            "reportCount": len(reports),
            "studentCount": students,
            "sources": dict(Counter(r["source"] for r in reports)),
            "firstReportedAt": min(r["reportedAt"] for r in reports),
            "lastReportedAt": max(r["reportedAt"] for r in reports),
            "themes": dict(themes),
            "topTheme": top_theme,
            "urgency": urgency,
            "urgencyReason": (f"{len(reports)} report(s) from {students} student(s); "
                              f"dominant complaint '{top_theme}'"),
            "clusters": clusters,
        })

    return sorted(groups, key=lambda g: (-g["urgency"], g["lastReportedAt"]))


def summarise(groups: list[dict], total_reports: int) -> dict:
    by_theme = Counter()
    for group in groups:
        by_theme.update(group["themes"])
    clustered = sum(len(g["clusters"]) for g in groups)
    repeated = [c for g in groups for c in g["clusters"] if c["count"] > 1]
    return {
        "reportedQuestions": len(groups),
        "reports": total_reports,
        "commentClusters": clustered,
        "repeatedClusters": len(repeated),
        "reportsInRepeatedClusters": sum(c["count"] for c in repeated),
        "byTheme": dict(by_theme.most_common()),
    }


def main() -> int:
    questions = load_questions()
    groups = group_reports_by_question(questions)
    total_reports = sum(len(q.get("reports") or []) for q in questions)

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Backend/work/questions.normalized.json",
        "summary": summarise(groups, total_reports),
        "groups": groups,
    }
    WORK.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    s = payload["summary"]
    print(f"questions reported : {s['reportedQuestions']}")
    print(f"reports            : {s['reports']}")
    print(f"comment clusters   : {s['commentClusters']} "
          f"({s['repeatedClusters']} shared by >1 student, "
          f"{s['reportsInRepeatedClusters']} reports)")
    print(f"themes             : {s['byTheme']}")
    print(f"wrote {OUTPUT.relative_to(BACKEND.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
