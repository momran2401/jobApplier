"""New postings from the public job-board feeds of companies you already track (Greenhouse, Lever, Ashby).
No scraping, no AI tokens: three JSON endpoints the boards publish for their own widgets. Matches are kept in
kv `discovered` until you add or dismiss them.

Every scan also records what each board listed in kv `posting_history`, so a role that comes back under a new
link on a later day is flagged as a repost (after career-ops' detect-reposts, MIT): same company, same set of
title words, a different URL, first seen on different days within 90 days. Two links first seen on the same day
are parallel openings, not a repost. Companies you marked do-not-apply, and companies inside your re-apply
cooldown, are skipped."""
import asyncio
import re
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx

from .store import ats_slug, now

REPOST_WINDOW_DAYS = 90
TITLE_NOISE = {"the", "a", "an", "of", "and", "or", "for", "in", "at", "to", "with", "-", "|", "/"}


def title_key(title):
    """The set of words in a title, so word order and punctuation churn do not matter but a city or seniority word does."""
    return frozenset(w for w in re.findall(r"[a-z0-9+#]+", str(title or "").lower()) if w not in TITLE_NOISE)


def reposts(history, company_id, title, url, today=None):
    """Earlier postings at this company with the same title under another link, first seen on an earlier day
    within the window. Empty when the posting is new or a parallel opening."""
    today = today or date.today()
    key = title_key(title)
    out = []
    for p in history.get(company_id, []):
        if p["url"] == url or title_key(p["title"]) != key:
            continue
        first = date.fromisoformat(p["first_seen"][:10])
        if 1 <= (today - first).days <= REPOST_WINDOW_DAYS:
            out.append({"url": p["url"], "first_seen": p["first_seen"][:10], "title": p["title"]})
    return out


def record_history(history, company_id, postings, today):
    """Remember what the board listed today: first and last seen per link, trimmed to the window."""
    rows = {p["url"]: p for p in history.get(company_id, [])}
    for p in postings:
        key = p["url"]
        if not key:
            continue
        row = rows.get(key) or {"url": key, "title": p["title"], "first_seen": today, "last_seen": today}
        row["last_seen"] = today
        rows[key] = row
    cutoff = (date.fromisoformat(today) - timedelta(days=REPOST_WINDOW_DAYS * 2)).isoformat()
    history[company_id] = [r for r in rows.values() if r["last_seen"] >= cutoff]
    return history


def cooling_down(company, jobs, cooldown_days, today=None):
    """The most recent application to this company within the cooldown window, else None."""
    if not cooldown_days:
        return None
    today = today or date.today()
    recent = [j for j in jobs if j.get("company_id") == company["id"] and j.get("submitted_at")
              and j.get("status") in {"submitted", "applied", "rejected", "offer"}
              and (today - date.fromisoformat(j["submitted_at"][:10])).days < int(cooldown_days)]
    return max(recent, key=lambda j: j["submitted_at"]) if recent else None

CLOSED_TEXT = re.compile(r"no longer accepting applications|position has been filled|this (job|position|posting) (is|has been) (closed|filled|removed)|"
                         r"job is no longer available|this job has expired|posting has expired|we are no longer accepting", re.I)


def feed_for(company, jobs):
    """(portal, slug) for a company whose jobs point at a supported board, else None."""
    for job in jobs:
        host = (urlsplit(job["url"]).hostname or "").lower()
        if job.get("company_id") != company["id"]:
            continue
        for portal, needle in (("greenhouse", "greenhouse.io"), ("lever", "lever.co"), ("ashby", "ashbyhq.com")):
            if host.endswith(needle):
                return portal, ats_slug(job["url"])
    return None


async def fetch_feed(client, portal, slug):
    if portal == "greenhouse":
        r = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        r.raise_for_status()
        return [{"url": j.get("absolute_url", ""), "title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
                 "posted_at": j.get("updated_at", "")} for j in r.json().get("jobs", [])]
    if portal == "lever":
        r = await client.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
        r.raise_for_status()
        return [{"url": j.get("hostedUrl", ""), "title": j.get("text", ""), "location": (j.get("categories") or {}).get("location", ""),
                 "posted_at": ""} for j in r.json()]
    if portal == "ashby":
        r = await client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        r.raise_for_status()
        return [{"url": j.get("jobUrl", ""), "title": j.get("title", ""), "location": j.get("location", ""),
                 "posted_at": j.get("publishedAt", "")} for j in r.json().get("jobs", [])]
    return []


def matches(title, keywords):
    words = [w.strip().lower() for w in re.split(r"[,\n]", keywords or "") if w.strip()]
    return not words or any(w in title.lower() for w in words)


async def find_new(store):
    """Scan every supported company once. Returns {"checked": n, "new": n, "errors": [...]}."""
    settings = store.settings()
    jobs = store.jobs()
    known = {j["url"].split("?")[0].rstrip("/") for j in jobs}
    found = store.get("discovered", {})
    dismissed = store.get("discover_dismissed", {})
    history = store.get("posting_history", {})
    today = now()[:10]
    checked, new, errors, skipped = 0, 0, [], 0
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "JobApplier/0.1"}) as client:
        for company in store.companies():
            feed = feed_for(company, jobs)
            if not feed:
                continue
            if company.get("do_not_apply") or cooling_down(company, jobs, settings.get("reapply_cooldown_days", 0)):
                skipped += 1
                continue
            checked += 1
            try:
                postings = await fetch_feed(client, *feed)
            except Exception as exc:
                errors.append(f"{company['name']}: {str(exc)[:80]}")
                continue
            postings = [{**p, "url": p["url"].split("?")[0].rstrip("/")} for p in postings]
            for p in postings:
                key = p["url"]
                if not key or key in known or key in dismissed or key in found or not matches(p["title"], settings.get("discover_keywords", "")):
                    continue
                earlier = reposts(history, company["id"], p["title"], key)
                found[key] = {**p, "url": key, "company": company["name"], "company_id": company["id"], "found_at": now(),
                              "repost_of": earlier[0]["first_seen"] if earlier else ""}
                new += 1
            record_history(history, company["id"], postings, today)
            await asyncio.sleep(0.5)
    store.set("discovered", found)
    store.set("posting_history", history)
    store.set("discover_last", {"at": now(), "checked": checked, "new": new, "errors": errors, "skipped": skipped})
    if new:
        store.event(None, f"Found {new} new posting(s) at the companies you track.")
    return {"checked": checked, "new": new, "errors": errors}
