# Deployment guide

The app is one FastAPI service that serves both the API and the frontend
(`login.html`, `student.html`, `index.html`) from the same origin, so login
cookies and every fetch just work — no CORS or cookie changes needed.

**Recommended:** deploy the whole app (backend + frontend) as a Voroa web
service. Vercel is optional; see the last section for what it can add.

---

## Deploy the full app on Voroa (getvoroa.com)

Voroa builds from GitHub, so the code must be on GitHub first
(this repo is already pushed and `.env` / `data/demo.sqlite3` are gitignored —
no secrets are committed).

1. Sign in at <https://app.getvoroa.com> and **Connect GitHub** (grant access to
   the repository — private repos work).
2. **New service → Web service**, pick the repository and branch (`main`).
3. Leave **Root directory** as `/`.
4. Commands:
   - **Build command:** *(leave empty — Voroa installs `requirements.txt` itself)*
   - **Start command:**
     ```
     uvicorn Backend.api:app --host 0.0.0.0 --port $PORT
     ```
5. **Settings → Health check path:** `/api/health`
6. **Environment** (optional — the demo runs without keys): paste the same
   variables as in `.env.example` — `SOLVER_API_KEY`, `SOLVER_BASE_URL`,
   `SOLVER_MODEL`, `REPLY_API_KEY`, `REPLY_BASE_URL`, `REPLY_MODEL`.
7. **Deploy.** Live at `https://<service-name>.onvoroa.com` with HTTPS.

Test accounts (seeded automatically on first boot):
`student / student123` · `faculty / faculty123`

Pages: `/login.html` · `/student.html` · `/` (faculty console).

### Free-tier behaviour to expect

| Behaviour | What it means for the demo |
|---|---|
| Sleeps after 15 min idle, wakes on next request | First visitor may see a short "starting" page; everything works after the wake-up |
| Filesystem resets on every deploy | Raised queries / new accounts reset to the committed demo state. Seed accounts are re-created on boot, and closed queries get their LLM replies backfilled on the next **Process queries** click |
| Single instance, 512 MB, Mumbai region | Plenty for this app; SQLite + JSON stay on the local disk |

If you want raised queries to survive redeploys, add a **persistent disk**
(paid) mounted at a path and point `demo_db.py`'s DB file there — not needed
for a recruiter demo.

### Updating after deploy

Turn on **auto-deploy** in the service settings; every `git push` to the
deploy branch ships a new build. Rollbacks are available under Deploys.

---

## What about Vercel?

Because the chosen mode is *full app on the backend*, the frontend is already
served by Voroa and Vercel has no required role. Options if you still want a
Vercel presence:

1. **Redirect landing (recommended, zero risk):** a Vercel project whose only
   job is redirecting visitors to the Voroa URL. Log-in, cookies and all
   requests stay same-origin on Voroa.
2. **Reverse-proxy rewrite:** a `vercel.json` with a rewrite of `/(.*)` to the
   Voroa URL. The browser stays on the Vercel domain while Voroa serves
   everything. Verify cookies behave as expected before sharing.
3. **True static split** (frontend on Vercel, API on Voroa) is **not
   recommended** for this app: login uses an HttpOnly cookie, which browsers
   treat as third-party on a cross-origin call (Safari blocks it outright).
   Supporting it would require `SameSite=None` cookies, `credentials:
   "include"` in every fetch and CORS configuration — more moving parts than
   the demo needs.

---

## Run on your own machine / LAN

```bash
pip install -r requirements.txt
python run.py                      # pipeline + build + serve on 127.0.0.1:8080
python run.py --host 0.0.0.0       # share with devices on the same network
```
