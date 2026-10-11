"""New postings from the public job-board feeds of companies you already track (Greenhouse, Lever, Ashby).
No scraping, no AI tokens: three JSON endpoints the boards publish for their own widgets. Matches are kept in
kv `discovered` until you add or dismiss them."""
import asyncio
import re
from urllib.parse import urlsplit

import httpx

from .store import ats_slug, now

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
    checked, new, errors = 0, 0, []
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "JobApplier/0.1"}) as client:
        for company in store.companies():
            feed = feed_for(company, jobs)
            if not feed:
                continue
            checked += 1
            try:
                postings = await fetch_feed(client, *feed)
            except Exception as exc:
                errors.append(f"{company['name']}: {str(exc)[:80]}")
                continue
            for p in postings:
                key = p["url"].split("?")[0].rstrip("/")
                if not key or key in known or key in dismissed or key in found or not matches(p["title"], settings.get("discover_keywords", "")):
                    continue
                found[key] = {**p, "url": key, "company": company["name"], "company_id": company["id"], "found_at": now()}
                new += 1
            await asyncio.sleep(0.5)
    store.set("discovered", found)
    store.set("discover_last", {"at": now(), "checked": checked, "new": new, "errors": errors})
    if new:
        store.event(None, f"Found {new} new posting(s) at the companies you track.")
    return {"checked": checked, "new": new, "errors": errors}
