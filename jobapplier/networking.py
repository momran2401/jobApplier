"""Networking list: people worth talking to at the companies you apply to, with a source behind every claim, and
first-message drafts you review and send yourself. People come from your own Connections.csv, profile links you
paste, and public web search. LinkedIn is read only for people already on the list (profile + recent activity),
in the app's own Chrome profile, a few people per run under a daily page cap; it never connects, follows or
messages anyone. Nothing here sends a message."""
import asyncio
import base64
import csv
import io
import json
import random
import re
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from .factcheck import check as fact_check
from .factcheck import describe as fact_describe
from .security import public_url
from .store import normalize_company, now
from .workflow import VOICE, fetch_text, plain

LIMITS = {"people_per_run": 5, "pages_per_day": 30, "min_delay": 4, "max_delay": 9, "window_days": 90}
DEFAULT_SETUP = {"target_role": "", "intro": "", "location": "", "people_per_run": 5, "linkedin_reading": True, "recency_required": False}
STYLES = ("Driver", "Influencer", "Steady", "Analyst", "insufficient evidence")
RESEARCH_STATUSES = ("pending", "ready", "needs_review", "unavailable")
DRAFT_STATUSES = ("none", "drafted", "needs_review", "reviewed", "closed")
TRIGGERS = ("move", "promotion", "open_role", "post", "none", "unknown")
# Phrases the guide rules out of a first message: pitches, flattery, asks for time or a referral.
BANNED = re.compile(r"pick your brain|impressive|referr|refer me|hop on a call|quick call|\b\d+ minutes\b|love to connect|grab (a )?coffee|"
                    r"any (open )?(roles|positions|openings)|put in a (good )?word", re.I)
LOGIN = "login"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
CSV_COLUMNS = ["person_id", "name", "profile_url", "company", "role", "role_source_url", "role_checked_at", "connected", "connected_on",
               "source", "trigger_kind", "trigger_date", "trigger_date_precision", "trigger_source_url", "trigger_note", "samples",
               "sample_gaps", "style_label", "style_confidence", "style_why", "style_patterns", "hook", "hook_url", "mutual_status",
               "mutual_name", "mutual_source", "flags", "research_status", "research_note", "researched_at",
               "draft_text", "draft_source_url", "review_note", "draft_status", "drafted_at"]

RESEARCH_TASK = """Record evidence about ONE person for a networking list, using ONLY the page texts given (each has a url).
Return JSON:
name (the person's name as the pages show it, or ""), role (current job title as the pages show it, or ""),
company (current employer as shown, or ""),
role_source_url (url of the page that shows the role, or ""),
trigger: {kind: move|promotion|open_role|post|none|unknown, date (ISO or ""), date_precision: day|month|unknown,
  source_url, note (one sentence)} — a recent move, promotion, relevant open role on their team, or notable post
  within the last window_days days. If the pages show none, kind "none" with empty fields. Never guess a date.
samples: up to 5 {url, date (ISO or ""), kind: post|comment|repost_intro|about, snippet (verbatim, <= 240 chars)}
  of the person's OWN writing. Exclude text quoted or reposted from other people. url must be one of the page or
  link urls given.
sample_gaps: what could not be read or was missing (string).
style: {label: Driver|Influencer|Steady|Analyst|insufficient evidence, confidence: low|medium|high, why (one
  sentence), patterns (sentence length, punctuation, slang, emoji — describe what the samples show; no invented
  statistics)}. Fewer than 2 usable samples means "insufficient evidence". Never infer personality from a photo or
  a job title.
hook: {text (one specific thing they wrote that could support a genuine question; verbatim or close, <= 200 chars),
  url} or empty strings.
mutual: {name, source_url} ONLY when a page explicitly names a shared connection between the candidate and this
  person; otherwise empty strings.
research_status: ready (role shown on a page AND a hook or sample with a url) | needs_review (relevant but evidence
  thin) | unavailable (pages blocked, empty, or not this person).
note: one sentence for the reviewer. Unknown stays unknown; never invent."""

DRAFT_TASK = """Write ONE first LinkedIn message from the candidate to this person, for the candidate's review (it is
never sent by you). Return JSON: draft_text (35-65 words, shorter if it reads better; plain text; exactly one
question), draft_source_url (the evidence url the message refers to; must be one of evidence_urls),
review_note (what the reviewer should check, or what evidence is missing; "" if nothing),
needs_review (true when no evidence supports a specific question — then draft_text must be "").
Rules: refer to one specific thing they wrote (from the evidence) and ask one easy question the candidate could
genuinely want answered, e.g. why they chose an approach — not for a call, a referral, or a job. Use the
candidate's introduction when useful; never invent the candidate's experience or interests. Style is a light cue
only: Driver → direct; Influencer → playful only if earned; Steady → warm; Analyst → precise; weak evidence →
neutral, natural tone. Match their formality without copying their phrases. Mention a mutual connection only if
mutual.status is "verified". No pitch, no flattery, no "impressive background", no "pick your brain", no jokes.
""" + VOICE


# ---- helpers ----
def canonical_profile(url):
    """A profile link without tracking parameters; LinkedIn links normalized to https://www.linkedin.com/in/<slug>."""
    url = str(url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host or p.username or p.password:
        raise ValueError("Use a public HTTP or HTTPS profile link.")
    path = p.path.rstrip("/")
    if host.endswith("linkedin.com"):
        m = re.match(r"^/(in|pub)/([^/]+)", path)
        if not m:
            raise ValueError(f"Not a LinkedIn profile link: {url[:80]}")
        return f"https://www.linkedin.com/in/{m.group(2)}"
    return f"{p.scheme}://{host}{path}"


def url_key(url):
    p = urlsplit(str(url or "").strip())
    return f"{(p.hostname or '').lower().removeprefix('www.')}{p.path.rstrip('/').lower()}"


def is_linkedin(url):
    return (urlsplit(url).hostname or "").lower().endswith("linkedin.com")


def is_login_wall(data):
    url, title = data.get("url", ""), data.get("title", "")
    return "login" in url or "authwall" in url or "checkpoint" in url or "uas/" in url or \
        (bool(re.search(r"sign in|join now|log in", title, re.I)) and "feed" not in url)


def company_matches(name, company):
    key = normalize_company(name)
    if not key:
        return False
    known = [normalize_company(company["name"]), *company.get("keys", []), *(normalize_company(a) for a in company.get("aliases", []))]
    return any(k and (k == key or (len(k) >= 4 and len(key) >= 4 and (k.startswith(key) or key.startswith(k)))) for k in known)


def parse_connections(data):
    """LinkedIn's Connections.csv. A whole data-archive zip is accepted too, but only Connections.csv is read from
    it; nothing else in the archive is kept. Returns rows with name, url, email, company, position, connected_on."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member = next((n for n in archive.namelist() if Path(n).name.lower() == "connections.csv"), None)
            if not member:
                raise ValueError("No Connections.csv in this archive.")
            if archive.getinfo(member).file_size > 50_000_000:
                raise ValueError("Connections.csv is unexpectedly large.")
            data = archive.read(member)
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lower().startswith("first name")), None)
    if start is None:
        raise ValueError("This doesn't look like LinkedIn's Connections.csv (no 'First Name' header).")
    rows = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        get = lambda k: (r.get(k) or "").strip()  # noqa: E731
        name = " ".join(x for x in (get("First Name"), get("Last Name")) if x)
        try:
            url = canonical_profile(get("URL"))
        except ValueError:
            url = ""
        if not name and not url:
            continue
        rows.append({"name": name, "url": url, "email": get("Email Address"), "company": get("Company"),
                     "position": get("Position"), "connected_on": get("Connected On")})
    return rows


def _unwrap(href):
    """Search engines wrap result links: DuckDuckGo in ?uddg=, Bing in /ck/a?u=a1<base64>."""
    q = parse_qs(urlsplit(href).query)
    if "uddg" in q:
        return unquote(q["uddg"][0])
    if "bing.com/ck/a" in href and q.get("u", [""])[0].startswith("a1"):
        raw = q["u"][0][2:]
        try:
            return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        except Exception:
            return href
    return href


async def web_search(query, limit=10):
    """Keyless public web search: DuckDuckGo's HTML endpoint first, Bing's HTML page as the fallback (both
    sometimes answer automated clients with a challenge page or generic results, so the caller treats an empty
    list as "nothing usable", never as proof that nobody exists). A site: filter in the query is enforced here
    too, because Bing tends to drop it."""
    site = next((m.group(1).lower() for m in [re.search(r"site:(\S+)", query)] if m), "")
    engines = [("https://html.duckduckgo.com/html/", {"q": query}, ".result", "a.result__a", ".result__snippet"),
               ("https://www.bing.com/search", {"q": query, "setlang": "en", "count": str(limit)}, "li.b_algo", "h2 a", ".b_caption p, p")]
    for base, params, item_sel, link_sel, snippet_sel in engines:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}) as client:
                r = await client.get(base, params=params)
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            continue
        out = []
        for res in soup.select(item_sel):
            a = res.select_one(link_sel)
            if not a:
                continue
            target = _unwrap(a.get("href", ""))
            if not target.startswith("http") or (site and site not in target.lower()):
                continue
            snippet = res.select_one(snippet_sel)
            out.append({"title": a.get_text(" ", strip=True), "url": target.split("?")[0], "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
        if out:
            return out[:limit]
    return []


def words(text):
    return len(re.findall(r"[A-Za-z0-9'’-]+", text or ""))


def evidence_urls(person):
    urls = {person.get("profile_url", "")}
    urls |= {s.get("url", "") for s in person.get("samples") or []}
    urls |= {(person.get("hook") or {}).get("url", ""), person.get("role_source_url", ""), (person.get("trigger") or {}).get("source_url", "")}
    return {url_key(u) for u in urls if u}


def check_draft(text, source_url, person):
    """Code-level checks on a draft (the model's word is not enough): length, one question, no banned asks,
    a source the evidence actually contains, and no mutual that isn't verified."""
    problems = []
    n = words(text)
    if n < 15 or n > 65:
        problems.append(f"{n} words (aim for 35-65)")
    if text.count("?") != 1:
        problems.append("must contain exactly one question")
    hit = BANNED.search(text)
    if hit:
        problems.append(f"contains \"{hit.group(0)}\"")
    if not source_url or url_key(source_url) not in evidence_urls(person):
        problems.append("source link is not in this person's evidence")
    mutual = person.get("mutual") or {}
    if mutual.get("name") and mutual.get("status") != "verified" and mutual["name"].split()[0].lower() in text.lower():
        problems.append("mentions a mutual connection that is not verified")
    return problems


def new_person(pid, name, url, **extra):
    return {"id": pid, "name": name[:120], "profile_url": url, "headline": "", "company": "", "company_id": None, "role": "",
            "role_source_url": "", "role_checked_at": "", "location": "", "source": "pasted", "connected": False, "connected_on": "",
            "email": "", "trigger": {"kind": "unknown", "date": "", "date_precision": "unknown", "source_url": "", "note": ""},
            "samples": [], "sample_gaps": "", "style": {"label": "insufficient evidence", "confidence": "low", "why": "", "patterns": ""},
            "hook": {"text": "", "url": ""}, "mutual": {"status": "none", "name": "", "source_url": ""}, "flags": [],
            "research_status": "pending", "research_note": "", "researched_at": "", "pages_read": [],
            "draft": {"text": "", "source_url": "", "review_note": "", "status": "none", "drafted_at": "", "version": 0},
            "note": "", "created_at": now(), "updated_at": now(), **extra}


class Networking:
    def __init__(self, store, browser, workflow):
        self.store, self.browser, self.workflow = store, browser, workflow

    # ---- setup & state ----
    def setup(self):
        return {**DEFAULT_SETUP, **self.store.get("networking_setup", {})}

    def save_setup(self, changes):
        setup = {**self.setup(), **{k: v for k, v in changes.items() if k in DEFAULT_SETUP}}
        for key in ("target_role", "intro", "location"):
            if not isinstance(setup[key], str):
                raise ValueError(f"{key} must be text.")
            setup[key] = setup[key].strip()[:400]
        if type(setup["people_per_run"]) is not int or not 1 <= setup["people_per_run"] <= 10:
            raise ValueError("People per run must be between 1 and 10.")
        if not isinstance(setup["linkedin_reading"], bool) or not isinstance(setup["recency_required"], bool):
            raise ValueError("Invalid setup switches.")
        self.store.set("networking_setup", setup)
        return setup

    def summary(self):
        connections = self.store.get("connections")
        return {"setup": self.setup(), "people": self.store.people(), "limits": LIMITS, "quota_left": self.quota_left(),
                "connections": {"count": connections["count"], "imported_at": connections["imported_at"]} if connections else None,
                "last_version": self.store.get("networking_version")}

    # ---- daily LinkedIn page quota ----
    def quota_left(self):
        q = self.store.get("linkedin_quota", {})
        used = q.get("count", 0) if q.get("day") == now()[:10] else 0
        return max(0, LIMITS["pages_per_day"] - used)

    def spend(self, n=1):
        q = self.store.get("linkedin_quota", {})
        day = now()[:10]
        self.store.set("linkedin_quota", {"day": day, "count": (q.get("count", 0) if q.get("day") == day else 0) + n})

    # ---- people ----
    def add_person(self, name, url, **fields):
        """Deduplicated by profile identity (the link), never by name alone. Returns (person, created)."""
        key = url_key(url) if url else ""
        for p in self.store.people():
            same_link = key and url_key(p["profile_url"]) == key
            same_unlinked = not key and not p["profile_url"] and p["name"].lower() == name.lower() and normalize_company(p["company"]) == normalize_company(fields.get("company", ""))
            if same_link or same_unlinked:
                changed = {k: v for k, v in fields.items() if v and not p.get(k)}
                if not p["name"] and name:
                    changed["name"] = name
                if changed:
                    p = self.store.save_person({**p, **changed, "updated_at": now()})
                return p, False
        person = new_person(self.store.new_person_id(), name, url, **fields)
        return self.store.save_person(person), True

    def import_connections(self, data):
        rows = parse_connections(data)
        if not rows:
            raise ValueError("Connections.csv has no rows.")
        self.store.set("connections", {"imported_at": now(), "count": len(rows), "rows": rows})
        matched = self.match_connections()
        self.store.event(None, f"Imported {len(rows)} LinkedIn connections; {matched} at your target companies joined the networking list.")
        return {"count": len(rows), "matched": matched}

    def match_connections(self):
        """Your first-degree connections at target companies join the list. Being connected to you says nothing
        about who knows anyone else, so mutuals stay unverified until you confirm them."""
        rows = (self.store.get("connections") or {}).get("rows", [])
        added = 0
        for company in self.store.companies():
            for r in rows:
                if company_matches(r["company"], company):
                    _, created = self.add_person(r["name"], r["url"], headline=r["position"], company=company["name"], company_id=company["id"],
                                                 source="connections_csv", connected=True, connected_on=r["connected_on"], email=r["email"])
                    added += created
        return added

    def add_links(self, urls, company_name=""):
        company = next((c for c in self.store.companies() if company_matches(company_name, c)), None) if company_name else None
        added, duplicates, errors = [], [], []
        for raw in urls[:50]:
            try:
                url = canonical_profile(raw)
                public_url(url)
                slug = url.rstrip("/").split("/")[-1]
                person, created = self.add_person("" if is_linkedin(url) else slug, url, source="pasted",
                                                  company=(company or {}).get("name", company_name.strip()), company_id=(company or {}).get("id"))
                (added if created else duplicates).append(person["id"])
            except ValueError as exc:
                errors.append({"url": raw, "error": str(exc)})
        return {"added": added, "duplicates": duplicates, "errors": errors}

    async def discover(self, company_ids):
        """Public web search for people at a company (no LinkedIn access): one query per company. Search engines
        often answer automated clients with nothing usable; an empty result is reported as such, not as "nobody"."""
        setup = self.setup()
        self.match_connections()
        added = 0
        for cid in company_ids[:10]:
            company = self.store.company(cid)
            query = f'site:linkedin.com/in "{company["name"]}" {setup["target_role"]}'.strip()
            for r in await web_search(query, limit=8):
                if "/in/" not in r["url"]:
                    continue
                try:
                    url = canonical_profile(r["url"])
                except ValueError:
                    continue
                # Titles look like "Name - Headline - Company | LinkedIn"; when LinkedIn hides the name the first
                # part is the headline itself ("Firmware Engineer at X"), so it is not taken as a name.
                parts = [x.strip() for x in re.split(r"\s[-–|]\s", r["title"]) if x.strip() and x.strip().lower() != "linkedin"]
                named = bool(parts) and not re.search(r"\b(at|@)\b", parts[0], re.I) and len(parts[0].split()) <= 4
                headline = " · ".join(x for x in (parts[1:3] if named else parts[:2]) if normalize_company(x) != normalize_company(company["name"]))
                _, created = self.add_person(parts[0] if named else "", url, headline=headline[:200], company=company["name"],
                                             company_id=company["id"], source="web_search")
                added += created
            await asyncio.sleep(random.uniform(1, 2.5))
        searched = len(company_ids[:10])
        note = f"{added} new {'person' if added == 1 else 'people'} from public search." if added else \
            "Public search returned nothing usable (search engines often block automated lookups). Paste profile links instead."
        self.store.event(None, f"Networking: {note}")
        return {"added": added, "searched": searched, "note": note}

    # ---- research ----
    async def read_sources(self, person, setup):
        """Pages read for one person: LinkedIn profile + recent activity (when allowed, within the daily cap),
        otherwise public search results and any non-LinkedIn profile page. Returns (pages, note)."""
        pages, note = [], ""
        url = person["profile_url"]
        if url and is_linkedin(url) and setup["linkedin_reading"]:
            if self.quota_left() < 2:
                note = "Daily LinkedIn page limit reached; used public search only."
            else:
                for kind, target in (("profile", url), ("activity", url + "/recent-activity/all/")):
                    await asyncio.sleep(random.uniform(LIMITS["min_delay"], LIMITS["max_delay"]))
                    data = await self.browser.read_page(target, wait_ms=4000, keep_open_if=is_login_wall)
                    self.spend(1)
                    if is_login_wall(data):
                        return pages, LOGIN
                    links = [{"label": link["label"][:120], "url": link["url"].split("?")[0]} for link in data.get("links", [])
                             if re.search(r"/feed/update/|/posts/|/pulse/", link["url"])][:40]
                    pages.append({"url": data["url"].split("?")[0], "kind": kind, "text": data["text"][:12000], "links": links})
        elif url and not is_linkedin(url):
            try:
                page = await fetch_text(url)
                pages.append({"url": page["url"], "kind": "profile", "text": page["text"][:12000], "links": []})
            except Exception as exc:
                note = f"Could not read {url}: {str(exc)[:120]}"
        if person["name"] and (not pages or setup["linkedin_reading"] is False or note):
            query = f'"{person["name"]}" "{person["company"]}" site:linkedin.com' if person["company"] else f'"{person["name"]}" site:linkedin.com'
            for r in await web_search(query, limit=6):
                pages.append({"url": r["url"], "kind": "search", "text": f"{r['title']}\n{r['snippet']}", "links": []})
        return pages, note

    def record(self, person, found, pages, setup):
        """Apply the model's findings, keeping only claims whose source is a page or link that was actually read."""
        allowed = {url_key(p["url"]) for p in pages} | {url_key(link["url"]) for p in pages for link in p.get("links", [])} | {url_key(person["profile_url"])}
        ok = lambda u: bool(u) and url_key(u) in allowed  # noqa: E731
        s = lambda v, n=300: str(v or "")[:n]  # noqa: E731
        trigger = found.get("trigger") if isinstance(found.get("trigger"), dict) else {}
        kind = trigger.get("kind") if trigger.get("kind") in TRIGGERS else "unknown"
        if kind not in {"none", "unknown"} and not ok(trigger.get("source_url")):
            kind, trigger = "unknown", {**trigger, "note": "The model named a trigger without a source among the pages read; discarded."}
        samples = [{"url": s(x.get("url")), "date": s(x.get("date"), 20), "kind": s(x.get("kind"), 20), "snippet": s(x.get("snippet"), 240)}
                   for x in (found.get("samples") or []) if isinstance(x, dict) and ok(x.get("url")) and x.get("snippet")][:5]
        style = found.get("style") if isinstance(found.get("style"), dict) else {}
        label = style.get("label") if style.get("label") in STYLES else "insufficient evidence"
        if len(samples) < 2:
            label = "insufficient evidence"
        hook = found.get("hook") if isinstance(found.get("hook"), dict) else {}
        hook = {"text": s(hook.get("text"), 200), "url": s(hook.get("url"))} if ok(hook.get("url")) and hook.get("text") else {"text": "", "url": ""}
        mutual = found.get("mutual") if isinstance(found.get("mutual"), dict) else {}
        if person["mutual"].get("status") != "verified":  # the user's own confirmation always wins
            person["mutual"] = {"status": "unverified", "name": s(mutual.get("name"), 80), "source_url": s(mutual.get("source_url"))} \
                if mutual.get("name") and ok(mutual.get("source_url")) else {"status": "none", "name": "", "source_url": ""}
        status = found.get("research_status") if found.get("research_status") in RESEARCH_STATUSES else "needs_review"
        role_url = s(found.get("role_source_url")) if ok(found.get("role_source_url")) else ""
        if status == "ready" and not (role_url and (hook["text"] or samples)):
            status = "needs_review"
        flags = []
        if kind in {"none", "unknown"}:
            flags.append("no recent trigger")
            if setup["recency_required"] and status == "ready":
                status = "needs_review"
        if person["mutual"]["status"] == "unverified":
            flags.append("mutual unverified")
        person.update({"name": person["name"] or s(found.get("name"), 120), "role": s(found.get("role"), 160) if role_url else person["role"], "company": s(found.get("company"), 120) or person["company"],
                       "role_source_url": role_url, "role_checked_at": now()[:10] if role_url else "",
                       "trigger": {"kind": kind, "date": s(trigger.get("date"), 20) if kind not in {"none", "unknown"} else "",
                                   "date_precision": trigger.get("date_precision") if trigger.get("date_precision") in {"day", "month", "unknown"} else "unknown",
                                   "source_url": s(trigger.get("source_url")) if kind not in {"none", "unknown"} else "", "note": s(trigger.get("note"), 300)},
                       "samples": samples, "sample_gaps": s(found.get("sample_gaps"), 400),
                       "style": {"label": label, "confidence": style.get("confidence") if style.get("confidence") in {"low", "medium", "high"} else "low",
                                 "why": s(style.get("why"), 300), "patterns": s(style.get("patterns"), 400)},
                       "hook": hook, "flags": flags, "research_status": status, "research_note": s(found.get("note"), 300),
                       "researched_at": now(), "pages_read": [p["url"] for p in pages], "updated_at": now()})
        return person

    async def research(self, key, ids=None):
        setup = self.setup()
        self.match_connections()
        people = [self.store.person(i) for i in ids] if ids else [p for p in self.store.people() if p["research_status"] == "pending"]
        people = people[:int(setup["people_per_run"])]
        if not people:
            self.store.event(None, "Networking: nobody is waiting for research.")
            return
        model = self.workflow.model(key)
        candidate = {"name": self.store.profile().get("identity", {}).get("first_name", ""), "target_role": setup["target_role"]}
        counts = {"ready": 0, "needs_review": 0, "unavailable": 0}
        login = False
        for i, person in enumerate(people, 1):
            who = person["name"] or person["profile_url"]
            self.workflow.stage(key, "Reading profiles", f"{who} ({i} of {len(people)})")
            pages, note = await self.read_sources(person, setup)
            if note == LOGIN:
                login = True
                break
            if not pages:
                person.update(research_status="unavailable", research_note=note or "Nothing could be read for this person.", researched_at=now(), updated_at=now())
                self.store.save_person(person)
                counts["unavailable"] += 1
                continue
            self.workflow.stage(key, "Recording evidence", f"{who}: {len(pages)} page(s) read")
            found = await model.json(RESEARCH_TASK, {"person": {k: person[k] for k in ("name", "profile_url", "headline", "company", "connected")},
                                                     "candidate": candidate, "today": date.today().isoformat(), "window_days": LIMITS["window_days"],
                                                     "pages": pages})
            person = self.record(person, found, pages, setup)
            if note:
                person["research_note"] = (note + " " + person["research_note"]).strip()
            self.store.save_person(person)
            counts[person["research_status"]] += 1
        version = self.snapshot("research")
        summary = f"Networking research: {counts['ready']} ready, {counts['needs_review']} to review, {counts['unavailable']} unavailable (version {version})."
        if login:
            summary += " LinkedIn asked for a login: a window is open for that (one time); log in there, then run research again."
        self.store.event(None, summary)

    # ---- drafts ----
    async def drafts(self, key, ids=None):
        setup = self.setup()
        people = [self.store.person(i) for i in ids] if ids else [p for p in self.store.people() if p["research_status"] == "ready" and p["draft"]["status"] == "none"]
        if not people:
            self.store.event(None, "Networking: no researched person is waiting for a draft.")
            return
        model = self.workflow.model(key)
        profile = self.store.profile_text()
        counts = {"drafted": 0, "needs_review": 0}
        for i, person in enumerate(people, 1):
            who = person["name"] or person["profile_url"]
            self.workflow.stage(key, "Writing drafts", f"{who} ({i} of {len(people)})")
            draft = {**person["draft"], "version": person["draft"].get("version", 0) + 1, "drafted_at": now()}
            if person["research_status"] != "ready":
                draft.update(text="", source_url="", status="needs_review", review_note=f"Research status is {person['research_status']}: {person['research_note']}".strip())
            else:
                evidence = {k: person[k] for k in ("name", "role", "company", "role_source_url", "trigger", "samples", "style", "hook", "mutual", "connected")}
                found = await model.json(DRAFT_TASK, {"person": evidence, "evidence_urls": sorted(u for u in evidence_urls(person)),
                                                      "candidate": {"introduction": setup["intro"], "target_role": setup["target_role"]}},
                                         stable={"candidate_profile": profile})
                text = plain(re.sub(r"\s+", " ", str(found.get("draft_text") or "")).strip()[:900])
                source = str(found.get("draft_source_url") or "")[:300]
                note = str(found.get("review_note") or "")[:400]
                self.workflow.stage(key, "Checking drafts", who)
                if found.get("needs_review") or not text:
                    draft.update(text="", source_url="", status="needs_review", review_note=note or "No evidence supports a specific question yet.")
                else:
                    problems = check_draft(text, source, person)
                    facts = fact_check(text, [profile, setup["intro"], json.dumps(evidence)])
                    if facts["blocking"]:
                        problems.append(fact_describe(facts))
                    draft.update(text=text, source_url=source, status="needs_review" if problems else "drafted",
                                 review_note="; ".join(problems) + (f". {note}" if note else "") if problems else note)
            person["draft"] = draft
            person["updated_at"] = now()
            self.store.save_person(person)
            counts[draft["status"]] += 1
        version = self.snapshot("drafts")
        self.store.event(None, f"Networking drafts: {counts['drafted']} drafted, {counts['needs_review']} need your review (version {version}). Nothing was sent.")

    def update_person(self, pid, changes):
        """Your decisions: edit a draft, mark it reviewed or closed, confirm a mutual, keep a note."""
        person = self.store.person(pid)
        draft = dict(person["draft"])
        if "draft_text" in changes:
            draft["text"] = str(changes["draft_text"])[:900]
            if draft["status"] in {"none", "needs_review"} and draft["text"].strip():
                draft["status"] = "drafted"
        if "draft_status" in changes:
            if changes["draft_status"] not in {"drafted", "reviewed", "closed"}:
                raise ValueError("Draft status must be drafted, reviewed or closed.")
            draft["status"] = changes["draft_status"]
        if "mutual_name" in changes:
            name = str(changes["mutual_name"]).strip()[:80]
            person["mutual"] = {"status": "verified" if name else "none", "name": name, "source_url": "confirmed by you"} if name else {"status": "none", "name": "", "source_url": ""}
            person["flags"] = [f for f in person.get("flags", []) if f != "mutual unverified"]
        if "note" in changes:
            person["note"] = str(changes["note"])[:1000]
        person.update(draft=draft, updated_at=now())
        return self.store.save_person(person)

    # ---- files ----
    def list_csv(self, people=None):
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for p in people if people is not None else self.store.people():
            t, st, h, m, d = p["trigger"], p["style"], p["hook"], p["mutual"], p["draft"]
            w.writerow({"person_id": p["id"], "name": p["name"], "profile_url": p["profile_url"], "company": p["company"], "role": p["role"],
                        "role_source_url": p["role_source_url"], "role_checked_at": p["role_checked_at"], "connected": "yes" if p["connected"] else "no",
                        "connected_on": p["connected_on"], "source": p["source"], "trigger_kind": t["kind"], "trigger_date": t["date"],
                        "trigger_date_precision": t["date_precision"], "trigger_source_url": t["source_url"], "trigger_note": t["note"],
                        "samples": " ; ".join(f"{s['url']} | {s['date'] or 'date unknown'} | {s['snippet']}" for s in p["samples"]),
                        "sample_gaps": p["sample_gaps"], "style_label": st["label"], "style_confidence": st["confidence"], "style_why": st["why"],
                        "style_patterns": st["patterns"], "hook": h["text"], "hook_url": h["url"], "mutual_status": m["status"], "mutual_name": m["name"],
                        "mutual_source": m["source_url"], "flags": "; ".join(p.get("flags", [])), "research_status": p["research_status"],
                        "research_note": p["research_note"], "researched_at": p["researched_at"], "draft_text": d["text"], "draft_source_url": d["source_url"],
                        "review_note": d["review_note"], "draft_status": d["status"], "drafted_at": d["drafted_at"]})
        return out.getvalue()

    def drafts_md(self, people=None):
        lines = ["# First-message drafts", "", "Review each one, change anything you would never say, then send it yourself. None of these were sent.", ""]
        for p in people if people is not None else self.store.people():
            d = p["draft"]
            if d["status"] == "none":
                continue
            lines += [f"## {p['name'] or p['profile_url']} — {p['company'] or 'company unknown'} ({p['id']})",
                      f"{p['role'] or 'role unknown'} · style: {p['style']['label']} ({p['style']['confidence']}) · draft: {d['status']}", ""]
            lines += [f"> {d['text']}", ""] if d["text"] else ["> (no draft: " + (d["review_note"] or "needs review") + ")", ""]
            if d["source_url"]:
                lines.append(f"Source: {d['source_url']}")
            if d["review_note"] and d["text"]:
                lines.append(f"Review note: {d['review_note']}")
            lines += [f"Profile: {p['profile_url']}", ""]
        return "\n".join(lines)

    def snapshot(self, reason):
        """A frozen, versioned copy of the list, its evidence and the drafts, plus the current files, under data/networking."""
        people = self.store.people()
        stamp = now()[:19].replace(":", "").replace("-", "").replace("T", "-")
        root = self.store.root / "networking"
        (root / "versions").mkdir(parents=True, exist_ok=True)
        (root / "networking-list.csv").write_text(self.list_csv(people))
        (root / "drafts.md").write_text(self.drafts_md(people))
        (root / "versions" / f"{stamp}-{reason}-networking-list.csv").write_text(self.list_csv(people))
        (root / "versions" / f"{stamp}-{reason}-evidence.json").write_text(json.dumps({"version": stamp, "reason": reason, "people": people}, indent=1))
        (root / "versions" / f"{stamp}-{reason}-drafts.md").write_text(self.drafts_md(people))
        self.store.set("networking_version", {"version": stamp, "reason": reason, "at": now()})
        return stamp
