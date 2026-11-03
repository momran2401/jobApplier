import asyncio
import difflib
import hashlib
import io
import os
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path

from pypdf import PdfReader

from .store import now

MAX_FILE = 20 * 1024 * 1024
KINDS = {"resume", "latex", "linkedin", "transcript", "cover_template", "supporting"}


def safe_unzip(data, dest):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if len(z.infolist()) > 1000 or sum(i.file_size for i in z.infolist()) > 50 * 1024 * 1024:
            raise ValueError("Overleaf ZIP is too large (50 MB expanded / 1,000 files maximum).")
        for item in z.infolist():
            p = Path(item.filename)
            if p.is_absolute() or ".." in p.parts or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError("ZIP contains an unsafe path or symbolic link.")
            if "__MACOSX" in p.parts or p.name.startswith("."):
                continue
            target = dest / p
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(item))


def save_upload(store, name, kind, data):
    if kind not in KINDS:
        raise ValueError("Unknown document type.")
    if len(data) > MAX_FILE:
        raise ValueError("Maximum upload size is 20 MB.")
    ext = Path(name).suffix.lower()
    if ext not in {".pdf", ".tex", ".zip", ".txt", ".md"}:
        raise ValueError("Upload PDF, TEX, ZIP, TXT, or Markdown files.")
    if kind == "latex" and ext not in {".zip", ".tex"}:
        raise ValueError("Overleaf source must be a ZIP or TEX file.")
    did = str(uuid.uuid4())
    folder = store.root / "documents" / did
    folder.mkdir(parents=True, mode=0o700)
    path = folder / ("source" + ext)
    path.write_bytes(data)
    text, pages, tex_files = "", None, []
    if ext == ".pdf":
        pdf = PdfReader(io.BytesIO(data))
        pages = len(pdf.pages)
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)[:100000]
    elif ext == ".zip":
        safe_unzip(data, folder / "project")
        tex_files = sorted(str(p.relative_to(folder / "project")) for p in (folder / "project").rglob("*.tex"))
        text = "\n\n".join(f"FILE: {f}\n{(folder / 'project' / f).read_text(errors='replace')}" for f in tex_files)[:100000]
    else:
        text = data.decode("utf-8", errors="replace")[:100000]
        if ext == ".tex":
            (folder / "project").mkdir()
            (folder / "project" / "main.tex").write_bytes(data)
            tex_files = ["main.tex"]
    doc = {"id": did, "name": Path(name).name, "kind": kind, "path": str(path.relative_to(store.root)),
           "text": text, "pages": pages, "tex_files": tex_files, "sha256": hashlib.sha256(data).hexdigest(),
           "created_at": now()}
    store.save_document(doc)
    return doc


def latex_source(store):
    """The main .tex of the newest LaTeX upload, with the skills markers that would be used: {text, name, start, end}."""
    source = store.document_by_kind("latex")
    if not source:
        return {"text": "", "name": "", "start": "", "end": ""}
    settings = store.settings()
    path = store.root / "documents" / source["id"] / "project" / main_tex(source, settings)
    text = path.read_text(errors="replace") if path.is_file() else ""
    start, end = skills_markers(text, settings) if text else (None, None)
    return {"text": text, "name": source["name"], "start": start or "", "end": end or ""}


def save_latex_source(store, text):
    """Store pasted LaTeX as the resume source (replacing earlier LaTeX uploads) and report the markers found."""
    text = text.replace("\r\n", "\n").strip() + "\n"
    if "\\begin{document}" not in text or "\\end{document}" not in text:
        raise ValueError("Paste the whole .tex file, including \\begin{document} and \\end{document}.")
    for old in [d for d in store.documents(with_text=False) if d["kind"] == "latex"]:
        store.delete_document(old["id"])
        shutil.rmtree(store.root / "documents" / old["id"], ignore_errors=True)
    save_upload(store, "resume.tex", "latex", text.encode())
    return latex_source(store)


def latex_escape(text):
    mapping = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
               "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(mapping.get(c, c) for c in text)


def replace_skills(source, start, end, skills):
    """Legacy flat replacement: one comma-separated line between the markers."""
    return replace_block(source, start, end, "\n" + ", ".join(latex_escape(s) for s in skills) + "\n")


def replace_block(source, start, end, body):
    if not start or not end or source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Set unique skills start/end markers from your LaTeX source in Settings.")
    a = source.index(start) + len(start)
    b = source.index(end, a)
    return source[:a] + body + source[b:]


def block_between(source, start, end):
    a = source.index(start) + len(start)
    return source[a:source.index(end, a)]


KEEP_CATEGORIES = ("languages",)


def render_skills(categories, original_block=""):
    """Skills in the resume's own style: `\\textbf{Category:} a, b, c\\par` lines.
    Lines of the original block whose category is in KEEP_CATEGORIES (spoken languages) are kept verbatim."""
    lines = []
    for cat in categories:
        name, items = str(cat.get("name", "")).strip().rstrip(":"), [str(i).strip() for i in cat.get("items") or [] if str(i).strip()]
        if not name or not items or name.lower() in KEEP_CATEGORIES:
            continue
        lines.append(f"\\textbf{{{latex_escape(name)}:}}\n{', '.join(latex_escape(i) for i in items)}\\par")
    for m in re.finditer(r"(\\textbf\{(?:" + "|".join(KEEP_CATEGORIES) + r"):?\}[\s\S]*?)(?=\n\s*\\textbf|\n\s*\n|$)", original_block, re.IGNORECASE):
        lines.append(m.group(1).strip())
        break
    return "\n\n" + "\n\n".join(lines) + "\n\n"


def apply_edits(source, edits):
    """Apply accepted experience edits: each `original` must appear exactly once in the source."""
    for e in edits or []:
        original, proposed = str(e.get("original", "")), str(e.get("proposed", ""))
        if not original.strip() or source.count(original) != 1:
            raise ValueError("An experience edit no longer matches the resume source exactly; re-run tailoring.")
        source = source.replace(original, proposed)
    return source


def validate_edits(source, edits, limit=3):
    """Keep only edits whose original snippet occurs exactly once in the source (so they can be applied safely)."""
    valid = []
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        original, proposed = str(e.get("original", "")).strip(), str(e.get("proposed", "")).strip()
        if original and proposed and original != proposed and source.count(original) == 1:
            valid.append({"original": original, "proposed": proposed, "reason": str(e.get("reason", ""))[:300]})
        if len(valid) == limit:
            break
    return valid


def trim_one(categories):
    """Drop the last item of the longest category (the lowest-priority item); returns (categories, dropped) or None."""
    best = max((c for c in categories if c.get("items")), key=lambda c: len(c["items"]), default=None)
    if not best or len(best["items"]) <= 1 and sum(len(c.get("items") or []) for c in categories) <= 1:
        return None
    dropped = best["items"].pop()
    return [c for c in categories if c.get("items")], f"{best['name']}: {dropped}"


async def compile_tex(directory, main):
    path = (directory / main).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError("Choose an existing main .tex file from the uploaded project.")
    # Run only user source in restricted TeX mode; never accept model-generated executable TeX.
    if shutil.which("tectonic"):
        cmd = ["tectonic", "--untrusted", "--keep-logs", str(path.name)]
    elif shutil.which("pdflatex"):
        cmd = ["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", path.name]
    else:
        raise ValueError("LaTeX is not installed. Install Tectonic, or upload a prepared resume PDF for this application.")
    env = {**os.environ, "openin_any": "p", "openout_any": "p", "shell_escape": "f"}
    proc = await asyncio.create_subprocess_exec(*cmd, cwd=path.parent, env=env,
                                              stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), 90)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ValueError("Resume compilation timed out.") from None
    except asyncio.CancelledError:
        proc.kill()
        raise
    if proc.returncode:
        raise ValueError("LaTeX compilation failed. Check the source and dependencies. " + output.decode(errors="replace")[-1200:])
    pdf = path.with_suffix(".pdf")
    reader = PdfReader(pdf)
    if not reader.pages or not any(p.extract_text() for p in reader.pages):
        raise ValueError("The compiled PDF has no extractable text.")
    return pdf, len(reader.pages)


SKILLS_SECTION = re.compile(r"\\section\*?\{[^}]*skills[^}]*\}", re.I)
NEXT_SECTION = re.compile(r"\\section\*?\{[^}]*\}|\\end\{document\}")


def skills_markers(source, settings):
    """(start, end) markers for the skills block. Markers set in Settings win when both occur exactly once in the
    source; otherwise the skills section is found on its own: its `\\section{... Skills ...}` heading up to the next
    section heading (or `\\end{document}`), as long as both are unique in the source. (None, None) when nothing fits."""
    start, end = (settings.get("skills_start") or "").strip(), (settings.get("skills_end") or "").strip()
    if start and end and source.count(start) == 1 and source.count(end) == 1 and source.index(start) < source.index(end):
        return start, end
    heading = SKILLS_SECTION.search(source)
    if not heading or source.count(heading.group(0)) != 1:
        return None, None
    after = NEXT_SECTION.search(source, heading.end())
    if not after or source.count(after.group(0)) != 1:
        return None, None
    return heading.group(0), after.group(0)


def main_tex(source, settings):
    """The main .tex of the uploaded project: the configured name if it exists there, else the only file."""
    files = source.get("tex_files") or []
    configured = (settings.get("tex_main") or "").strip()
    if configured in files:
        return configured
    if len(files) == 1:
        return files[0]
    return configured


async def build_resume(store, jid, skills=None, *, categories=None, edits=None, max_pages=1, attempts=8):
    """Tailored resume PDF. With `categories`, the skills block is rendered in the resume's style and trimmed
    item by item until the PDF fits `max_pages` (the one-page rule). `edits` are accepted experience edits."""
    docs, settings = store.documents(), store.settings()
    source = next((d for d in docs if d["kind"] == "latex"), None)
    master = next((d for d in docs if d["kind"] == "resume" and d["path"].endswith(".pdf")), None)
    out = store.root / "artifacts" / jid / str(uuid.uuid4())
    out.mkdir(parents=True, mode=0o700)
    diff, warning, dropped, skills_block, tex, cats = "", "", [], "", "", []
    start = end = None
    if source:
        main = main_tex(source, settings)
        main_path = store.root / "documents" / source["id"] / "project" / main
        if main_path.is_file():
            start, end = skills_markers(main_path.read_text(), settings)
    if source and start and end:
        original = store.root / "documents" / source["id"] / "project"
        shutil.copytree(original, out / "baseline")
        shutil.copytree(original, out / "tailored")
        baseline_pdf, baseline_pages = await compile_tex(out / "baseline", main)
        target = (out / "tailored" / main).resolve()
        if not target.is_relative_to((out / "tailored").resolve()):
            raise ValueError("Invalid main file.")
        old = target.read_text()
        base = apply_edits(old, edits) if edits else old
        if edits:
            replace_block(base, start, end, "")  # markers must survive the edits
        cats = [dict(c, items=list(c.get("items") or [])) for c in (categories or [])]  # copies: trimming mutates
        original_block = block_between(old, start, end)
        for attempt in range(attempts + 1):
            if cats:
                skills_block = render_skills(cats, original_block)
                new = replace_block(base, start, end, skills_block)
            else:
                new = replace_skills(base, start, end, skills or [])
            target.write_text(new)
            pdf, pages = await compile_tex(out / "tailored", main)
            limit = max(max_pages, baseline_pages)
            if pages <= limit or not cats or attempt == attempts:
                break
            trimmed = trim_one(cats)
            if not trimmed:
                break
            cats, item = trimmed
            dropped.append(item)
        tex = new
        diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="Master", tofile="Tailored"))
        if pages > max(max_pages, baseline_pages):
            warning = f"The tailored resume is {pages} pages even after trimming {len(dropped)} skills. Inspect the PDF layout."
        elif pages != baseline_pages:
            warning = f"Page count changed from {baseline_pages} to {pages}. Inspect the PDF layout."
        (out / "resume.tex").write_text(new)
    elif master:
        pdf, pages = store.root / master["path"], master["pages"]
        warning = ("Using your original PDF unchanged. " + ("No skills section was found in your LaTeX source; set the skills markers in Settings."
                   if source else "Paste your resume's LaTeX source on the Profile page to enable skills tailoring."))
    else:
        raise ValueError("Upload a resume PDF, or paste your resume's LaTeX source on the Profile page.")
    shutil.copyfile(pdf, out / "resume.pdf")
    return {"path": str((out / "resume.pdf").relative_to(store.root)), "sha256": hashlib.sha256((out / "resume.pdf").read_bytes()).hexdigest(),
            "pages": pages, "diff": diff, "warning": warning, "dropped": dropped, "skills_block": skills_block.strip(),
            "tex_path": str((out / "resume.tex").relative_to(store.root)) if tex else "",
            "categories": cats}


LETTER_TEMPLATE = r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{parskip}
\pagestyle{empty}
\begin{document}
{\large\bfseries %(name)s}\par
%(contact)s\par
\vspace{1.2em}
%(date)s\par
\vspace{1.2em}
%(body)s
\end{document}
"""


async def build_letter(store, jid, text, identity, date_text):
    """The cover letter as a PDF (minimal LaTeX, compiled like the resume). Returns None if LaTeX is unavailable."""
    if not shutil.which("tectonic") and not shutil.which("pdflatex"):
        return None
    out = store.root / "artifacts" / jid / ("letter-" + hashlib.sha256(text.encode()).hexdigest()[:12])
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = latex_escape(" ".join(x for x in (identity.get("first_name", ""), identity.get("last_name", "")) if x))
    contact = " \\textbar\\ ".join(latex_escape(x) for x in (identity.get("email", ""), identity.get("phone", ""), identity.get("city", "")) if x)
    paragraphs = [latex_escape(p.strip()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    body = "\n\n".join(p.replace("\n", "\\\\\n") for p in paragraphs)
    (out / "letter.tex").write_text(LETTER_TEMPLATE % {"name": name, "contact": contact, "date": latex_escape(date_text), "body": body})
    try:
        pdf, _ = await compile_tex(out, "letter.tex")
    except ValueError:
        return None
    return {"path": str(pdf.relative_to(store.root)), "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
