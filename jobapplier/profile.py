"""The master profile: one Markdown document with fixed sections that every AI task reads.

`Identity`, `Links` and `Answers` hold `- Key: value` lines the app can look up (the answer bank used to fill
forms). The other sections are free Markdown the user edits directly. Every save keeps a dated copy in
`profile/history/` and bumps `profile_version` (a hash of the text) so prepared packages know to re-tailor.
"""
import hashlib
import re
from datetime import datetime, timezone

SECTIONS = ["Identity", "Education", "Experience", "Projects", "Skills", "Publications",
            "Awards & Certifications", "Coursework & Training", "Links", "Answers", "Notes"]
KV_SECTIONS = {"Identity", "Links", "Answers"}
IDENTITY_KEYS = {"first_name": "First name", "last_name": "Last name", "email": "Email", "phone": "Phone",
                 "city": "City", "school": "School", "degree": "Degree", "discipline": "Field of study",
                 "graduation_month": "Graduation month", "graduation_year": "Graduation year"}
LINK_KEYS = {"linkedin_url": "LinkedIn", "portfolio_url": "Portfolio", "github_url": "GitHub"}
TEMPLATE_HINTS = {
    "Identity": "- First name: \n- Last name: \n- Email: \n- Phone: \n- City: \n- School: \n- Degree: \n- Field of study: \n- Graduation month: \n- Graduation year: ",
    "Links": "- LinkedIn: \n- Portfolio: \n- GitHub: ",
    "Answers": "- Will you require sponsorship (now or in the future) to work?: ",
}


def empty_doc():
    parts = ["# Master profile", "",
             "Everything the assistant knows about you. Edit freely; keep the section headings.", ""]
    for name in SECTIONS:
        parts += [f"## {name}", "", TEMPLATE_HINTS.get(name, ""), ""]
    return "\n".join(parts).rstrip() + "\n"


def sections(text):
    """Split the document into {section name: body}. Unknown headings are kept under their own name."""
    result, current, body = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                result[current] = "\n".join(body).strip("\n")
            current, body = m.group(1).strip(), []
        elif current is not None:
            body.append(line)
    if current is not None:
        result[current] = "\n".join(body).strip("\n")
    return result


def kv_lines(body):
    """`- Key: value` lines of a section, in order, skipping blanks."""
    result = {}
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s*([^:]{1,120}?)\s*:\s*(.*?)\s*$", line)
        if m and m.group(2):
            result[m.group(1).strip()] = m.group(2).strip()
    return result


def slug(label):
    return re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")


def answer_bank(text):
    """Flat lookup for form filling: identity.<key>, links.<key>, answers.<question>."""
    secs = sections(text)
    reverse_identity = {v.lower(): k for k, v in IDENTITY_KEYS.items()}
    reverse_links = {v.lower(): k for k, v in LINK_KEYS.items()}
    bank = {}
    for label, value in kv_lines(secs.get("Identity", "")).items():
        bank["identity." + reverse_identity.get(label.lower(), slug(label))] = value
    for label, value in kv_lines(secs.get("Links", "")).items():
        bank["links." + reverse_links.get(label.lower(), slug(label))] = value
    for label, value in kv_lines(secs.get("Answers", "")).items():
        bank["answers." + label] = value
    return bank


def identity(text):
    """Identity facts keyed like the old profile (first_name, email, ...)."""
    bank = answer_bank(text)
    facts = {k[len("identity."):]: v for k, v in bank.items() if k.startswith("identity.")}
    facts.update({k[len("links."):]: v for k, v in bank.items() if k.startswith("links.")})
    return facts


def skills_list(text):
    """Individual skills from the Skills section: bullets, and `Category: a, b, c` lines split into items."""
    items = []
    for line in sections(text).get("Skills", "").splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", line).strip()
        line = re.sub(r"\*\*", "", line)
        if not line:
            continue
        if ":" in line and not line.lower().startswith("http"):
            line = line.split(":", 1)[1]
        for item in re.split(r",|;|\s\|\s|·", line):
            item = item.strip().rstrip(".")
            if item and item.lower() not in {x.lower() for x in items}:
                items.append(item)
    return items


def _norm(line):
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", line)).strip().lower()


def append(text, additions=None, identity_updates=None):
    """Add lines under sections (skipping lines already present) and set/insert identity or link values.
    Returns (new_text, change_count). Never removes anything."""
    secs = sections(text)
    changed = 0
    for section, lines in (additions or {}).items():
        if section not in SECTIONS:
            continue
        existing = {_norm(line) for line in secs.get(section, "").splitlines()}
        answered = {k.lower() for k in kv_lines(secs.get(section, ""))}
        new = []
        for line in lines or []:
            line = str(line).rstrip()
            if not line.strip():
                continue
            if section in KV_SECTIONS:
                m = re.match(r"^\s*[-*]\s*([^:]+?)\s*:\s*(.+)$", line)
                if not m or m.group(1).strip().lower() in answered:
                    continue  # key/value sections only take new `- Key: value` lines
                answered.add(m.group(1).strip().lower())
                filled = _fill_empty(secs.get(section, ""), m.group(1).strip(), m.group(2).strip())
                if filled is not None:  # the template already had this key with no value: fill it in place
                    secs[section] = filled
                    changed += 1
                    continue
            elif not re.match(r"^\s*([-*]\s|#|>|\||\d+\.)", line):
                line = "- " + line.strip()
            if _norm(line) in existing:
                continue
            existing.add(_norm(line))
            new.append(line)
        if new:
            body = secs.get(section, "").rstrip("\n")
            secs[section] = (body + "\n" if body else "") + "\n".join(new)
            changed += len(new)
    for key, value in (identity_updates or {}).items():
        value = str(value or "").strip()
        if not value:
            continue
        section, label = ("Links", LINK_KEYS[key]) if key in LINK_KEYS else ("Identity", IDENTITY_KEYS.get(key))
        if not label:
            continue
        body = secs.get(section, "")
        pattern = re.compile(rf"^([ \t]*[-*][ \t]*{re.escape(label)}[ \t]*:)[ \t]*(.*)$", re.IGNORECASE | re.MULTILINE)
        m = pattern.search(body)
        if m and m.group(2).strip():
            continue  # keep the user's value; conflicts are reported separately
        if m:
            secs[section] = body[:m.start()] + f"{m.group(1)} {value}" + body[m.end():]
        else:
            secs[section] = (body.rstrip("\n") + "\n" if body.strip() else "") + f"- {label}: {value}"
        changed += 1
    return render(text, secs), changed


def _fill_empty(body, label, value):
    """Fill `- Label: ` (empty) in place; None when the section has no such empty line."""
    pattern = re.compile(rf"^([ \t]*[-*][ \t]*{re.escape(label)}[ \t]*:)[ \t]*$", re.IGNORECASE | re.MULTILINE)
    m = pattern.search(body)
    return body[:m.start()] + f"{m.group(1)} {value}" + body[m.end():] if m else None


def render(original, secs):
    """Rebuild the document keeping the preamble and section order (unknown sections stay at the end)."""
    preamble = original.split("\n## ", 1)[0].rstrip("\n") if "\n## " in original else ""
    if not preamble.strip() or preamble.lstrip().startswith("## "):
        preamble = "# Master profile"
    order = SECTIONS + [s for s in secs if s not in SECTIONS]
    parts = [preamble, ""]
    for name in order:
        parts += [f"## {name}", "", secs.get(name, "").rstrip("\n"), ""]
    return "\n".join(parts).rstrip() + "\n"


def version(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def from_legacy(profile):
    """Build the first document from the old facts/skills/experience/answers profile so nothing is lost."""
    text = empty_doc()
    facts = profile.get("facts") or {}
    text, _ = append(text, identity_updates={k: v for k, v in facts.items() if k in IDENTITY_KEYS or k in LINK_KEYS})
    skills = [s["name"] if isinstance(s, dict) else str(s) for s in profile.get("skills") or []]
    additions = {}
    if skills:
        additions["Skills"] = ["- " + ", ".join(skills)]
    experience = [e.get("description") if isinstance(e, dict) else str(e) for e in profile.get("experience") or []]
    if any(experience):
        additions["Experience"] = ["- " + e for e in experience if e]
    answers = profile.get("answers") or {}
    if answers:
        additions["Answers"] = [f"- {q}: {a}" for q, a in answers.items() if str(a).strip()]
    text, _ = append(text, additions)
    return text


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
