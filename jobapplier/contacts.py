"""Referral contact finder: who to reach out to at a company, from the web and from LinkedIn people search
read in the app's own Chrome profile (after you log in there once). Read-only: it navigates and reads result
cards; it never connects, messages, or follows. Low volume by design (see LIMITS)."""
import asyncio
import random
import re
from urllib.parse import quote_plus, urlsplit

from .ai import Model
from .store import now
from .workflow import fetch_text

LIMITS = {"pages_per_job": 2, "results_per_day": 40, "min_delay": 4, "max_delay": 9}
RANK_TASK = """Rank these people as referral or informational-interview contacts for the candidate and job given.
Prefer the kind of person described in `looking_for`. Return JSON: contacts (array of {name, headline, url, source,
score 0-100, why (one short sentence), note (a 2-3 sentence personalized connection/introduction message in the
candidate's voice, no flattery, mentions one concrete shared interest from the profile and asks for a brief chat
or referral)}). Keep only people plausibly at the company or recruiting for it; at most 12. Never invent people."""


def is_login_wall(data):
    url, title = data.get("url", ""), data.get("title", "")
    return "login" in url or "authwall" in url or "checkpoint" in url or bool(re.search(r"sign in|join now|log in", title, re.I)) and "feed" not in url


class ContactFinder:
    def __init__(self, store, browser, progress, stage, detail):
        self.store, self.browser = store, browser
        self.progress, self.stage, self.detail = progress, stage, detail

    # ---- quota ----
    def quota_left(self):
        q = self.store.get("linkedin_quota", {})
        used = q.get("count", 0) if q.get("day") == now()[:10] else 0
        return max(0, LIMITS["results_per_day"] - used)

    def spend(self, n):
        q = self.store.get("linkedin_quota", {})
        day = now()[:10]
        self.store.set("linkedin_quota", {"day": day, "count": (q.get("count", 0) if q.get("day") == day else 0) + n})

    # ---- sources ----
    async def web_search(self, query, limit=8):
        """DuckDuckGo's HTML endpoint, parsed for result links (no API key needed)."""
        try:
            page = await fetch_text("https://html.duckduckgo.com/html/?q=" + quote_plus(query))
        except Exception:
            return []
        results = []
        for m in re.finditer(r"(https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+)", page["text"]):
            results.append({"url": m.group(1), "label": "", "source": "web"})
        for line in page["text"].splitlines():
            if re.search(r"recruit|talent|university|campus|hiring|engineer|manager", line, re.I) and len(line) < 200:
                results.append({"url": "", "label": line.strip(), "source": "web"})
        seen, out = set(), []
        for r in results:
            key = r["url"] or r["label"]
            if key and key not in seen:
                seen.add(key)
                out.append(r)
        return out[:limit]

    async def linkedin_people(self, company, description, pages):
        """Read LinkedIn people-search result cards in the app's Chrome. Returns ([cards], note)."""
        cards, note = [], ""
        keywords = f"{company} {description}".strip()
        for n in range(1, pages + 1):
            if self.quota_left() <= 0:
                note = "Daily LinkedIn reading limit reached."
                break
            url = f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(keywords)}&page={n}"
            await asyncio.sleep(random.uniform(LIMITS["min_delay"], LIMITS["max_delay"]))
            data = await self.browser.read_page(url, wait_ms=4000, keep_open_if=is_login_wall)
            host = urlsplit(data["url"]).hostname or ""
            if is_login_wall(data):
                note = "LinkedIn wants you to log in. A window is open for that (one time); log in there, then run Find contacts again. Later searches run hidden."
                break
            if "linkedin.com" not in host:
                break
            found = [{"name": link["label"].split("\n")[0][:80], "headline": " ".join(link["label"].split("\n")[1:3])[:200], "url": link["url"].split("?")[0], "source": "linkedin"}
                     for link in data["links"] if "/in/" in link["url"] and link["label"] and "\n" in link["label"]]
            unique = {c["url"]: c for c in found}
            cards += list(unique.values())
            self.spend(len(unique))
            if len(unique) < 5:
                break
        return cards, note

    # ---- run ----
    async def find(self, key, job_ids, description):
        profile = self.store.profile_text()
        total = len(job_ids)
        for i, jid in enumerate(job_ids, 1):
            job = self.store.job(jid)
            company = self.store.link_job_company(jid)
            self.stage(key, "Searching the web", f"{company['name']} ({i} of {total})")
            candidates = await self.web_search(f'site:linkedin.com/in "{company["name"]}" {description}')
            if company.get("domain"):
                for path in ("/about", "/team", "/careers"):
                    try:
                        page = await fetch_text(f"https://{company['domain']}{path}")
                        for m in re.finditer(r"[A-Z][a-z]+ [A-Z][a-z]+(?:,| -| —)? (?:[A-Za-z ]{0,40}?)(recruit|talent|engineer|manager|lead|director)[A-Za-z ]{0,40}", page["text"]):
                            candidates.append({"url": f"https://{company['domain']}{path}", "label": m.group(0)[:160], "source": "company site"})
                    except Exception:
                        continue
            self.stage(key, "Reading LinkedIn", f"{company['name']} · read-only, {LIMITS['pages_per_job']} pages max")
            cards, note = await self.linkedin_people(company["name"], description, LIMITS["pages_per_job"])
            if note:
                self.store.event(jid, note)
            people = [{"name": c["name"], "headline": c["headline"], "url": c["url"], "source": c["source"]} for c in cards]
            people += [{"name": c["label"][:80], "headline": c["label"], "url": c["url"], "source": c["source"]} for c in candidates if c["label"] or c["url"]]
            if not people:
                self.store.event(jid, f"No contacts found for {company['name']} yet." + (" " + note if note else ""))
                continue
            self.stage(key, "Ranking contacts", f"{company['name']}: {len(people)} candidates")
            found = await Model(self.store, jid).json(RANK_TASK, {"looking_for": description, "people": people[:40]},
                                                   stable={"profile": profile, "job": {"company": company["name"], "title": job.get("title"), "summary": job.get("summary", "")}}, fast=True)
            known = {p["url"] for p in people if p["url"]} | {p["name"] for p in people}
            ranked = [c for c in (found.get("contacts") or []) if isinstance(c, dict) and (c.get("url") in known or c.get("name") in known)]
            existing = {c.get("url") or c.get("name"): c for c in company.get("contacts", [])}
            for c in ranked:
                entry = {"name": str(c.get("name", ""))[:80], "headline": str(c.get("headline", ""))[:200], "url": str(c.get("url", ""))[:300],
                         "source": str(c.get("source", ""))[:30], "score": int(c.get("score") or 0), "why": str(c.get("why", ""))[:200],
                         "note": str(c.get("note", ""))[:600], "job_id": jid, "found_at": now(), "status": "found"}
                existing[entry["url"] or entry["name"]] = {**existing.get(entry["url"] or entry["name"], {}), **entry}
            company = self.store.company(company["id"])
            company["contacts"] = sorted(existing.values(), key=lambda c: -c.get("score", 0))[:30]
            self.store.save_company(company)
            top = next((c for c in company["contacts"] if c.get("job_id") == jid), None)
            if top:
                self.store.update(jid, {"contact_profile_url": top.get("url", ""), "outreach_status": "contact found"}, invalidate=False)
            self.store.event(jid, f"Found {len(ranked)} contact(s) at {company['name']}.")
