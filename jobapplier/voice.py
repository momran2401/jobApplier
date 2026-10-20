"""Voice guardrail for text written in the candidate's name: letters, form answers, networking and follow-up drafts.

The VOICE prompt asks the model for plain, human writing; this is the deterministic check behind it, so the
model cannot talk its way past it. It looks for the words and constructions that mark text as machine-written:
the overused vocabulary, the dead openers and transitions, hype, and the "not X, but Y" reframe that every model
reaches for. The lists are adapted from career-ops' voice-dna template (career-ops-hq/career-ops, MIT; see
jobapplier/rubrics/LICENSE-career-ops), trimmed to the words that are tells in a job-search letter rather than
ordinary engineering vocabulary ("scalable", "robust" and "integrated" stay allowed).

Like the fact check, this never rewrites anything: it reports the problems, the workflow asks the model once for
a rewrite, and whatever remains is shown to the reviewer."""
import re

WORDS = """delve delves delving realm tapestry paradigm cutting-edge revolutionize revolutionise intricate intricacies
showcase showcases showcasing pivotal meticulously meticulous vibrant unparalleled leverage leveraging leveraged synergy
synergies game-changer game-changing testament commendable groundbreaking foster fostering holistic garner trailblazing
unleash transformative seamless seamlessly empower empowering elevate elevating mission-critical visionary disruptive
reimagine reimagining unprecedented state-of-the-art supercharge future-proof unlock unlocking democratize
revolutionary synergize captivate underscores""".split()
PHRASES = ["in today's", "in today’s", "it's important to note", "it is important to note", "it's worth noting", "it is worth noting",
           "in order to", "let's dive", "let's explore", "let's unpack", "dive into", "at the end of the day", "moving forward",
           "to put this in perspective", "it goes without saying", "i would be happy to", "i'd be happy to",
           "i hope this email finds you well", "i hope this message finds you well", "i am writing to express", "i am excited to apply",
           "i am thrilled", "i am passionate about", "just checking in", "just following up", "touching base", "circling back",
           "reaching out", "furthermore", "additionally", "moreover", "that being said", "that said", "with that in mind",
           "on top of that", "it is also worth mentioning", "a perfect fit", "the perfect candidate", "hit the ground running",
           "think outside the box", "proven track record", "results-driven", "self-starter", "go-getter", "wear many hats",
           "let that sink in", "this changes everything", "serves as a", "stands as a"]
# "Not X, but Y" and its disguises: the sentence negates one framing, then asserts the corrected one.
REFRAMES = [re.compile(p, re.I) for p in (
    r"\bnot (just|only|merely|simply) [^.!?]{2,80}?,? but (also )?",
    r"\b(isn't|is not|wasn't|was not|aren't|are not) (just |only |simply )?about [^.!?]{2,80}?[.;,]? (it's|it is|they're|they are) about\b",
    r"\b(isn't|is not|wasn't) (just |only |simply )?(a |an )?[^.!?]{2,60}?[.;] (it's|it is|this is) (a |an )?",
    r"\bno \w+, no \w+, just\b",
    r"\bforget [^.!?]{2,40}[.;:] ",
    r"\bless \w+, more \w+",
    r"\b(you|we) don't need [^.!?]{2,60}?[.;,] (you|we) need\b",
    r"\bthe question (isn't|is not) [^.!?]{2,60}?[.;,] (the question is|it's)\b",
    r"\bstop \w+ing [^.!?]{2,40}?[.;,] start\b",
)]
DASH = re.compile(r"(?<=[A-Za-z,.!?)])\s*[—–]\s*(?=[A-Za-z(])")
PHRASE_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(p) for p in PHRASES) + r")(?![a-z])", re.I)
WORD_RE = re.compile(r"(?<![a-z-])(" + "|".join(re.escape(w) for w in WORDS) + r")(?![a-z-])", re.I)
RULE_OF_THREE = re.compile(r"\b(\w+), (\w+),? and (\w+)\b")


def check(text):
    """Problems in `text`, each a short string naming what was found. Empty when the text passes."""
    text = str(text or "")
    found = []
    for m in WORD_RE.finditer(text):
        found.append(f'"{m.group(1)}"')
    for m in PHRASE_RE.finditer(text):
        found.append(f'"{m.group(1)}"')
    for pattern in REFRAMES:
        m = pattern.search(text)
        if m:
            found.append(f'"not X, but Y" construction: "{m.group(0).strip()[:60]}"')
            break
    if DASH.search(text):
        found.append("a dash used as punctuation between words")
    out = []
    for f in found:  # stable order, no duplicates
        if f.lower() not in {x.lower() for x in out}:
            out.append(f)
    return out


def describe(problems):
    return "; ".join(problems[:6]) + (f" and {len(problems) - 6} more" if len(problems) > 6 else "")


def rewrite_instruction(problems):
    """The one-line instruction added to the task when the model is asked to try again."""
    return ("The previous draft read like machine-written text. Remove or replace: " + ", ".join(problems[:8])
            + ". Write the way a real person talks: plain words, no stock phrases, state the point directly instead of "
            "negating something first. Keep every fact exactly as it was.")
