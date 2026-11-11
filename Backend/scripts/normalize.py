"""Normalise Acadza production question HTML into analysis-friendly text.

Reads data/questions.json, data/reports.json. Writes:
  work/questions.normalized.json  - one record per question, text + options + flags
  work/digest.md                  - human-readable audit sheet for labelling

No network, no LLM, stdlib only. Source data is never modified.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent      # Backend/
PROJECT = BACKEND.parent                               # repo root
DATA = PROJECT / "data"                                # provided, read-only
WORK = BACKEND / "work"                                # generated artifacts

CONTAINER = {"scq": "scq", "mcq": "mcq", "integerQuestion": "integerQuestion"}


def strip_html(raw: str) -> str:
    """HTML/MathML -> linear text. Block tags become newlines, everything else spaces."""
    if not raw:
        return ""
    s = raw
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*(h[1-6]|p|div|li|tr|table)\s*>", "\n", s)
    s = re.sub(r"(?i)<\s*(h[1-6]|p|div|li|tr|table)[^>]*>", "\n", s)
    s = re.sub(r"<[^>]*>", " ", s)
    s = html.unescape(s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def clean_lines(text: str) -> str:
    return "\n".join(ln.strip() for ln in text.split("\n") if ln.strip())


OPT_SPLIT = re.compile(r"(?<![\w)])\(([A-D])\)\s*", re.I)


def extract_options(text: str) -> tuple[str, list[dict]]:
    """Split '(A) ... (B) ...' style options out of the flattened question text.

    Assertion-reason items often contain '(A)' twice (the assertion is labelled A),
    so prefer the LAST run of consecutive A,B,C(,D) markers: that is the real option list.
    """
    matches = list(OPT_SPLIT.finditer(text))
    if len(matches) < 2:
        return text, []

    letters = [m.group(1).upper() for m in matches]
    start = None
    for i in range(len(matches) - 1):
        run = letters[i: i + 5]
        if run[:4] == ["A", "B", "C", "D"]:
            start = i
        elif run[:3] == ["A", "B", "C"] and i + 3 >= len(matches):
            start = i
    if start is None:
        start = 0
        # keep only the first occurrence of each letter, in order
        seen, keep = set(), []
        for m, letter in zip(matches, letters):
            if letter not in seen:
                seen.add(letter)
                keep.append(m)
        matches = keep
    else:
        matches = matches[start: start + 4]

    stem = text[: matches[0].start()].strip()
    options = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = re.sub(r"\s+", " ", text[m.end(): end]).strip(" .;,")
        options.append({"letter": m.group(1).upper(), "text": body})
    return stem, options


def norm_answer(qtype: str, raw) -> tuple[object, list[str]]:
    """Return (normalised answer, list of anomalies)."""
    anomalies: list[str] = []
    if raw is None:
        return "", ["answer_missing"]
    if isinstance(raw, list):
        vals = [str(x).strip() for x in raw]
        if any(v == "" for v in vals):
            anomalies.append("answer_array_has_empty_slot")
        vals = [v for v in vals if v]
        if qtype != "mcq":
            anomalies.append(f"answer_array_on_{qtype}")
        if len(vals) > 1:
            anomalies.append("multi_letter_answer")
        return ",".join(vals), anomalies
    s = str(raw).strip()
    if s == "":
        anomalies.append("answer_missing")
    if qtype == "scq" and not re.fullmatch(r"[A-D]", s, re.I):
        anomalies.append("scq_answer_not_single_letter")
    if qtype == "scq" and s != s.upper():
        anomalies.append("scq_answer_lowercase")
    if qtype == "integerQuestion" and not re.fullmatch(r"-?\d+(\.\d+)?", s):
        anomalies.append("integer_answer_not_numeric")
    return s, anomalies


LATEX_LEAK = re.compile(r"(\\frac|\\sqrt|\\times|\\left|\\right|\^\{|_\{)")
EXAM_REF = re.compile(r"\((?:JEE|NEET|AIIMS|AIPMT)[^)]{0,30}\)", re.I)
NOISE = re.compile(
    r"(space for rough work|rough work|question\s*(no|number)\s*[.:]?\s*\d+|"
    r"do not write|for office use|source\s*:)", re.I)


def analyse(raw_html: str) -> dict:
    text = clean_lines(strip_html(raw_html))
    flat = re.sub(r"\s+", " ", text)
    stem, options = extract_options(flat)
    flags = []
    if LATEX_LEAK.search(flat):
        flags.append("latex_leak")
    if EXAM_REF.search(flat):
        flags.append("exam_reference")
    if NOISE.search(flat):
        flags.append("noise")
    if re.search(r"&[a-z]+;|&#x?[0-9a-f]+;", flat, re.I):
        flags.append("entity_leak")
    if "<img" in raw_html.lower():
        flags.append("inline_img")
    return {
        "text": flat,
        "stem": re.sub(r"\s+", " ", stem),
        "options": options,
        "flags": flags,
        "has_mathml": "<math" in raw_html.lower(),
    }


def main() -> int:
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    reports = json.loads((DATA / "reports.json").read_text(encoding="utf-8"))
    taxonomy = json.loads((DATA / "taxonomy.json").read_text(encoding="utf-8"))

    by_q: dict[str, list] = {}
    for r in reports:
        by_q.setdefault(r["questionId"], []).append(r)

    records = []
    for q in questions:
        qtype = q["questionType"]
        body = q.get(CONTAINER.get(qtype, qtype)) or {}
        answer, answer_anomalies = norm_answer(qtype, body.get("answer"))
        qa = analyse(body.get("question", ""))
        sa = analyse(body.get("solution", ""))
        flags = [f"q:{f}" for f in qa["flags"]] + [f"sol:{f}" for f in sa["flags"]]
        flags += answer_anomalies
        imgs = body.get("quesImages") or []
        simgs = body.get("solutionImages") or []
        if imgs:
            flags.append("question_images")
        if simgs:
            flags.append("solution_images")
        if not body.get("solution", "").strip():
            flags.append("solution_empty")
        if len(qa["options"]) not in (4, 0):
            flags.append(f"option_count_{len(qa['options'])}")
        if qtype in ("scq", "mcq") and len(qa["options"]) < 4:
            flags.append("options_less_than_4")

        records.append({
            "questionId": q["_id"],
            "questionType": qtype,
            "subject": q.get("subject"),
            "chapter": q.get("chapter"),
            "level": q.get("level"),
            "answer": answer,
            "answerRaw": body.get("answer"),
            "questionText": qa["text"],
            "stem": qa["stem"],
            "options": qa["options"],
            "solutionText": sa["text"],
            "flags": flags,
            "questionImages": imgs,
            "solutionImages": simgs,
            "errorHistory": q.get("errorHistory") or [],
            "reports": by_q.get(q["_id"], []),
        })

    WORK.mkdir(exist_ok=True)
    (WORK / "questions.normalized.json").write_text(
        json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")

    # ---- audit sheet -------------------------------------------------------
    lines = ["# Question audit sheet", ""]
    lines.append(f"questions={len(records)} reports={len(reports)} "
                 f"verdicts={len(taxonomy['verdicts'])} repairs={len(taxonomy['repairs'])}")
    lines.append("")
    for i, q in enumerate(records, 1):
        lines.append(f"## [{i:02d}] {q['questionId']} · {q['questionType']} · "
                     f"{q['subject']}/{q['chapter']} · key=`{q['answer']}`")
        if q["flags"]:
            lines.append(f"flags: {', '.join(q['flags'])}")
        lines.append("")
        lines.append(f"Q: {q['questionText']}")
        if q["options"]:
            lines.append("OPTS: " + " | ".join(
                f"({o['letter']}) {o['text']}" for o in q["options"]))
        sol = q["solutionText"]
        if len(sol) > 700:
            sol = sol[:700] + " …"
        lines.append(f"SOL: {sol or '(empty)'}")
        if q["questionImages"] or q["solutionImages"]:
            lines.append(f"IMAGES: q={q['questionImages']} s={q['solutionImages']}")
        if q["errorHistory"]:
            lines.append(f"HISTORY: {json.dumps(q['errorHistory'], ensure_ascii=False)}")
        if q["reports"]:
            for r in q["reports"]:
                lines.append(f"REPORT {r['reportId']} [{r['source']}] "
                             f"{r['reportedAt']}: {r['comment']}")
        else:
            lines.append("REPORT: (none - audit surface)")
        lines.append("")
    (WORK / "digest.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"questions={len(records)} flagged={sum(1 for q in records if q['flags'])}")
    print(f"wrote {WORK / 'questions.normalized.json'}")
    print(f"wrote {WORK / 'digest.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
