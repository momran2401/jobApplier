"""The career-ops-inspired additions: fit evaluation rules, follow-up cadence, keyword variants and skill gaps,
the voice guardrail, the reply pre-match, repost detection, the knock-out pre-scan, and the do-not-apply gate."""
import asyncio
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from jobapplier import fit, followups, knockout, voice
from jobapplier.app import create_app
from jobapplier.discover import cooling_down, record_history, reposts, title_key
from jobapplier.match import coverage, present, skill_gaps
from jobapplier.monitor import candidates, company_in, sender_domain
from jobapplier.store import Store
from jobapplier.workflow import Workflow


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


# ---- fit ----
def test_fit_rules_cap_inferred_importance_and_derive_the_verdict():
    found = {"score": 4.2, "summary": "Good fit.", "hard_stops": [], "soft_gaps": ["no Verilog"],
             "requirements": [
                 {"text": "C++", "importance": "critical", "tier": "inferred", "quote": "", "match": "strong", "evidence": "C++ in projects"},
                 {"text": "FPGA", "importance": "high", "tier": "stated", "quote": "", "match": "missing"},
                 {"text": "Python", "importance": "critical", "tier": "stated", "quote": "Python is required", "match": "partial"},
                 {"text": "Verilog", "importance": "high", "tier": "structural", "quote": "Qualifications", "match": "strong"},
             ], "gaps": [{"requirement": "Python", "risk": "They will ask.", "mitigation": "Course projects."}]}
    r = fit.shape(found)
    assert r["score"] == 4.2 and r["decision"] == "apply" and r["label"] == "Apply"
    by = {x["text"]: x for x in r["requirements"]}
    assert by["C++"]["importance"] == "meaningful"  # an inferred importance can never be critical or high
    assert by["FPGA"]["tier"] == "structural"  # "stated" without the wording is downgraded
    assert [x["text"] for x in r["requirements"]][:2] == ["Python", "FPGA"]  # importance first, unmet before met
    assert fit.shape({"score": 3.7})["decision"] == "consider" and fit.shape({"score": 2.9})["decision"] == "skip"
    assert fit.shape({"score": 4.8, "hard_stops": ["No sponsorship"]})["decision"] == "consider"
    assert fit.shape({"score": 3.9, "hard_stops": ["No sponsorship"]})["decision"] == "skip"
    assert fit.shape({"score": "bad"})["score"] is None and fit.shape({})["decision"] == "consider"
    fields = fit.job_fields(r)
    assert fields["fit_score"] == 4.2 and fields["fit_decision"] == "Apply"


@pytest.mark.asyncio
async def test_fit_evaluation_survives_a_model_failure():
    class Model:
        async def json(self, *a, **k):
            raise ValueError("no credits")
    r = await fit.evaluate(Model(), "profile", {"title": "Intern", "description": "x"}, ["Python"])
    assert r["score"] is None and "no credits" in r["error"] and r["requirements"][0]["text"] == "Python"


# ---- follow-ups ----
def test_followup_cadence_phases():
    today = date(2026, 10, 20)
    job = {"id": "a", "status": "applied", "submitted_at": "2026-10-10", "follow_ups": []}
    c = followups.cadence(job, today)
    assert c["urgency"] == "overdue" and c["next_at"] == "2026-10-17" and c["count"] == 0
    job["follow_ups"] = [{"at": "2026-10-17", "text": "first"}]
    c = followups.cadence(job, today)
    assert c["urgency"] == "waiting" and c["next_at"] == "2026-10-24"
    job["follow_ups"].append({"at": "2026-10-19", "text": "second"})
    assert followups.cadence(job, today)["urgency"] == "cold"
    they_wrote = {**job, "follow_ups": [], "last_email": {"kind": "interview", "at": "2026-10-19T10:00:00+00:00"}}
    c = followups.cadence(they_wrote, today)
    assert c["urgency"] == "urgent" and c["phase"] == "responded" and c["next_at"] == "2026-10-20"
    assert followups.cadence({**job, "status": "rejected"}, today)["urgency"] == "none"
    assert followups.cadence({**job, "last_email": {"kind": "rejection", "at": "2026-10-12"}}, today)["urgency"] == "none"
    assert followups.cadence({"id": "b", "status": "new"}, today)["urgency"] == "none"
    board = followups.board([job, they_wrote, {"id": "c", "status": "saved"}], today)
    assert [r["urgency"] for r in board] == ["urgent", "cold"]


def test_company_history_reports_facts_not_verdicts():
    company = {"id": "c1"}
    jobs = [{"company_id": "c1", "status": "applied", "submitted_at": "2026-09-01"},
            {"company_id": "c1", "status": "rejected", "submitted_at": "2026-08-01"},
            {"company_id": "c2", "status": "applied", "submitted_at": "2026-09-01"}]
    h = followups.company_history(company, jobs, date(2026, 10, 1))
    assert h["applied"] == 2 and h["responded"] == 1 and h["silent"] == 1 and h["label"].startswith("mixed") and h["rejections"] == 1
    assert followups.company_history(company, jobs[:1], date(2026, 10, 1))["label"] == "silent so far (30 days)"
    assert followups.company_history({"id": "c9"}, jobs)["label"] == ""


# ---- keywords ----
def test_keyword_variants_and_boundaries():
    assert present("javascript", "built the ui in js and react")
    assert not present("java", "built the ui in javascript")
    assert present("kubernetes", "deployed on k8s")
    assert present("c++", "wrote c++ firmware") and not present("c", "wrote c++ firmware")
    assert present("microcontroller", "programmed stm32 microcontrollers")
    cov = coverage(["Python and JavaScript", "Kubernetes deployment experience", "Verilog RTL design"], "Python, JS, deployed on k8s")
    levels = {r["text"]: r["level"] for r in cov["requirements"]}
    assert levels["Python and JavaScript"] == "present" and levels["Kubernetes deployment experience"] == "thin" and levels["Verilog RTL design"] == "missing"
    assert cov["present"] == 1 and cov["thin"] == 1 and cov["covered"] == 2


def test_skill_gaps_three_buckets():
    gaps = skill_gaps(["Python", "PyTorch", "Kubernetes"], ["Python", "C++"], "## Projects\n- Trained a model with PyTorch.\n## Skills\n- Python, C++")
    assert gaps == {"supported": ["Python"], "in_profile": ["PyTorch"], "gap": ["Kubernetes"]}


# ---- voice ----
def test_voice_guardrail_catches_the_tells_and_passes_plain_text():
    bad = "I am excited to apply. This isn't just about code, it's about impact. Furthermore, I leverage cutting-edge tools — daily."
    problems = voice.check(bad)
    joined = " ".join(problems).lower()
    assert '"i am excited to apply"' in joined and '"leverage"' in joined and '"cutting-edge"' in joined
    assert any("not x, but y" in p.lower() for p in problems) and any("dash" in p for p in problems)
    assert voice.check("I built the radar front end in C++ and tested it on real hardware. Could we talk this week?") == []
    assert voice.check("Ranges like 2019-2021 and leveraged-buyout jokes stay out of it.") == []  # hyphen and bound word are not hits
    assert "Remove or replace" in voice.rewrite_instruction(problems)


# ---- reply monitor ----
def test_reply_prematch_by_company_name_and_domain():
    jobs = [{"id": "j1", "url": "https://jobs.lever.co/acme/1", "company": "Acme Robotics", "company_id": "c1", "title": "Robotics Intern", "status": "applied"},
            {"id": "j2", "url": "https://jobs.lever.co/acme/2", "company": "Acme Robotics", "company_id": "c1", "title": "Firmware Intern", "status": "applied"},
            {"id": "j3", "url": "https://careers.hp.com/x", "company": "HP", "company_id": "c2", "title": "Intern", "status": "applied"}]
    companies = [{"id": "c1", "name": "Acme Robotics", "domain": "acmerobotics.com", "aliases": []}, {"id": "c2", "name": "HP", "domain": "hp.com", "aliases": []}]
    found = list(candidates({"from": "recruiting@acmerobotics.com", "subject": "Your Firmware Intern application", "snippet": ""}, jobs, companies))
    assert {j["id"]: exact for j, exact in found} == {"j1": False, "j2": True}
    found = list(candidates({"from": "talent@gmail.com", "subject": "We use PHP here", "snippet": "no company named"}, jobs, companies))
    assert found == []  # "HP" inside "PHP" is not a match, and a shared mail domain proves nothing
    found = list(candidates({"from": "x@hp.com", "subject": "Update", "snippet": ""}, jobs, companies))
    assert [j["id"] for j, _ in found] == ["j3"] and found[0][1]  # only application at HP: exact
    assert sender_domain("Jane <jane@mail.acmerobotics.com>") == "acmerobotics.com"
    assert company_in("Thanks from the IBM team", "IBM") and not company_in("ibmx", "IBM")


@pytest.mark.asyncio
async def test_rejection_without_an_exact_match_is_a_suggestion_not_a_status(store):
    from jobapplier.monitor import Monitor
    a, _ = store.add_job("https://jobs.lever.co/acme/1")
    b, _ = store.add_job("https://jobs.lever.co/acme/2")
    for j, title in ((a, "Robotics Intern"), (b, "Firmware Intern")):
        store.update(j["id"], {"status": "applied", "company": "Acme Robotics", "title": title, "submitted_at": "2026-10-01"}, invalidate=False)
        store.link_job_company(j["id"])
    monitor = Monitor(store, None)
    msg = {"id": "m1", "from": "no-reply@acmerobotics.com", "subject": "Your application at Acme Robotics", "snippet": "We will not move forward.", "at": "2026-10-10T00:00:00+00:00"}

    class FakeModel:
        def __init__(self, *a, **k):
            pass
        async def json(self, task, data, **k):
            assert data["emails"][0]["candidates"]  # the code's candidates are handed to the model
            return {"results": [{"id": "m1", "kind": "rejection", "job_id": a["id"], "summary": "Rejected", "action": ""}]}
    with patch("jobapplier.monitor.Model", FakeModel):
        assert await monitor.apply([msg]) == 1
    ja, jb = store.job(a["id"]), store.job(b["id"])
    assert ja["status"] == "applied" and jb["status"] == "applied"  # two roles at the company: no automatic change
    assert ja["status_suggestion"]["status"] == "rejected" and jb["status_suggestion"] is None
    assert ja["last_email"]["kind"] == "rejection" and ja["last_email"]["exact"] is False


# ---- reposts and cooldown ----
def test_repost_detection_needs_a_different_link_on_a_later_day():
    history = {}
    record_history(history, "c1", [{"url": "https://x/1", "title": "Firmware Intern - Austin"}, {"url": "https://x/2", "title": "Firmware Intern - Boston"}], "2026-09-01")
    assert reposts(history, "c1", "Firmware Intern Austin", "https://x/9", date(2026, 9, 1)) == []  # same day: parallel openings
    assert reposts(history, "c1", "Firmware Intern Boston", "https://x/9", date(2026, 9, 20))[0]["url"] == "https://x/2"  # title identity, not similarity
    assert reposts(history, "c1", "Firmware Intern - Austin", "https://x/1", date(2026, 9, 20)) == []  # same link is the same posting
    assert reposts(history, "c1", "Firmware Intern Austin", "https://x/9", date(2027, 1, 1)) == []  # outside the window
    assert title_key("Senior Engineer, Radar") == title_key("Radar Senior Engineer")


def test_cooldown_finds_the_recent_application():
    company = {"id": "c1"}
    jobs = [{"company_id": "c1", "status": "applied", "submitted_at": "2026-09-01", "title": "A"},
            {"company_id": "c1", "status": "saved", "submitted_at": None, "title": "B"}]
    assert cooling_down(company, jobs, 90, date(2026, 10, 1))["title"] == "A"
    assert cooling_down(company, jobs, 20, date(2026, 10, 1)) is None
    assert cooling_down(company, jobs, 0, date(2026, 10, 1)) is None


# ---- knock-out pre-scan ----
def test_knockout_scan_finds_the_questions_and_knows_what_is_answered():
    snapshot = {"frames": [{"fields": [
        {"label": "Will you now or in the future require sponsorship for employment visa status?", "type": "select"},
        {"label": "Do you have a Bachelor's degree in Computer Science or a related field?", "type": "select"},
        {"label": "What is your current salary?", "type": "text"},
        {"label": "Are you a U.S. citizen?", "type": "select"},
        {"label": "First name", "type": "text"}]}]}
    hits = knockout.scan(snapshot, {"answers.sponsorship": "No, I do not need sponsorship"})
    by = {h["area"]: h for h in hits}
    assert by["sponsorship"]["kind"] == "knockout" and by["sponsorship"]["answered"]
    assert by["degree"]["kind"] == "knockout" and not by["degree"]["answered"]
    assert by["salary_history"]["kind"] == "warning" and by["status"]["kind"] == "warning"
    assert "first name" not in {h["label"].lower() for h in hits}
    assert knockout.scan(snapshot, {}, seen={h["label"] for h in hits}) == []


def test_prescan_hands_off_an_unanswered_knockout(store):
    wf = Workflow(store, None, None)
    job, _ = store.add_job("https://jobs.example.com/1")
    package = {"unresolved": []}
    snapshot = {"frames": [{"fields": [{"label": "Do you have at least 5 years of professional experience?", "type": "select"}]}]}

    class Page:
        url = "https://jobs.example.com/apply"
    with pytest.raises(ValueError, match="reject automatically"):
        wf.prescan(job["id"], package, Page(), snapshot, {})
    saved = store.job(job["id"])["package"]
    assert saved["knockouts"][0]["area"] == "experience" and saved["unresolved"][0].startswith("The form asks")
    wf.prescan(job["id"], saved, Page(), snapshot, {})  # already reported: nothing new, no second hand-off


# ---- do-not-apply ----
def test_do_not_apply_blocks_preparation_but_not_reading(store):
    wf = Workflow(store, type("B", (), {"lock": asyncio.Lock()})(), None)
    job, _ = store.add_job("https://jobs.example.com/1")
    store.update(job["id"], {"company": "Acme"}, invalidate=False)
    company = store.link_job_company(job["id"])
    store.save_company({**company, "do_not_apply": True, "do_not_apply_reason": "ghosted twice"})
    with pytest.raises(ValueError, match="ghosted twice"):
        wf.start(job["id"], "prepare")
    assert job["id"] not in wf.tasks


def test_settings_validation_for_the_new_fields(tmp_path):
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        ok = client.put("/api/settings", headers=h, json={"fit_threshold": 4, "reapply_cooldown_days": 30, "fit_enabled": False, "followups_enabled": True})
        assert ok.status_code == 200
        s = client.get("/api/state", headers=h).json()["settings"]
        assert s["fit_threshold"] == 4.0 and s["reapply_cooldown_days"] == 30 and s["fit_enabled"] is False
        assert client.put("/api/settings", headers=h, json={"fit_threshold": 7}).status_code == 400
        assert client.put("/api/settings", headers=h, json={"reapply_cooldown_days": 400}).status_code == 400


def test_followup_sent_moves_the_next_date_and_suggestion_can_be_confirmed(tmp_path):
    with patch("jobapplier.app.secret", return_value=None), TestClient(create_app(tmp_path)) as client:
        h = {"x-workspace-token": client.get("/api/bootstrap").json()["token"]}
        store = client.app.state.store
        job, _ = store.add_job("https://jobs.example.com/1")
        store.update(job["id"], {"status": "applied", "submitted_at": "2026-10-01", "followup_draft": {"subject": "s", "body": "Hello there"},
                                 "status_suggestion": {"status": "rejected", "kind": "rejection", "summary": "x", "at": "", "link": "", "reason": ""}}, invalidate=False)
        r = client.post(f"/api/jobs/{job['id']}/followup/sent", headers=h, json={"at": "2026-10-09"}).json()
        assert r["follow_ups"][0]["text"] == "Hello there" and r["followup_draft"] is None and r["followup_at"] == "2026-10-16" and r["last_contact_at"] == "2026-10-09"
        state = client.get("/api/state", headers=h).json()
        assert state["followups"][0]["job_id"] == job["id"]
        r = client.post(f"/api/jobs/{job['id']}/status-suggestion", headers=h, json={"accept": True}).json()
        assert r["status"] == "rejected" and r["status_suggestion"] is None
        assert client.post(f"/api/jobs/{job['id']}/status-suggestion", headers=h, json={"accept": True}).status_code == 400
