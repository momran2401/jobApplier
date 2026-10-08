"""Google Sheets tracker through an Apps Script web app pasted into the sheet (tracker/Code.gs).

No Google Cloud project or OAuth client: the sheet's own script holds the access, and the app holds a
connection link (web-app URL + secret token) in the Keychain. Row matching and every safety rule live here;
the script only reads cells and writes the ones it is given, after checking the row still matches.
"""
import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .security import save_secret, secret
from .store import canonical_url, now

SCRIPT = Path(__file__).resolve().parent.parent / "tracker" / "Code.gs"
FIELDS = {
    "id": "Application ID", "company": "Company", "title": "Job title", "status": "Status", "created_at": "Date added",
    "submitted_at": "Submitted at", "updated_at": "Last update", "deadline": "Deadline", "location": "Location",
    "url": "Job URL", "application_url": "Application URL", "portal_url": "Candidate portal",
    "status_url": "Application status URL", "requisition_id": "Requisition ID", "signin_method": "Sign-in method",
    "account_email": "Account email", "account_created": "Account created", "referral_status": "Referral status",
    "referral_contact": "Referral contact", "referral_url": "Referral link", "referral_notes": "Referral review notes",
    "review_notes": "Application notes", "skills_used": "Skills used", "resume_version": "Resume version",
    "confirmation": "Confirmation", "company_linkedin_url": "Company LinkedIn URL",
    "contact_profile_url": "Contact profile URL", "outreach_status": "Outreach status",
    "last_contact_at": "Last contact", "followup_at": "Follow-up date",
    "mode": "Mode", "action_required": "Action required", "last_email": "Last email",
}
MODE = {"manual": "Applied myself", "advisor": "Advisor", "assisted": "AI Applier", "autonomous": "Autonomous", "": ""}
STATUS = {"new": "Queued", "preparing": "Preparing", "needs_attention": "Needs attention", "ready": "Ready to review",
          "applied": "Applied", "advised": "Advised", "rejected": "Rejected", "offer": "Offer",
          "approved": "Approved", "submitting": "Submitting", "submitted": "Submitted", "uncertain": "Check outcome"}
REFERRAL = {"not_sought": "Not sought", "pending": "Pending referral", "received": "Referral received",
            "proceed_without": "Proceed without referral"}
SIGNIN = {"unknown": "", "none": "No account needed", "google": "Google", "apple": "Apple", "email_password": "Email and password", "other": "Other"}
# Columns that belong to you (and the future networking features): filled once, never overwritten by sync.
PRESERVE = {"company_linkedin_url", "contact_profile_url", "outreach_status", "last_contact_at", "followup_at",
            "review_notes", "referral_notes", "referral_contact", "referral_url"}
HOLDS = {"pending", "pending referral", "seeking", "requested", "hold", "on hold"}
APPLIED = {"applied", "submitted", "application submitted", "interview", "interviewing", "offer", "rejected", "withdrawn"}


def parse_link(link):
    url, _, token = (link or "").strip().partition("#")
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != "script.google.com" or not p.path.endswith("/exec") or len(token) < 32:
        raise ValueError("Paste the full connection link from the sheet's JobApplier → Show connection link menu.")
    return url, token


def cell(value):
    """Text from websites must not become a formula in your sheet (e.g. a job title starting with '=')."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


class Tracker:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()

    def connected(self):
        return bool(secret("tracker_link"))

    async def request(self, method, body=None, link=None):
        url, token = parse_link(link or secret("tracker_link"))
        # Apps Script answers through a redirect to googleusercontent.com; follow it. Google intermittently
        # answers that hop with an HTML 404, so reads retry. Writes don't: a lost reply may follow a completed
        # write, and the next sync finds those rows by Application ID instead of adding them again.
        attempts = 4 if method == "GET" else 1
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            for attempt in range(attempts):
                if method == "GET":
                    r = await client.get(url, params={"token": token})
                else:
                    r = await client.post(url, json={**body, "token": token})
                if "json" in r.headers.get("content-type", ""):
                    break
                if attempt < attempts - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
        if "json" not in r.headers.get("content-type", ""):
            raise ValueError("Google didn't return the tracker's response (it does this occasionally). Try again; "
                             "if it keeps failing, check the web app is deployed with access set to Anyone.")
        data = r.json()
        if data.get("error"):
            raise ValueError(data["error"])
        return data

    async def connect(self, link):
        parse_link(link)
        info = await self.request("GET", link=link)
        save_secret("tracker_link", link.strip())
        settings = self.store.settings()
        self.store.set("settings", {**settings, "sheet_sync_enabled": True})
        self.store.set("tracker", {"sheet": info.get("sheet", ""), "connected_at": now()})
        return {"sheet": info.get("sheet", ""), "rows": len(info.get("rows", []))}

    def disconnect(self):
        save_secret("tracker_link", "")
        self.store.set("settings", {**self.store.settings(), "sheet_sync_enabled": False})
        self.store.set("tracker", None)

    async def rows(self):
        if not self.store.settings()["sheet_sync_enabled"] or not self.connected():
            raise ValueError("Connect your tracker sheet first.")
        return (await self.request("GET")).get("rows", [])

    @staticmethod
    def row_value(row, key):
        return row.get(FIELDS[key], "") if row else ""

    def find_row(self, job, rows):
        ids = [i for i, row in enumerate(rows) if self.row_value(row, "id") == job["id"]]
        if len(ids) > 1:
            raise ValueError("Duplicate application IDs in the sheet. Resolve them before syncing.")
        if ids:
            return ids[0]
        matches = []
        for i, row in enumerate(rows):
            if self.row_value(row, "id"):
                continue
            for k in ("url", "application_url"):
                raw = str(self.row_value(row, k))
                hyperlink = re.match(r'^=HYPERLINK\("([^"]+)"', raw, re.IGNORECASE)
                raw = hyperlink.group(1) if hyperlink else raw
                try:
                    if raw and canonical_url(raw) in {job["url"], job.get("application_url")}:
                        matches.append(i)
                        break
                except ValueError:
                    pass
        if len(matches) > 1:
            raise ValueError("Multiple existing rows match this job link. Resolve the duplicates first.")
        return matches[0] if matches else None

    def remote_hold(self, row):
        return str(self.row_value(row, "referral_status")).strip().lower() in HOLDS

    def remote_submitted(self, row):
        return str(self.row_value(row, "status")).strip().lower() in APPLIED

    def import_remote(self, job, row):
        changes = {}
        if self.remote_submitted(row) and job["status"] not in {"submitted", "submitting", "applied"}:
            changes.update(status="submitted", confirmation="Existing application recorded in your tracker",
                           submitted_at=str(self.row_value(row, "submitted_at")) or None)
        if self.remote_hold(row) and job["referral_status"] != "pending":
            changes["referral_status"] = "pending"
        # Pull manual networking context without overwriting a local value.
        for key in ("referral_url", "referral_contact", "referral_notes", "review_notes", "account_email", "portal_url", "status_url"):
            value = self.row_value(row, key)
            if value and not str(value).startswith("=") and not job.get(key):
                changes[key] = value
        if changes:
            job = self.store.update(job["id"], changes)
        return job

    @staticmethod
    def expect(row, key):
        """What the script must still see in this row before writing (guards against sorting mid-sync)."""
        return {FIELDS[key]: row.get(FIELDS[key], "")}

    def values(self, job):
        p = job.get("package") or {}
        v = {k: job.get(k, "") or "" for k in FIELDS}
        v.update(status=STATUS.get(job["status"], job["status"]),
                 referral_status=REFERRAL.get(job.get("referral_status"), job.get("referral_status") or ""),
                 signin_method=SIGNIN.get(job.get("signin_method"), job.get("signin_method") or ""),
                 account_created="Yes" if job.get("account_created") else "No",
                 skills_used=", ".join(p.get("selected_skills") or []),
                 mode=MODE.get(job.get("mode") or "", job.get("mode") or ""),
                 action_required=(job.get("action_required") or {}).get("message", "") if isinstance(job.get("action_required"), dict) else "",
                 last_email=(job.get("last_email") or {}).get("subject", "") if isinstance(job.get("last_email"), dict) else "",
                 resume_version=((p.get("resume") or {}).get("sha256") or "")[:12])
        return v

    async def write_referral(self, job, changes):
        """Explicit user edits may release a remote hold; routine sync never does."""
        if not self.store.settings()["sheet_sync_enabled"] or not self.connected():
            return
        async with self.lock:
            rows = await self.rows()
            i = self.find_row(job, rows)
            if i is None:
                return
            row, values = rows[i], {}
            for k in ("referral_status", "referral_url", "referral_contact", "referral_notes"):
                if k in changes:
                    if str(self.row_value(row, k)).startswith("="):
                        raise ValueError("The referral column contains a formula. Edit it in the sheet instead.")
                    value = REFERRAL.get(changes[k], changes[k]) if k == "referral_status" else changes[k]
                    values[FIELDS[k]] = cell(value)
            if values:
                key = "id" if self.row_value(row, "id") else "url"
                await self.request("POST", {"headers": list(FIELDS.values()),
                                            "updates": [{"row": row["_row"], "expect": self.expect(row, key), "values": values}]})

    async def check_hold(self, job):
        if not self.store.settings()["sheet_sync_enabled"] or not self.connected():
            return
        rows = await self.rows()
        i = self.find_row(job, rows)
        if i is not None and self.remote_submitted(rows[i]):
            self.import_remote(job, rows[i])
            raise ValueError("Your tracker already records an application for this job. It will not be submitted again.")
        if i is not None and self.remote_hold(rows[i]):
            self.store.update(job["id"], {"referral_status": "pending"})
            raise ValueError("This application is marked Pending referral in your tracker.")

    async def sync(self):
        async with self.lock:
            rows = await self.rows()
            updates, appends, synced, errors = [], [], [], []
            for job in self.store.jobs():
                try:
                    i = self.find_row(job, rows)
                    row = rows[i] if i is not None else None
                    if row:
                        job = self.import_remote(job, row)
                    values = self.values(job)
                    preserve = set(PRESERVE)
                    if row and self.remote_submitted(row):
                        preserve |= {"status", "submitted_at", "confirmation"}
                    if row is None:
                        appends.append({"values": {FIELDS[k]: cell(v) for k, v in values.items()}})
                    else:
                        changed = {FIELDS[k]: cell(v) for k, v in values.items()
                                   if not str(row.get(FIELDS[k], "")).startswith("=")
                                   and not (k in preserve and row.get(FIELDS[k]))
                                   and str(row.get(FIELDS[k], "")) != str(v)}
                        key = "id" if self.row_value(row, "id") else "url"
                        if changed:
                            updates.append({"row": row["_row"], "expect": self.expect(row, key), "values": changed})
                    synced.append(job["id"])
                except ValueError as exc:
                    errors.append({"id": job["id"], "message": str(exc)[:300]})
            if updates or appends:
                await self.request("POST", {"headers": list(FIELDS.values()), "updates": updates, "appends": appends})
            for jid in synced:
                self.store.update(jid, {"sync_status": "synced", "sync_error": "", "last_sync_at": now()}, invalidate=False)
            for e in errors:
                self.store.update(e["id"], {"sync_status": "error", "sync_error": e["message"]}, invalidate=False)
            return {"synced": len(synced), "errors": errors}
