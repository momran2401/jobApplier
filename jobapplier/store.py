import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import profile as profiledoc


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def canonical_url(url):
    p = urlsplit(url.strip().replace("\\&", "&").replace("\\_", "_"))
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Use a public HTTP or HTTPS job link.")
    pairs = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_")
             and k not in {"gh_src", "source", "ref", "category", "jex", "target_level", "team"}]
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(pairs)), ""))


DEFAULT_SETTINGS = {
    "provider": "anthropic", "model": "claude-sonnet-5-5", "effort": "low", "ollama_url": "http://127.0.0.1:11434",
    "fallback_enabled": True, "fallback_model": "qwen3.5:9b-q4_K_M", "fast_model": "qwen3.5:4b",
    "ollama_context": 16384, "ollama_timeout": 600,
    "max_model_calls": 35, "max_output_tokens": 4000,
    "sheet_sync_enabled": False, "browser_channel": "chrome",
    "tex_main": "", "skills_start": "", "skills_end": "",
    "always_letter": False, "default_mode": "assisted",
    "autonomous_daily_cap": 10, "autonomous_delay_minutes": 5,
    "monitor_minutes": 0, "text_phone": "", "text_gateway": "tmomail.net", "text_kinds": ["interview", "action_required", "offer"],
    "quiet_from": 22, "quiet_to": 8,
}


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("JOBAPPLIER_DATA", "data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "workspace.sqlite"
        self.lock = threading.RLock()
        with self.connection() as c:
            c.executescript("""
              CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL,
                 revision INTEGER NOT NULL, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, job_id TEXT,
                 at TEXT NOT NULL, message TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS companies (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            """)
        os.chmod(self.path, 0o600)
        (self.root / "profile" / "history").mkdir(parents=True, exist_ok=True)
        if not self.profile_path.exists():
            self.save_profile_text(profiledoc.from_legacy(self.get("profile", {})), reason="created")
            if self.get("profile", {}).get("verified"):
                self.set("profile_meta", {**self.get("profile_meta", {}), "verified": True})

    # ---- master profile document ----
    @property
    def profile_path(self):
        return self.root / "profile" / "profile.md"

    def profile_text(self):
        return self.profile_path.read_text() if self.profile_path.exists() else profiledoc.empty_doc()

    def save_profile_text(self, text, reason="edited"):
        text = text.replace("\r\n", "\n")
        if not text.endswith("\n"):
            text += "\n"
        if self.profile_path.exists() and self.profile_path.read_text() == text:
            return self.profile()
        stamp = profiledoc.stamp()
        (self.root / "profile" / "history" / f"{stamp}-{reason}.md").write_text(text)
        self.profile_path.write_text(text)
        os.chmod(self.profile_path, 0o600)
        meta = self.get("profile_meta", {})
        self.set("profile_meta", {**meta, "version": profiledoc.version(text), "updated_at": now(), "reason": reason})
        return self.profile()

    def profile(self):
        """What the app and UI need to know about the profile (not the text itself)."""
        text = self.profile_text()
        meta = self.get("profile_meta", {})
        return {"version": meta.get("version") or profiledoc.version(text), "verified": bool(meta.get("verified")),
                "updated_at": meta.get("updated_at"), "identity": profiledoc.identity(text),
                "skills": len(profiledoc.skills_list(text)), "chars": len(text)}

    def profile_history(self):
        files = sorted((self.root / "profile" / "history").glob("*.md"), reverse=True)
        return [f.name for f in files[:50]]

    # ---- companies ----
    def companies(self):
        with self.connection() as c:
            rows = c.execute("SELECT data FROM companies ORDER BY rowid").fetchall()
        return [json.loads(r[0]) for r in rows]

    def company(self, cid):
        with self.connection() as c:
            row = c.execute("SELECT data FROM companies WHERE id=?", (cid,)).fetchone()
        if not row:
            raise KeyError("Company not found")
        return json.loads(row[0])

    def save_company(self, company):
        company["updated_at"] = now()
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO companies VALUES (?,?)", (company["id"], json.dumps(company)))
        return company

    def company_for(self, job, create=True):
        """The company a job belongs to, matched by name (and by hostname for company-run career sites)."""
        host = urlsplit(job["url"]).hostname or ""
        shared = any(host.endswith(h) for h in SHARED_ATS_HOSTS)
        name = job.get("company") or ""
        named = bool(name) and name != host
        # Candidate keys: the researched name, and for shared ATS links the company slug in the URL.
        keys = [k for k in (normalize_company(name) if named else "", normalize_company(ats_slug(job["url"])) if shared else "") if k]
        if not shared and not keys:
            keys = [normalize_company(host)]
        for company in self.companies():
            known = company.get("keys", [])
            if (not shared and host and host == company.get("domain")) or any(
                    k == x or (len(k) >= 4 and len(x) >= 4 and (x.startswith(k) or k.startswith(x))) for k in keys for x in known):
                for k in keys:
                    if k not in known:
                        known.append(k)
                        self.save_company({**company, "keys": known})
                if named and company["name"] != name and normalize_company(company["name"]) != normalize_company(name):
                    company = self.save_company({**company, "name": name, "aliases": list({*company.get("aliases", []), company["name"]})})
                return company
        if not create:
            return None
        display = name if named else (ats_slug(job["url"]) if shared else host.removeprefix("www."))
        company = {"id": str(uuid.uuid4()), "name": display, "keys": keys, "aliases": [],
                   "domain": "" if shared else host, "portal": portal_from_url(job["url"]), "signin_method": "unknown",
                   "account_email": "", "has_account": False, "google_signin": False, "credential": False,
                   "questions": [], "notes": "", "research": None, "contacts": [], "created_at": now(), "updated_at": now()}
        return self.save_company(company)

    def link_job_company(self, jid):
        job = self.job(jid)
        company = self.company_for(job)
        if job.get("company_id") != company["id"]:
            job = self.update(jid, {"company_id": company["id"]}, invalidate=False)
        return company

    def setup_questions(self, cid, job_id=None):
        """The up-front questions asked once per company before the applier signs in anywhere."""
        company = self.company(cid)
        if company.get("setup_asked"):
            return []
        name = company["name"]
        asked = [self.add_question(cid, f"How do you sign in at {name}'s careers site?", job_id, field="signin_method",
                                   choices=list(SIGNIN_CHOICES))]
        company = self.company(cid)
        company["setup_asked"] = True
        self.save_company(company)
        return asked

    def setup_pending(self, cid):
        return [q for q in self.company(cid)["questions"] if q.get("field") and not q.get("answer")]

    def add_question(self, cid, question, job_id=None, field=None, choices=None):
        company = self.company(cid)
        for q in company["questions"]:
            if q["question"].strip().lower() == question.strip().lower():
                return q
        q = {"id": str(uuid.uuid4()), "question": question.strip(), "answer": "", "asked_at": now(),
             "answered_at": None, "job_id": job_id, "field": field, "choices": choices or []}
        company["questions"].append(q)
        self.save_company(company)
        return q

    def answer_question(self, cid, qid, answer):
        company = self.company(cid)
        for q in company["questions"]:
            if q["id"] == qid:
                q.update(answer=str(answer).strip(), answered_at=now())
                if q.get("field") == "signin_method":  # a setup answer also fills in the company record
                    value = SIGNIN_CHOICES.get(q["answer"], "unknown")
                    company.update(signin_method=value, has_account=value not in {"unknown", "none"} or company.get("has_account", False),
                                   google_signin=value == "google" or company.get("google_signin", False))
                    if value == "email_password" and not company.get("credential"):
                        self.add_question(cid, f"Add your {company['name']} password under Companies → Saved login so the assistant can sign in "
                                               "(or sign in yourself in the Chrome window when it pauses). Type 'ok' when done.", q.get("job_id"))
                        company = self.company(cid)
                        company.update(signin_method=value, has_account=True)
                        for qq in company["questions"]:
                            if qq["id"] == qid:
                                qq.update(answer=str(answer).strip(), answered_at=now())
                break
        else:
            raise KeyError("Question not found")
        self.save_company(company)
        return company

    @contextmanager
    def connection(self):
        with self.lock:
            c = sqlite3.connect(self.path, timeout=15)
            c.row_factory = sqlite3.Row
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()

    def get(self, key, default=None):
        with self.connection() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))

    def settings(self):
        saved = dict(self.get("settings", {}))
        for old in ("openai_project", "sheet_id", "google_email", "sheet_tab", "sheet_mapping", "sheet_header_row"):
            saved.pop(old, None)  # Settings from removed integrations (OpenAI, Google OAuth Sheets).
        if saved.get("provider") == "openai":  # OpenAI support was removed; Claude replaces it.
            saved.update(provider=DEFAULT_SETTINGS["provider"], model=DEFAULT_SETTINGS["model"])
        return {**DEFAULT_SETTINGS, **saved}

    def jobs(self):
        with self.connection() as c:
            rows = c.execute("SELECT data FROM jobs ORDER BY rowid DESC").fetchall()
        return [json.loads(r[0]) for r in rows]

    def job(self, jid):
        with self.connection() as c:
            row = c.execute("SELECT data FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            raise KeyError("Application not found")
        return json.loads(row[0])

    def add_job(self, url):
        url = canonical_url(url)
        with self.connection() as c:
            old = c.execute("SELECT data FROM jobs WHERE url=?", (url,)).fetchone()
            if old:
                return json.loads(old[0]), False
            job = {"id": str(uuid.uuid4()), "url": url, "revision": 1,
                   "company": urlsplit(url).hostname, "title": "Untitled opportunity",
                   "status": "new", "referral_status": "not_sought", "referral_url": "",
                   "referral_contact": "", "referral_notes": "", "review_notes": "",
                   "signin_method": "unknown", "account_email": "", "account_created": False,
                   "portal_url": "", "status_url": "", "requisition_id": "", "location": "",
                   "deadline": "", "posting_kind": "opening", "fit_flags": [], "fit_acknowledged": False,
                   "created_at": now(), "updated_at": now(), "package": None, "approval": None,
                   "sync_status": "pending", "sync_error": "", "last_sync_at": None,
                   "error": "", "submitted_at": None, "confirmation": "", "description": "",
                   "mode": "", "action_required": None, "last_email": None, "company_id": None}
            c.execute("INSERT INTO jobs VALUES (?,?,?,?)", (job["id"], url, 1, json.dumps(job)))
        return job, True

    def update(self, jid, changes, *, invalidate=True, expected_revision=None):
        with self.connection() as c:
            row = c.execute("SELECT data FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                raise KeyError("Application not found")
            job = json.loads(row[0])
            if expected_revision is not None and expected_revision != job["revision"]:
                raise ValueError("This application changed. Refresh and review the latest version.")
            if invalidate:
                job["approval"] = None
                if job["status"] in {"approved", "ready"}:
                    job["status"] = "needs_attention"
            job.update(changes)
            job["revision"] += 1
            job["updated_at"] = now()
            if not set(changes).issubset({"sync_status", "sync_error", "last_sync_at"}):
                job["sync_status"] = "pending"
            c.execute("UPDATE jobs SET revision=?,data=? WHERE id=?", (job["revision"], json.dumps(job), jid))
        return job

    def event(self, jid, message):
        with self.connection() as c:
            c.execute("INSERT INTO events(job_id,at,message) VALUES (?,?,?)", (jid, now(), message))

    def events(self, jid=None):
        with self.connection() as c:
            rows = c.execute("SELECT * FROM events WHERE (? IS NULL OR job_id=?) ORDER BY id DESC LIMIT 100",
                             (jid, jid)).fetchall()
        return [dict(r) for r in rows]

    def save_document(self, doc):
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO documents VALUES (?,?)", (doc["id"], json.dumps(doc)))

    def documents(self):
        with self.connection() as c:
            rows = c.execute("SELECT data FROM documents ORDER BY rowid DESC").fetchall()
        return [json.loads(r[0]) for r in rows]

    def document(self, did):
        return next((d for d in self.documents() if d["id"] == did), None)

    def recover(self):
        for job in self.jobs():
            if not job.get("company_id"):
                self.link_job_company(job["id"])
            if job["status"] == "submitting":
                self.update(job["id"], {"status": "uncertain", "error": "Interrupted during submission. Check the portal before retrying."})
            elif job["status"] in {"preparing", "approved", "ready"}:
                self.update(job["id"], {"status": "needs_attention", "error": "Browser session restarted. Resume preparation to verify the form."})


SIGNIN_CHOICES = {"No account needed / I don't have one": "none", "Sign in with Google": "google", "Email and password": "email_password",
                  "Sign in with Apple": "apple", "Other / not sure": "other"}
SHARED_ATS_HOSTS = ("greenhouse.io", "lever.co", "myworkdayjobs.com", "icims.com", "ashbyhq.com", "smartrecruiters.com",
                    "taleo.net", "workable.com", "jobvite.com", "bamboohr.com", "successfactors.com", "applytojob.com")
PORTALS = {"greenhouse": "greenhouse", "lever.co": "lever", "myworkday": "workday", "icims": "icims", "ashbyhq": "ashby",
           "smartrecruiters": "smartrecruiters", "taleo": "taleo", "workable": "workable", "jobvite": "jobvite",
           "bamboohr": "bamboohr", "successfactors": "successfactors"}


def portal_from_url(url):
    host = (urlsplit(url).hostname or "").lower()
    return next((v for k, v in PORTALS.items() if k in host), "other")


def ats_slug(url):
    """The company part of a shared ATS link, e.g. job-boards.greenhouse.io/andurilindustries -> andurilindustries."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    host = urlsplit(url).hostname or ""
    if "myworkdayjobs" in host or "icims" in host or "successfactors" in host:
        return host.split(".")[0]
    return parts[0] if parts and parts[0] not in {"jobs", "job", "careers", "o", "en-us", "en"} else (parts[1] if len(parts) > 1 else host)


def normalize_company(name):
    text = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    text = re.sub(r"\b(corporation|corp|incorporated|inc|llc|ltd|limited|co|company|industries|holdings|group|plc|technologies|technology)\b", " ", text)
    return re.sub(r"\s+", "", text).strip()


def package_hash(job):
    return digest({"url": job["url"], "package": job["package"], "referral_url": job["referral_url"]})


def submission_block(job):
    if job["status"] in {"submitted", "applied"}:
        return "This application is already submitted."
    if job["referral_status"] == "pending":
        return "Pending referral: release the hold before submitting."
    if job.get("fit_flags") and not job.get("fit_acknowledged"):
        return "Review the fit concerns first."
    p = job.get("package") or {}
    if not p or not p.get("form_ready"):
        return "Prepare and verify the final application form first."
    if p.get("unresolved"):
        return "Resolve the outstanding application questions first."
    if not p.get("visual_reviewed"):
        return "Review the exact resume PDF and confirm its layout."
    if job["status"] not in {"ready", "approved"}:
        return "This application is not ready for submission."
    return None
