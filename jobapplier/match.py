"""Match report for a tailored resume: HackerRank's open-source resume rubric plus a keyword-coverage check.

The rubric (categories, caps, bonus rules, the system and criteria prompts) is copied verbatim from
interviewstreet/hiring-agent (MIT, Copyright (c) 2025 HackerRank) under jobapplier/rubrics/. As in hiring-agent,
the model supplies scores and evidence and Python enforces the caps and the bonus rules; GitHub signals are
fetched from the public API and appended to the resume text. The keyword-coverage part is ours and deterministic:
which of the posting's requirements the resume text actually mentions. Scores from an LLM rubric move by roughly
ten points between runs, so the UI says so."""
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pypdf import PdfReader

from .store import now

RUBRICS = Path(__file__).parent / "rubrics"
DEFAULT_RUBRIC = "software_engineering_intern"
CAVEAT = ("HackerRank's open-source intern rubric, run once. It weighs open source and projects heavily, and the same resume "
          "can move about ten points between runs. Treat it as one reviewer's opinion, not an ATS verdict.")
STOPWORDS = set("""a an the and or of to in on for with at by from as is are be been being this that these those you your
we our they their it its will can may must should would could have has had do does did not no yes any all some such
other than then there here into over under up out about more most very also using use used able strong experience
ability skills skill knowledge familiarity understanding working work plus preferred required requirement requirements
years year degree bachelor master pursuing currently student students including include includes etc""".split())
GITHUB_API = "https://api.github.com"


def load_rubric(name=DEFAULT_RUBRIC):
    """A role the way hiring-agent loads it: role.json plus the two prompt sources."""
    folder = RUBRICS / name
    role = json.loads((folder / "role.json").read_text())
    role["name"] = name
    role["criteria"] = (folder / "criteria.jinja").read_text()
    role["system_message"] = (folder / "system_message.jinja").read_text()
    return role


def bonus_lines(role):
    """The strict-bonus block hiring-agent renders into its system message from role.json."""
    lines = []
    for rule in role.get("bonus_rules", []):
        if "tiers" in rule:
            tiers = "; ".join(f"{t['points']} point{'s' if t['points'] != 1 else ''} for ranks {t['min_rank']}-{t['max_rank']}" for t in rule["tiers"])
            lines.append(f"- {rule['label']}: {tiers}; otherwise 0 points. {rule['description']}")
        else:
            lines.append(f"- {rule['label']}: allowed points are {', '.join(str(p) for p in [0, *rule['points']])}. {rule['description']}")
    return "\n".join(lines)


def render_task(role):
    """System message + criteria as one task text, with the Jinja bits resolved the way hiring-agent does."""
    system = role["system_message"].replace("{{ bonus_max }}", str(role["bonus_max"]))
    system = re.sub(r"\{% if bonus_rules %\}.*\{% endif %\}", "- **STRICT BONUS RULES**: Use only the values configured below. No other point value is valid.\n" + bonus_lines(role),
                    system, flags=re.S)
    criteria = role["criteria"].replace("{{ text_content }}", "(the resume is given as `resume_text` in the data; GitHub data, when present, follows it under === GITHUB DATA ===)")
    return system.strip() + "\n\n" + criteria.strip()


def allowed_points(rule):
    return sorted({0, *(t["points"] for t in rule["tiers"])} if "tiers" in rule else {0, *rule["points"]})


def points_for_rank(rank, tiers):
    for t in tiers:
        if rank is not None and t["min_rank"] <= rank <= t["max_rank"]:
            return t["points"]
    return 0


def validate(role, found):
    """hiring-agent's pydantic validation, in plain code: category caps, bonus points limited to the configured
    values (tier points must match the stated rank), bonus total capped, deductions non-negative, final clamped."""
    s = lambda v, n=600: str(v or "").strip()[:n]  # noqa: E731
    scores = found.get("scores") if isinstance(found.get("scores"), dict) else {}
    categories, subtotal = [], 0
    for c in role["categories"]:
        item = scores.get(c["key"]) if isinstance(scores.get(c["key"]), dict) else {}
        try:
            score = max(0, min(int(c["max"]), int(round(float(item.get("score", 0))))))
        except (TypeError, ValueError):
            score = 0
        subtotal += score
        categories.append({"key": c["key"], "label": c["label"], "score": score, "max": int(c["max"]), "evidence": s(item.get("evidence")) or "No evidence given."})
    items_in = ((found.get("bonus_points") or {}).get("items") if isinstance(found.get("bonus_points"), dict) else None) or {}
    bonus_items, bonus_sum = [], 0
    for rule in role.get("bonus_rules", []):
        item = items_in.get(rule["key"]) if isinstance(items_in.get(rule["key"]), dict) else {}
        try:
            points = int(item.get("points", 0) or 0)
        except (TypeError, ValueError):
            points = 0
        rank = None
        note = ""
        if "tiers" in rule:
            try:
                rank = int(item["rank"]) if item.get("rank") is not None else None
            except (TypeError, ValueError):
                rank = None
            expected = points_for_rank(rank, rule["tiers"])
            if points != expected:
                note = f" (model gave {points}; rank {rank} allows {expected})"
                points = expected
        elif points not in allowed_points(rule):
            note = f" (model gave {points}; allowed {allowed_points(rule)})"
            points = 0
        bonus_sum += points
        bonus_items.append({"key": rule["key"], "label": rule["label"], "points": points, "rank": rank, "evidence": (s(item.get("evidence")) or "No evidence.") + note})
    bonus_total = min(bonus_sum, int(role["bonus_max"]))
    ded = found.get("deductions") if isinstance(found.get("deductions"), dict) else {}
    try:
        deductions = max(0, int(round(float(ded.get("total", 0) or 0))))
    except (TypeError, ValueError):
        deductions = 0
    total = max(int(role["min_final_score"]), min(int(role["max_final_score"]), subtotal + bonus_total - deductions))
    lists = lambda key, n: [s(x, 200) for x in (found.get(key) or []) if isinstance(x, str) and x.strip()][:n]  # noqa: E731
    return {"rubric": role["name"], "position": role["position_title"], "total": total, "max": sum(c["max"] for c in role["categories"]) + int(role["bonus_max"]),
            "categories": categories, "bonus": {"total": bonus_total, "subtotal": bonus_sum, "max": int(role["bonus_max"]), "items": bonus_items},
            "deductions": {"total": deductions, "reasons": s(ded.get("reasons"))},
            "strengths": lists("key_strengths", 5), "improvements": lists("areas_for_improvement", 3)}


# ---- keyword coverage (deterministic) ----
def terms(text):
    return [t for t in re.findall(r"[a-z0-9+#.]+", text.lower()) if len(t.strip(".")) >= 2 and t not in STOPWORDS]


def coverage(requirements, resume_text):
    """Which posting requirements the resume text mentions: a requirement counts as covered when at least half of
    its meaningful terms appear in the resume (any order). Pure string matching, so it is repeatable."""
    haystack = " " + re.sub(r"\s+", " ", resume_text.lower()) + " "
    rows = []
    for req in requirements or []:
        words = list(dict.fromkeys(terms(str(req))))
        if not words:
            continue
        missing = [w for w in words if w not in haystack]
        rows.append({"text": str(req), "covered": len(missing) <= len(words) // 2, "missing": missing})
    covered = sum(1 for r in rows if r["covered"])
    return {"requirements": rows, "covered": covered, "total": len(rows), "percent": round(100 * covered / len(rows)) if rows else None}


# ---- GitHub signals (public API, no token) ----
def github_username(url):
    p = urlsplit(str(url or "").strip() if "://" in str(url or "") else "https://" + str(url or "").strip())
    if not (p.hostname or "").lower().endswith("github.com"):
        return ""
    parts = [x for x in p.path.split("/") if x]
    return parts[0] if parts and parts[0].lower() not in {"orgs", "settings"} else ""


async def github_signals(url, max_repos=15):
    """Profile and repositories the way hiring-agent gathers them: skips forks with under 5 forks, counts
    contributors per repo (open_source = more than one), and the user's own commit count. Unauthenticated, so
    only the most recently updated repos get a contributors call."""
    username = github_username(url)
    if not username:
        return {"used": False, "note": "No GitHub link in the profile."}
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "JobApplier"}
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            user = await client.get(f"{GITHUB_API}/users/{username}")
            if user.status_code != 200:
                return {"used": False, "note": f"GitHub user {username}: HTTP {user.status_code}."}
            u = user.json()
            repos = await client.get(f"{GITHUB_API}/users/{username}/repos", params={"sort": "updated", "per_page": 100, "type": "all"})
            repos.raise_for_status()
            projects = []
            for repo in repos.json():
                if repo.get("fork") and repo.get("forks_count", 0) < 5:
                    continue
                if len(projects) >= max_repos:
                    break
                contributors = await client.get(f"{GITHUB_API}/repos/{username}/{repo['name']}/contributors", params={"per_page": 100})
                people = contributors.json() if contributors.status_code == 200 and isinstance(contributors.json(), list) else []
                mine = next((c.get("contributions", 0) for c in people if (c.get("login") or "").lower() == username.lower()), 0)
                projects.append({"name": repo.get("name"), "description": repo.get("description"), "github_url": repo.get("html_url"),
                                 "live_url": repo.get("homepage") or None, "technologies": [repo["language"]] if repo.get("language") else [],
                                 "project_type": "open_source" if len(people) > 1 else "self_project", "contributor_count": len(people),
                                 "author_commit_count": mine, "total_commit_count": sum(c.get("contributions", 0) for c in people),
                                 "github_details": {"stars": repo.get("stargazers_count", 0), "forks": repo.get("forks_count", 0), "language": repo.get("language"),
                                                    "created_at": repo.get("created_at"), "updated_at": repo.get("updated_at"), "topics": repo.get("topics", []),
                                                    "fork": repo.get("fork", False), "archived": repo.get("archived", False)}})
    except Exception as exc:
        return {"used": False, "username": username, "note": f"GitHub lookup failed: {str(exc)[:120]}"}
    projects = [p for p in projects if p["author_commit_count"] > 0]
    projects.sort(key=lambda p: (p["author_commit_count"], p["github_details"]["stars"]), reverse=True)
    return {"used": True, "username": username, "profile": {"name": u.get("name"), "bio": u.get("bio"), "public_repos": u.get("public_repos"),
                                                            "followers": u.get("followers"), "created_at": u.get("created_at"), "blog": u.get("blog")},
            "projects": projects[:10], "total_projects": len(projects),
            "open_source": sum(1 for p in projects if p["project_type"] == "open_source"), "note": ""}


def github_text(data):
    if not data.get("used"):
        return ""
    lines = ["", "=== GITHUB DATA ===", f"Profile: {json.dumps(data['profile'])}",
             f"{data['total_projects']} repositories with the user's own commits ({data['open_source']} with more than one contributor):"]
    lines += [json.dumps(p) for p in data["projects"]]
    return "\n".join(lines)


def pdf_text(path):
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)[:60000]


async def score_resume(model, resume_text, requirements, github_url, rubric=DEFAULT_RUBRIC, github=True):
    """One rubric call on the resume text (+ GitHub data when a link is known) and the coverage check."""
    role = load_rubric(rubric)
    signals = await github_signals(github_url) if github and github_url else {"used": False, "note": "GitHub lookup is off." if not github else "No GitHub link in the profile."}
    found = await model.json(render_task(role), {"resume_text": resume_text + github_text(signals)})
    report = validate(role, found)
    report.update({"coverage": coverage(requirements, resume_text), "scored_at": now(), "caveat": CAVEAT,
                   "github": {k: signals.get(k) for k in ("used", "username", "total_projects", "open_source", "note")}})
    return report


async def match_report(model, store, resume, requirements, profile_identity, previous=None, github=True):
    """The report for a built resume; reused when the same PDF was already scored."""
    if previous and previous.get("resume_sha256") == resume.get("sha256") and not previous.get("error"):
        return previous
    try:
        text = await asyncio.to_thread(pdf_text, store.root / resume["path"])
        report = await score_resume(model, text, requirements, profile_identity.get("github_url", ""), github=github)
    except Exception as exc:
        return {"error": str(exc)[:300], "resume_sha256": resume.get("sha256"), "scored_at": now()}
    report["resume_sha256"] = resume.get("sha256")
    return report
