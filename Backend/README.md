# Backend

The triage pipeline remains three standalone scripts with JSON inputs and outputs, so each
stage can be re-run or inspected on its own. FastAPI serves the faculty console and routes
review actions through the existing JSON-backed edit and audit workflow.

| # | File | What it does | Output |
|---|---|---|---|
| 1 | `decision_layer.py` | Groups every report by question and collapses comments of the same kind into one cluster | `work/grouped_reports.json` |
| 2 | `llm_solver.py` | Solves each reported question **independently** - key, solution and comments are never in the prompt | `work/solved_answers.json` |
| 3 | `classifier.py` | Compares solve vs key vs solution vs clustered complaints, maps to a taxonomy verdict | `work/classification.json` |

`scripts/normalize.py` is a prerequisite, not a fourth stage: it turns the production
HTML/MathML into the clean records all three stages read.

## Run order

```bash
python Backend/scripts/normalize.py     # data/ -> work/questions.normalized.json
python Backend/decision_layer.py        # groups + clusters
python Backend/llm_solver.py            # needs LLM_API_KEY
python Backend/classifier.py            # verdicts + routes
```

Or use the launcher at the repo root, which runs the offline pipeline, refreshes the
console dataset and serves the faculty console in one command:

```bash
pip install -r requirements.txt
python run.py                # offline stages + build Backend/work/console.json + serve :8080
python run.py --solve        # additionally run pending independent solves (resumable, needs the key)
python run.py --replies      # additionally generate missing student replies (needs the key)
python run.py --full         # re-run both LLM stages from scratch
python run.py --port 9000 --serve-only  # serve API + console without running the pipeline
```

The launcher serves the static console and its same-origin FastAPI API together. The API
reads the built console dataset and sends faculty approvals/rejections through the existing
`Reply/apply_edit.py` validation and JSON audit workflow. Pipeline and LLM stages remain
CLI-driven.

Credentials for stages 2 and 6. Each stage reads its own key first, so the solver and the
reply writer can use different providers, models or budgets; a single shared key still
works as a fallback.

```
# stage 2 - independent solving (deterministic, temperature 0)
SOLVER_API_KEY=sk-...
SOLVER_BASE_URL=https://api.openai.com/v1   # optional
SOLVER_MODEL=gpt-4o-mini                    # optional

# stage 6 - student replies (warm, varied wording, temperature 0.6)
REPLY_API_KEY=sk-...
REPLY_BASE_URL=https://api.openai.com/v1    # optional
REPLY_MODEL=gpt-4o-mini                     # optional

# fallback only, used when SOLVER_API_KEY / REPLY_API_KEY are absent
LLM_API_KEY=sk-...
```

Two keys are worth it here: the solver batch (39 calls, retries, high reasoning) must not
exhaust the reply quota, the two stages want different models, and a leaked reply key
cannot spend solver budget.

`python Backend/llm_solver.py --dry-run` prints the exact prompt for one question without
calling anything - the quickest way to confirm the key and solution are absent.

## Why stage 2 is isolated

The solve is the only independent evidence in the pipeline. If the model can see the
stored key, it agrees with it; if it can see the solution, it copies the solution's
answer; if it can see the student's comment, it anchors on the complaint. Any of those
turns the comparison in stage 3 into a mirror. So `build_user_prompt()` accepts the stem
and options as separate arguments - the answer field has no path into the prompt.

## Safety in the prompt

`SYSTEM_PROMPT` in `llm_solver.py` treats question text as untrusted data, refuses
instruction-like content inside it, forbids role changes and prompt disclosure, and
requires a single JSON object. The reply is validated against the printed options and
the question type before it is stored; a malformed reply is retried once, then skipped.

## Ground truth (Phase 1)

`work/labels.json` is the hand-solved truth for all 44 reports; `scripts/check_labels.py`
validates it mechanically. It is the yardstick stage 3 is scored against, not an input to
the pipeline.

## Review and reply (`Backend/Reply/`)

| # | File | What it does | Output |
|---|---|---|---|
| 4 | `Reply/verification.py` | Turns a classified defect into a reviewable case: current value, independent solve, plain-language error explanation, before/after diff, student reports, severity | `work/verification_cases.json` |
| 5 | `Reply/apply_edit.py` | Faculty gate. Re-validates the edit against repair scope, writes it to the live store, records the audit entry, refuses a second apply | `work/live_questions.json`, `work/audit_log.jsonl` |
| 6 | `Reply/student_reply.py` | Model-generated reply per report, in two sections: student's mistake and app's mistake | `work/student_replies.json` |

`apply_edit.py` is deliberately CLI-first: the console will call the same
`approve_case()` / `reject_case()` functions, so the approval gate and audit trail are
identical whether a human uses the terminal or the UI.

```bash
python Backend/Reply/verification.py
python Backend/Reply/apply_edit.py --list
python Backend/Reply/apply_edit.py --approve <caseId> --reviewer "Dr Rao" --note "checked"
python Backend/Reply/student_reply.py
```

## Demo login, student panel and SQLite

`Backend/demo_db.py` adds one small SQLite database (`data/demo.sqlite3`) on top of the
JSON pipeline — it stores demo accounts, sessions and student queries only; every
pipeline artifact stays in JSON.

- `python run.py` seeds two demo accounts automatically:
  `student / student123` and `faculty / faculty123`. Signup (any role) is at `/login.html`.
- The student panel (`/student.html`) lists the **same 43 questions** the console serves
  (no invented content), grouped by subject, for practice plus a **Report question**
  button that raises a query with a short statement.
- A submitted query is appended to `data/reports.json` (continuing the `R###` ids) and
  the existing offline pipeline runs unchanged:
  `normalize -> decision_layer -> classifier -> verification -> build_console_data`.
  Verdicts the classifier does not mark as `defect` are closed automatically; genuine
  queries surface in the faculty queue.
- API: `/api/auth/{signup,login,logout,me}`, `/api/practice/questions`, `/api/queries`,
  `/api/queries/mine`, and faculty-only `/api/pipeline/run` (also exposed as the
  **Process queries** button in the console). `/api/console` and `/api/cases/*` require
  a faculty session; students get 403.
- Closing replies: when the AI closes a query (verdict other than `defect`),
  `student_reply.generate_closed_reply()` writes the student's reply with the LLM -
  positive, appreciative, asks the student to re-check before reporting further. The
  wording is never templated or hardcoded; a reply the tone validator rejects is
  regenerated (3 attempts), and a query closed while the model is unavailable gets its
  reply backfilled on the next pipeline run.

## Status

Stages 1-4 are implemented. Stages 2 (`llm_solver.py`) and 6 (`student_reply.py`) call an
LLM and have not been run here because this environment has no API key; everything else
runs offline.

Verified offline:

- stage 3 against the Phase 1 labels: 43/44 verdicts, 43/44 routes (placeholder solves)
- stage 4: 20 defect cases, 3 with a ready edit, 17 flagged as needing an authored fix
- stage 5: approve writes the live store, appends `errorHistory`, logs the audit entry, and
  refuses a second approval; reject records the reason
- stage 6: prompt dry-run shows the facts and delimiters; reply validation blocks wrong
  categories, markdown, and model-disclosure phrases. No template fallback exists by design.

`student_reply.py` has no hardcoded replies: without a key it stops and tells you to set
one rather than inventing text.
