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


@pytest.mark.asyncio
async def test_contact_finder_reads_only_respects_limits_and_ranks(store, monkeypatch):
    from jobapplier import ai
    from jobapplier import contacts as contacts_module
    from jobapplier.contacts import LIMITS, ContactFinder
    job, _ = store.add_job("https://careers.acme.com/jobs/1")
    store.update(job["id"], {"company": "Acme", "title": "EE Intern"}, invalidate=False)
    visited = []

    async def read_page(url, wait_ms=0, keep_open_if=None):
        visited.append(url)
        label = lambda n: f"Person {n}\nUniversity Recruiter at Acme\nAustin"  # noqa: E731
        return {"url": url, "title": "People", "text": "", "links": [{"label": label(n), "url": f"https://www.linkedin.com/in/person{n}?x=1"} for n in range(6)]}
    browser = type("B", (), {"read_page": staticmethod(read_page)})()
    monkeypatch.setattr(contacts_module.asyncio, "sleep", AsyncMock())

    async def no_web(url):
        raise ValueError("offline")
    monkeypatch.setattr(contacts_module, "fetch_text", no_web)

    async def fake_json(self, task, data, stable=None, local_ok=True, fast=False):
        people = data["people"]
        return {"contacts": [{"name": people[0]["name"], "headline": people[0]["headline"], "url": people[0]["url"], "source": "linkedin",
                              "score": 90, "why": "Recruits interns", "note": "Hi Person 0, I'm applying to Acme..."},
                             {"name": "Made Up", "url": "https://www.linkedin.com/in/nobody", "score": 99, "note": "x"}]}
    monkeypatch.setattr(ai.Model, "json", fake_json)
    finder = ContactFinder(store, browser, {}, lambda *a, **k: None, lambda *a, **k: None)
    await finder.find("contacts:test", [job["id"]], "university recruiters")
    assert len(visited) == LIMITS["pages_per_job"] and all("/search/results/people/" in v for v in visited)
    company = store.company_for(store.job(job["id"]), create=False)
    assert [c["name"] for c in company["contacts"]] == ["Person 0"]  # invented people are dropped
    assert company["contacts"][0]["url"] == "https://www.linkedin.com/in/person0" and store.job(job["id"])["contact_profile_url"].endswith("person0")
    assert finder.quota_left() == LIMITS["results_per_day"] - 12
    # Quota exhausted: no more LinkedIn reads.
    store.set("linkedin_quota", {"day": store.get("linkedin_quota")["day"], "count": LIMITS["results_per_day"]})
    visited.clear()
    cards, note = await finder.linkedin_people("Acme", "recruiters", 2)
    assert cards == [] and "limit" in note and visited == []


@pytest.mark.asyncio
async def test_contact_finder_asks_for_linkedin_login(store, monkeypatch):
    from jobapplier import contacts as contacts_module
    from jobapplier.contacts import ContactFinder

    async def read_page(url, wait_ms=0, keep_open_if=None):
        return {"url": "https://www.linkedin.com/authwall?x", "title": "Sign in | LinkedIn", "text": "", "links": []}
    monkeypatch.setattr(contacts_module.asyncio, "sleep", AsyncMock())
    finder = ContactFinder(store, type("B", (), {"read_page": staticmethod(read_page)})(), {}, lambda *a, **k: None, lambda *a, **k: None)
    cards, note = await finder.linkedin_people("Acme", "recruiters", 2)
    assert cards == [] and "log in" in note.lower()


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
