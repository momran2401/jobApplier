import asyncio
import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from jobapplier.app import create_app
from jobapplier.browser import Browser
from jobapplier.documents import latex_escape, replace_skills, safe_unzip
from jobapplier.store import Store, canonical_url, package_hash, submission_block
from jobapplier.tracker import FIELDS, Tracker, parse_link
from jobapplier.workflow import Workflow


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


def ready_job(store):
    job, _ = store.add_job("https://jobs.example.com/123")
    return store.update(job["id"], {"status": "ready", "package": {
        "form_ready": True, "visual_reviewed": True, "unresolved": [], "resume": {"sha256": "abc"}}})


def test_canonicalization_preserves_requisition_identity(store):
    a, created = store.add_job("https://example.com/job?id=123&utm_source=email")
    b, created_again = store.add_job("https://example.com/job?id=123")
    c, _ = store.add_job("https://example.com/job?id=456")
    assert created and not created_again
    assert a["id"] == b["id"] != c["id"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "https://user:password@example.com"])
def test_reject_unsafe_job_urls(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_referral_hold_wins_over_approval(store):
    job = ready_job(store)
    job = store.update(job["id"], {"approval": {"hash": package_hash(job)}, "status": "approved"}, invalidate=False)
    job = store.update(job["id"], {"referral_status": "pending"})
    assert job["approval"] is None
    assert "referral" in submission_block(job).lower()


def test_revision_conflict_does_not_overwrite(store):
    job = ready_job(store)
    store.update(job["id"], {"review_notes": "new note"})
    with pytest.raises(ValueError, match="changed"):
        store.update(job["id"], {"review_notes": "stale note"}, expected_revision=job["revision"])
    assert store.job(job["id"])["review_notes"] == "new note"


def test_package_edits_change_approval_hash(store):
    job = ready_job(store)
    old = package_hash(job)
    job["package"]["answers"] = [{"question": "Relocate?", "answer": "Yes"}]
    assert package_hash(job) != old


@pytest.mark.parametrize("status,expected", [("submitting", "uncertain"), ("preparing", "needs_attention"), ("approved", "needs_attention"), ("ready", "needs_attention"), ("submitted", "submitted")])
def test_restart_does_not_resubmit(store, status, expected):
    job = ready_job(store)
    store.update(job["id"], {"status": status}, invalidate=False)
    store.recover()
    assert store.job(job["id"])["status"] == expected


def test_pdf_review_required(store):
    job = ready_job(store)
    job["package"]["visual_reviewed"] = False
    assert "PDF" in submission_block(job)


def test_zip_path_traversal_and_bomb_limits(tmp_path):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("../../evil.tex", "bad")
    with pytest.raises(ValueError, match="unsafe"):
        safe_unzip(payload.getvalue(), tmp_path)


def test_skills_replacement_preserves_everything_else():
    source = "Experience must stay.\n% START\nold skills\n% END\nEducation must stay."
    result = replace_skills(source, "% START", "% END", ["C++", "R&D", "Python_3"])
    assert result == "Experience must stay.\n% START\nC++, R\\&D, Python\\_3\n% END\nEducation must stay."
    assert r"\textbackslash{}input" == latex_escape(r"\input")
    with pytest.raises(ValueError):
        replace_skills(source, "missing", "% END", [])


LINK = "https://script.google.com/macros/s/abc/exec#" + "t" * 40


def row(n, **values):
    return {"_row": n, **{FIELDS[k]: v for k, v in values.items()}}


@pytest.fixture
def tracker(store, monkeypatch):
    store.set("settings", {**store.settings(), "sheet_sync_enabled": True})
    t = Tracker(store)
    monkeypatch.setattr(t, "connected", lambda: True)
    t.request = AsyncMock(return_value={})
    return t


def test_tracker_matching_after_sort_and_link_validation(store, tracker):
    job = ready_job(store)
    assert tracker.find_row(job, [row(2, id="other", url="https://example.com/2"), row(3, id=job["id"], url=job["url"])]) == 1
    assert tracker.find_row(job, [row(2, url='=HYPERLINK("https://jobs.example.com/123","Apply")')]) == 0
    assert tracker.remote_hold(row(2, referral_status="Pending referral"))
    assert parse_link(LINK)[1] == "t" * 40
    with pytest.raises(ValueError):
        parse_link("https://evil.example.com/exec#" + "t" * 40)


@pytest.mark.asyncio
async def test_remote_hold_blocks_submission(store, tracker):
    job = ready_job(store)
    tracker.rows = AsyncMock(return_value=[row(2, id=job["id"], referral_status="Pending referral")])
    with pytest.raises(ValueError, match="Pending referral"):
        await tracker.check_hold(job)
    assert store.job(job["id"])["referral_status"] == "pending"


@pytest.mark.asyncio
async def test_explicit_release_updates_remote_hold(store, tracker):
    job = ready_job(store)
    tracker.rows = AsyncMock(return_value=[row(5, id=job["id"], referral_status="Pending referral")])
    await tracker.write_referral(job, {"referral_status": "proceed_without"})
    body = tracker.request.call_args.args[1]
    assert body["updates"] == [{"row": 5, "expect": {"Application ID": job["id"]},
                                "values": {"Referral status": "Proceed without referral"}}]


@pytest.mark.asyncio
async def test_legacy_applied_row_blocks_duplicate(store, tracker):
    job = ready_job(store)
    tracker.rows = AsyncMock(return_value=[row(2, url=job["url"], status="Applied")])
    with pytest.raises(ValueError, match="already records"):
        await tracker.check_hold(job)
    assert store.job(job["id"])["status"] == "submitted"


@pytest.mark.asyncio
async def test_sync_preserves_formulas_notes_and_guards_rows(store, tracker):
    job = ready_job(store)
    other, _ = store.add_job("https://jobs.example.com/456")
    store.update(other["id"], {"title": "=IMPORTXML(\"https://evil.example\")"})
    tracker.rows = AsyncMock(return_value=[row(2, id=job["id"], url=job["url"], title='=CONCAT("Existing"," title")',
                                               review_notes="My own notes")])
    result = await tracker.sync()
    assert result["synced"] == 2
    body = tracker.request.call_args.args[1]
    update = body["updates"][0]
    assert update["row"] == 2 and update["expect"] == {"Application ID": job["id"]}
    assert "Job title" not in update["values"] and "Application notes" not in update["values"]
    assert update["values"]["Status"] == "Needs attention"  # pulling notes in counts as an edit
    assert store.job(job["id"])["review_notes"] == "My own notes"
    # Text scraped from websites can't become a live formula in the sheet.
    assert body["appends"][0]["values"]["Job title"].startswith("'=")


def snapshot(value="Ada"):
    return {"url": "https://jobs.example.com/apply", "frames": [{"frame": 0, "fields": [{"index": 0, "label": "First name", "type": "text", "value": value, "required": True, "options": []}], "buttons": [{"index": 0, "label": "Submit application"}]}]}


def test_browser_fingerprint_detects_form_changes():
    assert Browser.fingerprint(snapshot()) != Browser.fingerprint(snapshot("Someone else"))


@pytest.mark.asyncio
async def test_prompt_injection_cannot_click_submit(store):
    browser = Browser(store)
    page = type("Page", (), {"frames": [object()]})()
    s = snapshot()
    s["frames"][0]["buttons"][0]["disabled"] = False
    with pytest.raises(ValueError, match="not an allowed"):
        await browser.act(page, s, {"action": "next", "index": 0}, {}, {})
    with pytest.raises(ValueError, match="Unsupported"):
        await browser.act(page, s, {"action": "execute_javascript", "code": "submit()"}, {}, {})


@pytest.mark.asyncio
async def test_batch_timeout_is_uncertain_and_never_retried(store):
    job = ready_job(store)
    job = store.update(job["id"], {"status": "approved", "approval": {"hash": package_hash(job)}}, invalidate=False)
    browser = type("FakeBrowser", (), {"lock": asyncio.Lock(), "verify": AsyncMock(), "submit": AsyncMock(side_effect=TimeoutError("Submission timed out"))})()
    sheets = type("FakeSheets", (), {"check_hold": AsyncMock()})()
    workflow = Workflow(store, browser, sheets)
    await workflow._submit_batch([job["id"]])
    assert store.job(job["id"])["status"] == "uncertain"
    assert browser.submit.call_count == 1
    with pytest.raises(ValueError):
        workflow.submit_batch([job["id"]])


@pytest.mark.asyncio
async def test_modified_approval_cannot_enter_queue(store):
    job = ready_job(store)
    store.update(job["id"], {"status": "approved", "approval": {"hash": "wrong"}}, invalidate=False)
    workflow = Workflow(store, None, None)
    with pytest.raises(ValueError, match="current approval"):
        workflow.submit_batch([job["id"]])


def test_local_api_auth_csrf_and_profile_invalidation(tmp_path):
    with patch("jobapplier.app.secret", return_value=None):
        app = create_app(tmp_path)
        with TestClient(app) as client:
            assert client.get("/api/state").status_code == 401
            t = client.get("/api/bootstrap").json()["token"]
            headers = {"x-workspace-token": t}
            assert client.post("/api/jobs", json={"urls": ["https://example.com/job"]}).status_code == 401
            assert client.post("/api/jobs", headers={**headers,"origin":"https://evil.example"}, json={"urls": []}).status_code == 403
            response = client.post("/api/jobs", headers=headers, json={"urls": ["https://example.com/job"]})
            assert response.status_code == 200
            jid = response.json()["imported"][0]
            assert client.get("/api/state").json()["jobs"][0]["id"] == jid
            assert client.post(f"/api/jobs/{jid}/approve", headers=headers, json={"revision": 1}).status_code == 400
            doc = client.get("/api/profile/doc", headers=headers).json()
            assert "## Identity" in doc["text"] and doc["verified"] is False
            saved = client.put("/api/profile/doc", headers=headers, json={"text": doc["text"].replace("- Email: ", "- Email: me@example.com"), "verified": True}).json()
            assert saved["verified"] is True and saved["identity"]["email"] == "me@example.com" and saved["version"] != doc["version"]
            assert client.put("/api/profile/doc", headers=headers, json={"text": "no headings"}).status_code == 400
            assert client.get("/api/export").json()["schema_version"] == 1


@pytest.mark.asyncio
async def test_tracker_retries_flaky_reads_but_not_writes(store, monkeypatch):
    import httpx

    from jobapplier import tracker as tracker_module
    calls = []

    def handler(request):
        calls.append(request.method)
        if len(calls) == 1 or request.method == "POST":
            return httpx.Response(404, html="<html>Google error</html>")
        return httpx.Response(200, json={"sheet": "S", "rows": []})
    original = httpx.AsyncClient
    monkeypatch.setattr(tracker_module.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(tracker_module.asyncio, "sleep", AsyncMock())
    t = Tracker(store)
    assert (await t.request("GET", link=LINK))["sheet"] == "S"
    with pytest.raises(ValueError, match="Try again"):
        await t.request("POST", {"updates": []}, link=LINK)
    assert calls == ["GET", "GET", "POST"]


def test_profile_document_parsing_and_append(store):
    from jobapplier import profile as pd
    text = store.profile_text()
    assert [f"## {n}" in text for n in pd.SECTIONS] == [True] * len(pd.SECTIONS)
    text, n = pd.append(text, {"Skills": ["- Programming: Python, C++", "Fusion 360"], "Answers": ["- Sponsorship?: No", "not a kv line"],
                               "Bogus": ["ignored"]}, {"email": "me@example.com", "linkedin_url": "https://linkedin.com/in/me"})
    assert n == 5
    bank = pd.answer_bank(text)
    assert bank["identity.email"] == "me@example.com" and bank["links.linkedin_url"].endswith("/me") and bank["answers.Sponsorship?"] == "No"
    assert pd.skills_list(text) == ["Python", "C++", "Fusion 360"]
    # Appending the same things again changes nothing, and an answered identity key keeps the user's value.
    again, n2 = pd.append(text, {"Skills": ["- programming: python, c++"]}, {"email": "other@example.com"})
    assert n2 == 0 and again == text
    store.save_profile_text(text, reason="test")
    assert store.profile()["identity"]["email"] == "me@example.com" and len(store.profile_history()) == 2


def test_legacy_profile_migrates_into_document(tmp_path):
    first = Store(tmp_path)
    first.set("profile", {"facts": {"first_name": "Ada", "email": "ada@example.com"}, "skills": ["Python", {"name": "VHDL"}],
                          "experience": [{"description": "Built a CPU"}], "answers": {"Sponsorship?": "No"}, "verified": True})
    first.profile_path.unlink()
    second = Store(tmp_path)  # fresh start generates the document from the old profile
    text = second.profile_text()
    assert "- First name: Ada" in text and "- Python, VHDL" in text and "- Built a CPU" in text and "- Sponsorship?: No" in text
    assert second.profile()["verified"] is True


def test_companies_group_jobs_by_name_or_domain(store):
    a, _ = store.add_job("https://job-boards.greenhouse.io/andurilindustries/jobs/1")
    b, _ = store.add_job("https://job-boards.greenhouse.io/andurilindustries/jobs/2")
    c, _ = store.add_job("https://jobs.nvidia.com/careers/job/1")
    assert store.company_for(a)["id"] == store.company_for(b)["id"]
    assert store.company_for(a)["portal"] == "greenhouse" and store.company_for(a)["name"] == "andurilindustries"
    store.update(b["id"], {"company": "Anduril Industries"})
    assert store.company_for(store.job(b["id"]))["id"] == store.company_for(a)["id"]
    nvidia = store.link_job_company(c["id"])
    assert nvidia["domain"] == "jobs.nvidia.com" and store.job(c["id"])["company_id"] == nvidia["id"]
    q = store.add_question(nvidia["id"], "Do you already have an account?", c["id"])
    assert store.add_question(nvidia["id"], "do you already have an account? ")["id"] == q["id"]
    store.answer_question(nvidia["id"], q["id"], "Yes")
    assert store.company(nvidia["id"])["questions"][0]["answer"] == "Yes"


def test_company_credentials_stay_out_of_state(tmp_path):
    saved = {}
    with patch("jobapplier.app.secret", return_value=None), patch("jobapplier.app.save_secret", side_effect=lambda k, v: saved.__setitem__(k, v)):
        with TestClient(create_app(tmp_path)) as client:
            h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
            jid = client.post("/api/jobs", headers=h, json={"urls": ["https://jobs.example.com/1"]}).json()["imported"][0]
            client.app.state.store.link_job_company(jid)
            cid = client.get("/api/state", headers=h).json()["companies"][0]["id"]
            r = client.post(f"/api/companies/{cid}/credential", headers=h, json={"email": "me@example.com", "password": "hunter2"})
            assert r.json()["credential"] is True and r.json()["signin_method"] == "email_password"
            assert "hunter2" in saved["company:" + cid]
            assert "hunter2" not in client.get("/api/state", headers=h).text
            assert client.patch(f"/api/companies/{cid}", headers=h, json={"signin_method": "sso"}).status_code == 400


def test_log_application_records_applied_job_and_company_facts(tmp_path):
    with patch("jobapplier.app.secret", return_value=None):
        with TestClient(create_app(tmp_path)) as client:
            h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
            wf = client.app.state.workflow
            wf.run = AsyncMock()  # no network research in tests
            body = {"url": "https://jobs.example.com/careers/42", "applied_at": "2026-10-01", "signin_method": "google",
                    "account_email": "me@example.com", "referral_status": "pending", "referral_contact": "Sam", "notes": "Via career fair",
                    "title": "Firmware Intern", "company": "Example Robotics"}
            job = client.post("/api/jobs/log", headers=h, json=body).json()
            assert job["status"] == "applied" and job["mode"] == "manual" and job["submitted_at"] == "2026-10-01"
            assert job["title"] == "Firmware Intern" and job["review_notes"] == "Via career fair" and job["referral_contact"] == "Sam"
            company = client.get("/api/state", headers=h).json()["companies"][0]
            assert company["name"] == "Example Robotics" and company["signin_method"] == "google" and company["google_signin"] is True
            assert company["account_email"] == "me@example.com" and company["has_account"] is True
            assert client.post("/api/jobs/log", headers=h, json=body).status_code == 400  # already recorded
            assert client.post(f"/api/jobs/{job['id']}/prepare", headers=h).status_code == 400  # never re-applies
            assert submission_block(job).startswith("This application is already submitted")
            from jobapplier.tracker import Tracker
            values = Tracker(client.app.state.store).values(job)
            assert values["Status"] == "Applied" if "Status" in values else values["status"] == "Applied"
            assert values["mode"] == "Applied myself" and values["referral_status"] == "Pending referral"


@pytest.mark.asyncio
async def test_research_keeps_applied_status(store, monkeypatch):
    job, _ = store.add_job("https://jobs.example.com/1")
    store.update(job["id"], {"status": "applied", "mode": "manual"}, invalidate=False)
    wf = Workflow(store, None, None)

    async def fake_research(jid, model=None, text=None):
        store.update(jid, {"title": "Found title"}, invalidate=False)
    monkeypatch.setattr(wf, "research", fake_research)
    await wf.run(job["id"], "research")
    assert store.job(job["id"])["status"] == "applied" and store.job(job["id"])["title"] == "Found title"


def test_skills_renderer_keeps_languages_and_escapes():
    from jobapplier.documents import apply_edits, render_skills, trim_one, validate_edits
    original = "\n\\textbf{Programming/Software:}\nPython\\par\n\n\\textbf{Languages:}\nEnglish, French\n\n"
    block = render_skills([{"name": "Programming", "items": ["Python", "C++ & Rust"]}, {"name": "Languages", "items": ["Klingon"]}], original)
    assert "\\textbf{Programming:}\nPython, C++ \\& Rust\\par" in block
    assert "English, French" in block and "Klingon" not in block
    cats = [{"name": "A", "items": ["1", "2"]}, {"name": "B", "items": ["x"]}]
    assert trim_one(cats) == ([{"name": "A", "items": ["1"]}, {"name": "B", "items": ["x"]}], "A: 2")
    source = "Built a CPU\nBuilt a CPU\nLed a team"
    assert validate_edits(source, [{"original": "Built a CPU", "proposed": "x"}, {"original": "Led a team", "proposed": "Led a team of 4", "reason": "r"}]) == \
        [{"original": "Led a team", "proposed": "Led a team of 4", "reason": "r"}]
    assert apply_edits(source, [{"original": "Led a team", "proposed": "Led a team of 4"}]).endswith("Led a team of 4")
    with pytest.raises(ValueError):
        apply_edits(source, [{"original": "Built a CPU", "proposed": "x"}])


@pytest.mark.asyncio
async def test_one_page_rule_trims_skills_until_it_fits(store, monkeypatch):
    from jobapplier import documents
    from jobapplier.documents import save_upload
    tex = "\\documentclass{article}\\begin{document}Hi\\section{TECHNICAL SKILLS}\n\\textbf{Old:}\nA\\par\n\\end{document}"
    save_upload(store, "cv.tex", "latex", tex.encode())
    store.set("settings", {**store.settings(), "tex_main": "main.tex", "skills_start": "\\section{TECHNICAL SKILLS}", "skills_end": "\\end{document}"})
    pages = iter([1, 2, 2, 1])  # baseline, then three tailored compiles

    async def fake_compile(directory, main):
        pdf = directory / "main.pdf"
        pdf.write_bytes(b"%PDF-fake")
        return pdf, next(pages)
    monkeypatch.setattr(documents, "compile_tex", fake_compile)
    job, _ = store.add_job("https://jobs.example.com/1")
    cats = [{"name": "Programming", "items": ["Python", "C++", "Rust"]}, {"name": "Embedded", "items": ["ESP32"]}]
    result = await documents.build_resume(store, job["id"], categories=cats)
    assert result["pages"] == 1 and result["dropped"] == ["Programming: Rust", "Programming: C++"] and result["warning"] == ""
    assert result["categories"] == [{"name": "Programming", "items": ["Python"]}, {"name": "Embedded", "items": ["ESP32"]}]
    assert "\\textbf{Programming:}\nPython\\par" in result["skills_block"] and result["tex_path"].endswith("resume.tex")
    assert (store.root / result["tex_path"]).read_text().count("\\textbf{Old:}") == 0


@pytest.mark.asyncio
async def test_advise_produces_package_without_browser(store, monkeypatch):
    from jobapplier import ai
    from jobapplier import workflow as wf_module
    store.save_profile_text(store.profile_text().replace("## Skills\n", "## Skills\n- Programming: Python, C++\n- Embedded: ESP32\n"), "seed")
    job, _ = store.add_job("https://jobs.example.com/1")
    store.update(job["id"], {"description": "We need Python and a cover letter.", "company": "Example Robotics", "requirements": ["Python"]}, invalidate=False)
    calls = []

    async def fake_json(self, task, data, stable=None, local_ok=True, fast=False):
        calls.append(task.split("\n")[0][:40])
        if task.startswith("Tailor"):
            return {"categories": [{"name": "Programming", "items": ["Python", "Fortran"]}], "tailoring_summary": "Python first.",
                    "suggested_edits": [{"original": "nope", "proposed": "x", "reason": "r"}]}
        if task.startswith("Summarize this employer"):
            return {"summary": "Builds robots.", "products": ["Arms"], "hq": "Austin", "size": "", "website": "example-robotics.com", "hiring_notes": []}
        if task.startswith("Draft a cover letter"):
            return {"letter": "Dear team,\n\nI build robots.\n\nThanks."}
        if task.startswith("List the questions"):
            return {"questions": [{"question": "Sponsorship?", "answer": "No", "confidence": "high"}]}
        return {}
    monkeypatch.setattr(ai.Model, "json", fake_json)

    async def fake_build(store_, jid, skills=None, *, categories=None, edits=None, max_pages=1, attempts=8):
        return {"path": "artifacts/x/resume.pdf", "sha256": "abc", "pages": 1, "diff": "", "warning": "", "dropped": [],
                "skills_block": "\\textbf{Programming:}\nPython\\par", "tex_path": "artifacts/x/resume.tex", "categories": categories}
    monkeypatch.setattr(wf_module, "build_resume", fake_build)

    async def fake_letter(store_, jid, text, identity, date_text):
        return {"path": "artifacts/x/letter.pdf", "sha256": "def"}
    monkeypatch.setattr(wf_module, "build_letter", fake_letter)
    wf = Workflow(store, None, None)
    await wf.run(job["id"], "advise")
    j = store.job(job["id"])
    assert j["status"] == "advised" and j["error"] == ""
    p = j["package"]
    assert p["categories"] == [{"name": "Programming", "items": ["Python"]}]  # Fortran isn't in the profile
    assert p["suggested_edits"] == []  # the snippet wasn't in the resume source
    assert p["letter"].startswith("Dear team") and p["letter_pdf"]["path"].endswith("letter.pdf")
    assert p["likely_questions"][0]["answer"] == "No" and p["skills_block"] if "skills_block" in p else p["resume"]["skills_block"]
    company = store.company_for(j, create=False)
    assert company["research"]["summary"] == "Builds robots." and company["research"]["website"] == "example-robotics.com"
    assert company["domain"] == "jobs.example.com"  # a company-run careers host stays the matching domain
    assert "Summarize this employer" in " ".join(calls) and len(calls) == 4


def snapshot_login(google=True, signup=False, otp=False):
    fields = [{"index": 0, "label": "Email", "type": "email", "value": "", "required": True, "options": [], "disabled": False},
              {"index": 1, "label": "Password", "type": "password", "value": "[REDACTED]", "required": True, "options": [], "disabled": False}]
    if signup:
        fields.append({"index": 2, "label": "First name", "type": "text", "value": "", "required": True, "options": [], "disabled": False})
    buttons = [{"index": 0, "label": "Sign in", "disabled": False}] + ([{"index": 1, "label": "Continue with Google", "disabled": False}] if google else [])
    text = "Create an account to apply" if signup else "Enter the verification code we sent" if otp else "Welcome back"
    if otp:
        fields = [{"index": 0, "label": "Verification code", "type": "text", "value": "", "required": True, "options": [], "disabled": False}]
    return {"url": "https://jobs.example.com/login", "frames": [{"frame": 0, "text": text, "fields": fields, "buttons": buttons, "links": [], "options": []}]}


def test_detect_auth_classifies_login_signup_and_otp():
    a = Browser.detect_auth(snapshot_login())
    assert a["login"] and not a["signup"] and a["google"] == 1 and a["email"] == 0 and a["password"] == 1
    assert Browser.detect_auth(snapshot_login(signup=True))["signup"] is True
    assert Browser.detect_auth(snapshot_login(otp=True))["otp"] is True
    assert Browser.detect_auth(snapshot()) is None  # an ordinary form


@pytest.mark.asyncio
async def test_prepare_signs_in_with_saved_login_but_model_never_sees_it(store, monkeypatch):
    from jobapplier import workflow as wf_module
    job, _ = store.add_job("https://jobs.example.com/1")
    company = store.link_job_company(job["id"])
    store.save_company({**company, "credential": True, "signin_method": "email_password", "has_account": True})
    monkeypatch.setattr(wf_module, "company_credential", lambda cid: {"email": "me@example.com", "password": "hunter2"})
    calls = []
    browser = type("B", (), {"detect_auth": staticmethod(Browser.detect_auth), "click_button": AsyncMock(),
                             "signin": AsyncMock(side_effect=lambda page, snap, auth, cred: calls.append(("signin", cred["email"]))),
                             "google_chooser": AsyncMock(return_value=False)})()
    wf = Workflow(store, browser, None)
    page = type("P", (), {"url": "https://jobs.example.com/login"})()
    package = {}
    msg = await wf.handle_auth(job["id"], page, snapshot_login(google=False), store.company(company["id"]), store.profile_text(), package)
    assert "saved login" in msg and calls == [("signin", "me@example.com")] and package["signin_attempts"] == 1
    # Google preferred when the company says so
    store.save_company({**store.company(company["id"]), "google_signin": True})
    msg = await wf.handle_auth(job["id"], page, snapshot_login(), store.company(company["id"]), store.profile_text(), package)
    assert "Google" in msg and browser.click_button.await_count == 1
    # OTP always hands off, and the hand-off records the question on the package
    with pytest.raises(ValueError, match="verification code"):
        await wf.handle_auth(job["id"], page, snapshot_login(otp=True), store.company(company["id"]), store.profile_text(), package)
    assert store.job(job["id"])["package"]["unresolved"][0].startswith("This step needs")
    assert "hunter2" not in str(store.job(job["id"]))


@pytest.mark.asyncio
async def test_prepare_waits_for_company_setup_answer(store, monkeypatch):
    job, _ = store.add_job("https://jobs.example.com/1")
    store.set("profile_meta", {**store.get("profile_meta", {}), "verified": True})
    wf = Workflow(store, type("B", (), {"lock": asyncio.Lock()})(), None)
    with pytest.raises(ValueError, match="How do you sign in"):
        await wf.prepare(job["id"])
    company = store.company_for(store.job(job["id"]))
    q = store.setup_pending(company["id"])[0]
    assert q["choices"] and q["field"] == "signin_method"
    store.answer_question(company["id"], q["id"], "Sign in with Google")
    company = store.company(company["id"])
    assert company["signin_method"] == "google" and company["google_signin"] and company["has_account"] and not store.setup_pending(company["id"])
    assert store.setup_questions(company["id"]) == []  # asked once


def test_accepted_edits_force_rebuild(tmp_path):
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        jid = client.post("/api/jobs", headers=h, json={"urls": ["https://jobs.example.com/1"]}).json()["imported"][0]
        store = client.app.state.store
        job = store.update(jid, {"status": "ready", "package": {"resume": {"sha256": "x"}, "form_ready": True, "visual_reviewed": True,
                                 "suggested_edits": [{"original": "a", "proposed": "b", "reason": "r"}, {"original": "c", "proposed": "d", "reason": "r"}]}})
        r = client.post(f"/api/jobs/{jid}/edits", headers=h, json={"accepted": [1], "revision": job["revision"]}).json()
        assert r["package"]["accepted_edits"] == [{"original": "c", "proposed": "d", "reason": "r"}]
        assert r["package"]["resume"] is None and r["status"] == "needs_attention" and r["package"]["visual_reviewed"] is False


def test_job_question_answer_lands_in_profile_and_unblocks_job(tmp_path):
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        jid = client.post("/api/jobs", headers=h, json={"urls": ["https://jobs.example.com/1"]}).json()["imported"][0]
        store = client.app.state.store
        store.update(jid, {"status": "needs_attention", "error": "Are you willing to relocate?",
                           "package": {"unresolved": ["Are you willing to relocate?"], "suggested_answer": "Yes"}})
        r = client.post(f"/api/jobs/{jid}/answer", headers=h, json={"answer": "Yes, anywhere in the US"}).json()
        assert r["package"]["unresolved"] == [] and r["error"] == ""
        assert "- Are you willing to relocate: Yes, anywhere in the US" in store.profile_text()
        from jobapplier import profile as pd
        assert pd.answer_bank(store.profile_text())["answers.Are you willing to relocate"] == "Yes, anywhere in the US"


@pytest.fixture
def auto_workflow(store, monkeypatch):
    """A workflow whose prepare() is faked to produce a complete, ready package; submit is a mock."""
    store.set("settings", {**store.settings(), "autonomous_delay_minutes": 0, "autonomous_daily_cap": 2})
    store.set("profile_meta", {**store.get("profile_meta", {}), "verified": True})
    browser = type("B", (), {"lock": asyncio.Lock(), "verify": AsyncMock(),
                             "submit": AsyncMock(return_value={"confirmed": True, "confirmation": "Thanks for applying", "status_url": "https://x", "receipt": ""})})()
    sheets = type("S", (), {"check_hold": AsyncMock(), "sync": AsyncMock()})()
    wf = Workflow(store, browser, sheets)
    outcome = {"ready": True, "flags": [], "unresolved": [], "pages": 1}

    async def fake_prepare(jid):
        job = store.job(jid)
        store.update(jid, {"status": "ready" if outcome["ready"] else "needs_attention", "fit_flags": outcome["flags"],
                           "package": {**(job.get("package") or {}), "form_ready": outcome["ready"], "unresolved": outcome["unresolved"],
                                       "visual_reviewed": False, "resume": {"sha256": "r", "pages": outcome["pages"], "warning": ""}}}, invalidate=False)
    monkeypatch.setattr(wf, "prepare", fake_prepare)
    return wf, browser, outcome


@pytest.mark.asyncio
async def test_autonomous_submits_when_every_check_passes(store, auto_workflow):
    wf, browser, _ = auto_workflow
    job, _ = store.add_job("https://jobs.example.com/1")
    store.update(job["id"], {"mode": "autonomous", "package": {"accepted_edits": [{"original": "a", "proposed": "b"}]}}, invalidate=False)
    await wf.run(job["id"], "autonomous")
    j = store.job(job["id"])
    assert j["status"] == "submitted" and browser.submit.await_count == 1
    assert j["package"]["accepted_edits"] == [] and j["package"]["auto_reviewed_at"]  # never edits experience


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,reason", [("ready", False, "did not reach"), ("flags", ["Needs US citizenship"], "Fit flags"),
                                                ("unresolved", ["Start date?"], "Open question"), ("pages", 2, "needs a look")])
async def test_autonomous_parks_instead_of_guessing(store, auto_workflow, field, value, reason):
    wf, browser, outcome = auto_workflow
    outcome[field] = value
    job, _ = store.add_job("https://jobs.example.com/1")
    await wf.run(job["id"], "autonomous")
    j = store.job(job["id"])
    assert j["status"] == "needs_attention" and reason in j["error"] and browser.submit.await_count == 0


@pytest.mark.asyncio
async def test_autonomous_respects_daily_cap(store, auto_workflow):
    wf, browser, _ = auto_workflow
    for n in range(2):
        j, _ = store.add_job(f"https://jobs.example.com/done{n}")
        store.update(j["id"], {"mode": "autonomous", "status": "submitted", "submitted_at": store.job(j["id"])["created_at"]}, invalidate=False)
    job, _ = store.add_job("https://jobs.example.com/new")
    await wf.run(job["id"], "autonomous")
    assert "Daily autonomous cap" in store.job(job["id"])["error"] and browser.submit.await_count == 0


@pytest.mark.asyncio
async def test_email_monitor_flags_actions_and_texts_only_configured_kinds(store, monkeypatch):
    from jobapplier import ai
    from jobapplier.monitor import Monitor
    store.set("settings", {**store.settings(), "sheet_sync_enabled": False, "text_phone": "5551234567", "text_kinds": ["interview"], "quiet_from": 0, "quiet_to": 0})
    applied, _ = store.add_job("https://jobs.example.com/1")
    store.update(applied["id"], {"status": "applied", "company": "Example Robotics", "title": "Firmware Intern"}, invalidate=False)
    other, _ = store.add_job("https://careers.acme.com/2")
    store.update(other["id"], {"status": "submitted", "company": "Acme", "title": "EE Intern"}, invalidate=False)
    sent = []

    async def fake_request(method, body=None, link=None):
        if body and body.get("action") == "mail":
            assert "example.com" in body["domains"] and "acme.com" in body["domains"]
            return {"messages": [
                {"id": "m1", "at": "2026-10-08T10:00:00Z", "from": "recruiting@example.com", "subject": "Interview: Firmware Intern", "snippet": "Pick a time", "link": "https://mail/1"},
                {"id": "m2", "at": "2026-10-08T11:00:00Z", "from": "noreply@acme.com", "subject": "Your application", "snippet": "We will not move forward", "link": "https://mail/2"},
                {"id": "m3", "at": "2026-10-08T12:00:00Z", "from": "news@acme.com", "subject": "Newsletter", "snippet": "...", "link": "https://mail/3"}]}
        if body and body.get("action") == "text":
            sent.append(body)
            return {"sent": True}
        return {}
    tracker = type("T", (), {"connected": lambda self: True, "request": staticmethod(fake_request), "sync": AsyncMock()})()

    async def fake_json(self, task, data, stable=None, local_ok=True, fast=False):
        jobs = {j["company"]: j["id"] for j in stable["jobs"]}
        return {"results": [{"id": "m1", "kind": "interview", "job_id": jobs["Example Robotics"], "summary": "Interview invitation", "action": "Pick a time slot"},
                            {"id": "m2", "kind": "rejection", "job_id": jobs["Acme"], "summary": "Rejected", "action": ""},
                            {"id": "m3", "kind": "other", "job_id": "", "summary": "Newsletter", "action": ""}]}
    monkeypatch.setattr(ai.Model, "json", fake_json)
    result = await Monitor(store, tracker).run()
    assert result["checked"] == 3 and result["updated"] == 2
    a = store.job(applied["id"])
    assert a["action_required"]["kind"] == "interview" and a["action_required"]["message"] == "Pick a time slot" and a["status"] == "applied"
    assert store.job(other["id"])["status"] == "rejected" and store.job(other["id"])["action_required"] is None
    assert sent == [{"action": "text", "to": "5551234567@tmomail.net", "message": "JobApplier: Example Robotics — interview. Interview invitation"}]
    # A second run skips already-seen messages.
    assert (await Monitor(store, tracker).run())["checked"] == 0


CONNECTIONS_CSV = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Ana,Lopez,https://www.linkedin.com/in/ana-lopez?trk=x,,Acme Industries,University Recruiter,12 Mar 2024
Bo,Chen,https://www.linkedin.com/in/bo-chen,bo@example.com,Other Corp,Engineer,01 Jan 2024
"""


def networking_for(store):
    from jobapplier.networking import Networking
    wf = Workflow(store, type("B", (), {"lock": asyncio.Lock()})(), None)
    return Networking(store, None, wf)


def test_connections_csv_import_matches_target_companies_and_keeps_ids(store):
    from jobapplier.networking import parse_connections
    job, _ = store.add_job("https://careers.acme.com/jobs/1")
    store.update(job["id"], {"company": "Acme"}, invalidate=False)
    store.link_job_company(job["id"])
    n = networking_for(store)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Basic_LinkedInDataExport/Connections.csv", CONNECTIONS_CSV)
        z.writestr("Basic_LinkedInDataExport/messages.csv", "private")
    assert [r["name"] for r in parse_connections(archive.getvalue())] == ["Ana Lopez", "Bo Chen"]  # only Connections.csv is read
    result = n.import_connections(CONNECTIONS_CSV.encode())
    assert result == {"count": 2, "matched": 1}
    people = store.people()
    assert len(people) == 1 and people[0]["id"] == "person-001" and people[0]["connected"] and people[0]["profile_url"] == "https://www.linkedin.com/in/ana-lopez"
    assert "private" not in str(store.get("connections"))
    # The same link pasted again is the same person; a new one gets the next ID even after a deletion.
    added = n.add_links(["linkedin.com/in/Ana-Lopez/", "https://www.linkedin.com/in/new-person"], "Acme")
    assert added["duplicates"] == ["person-001"] and added["added"] == ["person-002"]
    store.delete_person("person-002")
    assert n.add_links(["https://www.linkedin.com/in/third"])["added"] == ["person-003"]
    with pytest.raises(ValueError):
        parse_connections(b"name,email\nx,y")


@pytest.mark.asyncio
async def test_networking_research_keeps_only_sourced_claims_and_respects_limits(store, monkeypatch):
    from jobapplier import ai
    from jobapplier import networking as module
    from jobapplier.networking import LIMITS
    n = networking_for(store)
    person, _ = n.add_person("Ana Lopez", "https://www.linkedin.com/in/ana-lopez", company="Acme")
    visited = []

    async def read_page(url, wait_ms=0, keep_open_if=None):
        visited.append(url)
        if "recent-activity" in url:
            return {"url": url, "title": "Activity", "text": "Ana Lopez: We just opened three firmware roles on my team. Why I love debugging...",
                    "links": [{"label": "post", "url": "https://www.linkedin.com/posts/ana-lopez_firmware-activity-123?utm=1"}]}
        return {"url": url, "title": "Ana Lopez | LinkedIn", "text": "Ana Lopez\nUniversity Recruiter at Acme\nAustin, Texas", "links": []}
    n.browser = type("B", (), {"read_page": staticmethod(read_page)})()
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(module, "web_search", AsyncMock(return_value=[]))

    async def fake_json(self, task, data, stable=None, local_ok=True, fast=False):
        return {"role": "University Recruiter", "company": "Acme", "role_source_url": "https://www.linkedin.com/in/ana-lopez",
                "trigger": {"kind": "open_role", "date": "2026-10-01", "date_precision": "day", "source_url": "https://www.linkedin.com/posts/ana-lopez_firmware-activity-123", "note": "Three firmware roles opened."},
                "samples": [{"url": "https://www.linkedin.com/posts/ana-lopez_firmware-activity-123", "date": "2026-10-01", "kind": "post", "snippet": "We just opened three firmware roles on my team."},
                            {"url": "https://www.linkedin.com/in/ana-lopez/recent-activity/all/", "date": "", "kind": "post", "snippet": "Why I love debugging..."},
                            {"url": "https://example.com/made-up", "date": "", "kind": "post", "snippet": "invented"}],
                "sample_gaps": "", "style": {"label": "Driver", "confidence": "medium", "why": "Short direct sentences.", "patterns": "short sentences, no emoji"},
                "hook": {"text": "Why I love debugging", "url": "https://www.linkedin.com/in/ana-lopez/recent-activity/all/"},
                "mutual": {"name": "Sam Mutual", "source_url": "https://nowhere.example/not-read"},
                "research_status": "ready", "note": "Recruits interns."}
    monkeypatch.setattr(ai.Model, "json", fake_json)
    await n.research("networking:research")
    assert visited == ["https://www.linkedin.com/in/ana-lopez", "https://www.linkedin.com/in/ana-lopez/recent-activity/all/"]
    p = store.person(person["id"])
    assert p["research_status"] == "ready" and p["role"] == "University Recruiter" and p["trigger"]["kind"] == "open_role"
    assert [s["snippet"] for s in p["samples"]] == ["We just opened three firmware roles on my team.", "Why I love debugging..."]  # invented source dropped
    assert p["mutual"] == {"status": "none", "name": "", "source_url": ""}  # a mutual without a read source is not recorded
    assert p["style"]["label"] == "Driver" and n.quota_left() == LIMITS["pages_per_day"] - 2
    assert (store.root / "networking" / "networking-list.csv").read_text().count("person-001") == 1
    assert len(list((store.root / "networking" / "versions").glob("*-evidence.json"))) == 1
    # Quota exhausted: LinkedIn is not read and the run falls back to public search.
    store.set("linkedin_quota", {"day": store.get("linkedin_quota")["day"], "count": LIMITS["pages_per_day"]})
    visited.clear()
    pages, note = await n.read_sources(p, n.setup())
    assert visited == [] and "limit" in note


@pytest.mark.asyncio
async def test_networking_research_stops_at_linkedin_login(store, monkeypatch):
    from jobapplier import networking as module
    n = networking_for(store)
    n.add_person("Ana Lopez", "https://www.linkedin.com/in/ana-lopez")

    async def read_page(url, wait_ms=0, keep_open_if=None):
        return {"url": "https://www.linkedin.com/authwall?x", "title": "Sign in | LinkedIn", "text": "", "links": []}
    n.browser = type("B", (), {"read_page": staticmethod(read_page)})()
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    await n.research("networking:research")
    assert store.people()[0]["research_status"] == "pending" and "log in" in store.events()[0]["message"].lower()


@pytest.mark.asyncio
async def test_networking_drafts_are_checked_and_never_sent(store, monkeypatch):
    from jobapplier import ai
    from jobapplier.networking import check_draft
    n = networking_for(store)
    n.save_setup({"intro": "I'm an ECE student at UT Austin.", "target_role": "firmware intern"})
    ready, _ = n.add_person("Ana Lopez", "https://www.linkedin.com/in/ana-lopez", company="Acme")
    ready.update(research_status="ready", role="Recruiter", role_source_url="https://www.linkedin.com/in/ana-lopez",
                 samples=[{"url": "https://www.linkedin.com/posts/ana-123", "date": "", "kind": "post", "snippet": "We opened three firmware roles."}],
                 hook={"text": "We opened three firmware roles.", "url": "https://www.linkedin.com/posts/ana-123"},
                 mutual={"status": "unverified", "name": "Sam Mutual", "source_url": ""})
    store.save_person(ready)
    thin, _ = n.add_person("Bo Chen", "https://www.linkedin.com/in/bo-chen", company="Acme")
    store.save_person({**thin, "research_status": "needs_review", "research_note": "No writing found."})
    good = "Ana, your post about opening three firmware roles caught my eye. I'm an ECE student at UT Austin working on embedded projects. What made the team decide to grow firmware specifically this year?"
    assert check_draft(good, "https://www.linkedin.com/posts/ana-123", ready) == []
    assert check_draft(good + " Could you refer me?", "https://www.linkedin.com/posts/ana-123", ready) == ["must contain exactly one question", 'contains "refer me"']
    assert "source link" in check_draft(good, "https://elsewhere.example/post", ready)[0]
    assert "mutual" in check_draft(good.replace("Ana,", "Ana, Sam mentioned you."), "https://www.linkedin.com/posts/ana-123", ready)[0]
    calls = []

    async def fake_json(self, task, data, stable=None, local_ok=True, fast=False):
        calls.append(data["person"]["name"])
        return {"draft_text": good, "draft_source_url": "https://www.linkedin.com/posts/ana-123", "review_note": "", "needs_review": False}
    monkeypatch.setattr(ai.Model, "json", fake_json)
    await n.drafts("networking:drafts", [ready["id"], thin["id"]])
    assert calls == ["Ana Lopez"]  # nothing is drafted without ready evidence
    a, b = store.person(ready["id"]), store.person(thin["id"])
    assert a["draft"]["status"] == "drafted" and a["draft"]["text"] == good and a["research_status"] == "ready"
    assert b["draft"]["status"] == "needs_review" and b["draft"]["text"] == "" and "needs_review" in b["draft"]["review_note"]
    assert "> " + good in n.drafts_md() and "sent" in n.drafts_md()
    # Your decision: reviewed/closed, a confirmed mutual, an edited draft.
    a = n.update_person(a["id"], {"draft_status": "reviewed", "mutual_name": "Sam Mutual"})
    assert a["draft"]["status"] == "reviewed" and a["mutual"]["status"] == "verified"
    with pytest.raises(ValueError):
        n.update_person(a["id"], {"draft_status": "sent"})


def test_networking_routes_and_state(tmp_path):
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        r = client.put("/api/networking/setup", headers=h, json={"target_role": "firmware intern", "people_per_run": 3, "linkedin_reading": False})
        assert r.json()["people_per_run"] == 3 and r.json()["linkedin_reading"] is False
        assert client.put("/api/networking/setup", headers=h, json={"people_per_run": 50}).status_code == 400
        r = client.post("/api/networking/connections", headers=h, files={"file": ("Connections.csv", CONNECTIONS_CSV.encode(), "text/csv")})
        assert r.json()["count"] == 2
        r = client.post("/api/networking/people", headers=h, json={"urls": "https://www.linkedin.com/in/someone\nnot a link", "company": "Acme"})
        assert r.json()["added"] == ["person-001"] and r.json()["errors"]
        state = client.get("/api/state", headers=h).json()["networking"]
        assert state["connections"]["count"] == 2 and state["people"][0]["id"] == "person-001" and state["setup"]["target_role"] == "firmware intern"
        assert "person-001" in client.get("/api/networking/export?kind=csv", headers=h).text
        assert client.delete("/api/networking/people/person-001", headers=h).json() == {"deleted": "person-001"}
        assert client.delete("/api/networking/people/person-001", headers=h).status_code == 404


def test_cover_letter_written_when_mentioned_even_if_optional():
    from jobapplier.workflow import mentions_letter
    assert mentions_letter({"description": "A cover letter is optional but welcome."})
    assert mentions_letter({"description": "", "requirements": ["Resume and cover-letter"]})
    assert not mentions_letter({"description": "Send us your resume.", "requirements": ["Python"]})


@pytest.mark.asyncio
async def test_stop_cancels_running_work_but_not_a_submission_in_flight(store):
    wf = Workflow(store, type("B", (), {"lock": asyncio.Lock()})(), None)
    a, _ = store.add_job("https://jobs.example.com/a")
    b, _ = store.add_job("https://jobs.example.com/b")
    store.update(b["id"], {"status": "submitting"}, invalidate=False)
    async def forever():
        await asyncio.sleep(3600)
    wf.tasks[a["id"]] = asyncio.create_task(forever())
    wf.tasks[b["id"]] = asyncio.create_task(forever())
    wf.tasks["ingest:x"] = asyncio.create_task(forever())
    await asyncio.sleep(0)
    assert wf.stop_all() == 2
    await asyncio.sleep(0)
    assert wf.tasks[a["id"]].cancelled() and wf.tasks["ingest:x"].cancelled() and not wf.tasks[b["id"]].cancelled()
    assert a["id"] in wf.cancelled and b["id"] not in wf.cancelled
    wf.tasks[b["id"]].cancel()


@pytest.mark.asyncio
async def test_company_logo_is_fetched_cached_and_served(tmp_path, monkeypatch):
    import httpx

    from jobapplier import logos
    assert logos.root_domain("jobs.nvidia.com") == "nvidia.com" and logos.root_domain("careers.foo.co.uk") == "foo.co.uk"
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 200

    def handler(request):
        if "duckduckgo" in request.url.host and "nvidia.com" in request.url.path:
            return httpx.Response(200, content=png, headers={"content-type": "image/png"})
        return httpx.Response(404, text="nope")
    original = httpx.AsyncClient
    monkeypatch.setattr(logos.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "follow_redirects"}))
    monkeypatch.setattr(logos, "public_url", lambda url: url)
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        store = client.app.state.store
        job, _ = store.add_job("https://jobs.nvidia.com/careers/job/1")
        company = store.link_job_company(job["id"])
        company = await logos.ensure_logo(store, company)
        assert company["logo"].endswith(".png") and (tmp_path / "logos" / company["logo"]).read_bytes() == png
        r = client.get(f"/api/logo/{company['id']}", headers=h)
        assert r.status_code == 200 and r.content == png
        other, _ = store.add_job("https://careers.unknown-zz.com/1")
        oc = await logos.ensure_logo(store, store.link_job_company(other["id"]))
        assert not oc.get("logo") and oc["logo_checked_at"]  # 404 recorded, no retry storm
        assert client.get(f"/api/logo/{oc['id']}", headers=h).status_code == 404


def test_log_application_saves_password_to_keychain_only(tmp_path):
    saved = {}
    with patch("jobapplier.app.secret", return_value=None), patch("jobapplier.app.save_secret", side_effect=lambda k, v: saved.__setitem__(k, v)), \
            TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        client.app.state.workflow.run = AsyncMock()
        body = {"url": "https://jobs.example.com/9", "signin_method": "email_password", "account_email": "me@example.com", "password": "hunter2"}
        job = client.post("/api/jobs/log", headers=h, json=body).json()
        company = client.get("/api/state", headers=h).json()["companies"][0]
        assert company["credential"] is True and company["account_email"] == "me@example.com"
        assert "hunter2" in saved["company:" + company["id"]] and "hunter2" not in str(job)
        assert "hunter2" not in client.get("/api/state", headers=h).text
        # Social sign-in: any password sent is ignored.
        saved.clear()
        client.post("/api/jobs/log", headers=h, json={"url": "https://jobs.other.com/1", "signin_method": "google", "password": "x"})
        assert saved == {}


def test_store_job_to_apply_later_then_mark_applied(tmp_path):
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        client.app.state.workflow.run = AsyncMock()
        job = client.post("/api/jobs/log", headers=h, json={"url": "https://jobs.example.com/7", "stage": "saved", "notes": "Due Friday",
                                                            "signin_method": "google"}).json()
        assert job["status"] == "saved" and job["submitted_at"] is None and job["review_notes"] == "Due Friday"
        assert client.post("/api/jobs/log", headers=h, json={"url": "https://jobs.example.com/7", "stage": "saved"}).status_code == 400
        from jobapplier.tracker import Tracker
        assert Tracker(client.app.state.store).values(job)["status"] == "To apply"
        done = client.post(f"/api/jobs/{job['id']}/applied", headers=h).json()
        assert done["status"] == "applied" and done["submitted_at"] and done["review_notes"] == "Due Friday" and done["signin_method"] == "google"
        assert client.post(f"/api/jobs/{job['id']}/applied", headers=h).status_code == 400


def test_match_rubric_validation_enforces_hackerrank_caps_and_bonus_rules():
    from jobapplier.match import coverage, load_rubric, render_task, validate
    role = load_rubric()
    task = render_task(role)
    assert "{%" not in task and "{{" not in task and "4 points for ranks 1-100" in task and "open_source: 0-35" in task
    found = {"scores": {"open_source": {"score": 50, "evidence": "all personal repos"}, "self_projects": {"score": 12, "evidence": "x"},
                        "production": {"score": "7", "evidence": "internship"}, "technical_skills": {"score": -3, "evidence": ""}},
             "bonus_points": {"items": {"gsoc": {"points": 5, "evidence": "GSoC 2025"}, "orchestrate": {"rank": 150, "points": 4, "evidence": "rank 150"},
                                        "founder": {"points": 7, "evidence": "founded"}, "portfolio": {"points": 2, "evidence": "site"}}},
             "deductions": {"total": -4, "reasons": "none"}, "key_strengths": ["a", "b", "c", "d", "e", "f"], "areas_for_improvement": ["x"]}
    r = validate(role, found)
    by = {c["key"]: c for c in r["categories"]}
    assert by["open_source"]["score"] == 35 and by["production"]["score"] == 7 and by["technical_skills"]["score"] == 0 and by["ai_fluency"]["score"] == 0
    assert by["technical_skills"]["evidence"] == "No evidence given."
    bonus = {b["key"]: b for b in r["bonus"]["items"]}
    assert bonus["orchestrate"]["points"] == 3 and "rank 150 allows 3" in bonus["orchestrate"]["evidence"]  # tier points come from the rank
    assert bonus["founder"]["points"] == 0 and "allowed [0, 3, 4, 5]" in bonus["founder"]["evidence"]
    assert bonus["gsoc"]["points"] == 5 and bonus["portfolio"]["points"] == 2 and bonus["linkedin"]["points"] == 0
    assert r["bonus"]["total"] == 10 and r["deductions"]["total"] == 0 and r["total"] == 35 + 12 + 7 + 10 and r["max"] == 130
    assert len(r["strengths"]) == 5
    assert validate(role, {})["total"] == 0
    cov = coverage(["Experience with C++ and embedded firmware", "Kubernetes and Go", ""], "Skills: C/C++, Python, embedded firmware on STM32")
    assert cov["total"] == 2 and cov["covered"] == 1 and cov["percent"] == 50 and cov["requirements"][1]["missing"] == ["kubernetes", "go"]


@pytest.mark.asyncio
async def test_match_report_reuses_scores_and_survives_failures(store, tmp_path, monkeypatch):
    from jobapplier import match as module
    from jobapplier.match import match_report
    pdf = tmp_path / "resume.pdf"
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(str(pdf))
    monkeypatch.setattr(module, "pdf_text", lambda path: "Python firmware STM32")
    monkeypatch.setattr(module, "github_signals", AsyncMock(return_value={"used": True, "username": "me", "profile": {}, "projects": [], "total_projects": 0, "open_source": 0, "note": ""}))
    calls = []

    class M:
        async def json(self, task, data, **kw):
            calls.append(data)
            return {"scores": {"technical_skills": {"score": 6, "evidence": "Python"}}, "bonus_points": {"items": {}}, "deductions": {"total": 0, "reasons": ""},
                    "key_strengths": ["Python"], "areas_for_improvement": ["projects"]}
    resume = {"path": str(pdf.relative_to(tmp_path)), "sha256": "abc"}
    store.root = tmp_path
    r = await match_report(M(), store, resume, ["Python", "Rust"], {"github_url": "https://github.com/me"})
    assert r["total"] == 6 and r["coverage"]["percent"] == 50 and r["github"]["used"] and r["resume_sha256"] == "abc" and "GITHUB DATA" in calls[0]["resume_text"]
    assert await match_report(M(), store, resume, [], {}, previous=r) is r and len(calls) == 1  # same PDF: no second call
    monkeypatch.setattr(module, "pdf_text", lambda path: (_ for _ in ()).throw(ValueError("bad pdf")))
    bad = await match_report(M(), store, {"path": "x.pdf", "sha256": "zzz"}, [], {})
    assert "bad pdf" in bad["error"]


def test_plain_voice_strips_em_dashes_between_words():
    from jobapplier.workflow import plain
    assert plain("We shipped it — fast — and it worked. 2019–2021 at CERN – really.") == "We shipped it, fast, and it worked. 2019–2021 at CERN, really."


def test_sync_merges_two_workspaces_through_a_shared_folder(tmp_path, monkeypatch):
    from jobapplier.sync import Sync, decrypt, derive_key
    folder = tmp_path / "icloud"
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    for st in (a, b):
        st.set("settings", {**st.settings(), "sync_enabled": True, "sync_folder": str(folder)})
    sa, sb = Sync(a, lambda: "pass phrase"), Sync(b, lambda: "pass phrase")
    (a.root / "documents" / "d1").mkdir(parents=True)
    (a.root / "documents" / "d1" / "resume.pdf").write_bytes(b"%PDF-1.4 fake")
    job, _ = a.add_job("https://jobs.example.com/1")
    a.update(job["id"], {"title": "Firmware Intern", "status": "applied"}, invalidate=False)
    a.save_profile_text("## Identity\n- First name: Mustafa\n", reason="edited")
    from jobapplier.networking import Networking
    pa = Networking(a, None, None).add_person("Ana Lopez", "https://www.linkedin.com/in/ana-lopez")[0]
    assert sa.push() is True and sa.push() is False  # unchanged state is not rewritten
    enc = next(folder.glob("*.json.enc")).read_bytes()
    assert b"Firmware Intern" not in enc and b"linkedin" not in enc  # ciphertext only
    with pytest.raises(ValueError, match="passphrase"):
        decrypt(derive_key("wrong", (folder / "salt").read_bytes()), enc)
    assert sb.pull() == [sa.machine()["name"]]
    assert b.job(job["id"])["title"] == "Firmware Intern" and b.profile_text().startswith("## Identity")
    assert (b.root / "documents" / "d1" / "resume.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert b.person(pa["id"])["name"] == "Ana Lopez" and b.get("person_seq") == 1
    # Newest edit wins per record, in either direction; a delete travels as a tombstone.
    b.update(job["id"], {"review_notes": "from B"}, invalidate=False)
    b.delete_person(pa["id"])
    assert sb.push() and sa.pull() == [sb.machine()["name"]]
    assert a.job(job["id"])["review_notes"] == "from B" and a.people() == []
    a.update(job["id"], {"review_notes": "from A later"}, invalidate=False)
    sa.push()
    sb.pull()
    assert b.job(job["id"])["review_notes"] == "from A later"
    # Same ID handed to different people while apart: the local one moves, the remote one lands.
    na, nb = Networking(a, None, None), Networking(b, None, None)
    pa2 = na.add_person("Bo Chen", "https://www.linkedin.com/in/bo-chen")[0]
    pb2 = nb.add_person("Cy Park", "https://www.linkedin.com/in/cy-park")[0]
    assert pa2["id"] == pb2["id"]
    sa.push()
    sb.pull()
    names = {p["profile_url"]: p["id"] for p in b.people()}
    assert names["https://www.linkedin.com/in/bo-chen"] == pa2["id"] and names["https://www.linkedin.com/in/cy-park"] != pb2["id"]
    assert "Synced" in b.events()[0]["message"]


def test_fact_check_blocks_unsupported_numbers_and_flags_names():
    from jobapplier.factcheck import check, describe
    sources = ["## Experience\n- Built a LiDAR rig used by 20 engineers at Texas A&M, 2025. Python, C++, STM32.",
               "Anduril seeks EE interns for a 12-week in-person Summer 2027 internship."]
    ok = check("At Texas A&M I built a LiDAR rig used by 20 engineers in Python. I'd like to join the 12-week Summer 2027 program.", sources)
    assert ok == {"blocking": [], "warnings": []}
    bad = check("I cut setup time 40% and led a team of 5 at Lockheed Martin using Rust, saving $30k.", sources)
    assert bad["blocking"] == ["40%", "$30k"] and bad["warnings"] == ["Lockheed Martin", "Rust"]
    assert "40%" in describe(bad) and "Lockheed" in describe(bad)
    assert check("Reach me at me@example.com", ["Email: me@example.com"])["blocking"] == []
    assert check("Reach me at other@example.com", ["Email: me@example.com"])["blocking"] == ["other@example.com"]


def test_job_posting_json_ld_and_closed_detection():
    from bs4 import BeautifulSoup

    from jobapplier.discover import CLOSED_TEXT
    from jobapplier.workflow import job_posting
    html = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"EE Intern",
      "hiringOrganization":{"@type":"Organization","name":"Acme"},"datePosted":"2026-09-01","validThrough":"2026-12-31T00:00",
      "jobLocation":{"@type":"Place","address":{"addressLocality":"Austin","addressRegion":"TX"}},
      "description":"<p>Build <b>firmware</b>.</p><ul><li>C++</li></ul>"}</script></head><body>JS app</body></html>"""
    p = job_posting(BeautifulSoup(html, "html.parser"))
    assert p["title"] == "EE Intern" and p["company"] == "Acme" and p["location"] == "Austin, TX" and p["valid_through"] == "2026-12-31"
    assert "firmware" in p["description"] and "C++" in p["description"]
    assert job_posting(BeautifulSoup("<html><body>plain</body></html>", "html.parser")) is None
    assert CLOSED_TEXT.search("Sorry, this job is no longer available.") and not CLOSED_TEXT.search("Apply now")


@pytest.mark.asyncio
async def test_research_uses_structured_posting_and_marks_expired(store, monkeypatch):
    from jobapplier import ai
    from jobapplier import workflow as module
    job, _ = store.add_job("https://boards.greenhouse.io/acme/jobs/1")

    async def fake_fetch(url):
        return {"url": url, "title": "x", "text": "Title: EE Intern\n\nBuild firmware. No longer accepting applications.",
                "posting": {"title": "EE Intern", "company": "Acme", "location": "Austin, TX", "date_posted": "2026-01-01", "valid_through": "2026-02-01", "description": "Build firmware."}}
    monkeypatch.setattr(module, "fetch_text", fake_fetch)

    async def fake_json(self, task, data, stable=None, local_ok=True, fast=False):
        return {"company": "ACME Corp (guess)", "title": "Intern", "requirements": ["C++"], "fit_flags": [], "summary": "s"}
    monkeypatch.setattr(ai.Model, "json", fake_json)
    monkeypatch.setattr(module, "ensure_logo", AsyncMock(), raising=False)
    wf = Workflow(store, type("B", (), {"lock": asyncio.Lock()})(), None)
    await wf.research(job["id"])
    j = store.job(job["id"])
    assert j["title"] == "EE Intern" and j["company"] == "Acme" and j["location"] == "Austin, TX" and j["deadline"] == "2026-02-01"
    assert j["posting_status"] == "closed"  # the closed-text wins over the model's reading
    with pytest.raises(ValueError, match="closed"):
        await wf.autonomous(job["id"])


@pytest.mark.asyncio
async def test_discover_finds_new_postings_from_tracked_boards(store, monkeypatch):
    from jobapplier import discover
    job, _ = store.add_job("https://job-boards.greenhouse.io/acme/jobs/1")
    store.update(job["id"], {"company": "Acme"}, invalidate=False)
    store.link_job_company(job["id"])
    other, _ = store.add_job("https://careers.example.com/x")
    store.link_job_company(other["id"])

    async def fake_feed(client, portal, slug):
        assert (portal, slug) == ("greenhouse", "acme")
        return [{"url": "https://job-boards.greenhouse.io/acme/jobs/1?x=1", "title": "Known", "location": "", "posted_at": ""},
                {"url": "https://job-boards.greenhouse.io/acme/jobs/2", "title": "Firmware Intern", "location": "Austin", "posted_at": ""},
                {"url": "https://job-boards.greenhouse.io/acme/jobs/3", "title": "Staff Accountant", "location": "", "posted_at": ""}]
    monkeypatch.setattr(discover, "fetch_feed", fake_feed)
    monkeypatch.setattr(discover.asyncio, "sleep", AsyncMock())
    r = await discover.find_new(store)
    assert r == {"checked": 1, "new": 1, "errors": []}
    found = store.get("discovered")
    assert list(found) == ["https://job-boards.greenhouse.io/acme/jobs/2"] and found[list(found)[0]]["company"] == "Acme"
    assert (await discover.find_new(store))["new"] == 0  # already listed


def test_sync_folds_the_same_company_created_on_both_macs_and_backs_up_first(tmp_path):
    from jobapplier.sync import Sync
    folder = tmp_path / "icloud"
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    for st in (a, b):
        st.set("settings", {**st.settings(), "sync_enabled": True, "sync_folder": str(folder)})
    sa, sb = Sync(a, lambda: "pw"), Sync(b, lambda: "pw")
    ja, _ = a.add_job("https://job-boards.greenhouse.io/andurilindustries/jobs/1")
    a.update(ja["id"], {"company": "Anduril Industries"}, invalidate=False)
    ca = a.link_job_company(ja["id"])
    a.save_company({**a.company(ca["id"]), "domain": "anduril.com"})
    jb, _ = b.add_job("https://job-boards.greenhouse.io/andurilindustries/jobs/2")
    b.update(jb["id"], {"company": "Anduril"}, invalidate=False)
    cb = b.link_job_company(jb["id"])
    assert ca["id"] != cb["id"]
    sa.push()
    assert sb.pull()
    companies = b.companies()
    assert len(companies) == 1 and companies[0]["id"] == cb["id"] and companies[0]["domain"] == "anduril.com"  # folded, not duplicated
    assert b.job(ja["id"])["company_id"] == cb["id"]  # the remote job points at our company
    assert list((b.root / "backups").glob("*before-first-sync-*/workspace.sqlite"))
    sa.push()
    a.save_company({**a.company(ca["id"]), "notes": "later"})
    sa.push()
    sb.pull()
    assert len(b.companies()) == 1 and len(list((b.root / "backups").iterdir())) == 1  # one backup per new Mac


def test_route_fragments_and_exact_links_keep_different_jobs_apart(store):
    a, _ = store.add_job("https://careers.example.com/#/job/101?utm_source=x")
    b, created = store.add_job("https://careers.example.com/#/job/202")
    assert created and a["id"] != b["id"]  # single-page sites put the job ID after #
    assert store.add_job("https://careers.example.com/#top")[0]["url"] == "https://careers.example.com"  # plain anchors still ignored
    c, _ = store.add_job("https://jobs.example.com/view?id=7&team=radar")
    same, created = store.add_job("https://jobs.example.com/view?id=7&team=sonar")
    assert not created and same["id"] == c["id"]
    other, created = store.add_job("https://jobs.example.com/view?id=7&team=sonar", exact=True)
    assert created and other["id"] != c["id"]  # "Log anyway" keeps every parameter


def test_log_anyway_and_delete_job(tmp_path):
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        wf = client.app.state.workflow
        wf.run = AsyncMock()
        body = {"url": "https://jobs.example.com/view?id=7&team=radar", "title": "Radar Intern", "company": "Acme"}
        first = client.post("/api/jobs/log", headers=h, json=body).json()
        dupe = client.post("/api/jobs/log", headers=h, json={**body, "url": "https://jobs.example.com/view?id=7&team=sonar", "title": "Sonar Intern"})
        assert dupe.status_code == 400 and dupe.json()["detail"].startswith("DUPLICATE:") and "Radar Intern" in dupe.json()["detail"]
        second = client.post("/api/jobs/log", headers=h, json={**body, "url": "https://jobs.example.com/view?id=7&team=sonar", "title": "Sonar Intern", "force": True}).json()
        assert second["id"] != first["id"] and second["title"] == "Sonar Intern"
        assert client.delete(f"/api/jobs/{first['id']}", headers=h).json() == {"deleted": first["id"]}
        store = client.app.state.store
        assert [j["id"] for j in store.jobs()] == [second["id"]] and first["id"] in store.get("tombstones")
        assert client.delete(f"/api/jobs/{first['id']}", headers=h).status_code == 404


def test_sync_carries_job_deletions(tmp_path):
    from jobapplier.sync import Sync
    folder = tmp_path / "icloud"
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    for st in (a, b):
        st.set("settings", {**st.settings(), "sync_enabled": True, "sync_folder": str(folder)})
    sa, sb = Sync(a, lambda: "pw"), Sync(b, lambda: "pw")
    job, _ = a.add_job("https://jobs.example.com/1")
    sa.push()
    sb.pull()
    assert b.job(job["id"])
    b.delete_job(job["id"])
    sb.push()
    sa.pull()
    assert a.jobs() == []
    sa.push()
    sb.pull()
    assert b.jobs() == []  # does not come back


def test_sync_settles_and_carries_documents(tmp_path):
    from jobapplier.documents import save_upload
    from jobapplier.sync import Sync
    folder = tmp_path / "icloud"
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    for st in (a, b):
        st.set("settings", {**st.settings(), "sync_enabled": True, "sync_folder": str(folder)})
    sa, sb = Sync(a, lambda: "pw"), Sync(b, lambda: "pw")
    doc = save_upload(a, "notes.txt", "supporting", b"Built a radar front end.")
    save_upload(b, "notes copy.txt", "supporting", b"Built a radar front end.")  # same file uploaded on both
    job, _ = a.add_job("https://job-boards.greenhouse.io/acme/jobs/1")
    a.update(job["id"], {"company": "Acme"}, invalidate=False)
    a.link_job_company(job["id"])
    sa.push()
    sb.pull()
    assert len(b.documents()) == 1  # same content, not listed twice
    c = Store(tmp_path / "c")
    c.set("settings", {**c.settings(), "sync_enabled": True, "sync_folder": str(folder)})
    sc = Sync(c, lambda: "pw")
    sc.pull()
    assert [d["id"] for d in c.documents()] == [doc["id"]] and (c.root / doc["path"]).read_bytes() == b"Built a radar front end."
    # After one round each way, nothing keeps bouncing between the Macs.
    for _ in range(3):
        sb.push()
        sa.pull()
        sa.push()
        sb.pull()
    before = (a.events(), b.events())
    assert sb.push() is False and sa.push() is False
    sa.pull()
    sb.pull()
    assert (a.events(), b.events()) == before


@pytest.mark.asyncio
async def test_stop_marks_progress_stopping_and_does_not_wait_for_the_sheet(store):
    """The Stop button must clear the dashboard right away: the entry shows as stopping at once and the
    tracker-sheet sync that normally follows a run happens in the background, not on the stop path."""
    class Sheets:
        calls = 0
        async def sync(self):
            Sheets.calls += 1
            await asyncio.sleep(3600)
    wf = Workflow(store, type("B", (), {"lock": asyncio.Lock()})(), Sheets())
    store.set("settings", {**store.settings(), "sheet_sync_enabled": True})
    job, _ = store.add_job("https://jobs.example.com/slow")

    async def slow(jid):
        await asyncio.sleep(3600)
    wf.research = slow
    wf.start(job["id"], "research")
    await asyncio.sleep(0)
    assert job["id"] in wf.progress and not wf.progress[job["id"]].get("stopping")
    assert wf.stop_all() == 1
    assert wf.progress[job["id"]]["stopping"] and wf.progress[job["id"]]["detail"] == "Stopping"
    await asyncio.wait_for(asyncio.gather(wf.tasks[job["id"]], return_exceptions=True), 1)
    assert job["id"] not in wf.progress
    assert store.job(job["id"])["status"] == "needs_attention" and "Paused" in store.job(job["id"])["error"]
    await asyncio.sleep(0)
    assert Sheets.calls == 1
