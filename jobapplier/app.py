import asyncio
import json
import os
import re
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import logos
from . import profile as profiledoc
from .ai import local_base, model_list, ollama_models, start_ollama
from .browser import Browser
from .documents import MAX_FILE, save_upload
from .monitor import Monitor
from .security import save_secret, secret
from .store import Store, normalize_company, now
from .tracker import FIELDS, SCRIPT, Tracker
from .workflow import Workflow, fetch_text


class ImportJobs(BaseModel):
    urls: list[str] = Field(max_length=100)
    mode: Literal["assisted", "advisor", "autonomous"] = "assisted"


class Revision(BaseModel):
    revision: int


class Batch(BaseModel):
    ids: list[str] = Field(max_length=100)


class JobLog(BaseModel):
    url: str
    stage: Literal["applied", "saved"] = "applied"  # saved = stored to apply later
    applied_at: str = ""
    signin_method: Literal["unknown", "google", "apple", "email_password", "other"] = "unknown"
    account_email: str = ""
    password: str = ""  # saved to the Keychain for the company; never stored on the job
    account_created: bool = False
    referral_status: Literal["not_sought", "pending", "received", "proceed_without"] = "not_sought"
    referral_contact: str = ""
    notes: str = ""
    title: str = ""
    company: str = ""


class JobEdit(BaseModel):
    revision: int
    company: str | None = None
    title: str | None = None
    referral_status: Literal["not_sought", "pending", "received", "proceed_without"] | None = None
    referral_url: str | None = None
    referral_contact: str | None = None
    referral_notes: str | None = None
    review_notes: str | None = None
    signin_method: Literal["unknown", "google", "apple", "email_password", "other"] | None = None
    account_email: str | None = None
    account_created: bool | None = None
    fit_acknowledged: bool | None = None


def public_company(company):
    """A company record for the UI: never the Keychain secret, just whether one is saved."""
    return {k: v for k, v in company.items() if k != "keys"}


def create_app(data_dir=None):
    store = Store(data_dir)
    browser = Browser(store)
    sheets = Tracker(store)
    monitor = Monitor(store, sheets)
    workflow = Workflow(store, browser, sheets)
    token = secrets.token_urlsafe(40)

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        monitor.start_background(store.settings().get("monitor_minutes") or 0)
        asyncio.create_task(logos.fill_missing(store))
        yield
        await workflow.stop()

    app = FastAPI(title="JobApplier", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store, app.state.workflow, app.state.sheets = store, workflow, sheets
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin access is disabled."}, status_code=403)
        exempt = request.url.path in {"/api/bootstrap", "/oauth/callback"}
        if request.url.path.startswith("/api/") and not exempt:
            candidate = request.headers.get("x-workspace-token", "")
            if request.method == "GET":
                candidate = candidate or request.cookies.get("workspace", "")
            if not secrets.compare_digest(candidate, token):
                return JSONResponse({"detail": "Open the local dashboard to authenticate."}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; frame-ancestors 'self'"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Item not found."}, status_code=404)

    @app.get("/api/bootstrap")
    async def bootstrap():
        response = JSONResponse({"token": token})
        response.set_cookie("workspace", token, httponly=True, samesite="strict")
        return response

    @app.get("/api/state")
    async def state():
        docs = [{k: v for k, v in d.items() if k != "text"} for d in store.documents()]
        ingested = store.get("profile_sources", {})
        for d in docs:
            d["ingested"] = ingested.get(d["id"], {}).get("sha256") == d.get("sha256")
        proposals = [store.get("ingest:" + d["id"]) for d in docs]
        return {"jobs": store.jobs(), "documents": docs, "profile": store.profile(),
                "proposals": [p for p in proposals if p], "companies": [public_company(c) for c in store.companies()],
                "settings": store.settings(),
                "tracker": store.get("tracker"), "usage": store.get("usage", {}),
                "last_inference": store.get("last_inference"), "progress": workflow.progress, "monitor": monitor.state(),
                "batch_running": bool(workflow.batch_task and not workflow.batch_task.done()),
                "events": store.events(), "sheet_fields": FIELDS,
                "capabilities": {"latex": bool(shutil.which("tectonic") or shutil.which("pdflatex")),
                                 "anthropic_key": bool(secret("anthropic_key")),
                                 "codex": bool(shutil.which("codex") or os.path.exists("/opt/homebrew/bin/codex")), "tracker_connected": sheets.connected()}}

    @app.post("/api/jobs")
    async def import_jobs(body: ImportJobs):
        imported, duplicates, errors = [], [], []
        for url in body.urls:
            try:
                job, created = store.add_job(url)
                if created or not job.get("mode"):
                    store.update(job["id"], {"mode": body.mode}, invalidate=False)
                    company = store.link_job_company(job["id"])
                    asyncio.create_task(logos.ensure_logo(store, company))
                    if body.mode in {"assisted", "autonomous"}:
                        store.setup_questions(company["id"], job["id"])
                (imported if created else duplicates).append(job["id"])
            except ValueError as exc:
                errors.append({"url": url, "error": str(exc)})
        return {"imported": imported, "duplicates": duplicates, "errors": errors}

    @app.post("/api/jobs/log")
    async def log_job(body: JobLog):
        """Tracker mode: record an application you submitted yourself, or store a job to apply to later."""
        job, created = store.add_job(body.url)
        if not created and job["status"] in {"submitted", "applied"}:
            raise ValueError("This job is already recorded as applied.")
        if not created and body.stage == "saved" and job["status"] == "saved":
            raise ValueError("This job is already saved.")
        applied_at = body.applied_at.strip() or now()[:10]
        stage = {"status": "applied", "submitted_at": applied_at, "confirmation": "Applied myself"} if body.stage == "applied" \
            else {"status": "saved", "submitted_at": None, "confirmation": ""}
        changes = {**stage, "mode": "manual",
                   "signin_method": body.signin_method, "account_email": body.account_email.strip(),
                   "account_created": body.account_created, "referral_status": body.referral_status,
                   "referral_contact": body.referral_contact.strip(), "review_notes": body.notes.strip(), "error": "",
                   "package": None, "approval": None}
        if body.title.strip():
            changes["title"] = body.title.strip()
        if body.company.strip():
            changes["company"] = body.company.strip()
        job = store.update(job["id"], changes, invalidate=False)
        company = store.link_job_company(job["id"])
        asyncio.create_task(logos.ensure_logo(store, company))
        # What you tell us about how you signed in is remembered for the company, not just this job.
        updates = {}
        if body.signin_method != "unknown" and company.get("signin_method") == "unknown":
            updates["signin_method"] = body.signin_method
        if body.account_email.strip() and not company.get("account_email"):
            updates["account_email"] = body.account_email.strip()
        if body.account_created or body.signin_method != "unknown":
            updates["has_account"] = True
        if body.signin_method == "google":
            updates["google_signin"] = True
        if body.password and body.signin_method in {"email_password", "other"}:
            if not body.account_email.strip():
                raise ValueError("Enter the account email with the password.")
            save_secret("company:" + company["id"], json.dumps({"email": body.account_email.strip(), "password": body.password}))
            updates.update(credential=True, account_email=body.account_email.strip(), has_account=True)
        if updates:
            store.save_company({**company, **updates})
        store.event(job["id"], f"Recorded as applied on {applied_at}." if body.stage == "applied" else "Saved to apply later.")
        if not job.get("description"):
            workflow.start(job["id"], "research")  # fills title, company and deadline in the background
        elif store.settings()["sheet_sync_enabled"]:
            asyncio.create_task(sheets.sync())
        return store.job(job["id"])

    @app.patch("/api/jobs/{jid}")
    async def edit_job(jid: str, body: JobEdit):
        job = store.job(jid)
        if job["status"] in {"preparing", "submitting"}:
            raise ValueError("Pause preparation before editing. Submission already in progress cannot be edited.")
        fields = body.model_dump(exclude_none=True, exclude={"revision"})
        if job["status"] == "submitted" and set(fields) - {"review_notes", "referral_notes", "referral_contact", "signin_method", "account_email", "account_created"}:
            raise ValueError("Submitted applications are immutable; update notes or account details only.")
        if job["revision"] != body.revision:
            raise ValueError("This application changed. Refresh before editing.")
        if any(k.startswith("referral_") for k in fields):
            await sheets.write_referral(job, fields)
        if "referral_url" in fields and fields["referral_url"] != job.get("referral_url"):
            page = browser.pages.pop(jid, None)
            if page:
                await page.close()
            browser.reviews.pop(jid, None)
            if job.get("package"):
                fields["package"] = {**job["package"], "form_ready": False}
        result = store.update(jid, fields, expected_revision=body.revision)
        store.event(jid, "Application details updated; any previous approval was cleared.")
        return result

    @app.post("/api/jobs/{jid}/research")
    async def research(jid: str):
        workflow.start(jid, "research")
        return {"started": True}

    @app.post("/api/jobs/{jid}/applied")
    async def mark_applied(jid: str):
        """A stored (to apply) job you've now applied to yourself; keeps its notes and sign-in details."""
        job = store.job(jid)
        if job["status"] in {"submitted", "applied", "submitting"}:
            raise ValueError("This job is already recorded as applied.")
        job = store.update(jid, {"status": "applied", "mode": job.get("mode") or "manual", "submitted_at": now()[:10],
                                 "confirmation": "Applied myself", "error": ""}, invalidate=False)
        store.event(jid, f"Recorded as applied on {job['submitted_at']}.")
        if store.settings()["sheet_sync_enabled"]:
            asyncio.create_task(sheets.sync())
        return job

    @app.post("/api/jobs/{jid}/answer")
    async def answer_job_question(jid: str, request: Request):
        """Answer the question an application paused on. The answer is saved under Answers in your profile
        (reused for every application) and the job becomes resumable."""
        body = await request.json()
        answer = str(body.get("answer", "")).strip()
        job = store.job(jid)
        p = dict(job.get("package") or {})
        question = (p.get("unresolved") or [None])[0] or str(body.get("question", "")).strip()
        if not answer or not question:
            raise ValueError("Nothing to answer.")
        if body.get("save_to_profile", True):
            key = question.rstrip("?.: ").replace(":", " -")[:160]
            text, changed = profiledoc.append(store.profile_text(), {"Answers": [f"- {key}: {answer}"]})
            if changed:
                store.save_profile_text(text, reason="answer")
        p.update(unresolved=[], suggested_answer="")
        store.event(jid, f"Answered: {question[:80]}")
        return store.update(jid, {"package": p, "error": ""}, invalidate=False)

    @app.post("/api/jobs/{jid}/edits")
    async def accept_edits(jid: str, request: Request):
        """Accept experience wording suggestions; they are applied to the tailored copy on the next preparation."""
        body = await request.json()
        job = store.job(jid)
        if job["status"] in {"preparing", "submitting", "submitted", "applied"}:
            raise ValueError("Pause preparation before changing accepted edits.")
        p = dict(job.get("package") or {})
        chosen = [int(i) for i in body.get("accepted", []) if isinstance(i, int)]
        suggestions = p.get("suggested_edits") or []
        p["accepted_edits"] = [suggestions[i] for i in chosen if 0 <= i < len(suggestions)]
        p["resume"] = None  # forces a rebuild with the accepted edits applied
        p.update(form_ready=False, visual_reviewed=False)
        return store.update(jid, {"package": p}, expected_revision=body.get("revision"))

    @app.post("/api/jobs/{jid}/advise")
    async def advise(jid: str):
        workflow.start(jid, "advise")
        return {"started": True}

    @app.post("/api/jobs/{jid}/autonomous")
    async def autonomous(jid: str):
        """Prepare and, if every check passes, submit without a review step. Anything uncertain parks the job instead."""
        store.update(jid, {"mode": "autonomous"}, invalidate=False)
        workflow.start(jid, "autonomous")
        return {"started": True}

    @app.post("/api/jobs/{jid}/prepare")
    async def prepare(jid: str):
        workflow.start(jid, "prepare")
        return {"started": True}

    @app.post("/api/jobs/{jid}/pause")
    async def pause(jid: str):
        workflow.cancelled.add(jid)
        task = workflow.tasks.get(jid)
        if task and not task.done():
            task.cancel()
        job = store.job(jid)
        if job["status"] == "approved":
            store.update(jid, {"status": "ready"})
        return {"paused": True}

    @app.post("/api/jobs/{jid}/review")
    async def reviewed(jid: str, body: Revision):
        job = store.job(jid)
        if job["status"] not in {"ready", "needs_attention"} or not job.get("package"):
            raise ValueError("Prepare the application before reviewing it.")
        p = {**job["package"], "visual_reviewed": True}
        return store.update(jid, {"package": p, "status": "ready" if p.get("form_ready") else "needs_attention"}, expected_revision=body.revision)

    @app.post("/api/jobs/{jid}/approve")
    async def approve(jid: str, body: Revision):
        return await workflow.approve(jid, body.revision)

    @app.post("/api/submit")
    async def submit(body: Batch):
        workflow.submit_batch(body.ids)
        return {"queued": len(body.ids)}

    @app.post("/api/jobs/{jid}/resolve-outcome")
    async def resolve(jid: str, request: Request):
        body = await request.json()
        job = store.job(jid)
        if job["status"] != "uncertain" or body.get("outcome") not in {"submitted", "not_submitted"}:
            raise ValueError("Only uncertain outcomes can be resolved here.")
        submitted = body["outcome"] == "submitted"
        store.event(jid, "User checked the portal and confirmed: " + body["outcome"])
        return store.update(jid, {"status": "submitted" if submitted else "needs_attention",
                                  "submitted_at": now() if submitted else None,
                                  "confirmation": "Confirmed manually in candidate portal" if submitted else "",
                                  "error": ""}, expected_revision=body.get("revision"))

    @app.post("/api/documents")
    async def upload(kind: str = Form(...), file: UploadFile = File(...)):
        data = await file.read(MAX_FILE + 1)
        doc = save_upload(store, file.filename or "upload.txt", kind, data)
        return {k: v for k, v in doc.items() if k != "text"}

    @app.get("/api/documents/{did}/text")
    async def document_text(did: str):
        doc = store.document(did)
        if not doc:
            raise KeyError(did)
        return {"text": doc["text"]}

    @app.post("/api/portfolio")
    async def portfolio(request: Request):
        body = await request.json()
        page = await fetch_text(body["url"])
        doc = save_upload(store, "portfolio.txt", "supporting", (page["url"] + "\n" + page["text"]).encode())
        return {k: v for k, v in doc.items() if k != "text"}

    @app.get("/api/profile/doc")
    async def profile_doc():
        return {"text": store.profile_text(), **store.profile(), "history": store.profile_history()}

    @app.put("/api/profile/doc")
    async def save_profile_doc(request: Request):
        body = await request.json()
        text = body.get("text")
        if not isinstance(text, str) or "## " not in text:
            raise ValueError("The profile must keep its section headings.")
        if len(text) > 400_000:
            raise ValueError("The profile is too large (400k character limit).")
        if any(j["status"] in {"preparing", "submitting"} for j in store.jobs()):
            raise ValueError("Pause active preparation before changing your profile.")
        meta = store.get("profile_meta", {})
        if "verified" in body:
            store.set("profile_meta", {**meta, "verified": bool(body["verified"])})
        return store.save_profile_text(text, reason="edited")

    @app.get("/api/profile/history/{name}")
    async def profile_history(name: str):
        path = (store.root / "profile" / "history" / name).resolve()
        if not path.is_relative_to(store.root / "profile" / "history") or not path.is_file():
            raise KeyError(name)
        return {"name": name, "text": path.read_text()}

    @app.post("/api/documents/{did}/ingest")
    async def ingest(did: str):
        workflow.start_ingest(did)
        return {"started": True}

    @app.post("/api/profile/append")
    async def profile_append(request: Request):
        body = await request.json()
        did = body.get("doc_id", "")
        additions = body.get("additions") or {}
        identity = body.get("identity") or {}
        if not isinstance(additions, dict) or not isinstance(identity, dict):
            raise ValueError("Nothing to append.")
        text, changed = profiledoc.append(store.profile_text(), additions, identity)
        if changed:
            store.save_profile_text(text, reason="append")
        doc = store.document(did)
        if doc:
            sources = store.get("profile_sources", {})
            sources[did] = {"sha256": doc.get("sha256"), "at": now(), "lines": changed}
            store.set("profile_sources", sources)
            store.set("ingest:" + did, None)
            store.event(None, f"Added {changed} line(s) from {doc['name']} to your profile.")
        return {"changed": changed, **store.profile()}

    @app.delete("/api/profile/proposal/{did}")
    async def dismiss_proposal(did: str):
        store.set("ingest:" + did, None)
        return {"dismissed": True}

    @app.patch("/api/companies/{cid}")
    async def edit_company(cid: str, request: Request):
        body = await request.json()
        company = store.company(cid)
        allowed = {"name": str, "signin_method": str, "account_email": str, "has_account": bool, "google_signin": bool,
                   "notes": str, "portal": str, "domain": str}
        for key, kind in allowed.items():
            if key in body:
                if not isinstance(body[key], kind):
                    raise ValueError(f"Invalid value for {key}.")
                company[key] = body[key]
        if company.get("signin_method") not in {"unknown", "none", "google", "apple", "email_password", "other"}:
            raise ValueError("Unknown sign-in method.")
        if "name" in body:
            key = normalize_company(body["name"])
            if key and key not in company["keys"]:
                company["keys"].append(key)
        return public_company(store.save_company(company))

    @app.get("/api/logo/{cid}")
    async def company_logo(cid: str):
        company = store.company(cid)
        if not company.get("logo"):
            raise HTTPException(404)
        path = (store.root / "logos" / company["logo"]).resolve()
        if not path.is_relative_to(store.root / "logos") or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    @app.post("/api/companies/{cid}/logo")
    async def refresh_logo(cid: str):
        company = await logos.ensure_logo(store, store.company(cid), force=True)
        return {"logo": bool(company.get("logo"))}

    @app.post("/api/companies/{cid}/credential")
    async def company_credential(cid: str, request: Request):
        body = await request.json()
        company = store.company(cid)
        email, password = str(body.get("email", "")).strip(), str(body.get("password", ""))
        if password and not email:
            raise ValueError("Enter the login email with the password.")
        save_secret("company:" + cid, json.dumps({"email": email, "password": password}) if password else "")
        company.update(credential=bool(password), account_email=email or company.get("account_email", ""),
                       has_account=company.get("has_account") or bool(password))
        if password and company.get("signin_method") == "unknown":
            company["signin_method"] = "email_password"
        return public_company(store.save_company(company))

    @app.post("/api/companies/{cid}/questions")
    async def add_company_question(cid: str, request: Request):
        body = await request.json()
        question = str(body.get("question", "")).strip()
        if not question:
            raise ValueError("Write the question first.")
        q = store.add_question(cid, question)
        if body.get("answer"):
            store.answer_question(cid, q["id"], body["answer"])
        return public_company(store.company(cid))

    @app.post("/api/companies/{cid}/questions/{qid}")
    async def answer_company_question(cid: str, qid: str, request: Request):
        body = await request.json()
        company = store.answer_question(cid, qid, body.get("answer", ""))
        return public_company(company)

    @app.put("/api/settings")
    async def save_settings(request: Request):
        changes = await request.json()
        allowed = {"provider", "model", "ollama_url", "max_model_calls", "max_output_tokens", "tex_main", "skills_start", "skills_end", "fallback_enabled", "fallback_model", "ollama_context", "ollama_timeout", "effort", "fast_model", "always_letter", "default_mode", "autonomous_daily_cap", "autonomous_delay_minutes",
                   "monitor_minutes", "text_phone", "text_gateway", "text_kinds", "quiet_from", "quiet_to"}
        if set(changes) - allowed:
            raise ValueError("Unknown settings.")
        settings = {**store.settings(), **changes}
        if settings["provider"] not in {"ollama", "anthropic", "codex"}:
            raise ValueError("Choose Codex, Claude, or Ollama.")
        if settings["effort"] not in {"low", "medium", "high"}:
            raise ValueError("Effort must be low, medium, or high.")
        if settings["default_mode"] not in {"assisted", "advisor", "autonomous"} or not isinstance(settings["always_letter"], bool):
            raise ValueError("Invalid mode settings.")
        for field, low, high in [("autonomous_daily_cap", 1, 50), ("autonomous_delay_minutes", 0, 120), ("monitor_minutes", 0, 240),
                                 ("quiet_from", 0, 23), ("quiet_to", 0, 23)]:
            if type(settings[field]) is not int or not low <= settings[field] <= high:
                raise ValueError(f"{field} must be an integer between {low} and {high}.")
        if not isinstance(settings["fallback_enabled"], bool) or not isinstance(settings["fallback_model"], str) or not settings["fallback_model"].strip():
            raise ValueError("Provide a fallback model and a valid fallback switch.")
        if not isinstance(settings["fast_model"], str):
            raise ValueError("Fast local model must be a model name, or empty to always use the main model.")
        for field, low, high in [("ollama_context", 4096, 32768), ("ollama_timeout", 120, 1800)]:
            if type(settings[field]) is not int or not low <= settings[field] <= high:
                raise ValueError(f"{field} must be an integer between {low} and {high}.")
        from urllib.parse import urlsplit
        if urlsplit(settings["ollama_url"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Ollama must use a local address.")
        if not 1 <= int(settings["max_model_calls"]) <= 100 or not 256 <= int(settings["max_output_tokens"]) <= 16000:
            raise ValueError("Model limits are outside the supported range.")
        phone = re.sub(r"\D", "", str(settings["text_phone"]))
        if phone and len(phone) != 10:
            raise ValueError("Enter a 10-digit US phone number, or leave it empty.")
        settings["text_phone"] = phone
        if settings["text_gateway"] not in {"tmomail.net", "vtext.com", "txt.att.net", "messaging.sprintpcs.com", "msg.fi.google.com"}:
            raise ValueError("Unknown text gateway.")
        if not isinstance(settings["text_kinds"], list) or set(settings["text_kinds"]) - {"interview", "assessment", "action_required", "offer", "rejection", "confirmation"}:
            raise ValueError("Invalid text notification kinds.")
        store.set("settings", settings)
        monitor.start_background(settings["monitor_minutes"])
        return settings

    @app.post("/api/secrets")
    async def save_credentials(request: Request):
        body = await request.json()
        if body.get("name") != "anthropic_key":
            raise ValueError("Unknown credential type.")
        save_secret(body["name"], body["value"])
        return {"saved": True}

    @app.post("/api/ollama/start")
    async def ollama_start():
        settings = store.settings()
        if not await start_ollama(local_base(settings)):
            raise ValueError("Ollama could not be started. Install it from ollama.com, then try again.")
        return {"models": await ollama_models(settings)}

    @app.get("/api/models")
    async def models(provider: Literal["ollama", "anthropic", "codex"] | None = None):
        settings = store.settings()
        return await model_list({**settings, "provider": provider or settings["provider"]})

    @app.post("/api/linkedin/open")
    async def linkedin_open():
        """Open LinkedIn in the app's own Chrome window so you can log in there once (the session is kept)."""
        await browser.open_site("https://www.linkedin.com/login")
        return {"opened": True}

    @app.post("/api/contacts/find")
    async def find_contacts(request: Request):
        body = await request.json()
        ids = [i for i in body.get("job_ids", []) if isinstance(i, str)][:10]
        description = str(body.get("description", "")).strip()[:400]
        if not ids or not description:
            raise ValueError("Choose at least one job and describe who to look for.")
        for jid in ids:
            store.job(jid)
        return {"started": workflow.start_contacts(ids, description)}

    @app.patch("/api/companies/{cid}/contacts")
    async def edit_contact(cid: str, request: Request):
        body = await request.json()
        company = store.company(cid)
        for c in company.get("contacts", []):
            if (c.get("url") or c.get("name")) == body.get("key"):
                if body.get("status") in {"found", "contacted", "replied", "referred", "skip"}:
                    c["status"] = body["status"]
                if isinstance(body.get("note"), str):
                    c["note"] = body["note"][:600]
        return public_company(store.save_company(company))

    @app.post("/api/stop")
    async def stop_everything():
        """Stop button: cancels running preparations, advice, ingests, contact searches and the email check."""
        n = workflow.stop_all() + monitor.stop()
        store.event(None, f"Stopped {n} running operation(s)." if n else "Nothing was running.")
        return {"stopped": n}

    @app.post("/api/monitor/run")
    async def monitor_run(request: Request):
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        return await monitor.run(days=int(body.get("days", 7)) if body.get("days") else None)

    @app.post("/api/jobs/{jid}/action-done")
    async def action_done(jid: str):
        return store.update(jid, {"action_required": None}, invalidate=False)

    @app.get("/api/tracker/script")
    async def tracker_script():
        return {"script": SCRIPT.read_text()}

    @app.post("/api/tracker/connect")
    async def tracker_connect(request: Request):
        return await sheets.connect((await request.json()).get("link", ""))

    @app.post("/api/tracker/disconnect")
    async def tracker_disconnect():
        sheets.disconnect()
        return {"disconnected": True}

    @app.post("/api/sheets/sync")
    async def sync_sheet():
        return await sheets.sync()

    @app.get("/api/export")
    async def export():
        # Stable interchange contract for the future networking app; no secrets or document bodies.
        return {"schema_version": 1, "exported_at": now(), "applications": [
            {k: j.get(k, "") for k in FIELDS} for j in store.jobs()]}

    @app.get("/api/file/{path:path}")
    async def file(path: str):
        target = (store.root / path).resolve()
        if not target.is_relative_to(store.root) or not target.is_file() or target.suffix.lower() not in {".pdf", ".png", ".txt", ".tex"}:
            raise HTTPException(404)
        if target.parts[len(store.root.parts)] not in {"documents", "artifacts"}:
            raise HTTPException(404)
        return FileResponse(target)

    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:
        @app.get("/")
        async def missing_frontend():
            return HTMLResponse("<h1>JobApplier</h1><p>Build the frontend with npm run build in frontend/.</p>")
    return app


app = create_app()
