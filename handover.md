# JobApplier handover

Last updated: 2026-10-05 (America/Chicago).
Project root: `/Users/mustafaomran/Documents/JobApplier`.

## UPDATE 2026-10-08 — v2 restructure complete (Profile · Tracker · Advisor · AI Applier · Autonomous · Email · Contacts)

Plan: `/Users/mustafaomran/.claude/plans/pasted-content-id-d894-jobapplier-abundant-truffle.md`. User guide: `docs/USER_GUIDE.md`.
76 tests pass; frontend builds. AI: Codex (ChatGPT Plus login) primary, Qwen local fallback; Claude key optional.

- **Master profile**: `data/profile/profile.md` (fixed H2 sections; `- Key: value` lines in Identity/Links/Answers feed the
  answer bank) + `data/profile/history/`. `jobapplier/profile.py` parses/appends; `workflow.ingest_document` proposes additions
  per uploaded document (kv `ingest:<doc_id>`), `POST /api/profile/append` writes them. Legacy kv `profile` migrated on first run.
- **Companies**: `companies` table (`store.company_for/link_job_company`), setup question "How do you sign in at X?" asked once
  (`store.setup_questions`), answers map to company fields; saved login in Keychain `company:<id>` (`security.company_credential`).
- **Tracker mode**: `POST /api/jobs/log` records applications you submitted yourself (status `applied`, mode `manual`).
- **Advisor**: `workflow.advise` → status `advised`; Advice tab (skills LaTeX, tailored .tex/PDF, letter PDF, likely questions,
  company research). Resume builder (`documents.build_resume`) renders category skills in the resume's style, keeps the
  Languages line, enforces one page by trimming; `build_letter` makes the cover-letter PDF; `main_tex` resolves the main file.
- **AI Applier**: company-question gating, code-driven sign-in (`browser.detect_auth/signin/signup/google_chooser`,
  `workflow.handle_auth`), hand-off questions go to `POST /api/jobs/{id}/answer` (saved to profile Answers), accepted experience
  edits via `POST /api/jobs/{id}/edits` (applied to the tailored copy only).
- **Autonomous**: `workflow.autonomous` (`POST /api/jobs/{id}/autonomous`): preconditions, daily cap, delay with Pause, then
  `_submit_batch`. Verified only through the park path on a real job (never submitted anything).
- **Email monitor**: `jobapplier/monitor.py` + `tracker/Code.gs` actions `mail`/`text` (GmailApp read-only, MailApp to carrier
  gateway). **The user's deployed script is the old version** — they must paste the new `Code.gs` and deploy a new version
  (same URL) and authorize Gmail once. Until then the mail check returns 0 messages.
- **Contacts**: `jobapplier/contacts.py` (DuckDuckGo HTML search, company pages, LinkedIn people search read-only in the app's
  Chrome with limits in `LIMITS`; kv `linkedin_quota`), `POST /api/contacts/find`. LinkedIn needs a one-time manual login in
  that Chrome profile.
- Live state: 5 jobs; Anduril is `advised` → then `needs_attention` with the in-person question pending; Anduril company
  sign-in answered "No account needed". Tracker sheet connected and synced.

## UPDATE 2026-10-05 — Claude replaces OpenAI (supersedes OpenAI details below)

- **OpenAI removed.** Providers are `anthropic` (primary) and `ollama`. Default model `claude-sonnet-5-5`, effort `low`, via the official `anthropic` SDK (1.11, in `.venv` and `pyproject.toml`). Key: Keychain `anthropic_key` or `ANTHROPIC_API_KEY` (env / root `.env`). **No Anthropic key is stored yet** — the user must add one in Settings.
- Requests cache the system prompt + stable context (profile/job/answers) and opt into server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`).
- Local fallback triggers only on HTTP 402 `billing_error` (or a 400 "credit balance" message). It runs text tasks (research, profile, skills, cover letter); **browser form decisions (`local_ok=False`) pause** instead.
- Legacy saved `provider: "openai"` auto-migrates; live DB is now `anthropic` / `claude-sonnet-5-5`; Sheets/LaTeX settings preserved.
- **Qwen `qwen3.5:9b-q4_K_M` is installed** (6.6 GB). Real fallback smoke test (mocked 402 → real Ollama) passed: `{"ok": true}` in ~22 s; browser decision paused as designed.
- Verification: 41 tests pass, ruff clean, frontend built. `.claude/launch.json` starts the server on 127.0.0.1:8765.
- Confirmed product vision and Phase 2/3 roadmap (tectonic + markers, PDF cover letter, acceptable experience edits, import open Chrome tabs, Google sign-in recording, tracker columns, read-only school Gmail polling + T-Mobile SMS-gateway texts; job discovery and LinkedIn deferred) are in `/Users/mustafaomran/.claude/plans/pasted-content-id-d894-jobapplier-abundant-truffle.md`.

## Core goal and latest user decision

Build one personal job-application assistant for Mustafa:

1. Read a resume PDF, Overleaf/LaTeX source, LinkedIn export, portfolio, transcript, and optional cover-letter template.
2. Maintain a human-verified profile and reusable answers. Never invent skills, experience, or eligibility.
3. Read job links, identify requirements, and tailor the skills section first. Experience rewrites should be minimal and remain suggestions unless explicitly adopted.
4. Prepare application forms in a dedicated Chrome session; hand off login, verification codes, CAPTCHA, assessments, and uncertain answers.
5. Show the exact documents, answers, changes, and a short job-specific summary for review.
6. Require approval before submission. Respect pending-referral holds, avoid duplicates, and never blindly retry uncertain submissions.
7. Track jobs, account email/sign-in method, application/portal links, outcomes, and referral/networking fields in the user's Google Sheet.

**Latest architecture decision:** one JobApplier app, OpenAI models as primary, Ollama Qwen as fallback when API credits/quota are exhausted. The user explicitly requested removal of the separate “New agent” / Agent session feature. Do not rebuild it. LinkedIn networking/outreach is deferred. No recruiter messaging is authorized by this task; do not infer religion from names or profiles.

The user's computer is a 2022 M2 MacBook Air with 16 GB RAM. The recommended local fallback is `qwen3.5:9b-q4_K_M`, initially at 16K context and one application at a time.

## Current architecture

- **Backend:** Python 3.11+ / FastAPI; installed `.venv` uses Homebrew Python 3.14.6.
- **Frontend:** React 19, TypeScript, Vite 6, lucide icons. Production assets are built into `frontend/dist/` and served by FastAPI.
- **Storage:** SQLite at `data/workspace.sqlite`; uploaded files, artifacts, browser profiles are under ignored `data/`. Preserve this user data.
- **Credentials:** macOS Keychain service `JobApplier`. OpenAI also reads `OPENAI_API_KEY` from environment or the root `.env` file. The secret lookup is cached; restart after changing `.env`. Never print or commit credentials.
- **Primary inference:** OpenAI Responses API through httpx, JSON output, `store: false`. No Agents API, saved agent definition, or hosted session is needed.
- **Fallback inference:** local Ollama `/api/chat`, JSON format, `think: false`, explicit context/output limits and local timeout.
- **Browser:** Playwright persistent dedicated Chrome profiles, bounded model actions, final-submit action separate from model-controlled filling/navigation.
- **Sheets:** direct Google OAuth Desktop-client integration; school-account identity check; column mapping, conservative row matching, formula/manual-note preservation, referral holds, legacy applied-row detection.
- **Local access:** workspace token, same-origin checks, local Host allowlist, restricted artifact downloads. Do not expose this server publicly.

### AI routing behavior implemented

- OpenAI is the code default, with `gpt-5.6-terra` as the default model; the previously selected live model was also `gpt-5.6-terra` and was preserved during implementation.
- Project defaults to `proj_1G2DgZyfaFz28GQfbDZBZAFi`; editable in Settings and sent as `OpenAI-Project`.
- `fallback_enabled: true`, `fallback_model: qwen3.5:9b-q4_K_M`.
- `ollama_context: 16384`, `ollama_timeout: 600` seconds.
- Fallback triggers on HTTP 429 with confirmed credit/quota/spend-limit codes or `error.type == insufficient_quota`.
- Ordinary rate limits, invalid keys, model-access failures, network errors, and malformed outputs do NOT silently switch providers.
- After a quota-triggered switch, the same `Model` instance uses Ollama for the remainder of that run. A new run tries the selected primary again.
- The fallback receives the same task/input, but never the OpenAI authorization header.
- Switches are recorded in activity events; Settings shows the last inference provider/model.
- If Ollama is missing/stopped or the model cannot run, preparation reports an actionable error. No browser submission is replayed.
- Incomplete hosted responses, truncated local responses, and invalid JSON are rejected.

## Latest verified state — important changes since the last final message

Read directly from the workspace while writing this file:

- **Persisted provider is now `ollama`, with `model: ""` (blank).** This differs from the OpenAI selection saved earlier in this session and likely reflects subsequent user interaction. Do not assume that the database still matches the code defaults. No setting was overwritten while writing this handover.
- Fallback is still enabled with Qwen 9B, 16,384 context, and 600-second timeout.
- The profile is marked verified. Existing documents include the resume PDF, `markdownresume.tex`, LinkedIn PDF, transcript, and multiple `portfolio.txt` supporting records. Do not re-import or delete these without inspecting them.
- Five jobs exist: NVIDIA, Anduril, Apple, Accenture, Google. All currently have `needs_attention` status. No real application submission has been validated by this work.
- Sheet sync is disabled, and no tab is mapped (`sheet_tab` is empty).
- LaTeX settings: main file `markdownresume.tex`; markers shown in the saved strings are `\\section{TECHNICAL SKILLS}` and `\\end{document}`. Verify the actual marker escaping and source boundaries before compiling. These broad boundaries may not be appropriate for preserving skills layout.
- The UI last reported no LaTeX compiler available. Resume tailoring/compilation still needs attended validation.
- Saved old agent/session IDs are both absent. There is no known remote agent created by our implementation to delete.
- **Disk is now approximately 13 GiB free** (`df -h .` at handover time). Earlier the Qwen download failed with “no space left on device” and only about 1.5 GiB remained. Space appears to have been freed since then; reassess before resuming.

### Live services and paths

- Dashboard: `http://127.0.0.1:8765`.
- Last backend process started as PID 22660, tool exec session 92174, with:
  `.venv/bin/python -m uvicorn jobapplier.app:app --host 127.0.0.1 --port 8765 --no-access-log`
- Treat process/session IDs as historical hints; verify before stopping/restarting anything.
- The Codex in-app browser last showed Settings in tab 5, marked as a deliverable. Browser handles may be stale next session; discover the current tab instead of assuming it survives.
- Ollama is installed, but its CLI was not on PATH. Use:
  `/Applications/Ollama.app/Contents/Resources/ollama`
- The last completed `ollama list` before downloading showed no models. Qwen installation never reached success; do not claim it is installed until checking again.

### Google account and tracker

Use school account **momran@utexas.edu**, nicknamed **school**, not `momran2401@gmail.com`.

Tracker: https://docs.google.com/spreadsheets/d/1h-FtdARuZdO4HKtX0taKzLf_vFyEgX0CBEDQCgUwh1Q/edit?gid=0

Codex's Google connection does not automatically authorize the standalone app. Earlier connector attempts returned 403, then unavailable/unknown-tool errors. Actual sheet content has not been verified/edited by this implementation. The app needs its own OAuth setup and mapping before sync.

### Seed job links

- NVIDIA: https://jobs.nvidia.com/careers/job/893397026226
- Anduril: https://job-boards.greenhouse.io/andurilindustries/jobs/5148101007?gh_jid=5148101007
- Apple: https://jobs.apple.com/en-us/details/200663981-3810/hardware-undergrad-engineering-internships?team=STDNT
- Accenture: https://www.accenture.com/us-en/careers/jobdetails?id=R00356105_en
- Google: https://www.google.com/about/careers/applications/jobs/results/112499004540887750-user-experience-engineer-intern-bsms-summer-2027

Apple was classified as an expression of interest. Earlier research noted an October 9, 2026 deadline for Google; recheck live listings before relying on that information.

## Files currently involved

No unfinished editor patch remains. These are the files changed or actively relevant to continuation, all relative to this project root.

### Changed in the latest simplification/fallback work

| File | Current responsibility/change |
| --- | --- |
| `jobapplier/ai.py` | OpenAI-first inference, quota classification, per-run local fallback, project header, explicit Ollama context/timeout, usage and last-provider reporting, output rejection. |
| `jobapplier/app.py` | Removed all agent routes/state; added fallback settings validation, last-inference state, and provider-specific model listing. |
| `jobapplier/store.py` | OpenAI defaults, project ID, Qwen fallback settings, context/timeout defaults. Existing SQLite/job/review logic retained. |
| `jobapplier/security.py` | Added simple non-executing root `.env` lookup for OPENAI_API_KEY; existing environment/Keychain support retained. |
| `frontend/src/App.tsx` | Removed Agent session navigation; added fallback controls, installed-model check, project/context/timeout inputs, and last-provider display. |
| `frontend/src/style.css` | Removed AgentPanel-only CSS. Existing application styling retained. |
| `tests/test_ai.py` | New provider routing/quota/failure/configuration tests replacing the removed Agents API suite. |
| `README.md` | Removed obsolete Agents API setup; documented OpenAI primary, local fallback, installation, limits, and recovery behavior. |
| `handover.md` | This handover document. |

### Existing core files to understand before further changes

| File | Role |
| --- | --- |
| `jobapplier/workflow.py` | Research, profile extraction, preparation, resume/letter decisions, browser handoffs, review/approval/submission orchestration. |
| `jobapplier/browser.py` | Persistent Chrome sessions, field snapshots, bounded actions, reviewed-form fingerprints, final submission and confirmation checks. |
| `jobapplier/documents.py` | Safe uploads/ZIP extraction, text extraction, skills replacement in copied LaTeX projects, compilation, artifact checks. |
| `jobapplier/sheets.py` | OAuth, identity check, sheet inspection/mapping, conservative sync, duplicates, formula preservation, remote hold checks. |
| `jobapplier/__init__.py` | Package initialization. |
| `tests/test_workflow.py` | Existing workflow, approval, duplicate, browser boundary, archive, and sheet safety tests. |
| `frontend/src/main.tsx` | React entry point. |
| `frontend/index.html` | Frontend document. |
| `frontend/package.json`, `frontend/package-lock.json` | Frontend dependencies/build scripts. |
| `frontend/tsconfig.json`, `frontend/vite.config.ts` | TypeScript/Vite configuration. |
| `pyproject.toml` | Python dependencies, package discovery, pytest/ruff configuration. |
| `Start JobApplier.command` | macOS launcher. |
| `.gitignore` | Ignores credentials, data, dependencies, build output and TS build cache. |
| `.env.example` | OPENAI_API_KEY placeholder only; contains no credential. |

`frontend/dist/` was rebuilt; it is generated and ignored. `frontend/tsconfig.tsbuildinfo`, `.venv`, and caches are generated. `data/workspace.sqlite` contains live user configuration and was updated earlier to OpenAI primary/fallback enabled, but its latest provider value has since changed as noted above. Never replace this database with test fixtures.

### Removed files — intentional, do not restore

- `jobapplier/agents_api.py`
- `frontend/src/AgentPanel.tsx`
- `tests/test_agents_api.py`
- `agents/definition.json`
- `agents/initial_message.txt`
- `scripts/agents-http.sh`
- `scripts/run-agent.sh`

Empty `agents/` or `scripts/` directories or stale ignored caches may remain; they are not active features.

### Temporary helper files outside the project

These may disappear between sessions; they are not shipped application code:

- `/tmp/remove_jobapplier_agent.py` — already-run removal script; do not rerun.
- `/tmp/update_jobapplier_ai.py` — already-run source rewrite; do not rerun.
- `/tmp/update_jobapplier_settings.py` — already-run frontend/docs rewrite; do not rerun.
- `/tmp/jobapplier_smoke.py` — tiny generic JSON response test for a selected provider, using a temporary workspace and existing credentials.
- `/tmp/jobapplier_fallback_smoke.py` — tiny generic OpenAI-to-Ollama fallback test, using a temporary workspace. Created but **not run** because Qwen download failed. It reads current live settings, so the currently blank model must be resolved or explicitly overridden in the test first.

## What changed over this conversation/session

1. Built the original local application workflow, frontend, persistence, document handling, browser controls, tracker integration, and tests.
2. At the user's subsequent explicit request, added an OpenAI Agents API/curl research-session feature with saved definitions, SSE streaming, recovery and tests. It never completed a live remote session because credentials were initially unavailable.
3. Explained that the second feature was separate from actual application preparation. User chose a single app with OpenAI primary and Ollama fallback.
4. Removed the separate Agents feature and its scripts/tests/configuration entirely from active code.
5. Implemented quota-only automatic fallback, Settings controls, project scoping, local context/timeout configuration, logs, and tests. Retained the previously selected primary model rather than silently changing it to the removed agent's gpt-6-astra.
6. Verified the simplified UI, removed Agent navigation, rebuilt assets, and restarted the backend.
7. Found Ollama installed but with no downloaded models; attempted to install Qwen 9B.
8. A live OpenAI smoke test reached the service and returned exhausted-credit/quota HTTP 429. Qwen download then failed due to full disk. No successful live generation or live fallback has been demonstrated yet.
9. While creating this handover, observed newly available disk space and the changed saved provider/blank model. Recorded these without overwriting the user's state.

## Verification completed

Last code verification after the simplification:

```sh
.venv/bin/ruff check jobapplier tests
.venv/bin/python -m pytest -q
(cd frontend && npm run build)
```

Results: **42 tests passed**, ruff clean, frontend production build successful. There is one Starlette TestClient/httpx deprecation warning; it did not fail tests.

Tests cover quota codes, identical input on fallback, no credential forwarding to Ollama, local context/timeouts, per-run fallback persistence, retrying primary in new runs, no fallback on other errors, unavailable local models, malformed output rejection, incomplete hosted output, removed routes, plus the existing workflow safeguards.

Browser checks confirmed the separate Agent session navigation is absent and the fallback controls render correctly. These are UI and mocked-boundary tests; they do not prove an authenticated employer application can complete.

## Failed approaches and unresolved limitations

- **Separate hosted agent architecture:** functioned as a separate research feature, not as the original application assistant. Removed at the user's request rather than continuing two disconnected AI paths.
- **Initial missing OPENAI_API_KEY:** prevented live Agents testing. Later the ordinary app's Keychain key became available. Do not assume the earlier missing-key state is current.
- **Sandboxed live OpenAI smoke test:** reported no key because Keychain access was unavailable in that execution context. Escalated execution reached OpenAI and returned HTTP 429 for exhausted quota. Do not replace the key just because a sandboxed read cannot see it.
- **Primary live test:** no successful inference; quota exhaustion prevented confirming model generation/access beyond the quota response. Replenishing project/API credits or correcting project billing is user work, not an automatic purchase.
- **Ollama CLI not on PATH:** resolved by using its installed app-bundle executable. Sandboxed localhost access failed with “operation not permitted”; approved/escalated execution worked.
- **Qwen installation:** download failed with `no space left on device`. The completed projector blob and partial main-model download were retained for resumption. Do not delete unrelated files or purge caches without authorization.
- **Google connector:** earlier 403 and unknown-tool errors; direct app OAuth remains the implemented approach. School nickname in Codex does not populate standalone OAuth tokens.
- **Real workflows:** no complete live preparation/submission/tracker cycle has been validated. Custom employer controls may need manual handling. Cover-letter attachments currently use TXT; PDF-only requirements need manual upload. Experience rewrites are suggestions. LaTeX compiler/layout/markers need verification.
- **Development-only hiccups already fixed:** a CSS append used a root-relative path while cwd was frontend; corrected and rebuilt. Stale browser handles were resolved by opening a fresh tab. These are not current blockers.

### Partial Qwen download details

Failed command (resumable):

```sh
/Applications/Ollama.app/Contents/Resources/ollama pull qwen3.5:9b-q4_K_M
```

- Historical download tool session: 23468, exited with code 1.
- Projector layer `f836f08f9211` reached 100% (about 921 MB).
- Main layer `02d45dc1cf45` reached approximately 86% (about 4.8 GB of 5.6 GB reported at failure).
- Partial file:
  `/Users/mustafaomran/.ollama/models/blobs/sha256-02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d-partial`
- Companion `-partial-0` through `-partial-15` metadata files exist. Leave them for Ollama to resume/verify.
- Last `ls -lh` showed a 5.2G apparent file size, which is not the same as completed downloaded content.

## Exact next actionable steps

### 1. Reconcile current settings before sending any model request

Inspect Settings and the latest persisted provider/model. The user's stated preference is OpenAI primary with Qwen fallback, but the latest saved state is Ollama with an empty primary model. Do not infer that blank means a valid model. Confirm whether their intervening UI selection was intentional if necessary; otherwise make the intended OpenAI-primary configuration concrete using the latest conversation. Preserve profile/documents/jobs/Sheets settings.

For the previously agreed configuration: provider `openai`, model `gpt-5.6-terra`, project as above, fallback enabled with `qwen3.5:9b-q4_K_M`. Model choice remains editable; do not silently substitute gpt-6-astra.

### 2. Resume and verify the Qwen download

The user has apparently freed disk space. First check `df -h .`; then, if space remains sufficient, run:

```sh
/Applications/Ollama.app/Contents/Resources/ollama pull qwen3.5:9b-q4_K_M
/Applications/Ollama.app/Contents/Resources/ollama list
```

Use the available approved prefix/escalation when localhost/network/sandbox access requires it. Wait for explicit download success. Do not claim installation merely because a partial blob exists. If storage fails again, explain the exact blocker and let the user decide what to delete; do not silently switch to a smaller model.

### 3. Run a tiny live fallback smoke test

Inspect/recreate `/tmp/jobapplier_fallback_smoke.py`, ensure its model is nonempty, then execute it with the access required for Keychain and localhost. It sends only a generic request to return `{"ok": true}`, with no resume or private profile data. It uses a temporary database and must not modify production job statuses.

Expected while API quota remains exhausted: OpenAI returns a recognized quota error, then Qwen returns valid JSON, and reported last provider is `ollama` with `fallback: true`. If credits were replenished meanwhile, a successful OpenAI response is fine; test the fallback separately using a mocked primary boundary and real local inference rather than trying to exhaust paid credits.

If Qwen fails to load or is too slow at 16K, record the exact error/memory conditions. Do not promise 256K usable context on this 16 GB Mac. Keep browser submissions disabled during tests.

### 4. Resolve OpenAI billing/access separately

Tell the user the live test received exhausted-credit/quota HTTP 429 for the saved key/project. They can add API credits or adjust their project billing/limits. ChatGPT/Codex subscription limits are separate. Once resolved, rerun the tiny primary smoke test; verify model access rather than assuming it from a previous quota error. Do not print or request credentials in chat.

### 5. Verify the UI and restart only if needed

In Settings, use **Check installed local models** and confirm Qwen is found. Confirm OpenAI remains the intended primary, fallback is enabled, and context/timeout are 16,384/600. Check that no Agent session navigation remains. If backend code changes, restart the known local app gracefully; do not kill unrelated processes.

```sh
.venv/bin/python -m uvicorn jobapplier.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

### 6. Complete one attended application-preparation test

Only after inference works: inspect existing verified profile and uploads, verify LaTeX markers/compiler, research one still-open supplied job, and prepare its materials/form. Have the user check missing eligibility facts and the exact PDF/answers. Validate custom fields and sign-in handoffs. Do not submit just to test. Submission still requires the user's review/approval of that concrete application package.

### 7. Connect and validate the school tracker

Finish standalone Google Desktop OAuth with `momran@utexas.edu`, inspect the actual sheet/header row, review mappings, and then save mapping/add required columns as authorized. Preserve existing formulas/notes and detect already-applied rows. Verify referral holds prevent submission and successful sync does not create duplicates. Keep networking/outreach deferred.

### 8. Re-run relevant checks after code changes

```sh
.venv/bin/ruff check jobapplier tests
.venv/bin/python -m pytest -q
(cd frontend && npm run build)
```

Do not repeat broad testing without changes or a new concern. Do not reintroduce the removed Agents API architecture. The immediate remaining objective is a working OpenAI-primary/local-fallback inference path, then an attended end-to-end application-preparation and tracker test.
