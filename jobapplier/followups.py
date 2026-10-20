"""Follow-up cadence and company history, computed from what the workspace already records.

Cadence (after career-ops' followup-cadence, MIT): once an application is in, the first follow-up is due after
seven days, the next seven days after that, and after two follow-ups with no reply the application is cold. When
the employer has written (interview, assessment, something they need), a reply from you is urgent within a day
and then every three days. Rejections and offers end the cadence. Only follow-ups you say you sent are recorded;
the app drafts, you send.

Company history reports facts about an employer's responsiveness to you, never a verdict: "silent so far" is a
high-volume inbox as often as anything else."""
from datetime import date, datetime, timedelta

CADENCE = {"applied_first": 7, "applied_next": 7, "applied_max": 2, "responded_first": 1, "responded_next": 3, "cold_after": 2}
WAITING = {"submitted", "applied"}
REPLIED = {"interview", "assessment", "action_required", "offer"}
BANNED_OPENERS = ["just checking in", "just following up", "touching base", "circling back", "i wanted to follow up",
                  "i hope this email finds you well", "i hope this message finds you well"]


def _day(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date() if "T" in str(value) else date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def cadence(job, today=None):
    """{urgency, next_at, days_since, count, phase, reason} for one job; urgency is none for jobs not waiting."""
    today = today or date.today()
    status = job.get("status")
    sent = [f for f in (job.get("follow_ups") or []) if _day(f.get("at"))]
    count = len(sent)
    applied = _day(job.get("submitted_at"))
    none = {"urgency": "none", "next_at": "", "days_since": None, "count": count, "phase": "", "reason": ""}
    if status not in WAITING or not applied:
        return none
    email = job.get("last_email") if isinstance(job.get("last_email"), dict) else None
    last_sent = max((_day(f["at"]) for f in sent), default=None)
    if email and email.get("kind") in REPLIED and _day(email.get("at")):
        # They wrote: respond within a day, then nudge every three days until you log a reply.
        since = _day(email["at"])
        if last_sent and last_sent >= since:
            next_at = last_sent + timedelta(days=CADENCE["responded_next"])
            phase, reason = "responded", f"They wrote on {since}; you replied {last_sent}."
        else:
            next_at = since + timedelta(days=CADENCE["responded_first"])
            phase, reason = "responded", f"They wrote on {since} ({email.get('kind', '').replace('_', ' ')}); no reply from you recorded."
        urgency = "urgent" if today >= next_at else "waiting"
        return {"urgency": urgency, "next_at": next_at.isoformat(), "days_since": (today - since).days, "count": count, "phase": phase, "reason": reason}
    if email and email.get("kind") == "rejection":
        return none
    if count >= CADENCE["applied_max"]:
        return {"urgency": "cold", "next_at": "", "days_since": (today - applied).days, "count": count, "phase": "applied",
                "reason": f"{count} follow-ups sent with no reply. Time to let this one go, or try a different contact."}
    base = last_sent or applied
    next_at = base + timedelta(days=CADENCE["applied_first"] if not last_sent else CADENCE["applied_next"])
    urgency = "overdue" if today >= next_at else "waiting"
    reason = f"Applied {applied}" + (f", last follow-up {last_sent}" if last_sent else "") + f". Follow-up {count + 1} due {next_at}."
    return {"urgency": urgency, "next_at": next_at.isoformat(), "days_since": (today - applied).days, "count": count, "phase": "applied", "reason": reason}


ORDER = {"urgent": 0, "overdue": 1, "waiting": 2, "cold": 3, "none": 4}


def board(jobs, today=None):
    """Every job with a live cadence, most pressing first."""
    rows = []
    for job in jobs:
        c = cadence(job, today)
        if c["urgency"] != "none":
            rows.append({"job_id": job["id"], "company": job.get("company", ""), "title": job.get("title", ""), **c})
    rows.sort(key=lambda r: (ORDER[r["urgency"]], r["next_at"] or "9999"))
    return rows


FOLLOWUP_TASK = """Write a follow-up email from the candidate about an application. Return JSON {subject, body}.
The body is 3 to 4 sentences, under 150 words, plain text, no placeholders, no sign-off beyond the first name:
1. Name the role and the company and when the application was sent.
2. One concrete, specific point from the profile that matters for THIS role (a project, a result, a skill they
   asked for). A fact, not a claim about enthusiasm.
3. A soft ask with a time window: whether there is anything else they need, and that the candidate is free to talk
   this week or next.
If `previous_follow_ups` is not empty, this is a later follow-up: take a different angle (a new project, a
question about timing, or a recent update), never repeat the first one.
Never open with checking in, following up, touching base, circling back, or hoping the email finds them well.
Lead with the role, not with the ask. No flattery, no desperation."""


def company_history(company, jobs, today=None):
    """What this employer has done with your applications so far: counts and a plain label, no verdict."""
    today = today or date.today()
    mine = [j for j in jobs if j.get("company_id") == company["id"]]
    sent = [j for j in mine if j.get("status") in {"submitted", "applied", "rejected", "offer"} and j.get("submitted_at")]
    responded = [j for j in sent if j.get("last_email") or j.get("action_required") or j.get("status") in {"rejected", "offer"}]
    silent = [j for j in sent if j not in responded]
    oldest_silent = min((_day(j["submitted_at"]) for j in silent if _day(j["submitted_at"])), default=None)
    if not sent:
        label = ""
    elif responded and not silent:
        label = "responded before"
    elif responded:
        label = "mixed: responded before, silent on another"
    else:
        label = f"silent so far ({(today - oldest_silent).days} days)" if oldest_silent else "silent so far"
    return {"applied": len(sent), "responded": len(responded), "silent": len(silent), "label": label,
            "last_applied": max((j["submitted_at"][:10] for j in sent), default=""),
            "rejections": sum(1 for j in sent if j.get("status") == "rejected")}
