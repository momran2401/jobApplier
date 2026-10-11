"""Fact-check gate for generated text (letters, answers, networking drafts, wording suggestions).

Every number, date, percentage, email or link in a generated text must appear in a source the candidate
controls (profile, job posting, company research, networking evidence). Those are blocking: the text is not
used until they are gone. Capitalized names (companies, tools, places) that appear nowhere in the sources are
warnings: shown to the reviewer, not blocking, because the heuristic cannot tell a tool name from a sentence
start. Deterministic, no model involved, so the model cannot talk its way past it."""
import re

NUMBER = re.compile(r"(?<![A-Za-z/.-])(?:\$\s?)?\d[\d,]*(?:\.\d+)?\s?(?:%|k|K|M|x|X|\+)?(?![\d,.:/-]*[A-Za-z]{2,})")
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://\S+")
NAME = re.compile(r"\b([A-Z][A-Za-z0-9+#.&-]{1,}(?:\s(?:of|and|&|for|the)?\s?[A-Z][A-Za-z0-9+#.&-]{1,})*)")
STOP = set("""i i'm i've i'd i'll a an the and or but if in on at to for of with by from as is it its this that these those my
me our we you your he she they them his her their there here when where which who what why how one two three four five
six seven eight nine ten dear hi hello thanks thank best regards sincerely yours monday tuesday wednesday thursday friday
saturday sunday january february march april may june july august september october november december spring summer
fall winter autumn mr ms mrs dr team hiring manager university engineer engineering intern internship software hardware
electrical computer science degree role position company before after while since during year years today tomorrow
please also however because although first second next last mustafa""".split())


def normalize(text):
    return re.sub(r"[\s,]+", " ", str(text or "").lower())


def numbers(text):
    out = []
    for m in NUMBER.finditer(text):
        token = m.group(0).strip()
        digits = re.sub(r"\D", "", token)
        if not digits or (len(digits) == 1 and not re.search(r"[%$kKMx+]", token)):
            continue  # "a team of 5": too common and too short to verify; "40%" or "$5k" is a claim
        out.append(token)
    return out


def names(text):
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        for m in NAME.finditer(sentence):
            if m.start() == 0:
                continue  # sentence-initial capitalization proves nothing
            candidate = m.group(1).strip().rstrip(".,;:'’")
            words = candidate.split()
            if all(w.lower().strip(".,") in STOP for w in words):
                continue
            found.append(candidate)
    return found


def supported(token, haystacks):
    key = normalize(token).replace("$", "").strip()
    bare = re.sub(r"[^a-z0-9.%+#&-]", "", key)
    for h in haystacks:
        if key and key in h:
            return True
        if bare and bare in re.sub(r"[^a-z0-9.%+#&-]", "", h):
            return True
    return False


def check(text, sources):
    """Returns {"blocking": [...], "warnings": [...]} for `text` against the iterable of source texts."""
    haystacks = [normalize(s) for s in sources if s]
    blocking, warnings = [], []
    for token in numbers(text) + CONTACT.findall(text):
        if not supported(token, haystacks) and token not in blocking:
            blocking.append(token)
    for name in names(text):
        if not supported(name, haystacks) and not any(supported(w, haystacks) for w in name.split() if len(w) > 2) and name not in warnings:
            warnings.append(name)
    return {"blocking": blocking, "warnings": warnings}


def describe(result):
    parts = []
    if result["blocking"]:
        parts.append("not in your sources: " + ", ".join(result["blocking"][:8]))
    if result["warnings"]:
        parts.append("check these names: " + ", ".join(result["warnings"][:8]))
    return "; ".join(parts)
