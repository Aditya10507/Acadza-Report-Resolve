"""FastAPI adapter for the faculty console, demo auth and the student panel.

Routes faculty review actions through the existing JSON-backed edit and audit
workflow, serves the 43 practice questions to students, and records student
queries in data/demo.sqlite3. A query is pushed through the offline pipeline
(normalize -> decision_layer -> classifier -> verification -> console build)
unchanged, so only verdicts the AI marks as defects surface for faculty.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

from Backend import demo_db
from Backend.Reply import apply_edit, student_reply
from Backend.scripts import build_console_data

PROJECT = Path(__file__).resolve().parent.parent
CONSOLE_DATA = PROJECT / "Backend" / "work" / "console.json"
FRONTEND = PROJECT / "Frontend"
AUDIT = PROJECT / "Backend" / "work" / "audit_log.jsonl"

SESSION_COOKIE = "acadza_session"

# Offline pipeline stages a new student query goes through. The LLM stages
# (llm_solver / student_reply) are deliberately not in this list: reports land
# on the 43 known questions, whose independent solves already exist, and no
# pipeline code is modified by the demo.
PIPELINE_STAGES = [
    "Backend/scripts/normalize.py",
    "Backend/decision_layer.py",
    "Backend/classifier.py",
    "Backend/Reply/verification.py",
]

pipeline_lock = threading.Lock()
review_lock = threading.Lock()

app = FastAPI(title="Acadza Report-to-Fix API")

demo_db.init_db()


class Edit(BaseModel):
    field: str
    find: str
    replace: str


class ReviewRequest(BaseModel):
    reviewer: str
    note: str = ""


class ApproveRequest(ReviewRequest):
    edit: Edit | None = None


class Credentials(BaseModel):
    username: str
    password: str
    role: str = "student"


class QueryRequest(BaseModel):
    questionId: str
    comment: str


# ---- auth helpers ----------------------------------------------------------

def current_user(request: Request) -> dict | None:
    return demo_db.session_user(request.cookies.get(SESSION_COOKIE))


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


def require_faculty(request: Request) -> dict:
    user = require_user(request)
    if user["role"] != "faculty":
        raise HTTPException(status_code=403, detail="Faculty account required.")
    return user


def set_session_cookie(response: RedirectResponse | None, token: str):
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=7 * 24 * 3600)
    return response


# ---- auth endpoints --------------------------------------------------------

@app.post("/api/auth/signup")
def signup(credentials: Credentials) -> dict:
    try:
        user = demo_db.create_user(credentials.username, credentials.password,
                                   credentials.role)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    token, _ = demo_db.create_session(user)
    response = JSONResponse({"username": user["username"], "role": user["role"]})
    return set_session_cookie(response, token)


@app.post("/api/auth/login")
def login(credentials: Credentials) -> dict:
    user = demo_db.verify_login(credentials.username, credentials.password)
    if not user:
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    token, _ = demo_db.create_session(user)
    response = JSONResponse({"username": user["username"], "role": user["role"]})
    return set_session_cookie(response, token)


@app.post("/api/auth/logout")
def logout(request: Request) -> dict:
    # Sessions are stateless signed cookies, so clearing the cookie is the logout.
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/auth/me")
def me(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return {"username": user["username"], "role": user["role"]}


# ---- student panel ---------------------------------------------------------

def practice_questions() -> list[dict]:
    """The 43 questions the console already serves, trimmed for practice.

    Reads the built console dataset (whose answers reflect faculty-approved
    corrections) so the student panel never invents questions.
    """
    try:
        data = json.loads(CONSOLE_DATA.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail="Practice data is missing. Run the project pipeline to build it.",
        ) from error
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail="Console data is not valid JSON.") from error
    return [{
        "questionId": q["questionId"],
        "questionType": q["questionType"],
        "subject": q["subject"],
        "chapter": q["chapter"],
        "level": q["level"],
        "stem": q.get("stem") or "",
        "questionHtml": q["questionHtml"],
        "solutionHtml": q["solutionHtml"],
        "options": q["options"],
        "key": q["key"],
    } for q in data["questions"]]


@app.get("/api/practice/questions")
def get_practice(request: Request) -> dict:
    require_user(request)
    questions = practice_questions()
    subjects = sorted({q["subject"] for q in questions if q["subject"]})
    return {"subjects": subjects, "questions": questions}


@app.post("/api/queries")
def submit_query(payload: QueryRequest, request: Request,
                 background: BackgroundTasks) -> dict:
    """Record the query and return at once; the AI check runs after the response.

    The pipeline stages and the model-written reply take seconds to minutes, so
    they are queued as a background task. The student sees `submitted` first and
    gets the verdict (and the reply) on the next poll of `/api/queries/mine`.
    """
    user = require_user(request)
    known = {q["questionId"] for q in practice_questions()}
    if payload.questionId not in known:
        raise HTTPException(status_code=404, detail="Unknown question.")
    comment = (payload.comment or "").strip()
    if len(comment) < 5:
        raise HTTPException(status_code=400, detail="Describe the issue in a few words.")
    record = demo_db.add_query(payload.questionId, user["username"], comment)
    background.add_task(run_pipeline_in_background)
    return {"ok": True, "reportId": record["reportId"], "status": "submitted",
            "pipeline": {"queued": True}, "counts": demo_db.query_counts()}


@app.get("/api/queries/mine")
def my_queries(request: Request) -> dict:
    user = require_user(request)
    return {"queries": demo_db.queries_by_student(demo_db.student_id_for(user["username"]))}


# ---- pipeline --------------------------------------------------------------

def run_pipeline_in_background() -> None:
    """Background wrapper: never let a pipeline error escape into the server loop.

    A failure leaves the query at `submitted`, so the next student poll or the
    faculty pipeline button retries it. Nothing is silently marked resolved.
    """
    try:
        run_pipeline_job()
    except HTTPException as error:
        print(f"background pipeline failed: {error.detail}")
    except Exception as error:  # noqa: BLE001 - logged, not swallowed silently
        print(f"background pipeline failed: {error}")


def run_pipeline_job() -> dict:
    """Sync new queries into data/reports.json and run the offline stages.

    Every stage is invoked unchanged as its own script, exactly like run.py
    does; only the console build (already imported here) is called in-process.
    """
    with pipeline_lock:
        synced = demo_db.sync_queries_to_reports()
        if synced:
            for script in PIPELINE_STAGES:
                result = subprocess.run(
                    [sys.executable, str(PROJECT / script)],
                    cwd=PROJECT, capture_output=True, text=True)
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip().splitlines()
                    message = detail[-1] if detail else f"exit {result.returncode}"
                    raise HTTPException(
                        status_code=500,
                        detail=f"Pipeline stage {Path(script).name} failed: {message}")
        refresh_console()
        applied = update_query_statuses_from_console()
    return {"synced": synced, "statusUpdates": applied, **demo_db.query_counts()}


def update_query_statuses_from_console() -> int:
    """Map each still-pending query to a verdict so students see where it landed.

    Defect verdicts mean the AI judged the query genuine and it is waiting in
    the faculty queue; every other verdict closes the query and gets a
    model-written closing reply. Closed queries that predate the reply stage
    are backfilled on the next run.
    """
    try:
        data = json.loads(CONSOLE_DATA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    verdicts = {g["questionId"]: g.get("verdict", "") for g in data.get("groups", [])}
    updated = 0
    for query in demo_db.pending_status_queries():
        verdict = verdicts.get(query["question_id"])
        if verdict is None:
            continue
        if verdict == "defect":
            demo_db.update_query_status(query["report_id"], "sent_to_faculty", verdict)
        else:
            _close_with_reply(query["report_id"], query["question_id"],
                              query["comment"], verdict)
        updated += 1
    for query in demo_db.closed_queries_without_reply():
        _close_with_reply(query["report_id"], query["question_id"],
                          query["comment"], verdicts.get(query["question_id"], ""))
    return updated


def _close_with_reply(report_id: str, question_id: str, comment: str, verdict: str) -> None:
    """Close a query and attach its model-written reply (empty when the LLM is unavailable).

    The reply is generated by the same reply stage the pipeline uses - no
    hardcoded text - and a failure only means the reply is written later.
    """
    reply_text = ""
    try:
        reply_text = student_reply.generate_closed_reply(
            comment, verdict,
            student_reply.question_for(question_id),
            solve=student_reply.solve_for(question_id))
    except Exception as error:  # no key, network or validation failure: reply comes later
        print(f"closing reply for {report_id} deferred: {error}")
    demo_db.update_query_status(report_id, "closed_by_ai", verdict, reply_text)


def refresh_console() -> None:
    try:
        result = build_console_data.main()
    except SystemExit as error:
        detail = error.code if isinstance(error.code, str) else "Console data could not be rebuilt."
        raise HTTPException(status_code=503, detail=detail) from error
    if result != 0:
        raise HTTPException(status_code=500, detail="Console data could not be rebuilt.")


def ensure_pending(case_id: str) -> None:
    if not AUDIT.exists():
        return
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail="Review audit log is not valid JSONL.") from error
    if any(entry.get("caseId") == case_id and entry.get("action") in {"approve", "reject"}
           for entry in entries):
        raise HTTPException(status_code=409, detail=f"Case {case_id} already has a review decision.")


@app.post("/api/pipeline/run")
def pipeline_run(request: Request) -> dict:
    require_faculty(request)
    return run_pipeline_job()


# ---- faculty console -------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/console")
def get_console(request: Request) -> dict:
    require_faculty(request)
    try:
        return json.loads(CONSOLE_DATA.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=503,
            detail="Console data is missing. Run the project pipeline to build it.",
        ) from error
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=500, detail="Console data is not valid JSON.") from error


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, request: Request, review: ApproveRequest) -> dict:
    require_faculty(request)
    edit = None
    if review.edit is not None:
        edit = {
            "field": review.edit.field,
            "find": review.edit.find,
            "replace": review.edit.replace,
        }

    with review_lock:
        ensure_pending(case_id)
        try:
            case = apply_edit.approve_case(
                case_id,
                review.reviewer,
                review.note,
                override=edit,
            )
        except SystemExit as error:
            detail = error.code if isinstance(error.code, str) else "Approval was rejected."
            raise HTTPException(status_code=409, detail=detail) from error
        refresh_console()
    return {"case": case}


@app.post("/api/cases/{case_id}/reject")
def reject(case_id: str, request: Request, review: ReviewRequest) -> dict:
    require_faculty(request)
    with review_lock:
        ensure_pending(case_id)
        try:
            case = apply_edit.reject_case(case_id, review.reviewer, review.note)
        except SystemExit as error:
            detail = error.code if isinstance(error.code, str) else "Rejection was rejected."
            raise HTTPException(status_code=409, detail=detail) from error
        refresh_console()
    return {"case": case}


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
