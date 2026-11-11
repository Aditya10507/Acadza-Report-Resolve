# Tests

| File | What it checks | How to run |
|---|---|---|
| `test_api.py` | Demo auth (signup/login, roles, faculty-only console), practice questions, student query recording + sync, approve/reject persistence through the existing edit gate, audit writes, and duplicate/out-of-scope rejection. | `python -m pytest tests/test_api.py` |
| `static_sanity.js` | Every DOM id referenced by `Frontend/app.js` exists in `Frontend/index.html` (dynamic ids excluded), and `Backend/work/console.json` carries every key the console renders — the full builder/UI contract. | `node tests/static_sanity.js` |
| `frontend_smoke.mjs` | Faculty workflow: only unresolved defects appear, student reports and AI score are visible, and approve/reject actions persist through the API. | `npm i jsdom && node tests/frontend_smoke.mjs` |
| `frontend_smoke2.mjs` | Authored-fix workflow: field scope, exact-match validation, confirmation, and API persistence. | needs `jsdom` as above |
| `login_smoke.mjs` | Demo login page: tabs and role chooser wire up, student/faculty sign-in navigates to the right panel, bad credentials surface an error and re-enable the button. | needs `jsdom` as above |

`jsdom` is a dev-only dependency for the runtime tests (`npm i --no-save jsdom` in any
folder, or `npm i jsdom` here); the console itself has zero dependencies and does not
need Node to run or deploy.
