"""SQLite storage for the demo login and student-query workflow.

One small database (data/demo.sqlite3) holds everything the demo adds on top
of the JSON pipeline:

  users     - student / faculty accounts (signup + seeded demo accounts)
  sessions  - cookie tokens for the demo login
  queries   - questions a student reported from the practice panel; these are
              synced into data/reports.json (reportId/questionId/comment/...)
              so the existing pipeline picks them up unchanged.

Stdlib sqlite3 only. Pipeline artifacts stay in JSON - this database only
records demo state.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT / "data" / "demo.sqlite3"
REPORTS_JSON = PROJECT / "data" / "reports.json"

SESSION_DAYS = 7
ROLES = ("student", "faculty")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                role          TEXT NOT NULL CHECK (role IN ('student','faculty')),
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS queries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id   TEXT NOT NULL UNIQUE,
                question_id TEXT NOT NULL,
                student_id  TEXT NOT NULL,
                comment     TEXT NOT NULL,
                reported_at TEXT NOT NULL,
                synced      INTEGER NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'submitted',
                verdict     TEXT NOT NULL DEFAULT '',
                reply       TEXT NOT NULL DEFAULT ''
            );
        """)
        _migrate(conn)
        _seed_demo_users(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight migration for databases created before a column existed."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(queries)")}
    if "reply" not in columns:
        conn.execute("ALTER TABLE queries ADD COLUMN reply TEXT NOT NULL DEFAULT ''")


def _seed_demo_users(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    for username, password, role in [
        ("faculty", "faculty123", "faculty"),
        ("student", "student123", "student"),
    ]:
        create_user(username, password, role, conn=conn)


# ---- passwords (PBKDF2, demo-appropriate) ---------------------------------

def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()


def create_user(username: str, password: str, role: str,
                conn: sqlite3.Connection | None = None) -> dict:
    if role not in ROLES:
        raise ValueError("role must be 'student' or 'faculty'")
    if not re.fullmatch(r"\w{3,24}", username or ""):
        raise ValueError("username must be 3-24 letters, digits or underscores")
    if len(password or "") < 4:
        raise ValueError("password must be at least 4 characters")
    salt = secrets.token_bytes(16)
    now = datetime.now(timezone.utc).isoformat()
    own = conn is None
    conn = conn or connect()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, _hash(password, salt), salt.hex(), role, now))
        if own:
            conn.commit()
        return {"id": cur.lastrowid, "username": username, "role": role}
    except sqlite3.IntegrityError as error:
        if own:
            conn.rollback()
        raise ValueError("username is already taken") from error
    finally:
        if own:
            conn.close()


def verify_login(username: str, password: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username or "",)).fetchone()
    if not row or _hash(password or "", bytes.fromhex(row["salt"])) != row["password_hash"]:
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


# ---- sessions --------------------------------------------------------------

def create_session(user_id: int) -> tuple[str, str]:
    token = secrets.token_hex(16)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(),
             (now + timedelta(days=SESSION_DAYS)).isoformat()))
    return token, (now + timedelta(days=SESSION_DAYS)).isoformat()


def session_user(token: str | None) -> dict | None:
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT u.id, u.username, u.role, s.expires_at FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,)).fetchone()
    if not row or row["expires_at"] < datetime.now(timezone.utc).isoformat():
        return None
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def delete_session(token: str | None) -> None:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ---- student queries -------------------------------------------------------

def student_id_for(username: str) -> str:
    """Stable pipeline-shaped student id, e.g. stu_priya_12."""
    return "stu_" + re.sub(r"\W+", "_", (username or "").lower()).strip("_") or "stu_unknown"


def next_report_id() -> str:
    """Continue the R-numbering used by the existing data/reports.json."""
    highest = 0
    if REPORTS_JSON.exists():
        for report in json.loads(REPORTS_JSON.read_text(encoding="utf-8")):
            match = re.fullmatch(r"R(\d+)", str(report.get("reportId", "")))
            if match:
                highest = max(highest, int(match.group(1)))
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(CAST(SUBSTR(report_id, 2) AS INTEGER)) AS n FROM queries").fetchone()
    return f"R{max(highest, row['n'] or 0) + 1:03d}"


def add_query(question_id: str, student_username: str, comment: str) -> dict:
    report_id = next_report_id()
    now = datetime.now(timezone.utc).isoformat()
    student_id = student_id_for(student_username)
    with connect() as conn:
        conn.execute(
            "INSERT INTO queries (report_id, question_id, student_id, comment, reported_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (report_id, question_id, student_id, comment.strip(), now))
    return {"reportId": report_id, "questionId": question_id,
            "studentId": student_id, "comment": comment.strip(),
            "reportedAt": now, "source": "student portal"}


def unsynced_queries() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM queries WHERE synced = 0 ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def mark_synced(report_ids: list[str]) -> None:
    if not report_ids:
        return
    with connect() as conn:
        marks = ",".join("?" for _ in report_ids)
        conn.execute(
            f"UPDATE queries SET synced = 1 WHERE report_id IN ({marks})", report_ids)


def update_query_status(report_id: str, status: str, verdict: str = "",
                        reply: str | None = None) -> None:
    """Record a verdict; `reply` is only written when explicitly provided."""
    with connect() as conn:
        if reply is None:
            conn.execute("UPDATE queries SET status = ?, verdict = ? WHERE report_id = ?",
                         (status, verdict, report_id))
        else:
            conn.execute(
                "UPDATE queries SET status = ?, verdict = ?, reply = ? WHERE report_id = ?",
                (status, verdict, reply, report_id))


def queries_by_student(student_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT report_id, question_id, comment, reported_at, status, verdict, reply "
            "FROM queries WHERE student_id = ? ORDER BY id DESC",
            (student_id,)).fetchall()
    return [dict(row) for row in rows]


def closed_queries_without_reply() -> list[dict]:
    """Closed queries that still need their model-written closing reply."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT report_id, question_id, comment FROM queries "
            "WHERE status = 'closed_by_ai' AND reply = '' ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def pending_status_queries() -> list[dict]:
    """Queries still waiting for the pipeline to give them a verdict."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT report_id, question_id, comment, status FROM queries "
            "WHERE status = 'submitted' ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def query_counts() -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN synced = 0 THEN 1 ELSE 0 END) AS pending_sync, "
            "SUM(CASE WHEN status = 'sent_to_faculty' THEN 1 ELSE 0 END) AS with_faculty "
            "FROM queries").fetchone()
    return {key: row[key] or 0 for key in ("total", "pending_sync", "with_faculty")}


def sync_queries_to_reports() -> int:
    """Append unsynced queries to data/reports.json in the pipeline's shape.

    The pipeline (normalize.py onwards) reads data/reports.json and is left
    untouched; this only adds the new reports to it. Returns how many were added.
    """
    new_queries = unsynced_queries()
    if not new_queries:
        return 0
    existing = (json.loads(REPORTS_JSON.read_text(encoding="utf-8"))
                if REPORTS_JSON.exists() else [])
    known = {report["reportId"] for report in existing}
    added = []
    for query in new_queries:
        if query["report_id"] in known:
            continue
        added.append({
            "reportId": query["report_id"],
            "questionId": query["question_id"],
            "studentId": query["student_id"],
            "source": "student portal",
            "reportedAt": query["reported_at"],
            "comment": query["comment"],
        })
    if added:
        REPORTS_JSON.write_text(
            json.dumps(existing + added, indent=1, ensure_ascii=False), encoding="utf-8")
    mark_synced([query["report_id"] for query in new_queries])
    return len(added)
