"""Pre-scan of an application form for knock-out questions, before anything is filled in.

A knock-out question is one the ATS uses to reject automatically: years of experience, a degree, work
authorization or sponsorship, a salary floor. Finding it first (after career-ops' apply mode, MIT) means the
candidate decides how to answer, or not to apply, before time goes into the rest of the form. Two more kinds are
warned about, never acted on: a salary-history question (prohibited for employers in many US states) and a
question about immigration status rather than work authorization. Plain authorization and sponsorship questions
are lawful screening and only ever get the knock-out treatment.

Deterministic: regular expressions over the form's field labels. The profile's Answers section decides whether the
candidate already has an answer for the area; when it does not, the question is handed to the Dashboard."""
import re

AREAS = [
    ("experience", re.compile(r"\b(at least|minimum( of)?|more than|\d\+?)\s*\d*\s*(years|yrs)\b.*\b(experience|professional)\b|\byears of (professional |relevant |work )?experience\b", re.I),
     "years of experience"),
    ("degree", re.compile(r"\b(do you (have|hold)|have you (completed|earned|obtained)|will you have)\b.*\b(bachelor|master|degree|phd|diploma)\b|\b(bachelor|master)'?s? degree\b.*\?", re.I),
     "degree requirement"),
    ("sponsorship", re.compile(r"\b(require|need)\b.*\b(sponsorship|visa)\b|\bsponsor(ship)?\b.*\b(now or in the future|future)\b", re.I),
     "visa sponsorship"),
    ("authorization", re.compile(r"\b(legally )?authori[sz]ed to work\b|\bwork authori[sz]ation\b|\beligib(le|ility) to work\b", re.I),
     "work authorization"),
    ("salary", re.compile(r"\b(desired|expected|target|minimum)\s+(salary|compensation|pay|hourly rate)\b|\bsalary (expectation|requirement)s?\b|\bcompensation expectation", re.I),
     "salary expectation"),
]
WARNINGS = [
    ("salary_history", re.compile(r"\b(current|previous|prior|last|most recent)\s+(salary|compensation|pay|wage)\b|\bsalary history\b", re.I),
     "asks for salary history, which employers in many US states may not request; you are generally not obliged to answer"),
    ("status", re.compile(r"\bare you (a )?(u\.?s\.?|united states) citizen\b|\bcitizen(ship)? (or|status)\b|\bpermanent resident\b.*\?|\bon a permanent basis\b", re.I),
     "asks about immigration status rather than work authorization; the lawful form of this question is whether you are authorized to work"),
]
ANSWER_HINTS = {"experience": r"years|experience", "degree": r"degree|bachelor|master|graduat|gpa",
                "sponsorship": r"sponsor|visa", "authorization": r"authori|eligib|citizen|work in", "salary": r"salary|compensation|pay|hourly|wage"}


def scan(snapshot, answer_bank, seen=()):
    """Knock-out and warning hits on this page's fields, skipping labels already reported (`seen`).
    Each hit: {kind, area, label, answered, note}."""
    hits = []
    bank = " ".join(f"{k} {v}" for k, v in (answer_bank or {}).items() if k.startswith("answers.")).lower()
    for frame in snapshot.get("frames", []):
        for field in frame.get("fields", []):
            label = re.sub(r"\s+", " ", str(field.get("label") or "")).strip()
            if not label or label in seen or field.get("type") in {"submit", "button", "reset", "hidden", "password"}:
                continue
            for area, pattern, note in WARNINGS:
                if pattern.search(label):
                    hits.append({"kind": "warning", "area": area, "label": label[:200], "answered": True, "note": f"This question {note}."})
                    break
            else:
                for area, pattern, name in AREAS:
                    if pattern.search(label):
                        answered = bool(re.search(ANSWER_HINTS[area], bank))
                        hits.append({"kind": "knockout", "area": area, "label": label[:200], "answered": answered,
                                     "note": f"A {name} question. " + ("Your profile has an answer; the form may reject on it, so check it."
                                                                      if answered else "Your profile has no answer for this; add one before the form is filled.")})
                        break
    return hits
