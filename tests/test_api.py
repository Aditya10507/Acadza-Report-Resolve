import json

import pytest
from fastapi.testclient import TestClient

from Backend import api, demo_db
from Backend.Reply import apply_edit, student_reply

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def demo_db_and_faculty_session(tmp_path, monkeypatch):
    """Point the demo database at a temp file and log the client in as faculty."""
    monkeypatch.setattr(demo_db, "DB_PATH", tmp_path / "demo.sqlite3")
    monkeypatch.setattr(demo_db, "REPORTS_JSON", tmp_path / "reports.json")
    demo_db.init_db()
    client.cookies.clear()
    response = client.post("/api/auth/login",
                           json={"username": "faculty", "password": "faculty123"})
    assert response.status_code == 200
    yield
    client.cookies.clear()


@pytest.fixture
def review_files(tmp_path, monkeypatch):
    question_id = "question-1"
    case_id = question_id
    store = {
        question_id: {
            "_id": question_id,
            "questionType": "scq",
            "scq": {"answer": "B", "question": "Question"},
        }
    }
    cases = {
        "cases": [{
            "caseId": case_id,
            "questionId": question_id,
            "repair": "answer_key",
            "status": "pending_review",
            "suggestedEdit": {"field": "answer", "find": "B", "replace": "A"},
            "reports": [],
        }]
    }
    taxonomy = {"repairs": {"answer_key": {"fields": ["answer"], "auto": True}}}
    live_path = tmp_path / "live_questions.json"
    cases_path = tmp_path / "verification_cases.json"
    taxonomy_path = tmp_path / "taxonomy.json"
    audit_path = tmp_path / "audit_log.jsonl"
    live_path.write_text(json.dumps(store), encoding="utf-8")
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    taxonomy_path.write_text(json.dumps(taxonomy), encoding="utf-8")

    monkeypatch.setattr(apply_edit, "LIVE", live_path)
    monkeypatch.setattr(apply_edit, "CASES", cases_path)
    monkeypatch.setattr(apply_edit, "TAXONOMY", taxonomy_path)
    monkeypatch.setattr(apply_edit, "AUDIT", audit_path)
    monkeypatch.setattr(api, "AUDIT", audit_path)
    monkeypatch.setattr(api, "refresh_console", lambda: None)
    return question_id, live_path, cases_path, audit_path


def test_health_and_console_data(tmp_path, monkeypatch):
    console_path = tmp_path / "console.json"
    console_path.write_text('{"reports": []}', encoding="utf-8")
    monkeypatch.setattr(api, "CONSOLE_DATA", console_path)

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/console").json() == {"reports": []}


def test_console_requires_faculty_role():
    client.cookies.clear()
    assert client.get("/api/console").status_code == 401

    client.post("/api/auth/login", json={"username": "student", "password": "student123"})
    assert client.get("/api/console").status_code == 403

    client.post("/api/auth/login", json={"username": "faculty", "password": "faculty123"})
    assert client.get("/api/console").status_code == 200


def test_signup_login_and_rejects_duplicates():
    client.cookies.clear()
    created = client.post("/api/auth/signup", json={
        "username": "priya_12", "password": "pass1234", "role": "student"})
    assert created.status_code == 200
    assert created.json()["role"] == "student"
    assert client.get("/api/auth/me").json()["username"] == "priya_12"

    duplicate = client.post("/api/auth/signup", json={
        "username": "priya_12", "password": "pass1234", "role": "student"})
    assert duplicate.status_code == 400
    assert "already taken" in duplicate.json()["detail"]

    bad_login = client.post("/api/auth/login", json={
        "username": "priya_12", "password": "wrong"})
    assert bad_login.status_code == 401


def test_practice_questions_match_the_built_dataset():
    response = client.get("/api/practice/questions")
    assert response.status_code == 200
    data = response.json()
    console = json.loads(api.CONSOLE_DATA.read_text(encoding="utf-8"))
    assert len(data["questions"]) == len(console["questions"])
    assert set(data["subjects"]) == {q["subject"] for q in console["questions"] if q["subject"]}
    assert all(set(q) == {"questionId", "questionType", "subject", "chapter",
                          "level", "stem", "questionHtml", "solutionHtml", "options", "key"}
               for q in data["questions"])
    assert all(q["stem"] for q in data["questions"])


def test_student_query_is_recorded_and_synced(tmp_path, monkeypatch):
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "student", "password": "student123"})

    unknown = client.post("/api/queries", json={"questionId": "nope", "comment": "hello there"})
    assert unknown.status_code == 404

    short = client.post("/api/queries", json={"questionId": "60f744b3f1b9bc7cc7b4a949", "comment": "hi"})
    assert short.status_code == 400

    def fake_pipeline():
        synced = demo_db.sync_queries_to_reports()
        return {"synced": synced, "statusUpdates": 0, "total": 1,
                "pending_sync": 0, "with_faculty": 0}

    monkeypatch.setattr(api, "run_pipeline_job", fake_pipeline)
    response = client.post("/api/queries", json={
        "questionId": "60f744b3f1b9bc7cc7b4a949",
        "comment": "answer key looks wrong, I got a different answer",
    })
    assert response.status_code == 200
    assert response.json()["reportId"].startswith("R")

    reports = json.loads(demo_db.REPORTS_JSON.read_text(encoding="utf-8"))
    assert reports[-1]["questionId"] == "60f744b3f1b9bc7cc7b4a949"
    assert reports[-1]["source"] == "student portal"

    mine = client.get("/api/queries/mine").json()["queries"]
    assert mine and mine[0]["comment"].startswith("answer key")


def test_closed_queries_get_a_model_written_reply(tmp_path, monkeypatch):
    question_id = "60f744b3f1b9bc7cc7b4a949"
    demo_db.add_query(question_id, "student", "this answer is wrong")
    record = demo_db.queries_by_student(demo_db.student_id_for("student"))[0]
    demo_db.mark_synced([record["report_id"]])

    console_path = tmp_path / "console.json"
    console_path.write_text(json.dumps({
        "groups": [{"questionId": question_id, "verdict": "no_error"}],
    }), encoding="utf-8")
    monkeypatch.setattr(api, "CONSOLE_DATA", console_path)
    monkeypatch.setattr(api.student_reply, "generate_closed_reply",
                        lambda comment, verdict, question, solve=None: f"model wrote: {verdict}")

    assert api.update_query_statuses_from_console() == 1
    row = demo_db.queries_by_student(demo_db.student_id_for("student"))[0]
    assert row["status"] == "closed_by_ai"
    assert row["verdict"] == "no_error"
    assert row["reply"] == "model wrote: no_error"

    # a query closed before the reply stage existed is backfilled on the next run
    demo_db.update_query_status(row["report_id"], "closed_by_ai", "no_error", "")
    assert api.update_query_statuses_from_console() == 0
    row = demo_db.queries_by_student(demo_db.student_id_for("student"))[0]
    assert row["reply"] == "model wrote: no_error"


def test_closing_reply_validator_keeps_tone_positive():
    good = student_reply.validate_closed_reply({"reply":
        "Thanks for taking the time to report this! Our check found no error in the "
        "question - the stored key (A) is correct, so the confusion may be a display or "
        "reading issue on your side. Please re-read the question and your working before "
        "reporting further, and keep the reports coming."})
    assert good.startswith("Thanks")

    for bad in (
        {"reply": "You are wrong, the key is obviously correct. Stop reporting nonsense."},
        {"reply": "Sorry for the confusion, we will fix it soon."},
        {"reply": "Checked. Nothing found."},  # too short, no appreciation, no nudge
    ):
        with pytest.raises(ValueError):
            student_reply.validate_closed_reply(bad)


def test_approve_uses_existing_edit_gate_and_audit(review_files):
    question_id, live_path, cases_path, audit_path = review_files
    response = client.post(
        f"/api/cases/{question_id}/approve",
        json={
            "reviewer": "Faculty",
            "note": "checked independently",
            "edit": {"field": "answer", "find": "B", "replace": "A"},
        },
    )

    assert response.status_code == 200
    assert response.json()["case"]["status"] == "applied"
    assert json.loads(live_path.read_text(encoding="utf-8"))[question_id]["scq"]["answer"] == "A"
    assert json.loads(cases_path.read_text(encoding="utf-8"))["cases"][0]["status"] == "applied"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["action"] == "approve"
    assert audit["before"] == "B"
    assert audit["after"] == "A"


def test_approve_rejects_out_of_scope_edit(review_files):
    question_id, live_path, _, _ = review_files
    response = client.post(
        f"/api/cases/{question_id}/approve",
        json={
            "reviewer": "Faculty",
            "edit": {"field": "solution", "find": "Question", "replace": "Changed"},
        },
    )

    assert response.status_code == 409
    assert "may only touch" in response.json()["detail"]
    assert json.loads(live_path.read_text(encoding="utf-8"))[question_id]["scq"]["answer"] == "B"


def test_reject_records_review_and_blocks_duplicate(review_files):
    question_id, _, cases_path, audit_path = review_files
    response = client.post(
        f"/api/cases/{question_id}/reject",
        json={"reviewer": "Faculty", "note": "not reproducible"},
    )

    assert response.status_code == 200
    assert response.json()["case"]["status"] == "rejected"
    assert json.loads(cases_path.read_text(encoding="utf-8"))["cases"][0]["status"] == "rejected"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["action"] == "reject"

    duplicate = client.post(
        f"/api/cases/{question_id}/reject",
        json={"reviewer": "Faculty", "note": "duplicate"},
    )
    assert duplicate.status_code == 409
