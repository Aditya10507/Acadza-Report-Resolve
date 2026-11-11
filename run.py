"""One-command launcher for the Acadza report-to-fix console.

    python run.py                 # run the offline pipeline, refresh console.json, serve API + console
    python run.py --full          # also re-run both LLM stages (solver + replies)
    python run.py --solve         # only add the missing independent solves (resumable)
    python run.py --replies       # only generate missing student replies
    python run.py --serve-only    # serve existing console data without rebuilding it
    python run.py --port 9000     # serve on a different port

Safe to re-run: every stage skips work it has already done except normalize.py
and the two pure-derivation stages (decision_layer, classifier), which are
offline and take under a second. data/ is never modified.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
PYTHON = sys.executable
WORK = PROJECT / "Backend" / "work"

PIPELINE = [
    ["Backend/scripts/normalize.py"],
    ["Backend/decision_layer.py"],
    ["Backend/classifier.py"],
    ["Backend/Reply/verification.py"],
]

LLM_STAGES = {
    "solve": {
        "script": "Backend/llm_solver.py",
        "output": WORK / "solved_answers.json",
        "args_extra": [],
    },
    "replies": {
        "script": "Backend/Reply/student_reply.py",
        "output": WORK / "student_replies.json",
        "args_extra": [],
    },
}

BUILD = "Backend/scripts/build_console_data.py"


def run(script: str, extra: list[str] | None = None) -> None:
    cmd = [PYTHON, str(PROJECT / script), *(extra or [])]
    print(f"\n$ {' '.join(os.path.relpath(c, PROJECT) if str(c).startswith(str(PROJECT)) else str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=PROJECT)
    if result.returncode != 0:
        raise SystemExit(f"stage failed: {script} (exit {result.returncode})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the pipeline and serve the faculty console.")
    parser.add_argument("--full", action="store_true",
                        help="re-run both LLM stages (solver + replies) before serving")
    parser.add_argument("--solve", action="store_true",
                        help="run the independent solver (resumable; skips solved questions)")
    parser.add_argument("--replies", action="store_true",
                        help="generate missing student replies")
    parser.add_argument("--serve-only", action="store_true",
                        help="serve existing console data without rebuilding it")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8080")),
                        help="port to serve on (defaults to $PORT when the platform sets it)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (use 0.0.0.0 to share on the network)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args()

    if not (args.serve_only or args.full or args.solve or args.replies):
        # default: offline pipeline only, skip the costly LLM stages
        for [script] in PIPELINE:
            run(script)

    if args.full:
        for spec in LLM_STAGES.values():
            run(spec["script"], ["--force"])
    else:
        if args.solve:
            spec = LLM_STAGES["solve"]
            run(spec["script"], spec["args_extra"])
        if args.replies:
            run(LLM_STAGES["replies"]["script"])

    if not args.serve_only:
        run(BUILD)

    url = f"http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{args.port}"
    print(f"\nserving faculty console and API at {url} (bound to {args.host})")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        result = subprocess.run([
            PYTHON, "-m", "uvicorn", "Backend.api:app",
            "--host", args.host, "--port", str(args.port),
        ], cwd=PROJECT)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0
    return result.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
        sys.exit(0)
