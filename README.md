<p align="center">
  <img src="frontend/public/favicon.svg" width="72" height="72" alt="JobApplier logo">
</p>
<h1 align="center">JobApplier</h1>
<p align="center">A personal job-application assistant that runs on your Mac.</p>

## Description

JobApplier keeps one master profile about you, reads job postings you give it, tailors your resume's skills
section to each role (one page, always), drafts cover letters when a posting asks for one, fills application
forms in its own Chrome window, and submits only after you approve. It tracks every application in a Google
Sheet, watches your inbox for employer replies, and builds a networking list with evidence-backed first-message drafts you send yourself.

Everything stays local: your data in `data/`, passwords and the sheet link in your Mac's Keychain. The AI runs
through your ChatGPT Plus login (Codex), a Claude API key, or a local model with Ollama.

## Install

Requires macOS and Python 3.11+. The launcher downloads a project-local Node.js runtime and browser automation
runtime on first start, so it does not require Homebrew or a system-wide Node installation. [Tectonic](https://tectonic-typesetting.github.io)
is required for LaTeX resumes (`brew install tectonic`). Optional: [Ollama](https://ollama.com) for the local fallback model,
[Codex CLI](https://github.com/openai/codex) for the ChatGPT login (`brew install codex && codex login`).

```bash
git clone https://github.com/momran2401/jobapplier.git JobApplier && cd JobApplier
mkdir -p "$HOME/.local/bin"
ln -sfn "$PWD/bin/jobapplier" "$HOME/.local/bin/jobapplier"
```

If `jobapplier` is not found afterwards, add this line to `~/.zprofile`, open a new terminal, and run it again:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Run

```bash
jobapplier
```

Opens http://127.0.0.1:8765. `jobapplier stop` stops it. Keep the server private to this machine.

## Quick guide

1. **Profile** — upload your resume PDF, LaTeX source, LinkedIn export and transcript, click **Add to profile**
   on each, review what the assistant proposes, append. Edit the text directly to correct anything. Tick
   **Verified** when it's right.
2. **Settings** — pick the AI provider; set the LaTeX main file and skills markers; connect the tracker sheet
   (paste the provided script into a Google Sheet once).
3. **AI Applier** — click **Add job links** and choose a mode per batch:
   - **Advisor**: tailored skills (Overleaf text + PDF), cover letter, likely questions. You apply yourself.
   - **AI Applier**: fills the form and stops at Review. You approve; it submits.
   - **Autonomous**: submits without review; parks anything uncertain for you. Skills only, daily cap.
   Every tailored resume gets a **Match** report: HackerRank's open-source resume rubric plus requirement coverage.
4. **Questions** on the Dashboard — answer what the assistant can't infer (sign-in method per company,
   eligibility questions). Answers are saved to your profile and reused.
5. **Tracker** mode — **Log application** for jobs you applied to yourself; the mail icon checks your inbox
   for replies and flags what needs action.
6. **Networking** — import your LinkedIn Connections.csv or paste profile links, research people with a source
   behind every claim, and get first-message drafts (one real question, no pitch) that you review and send yourself.

The full guide is in [docs/USER_GUIDE.md](docs/USER_GUIDE.md). Tests: `.venv/bin/python -m pytest -q`.

## Disclaimer

Copyright © 2026 Mustafa Omran. All rights reserved. Personal software, provided as is, without warranty of
any kind. You are responsible for every application it submits on your behalf and for complying with the terms
of the job portals, LinkedIn, Google, OpenAI and Anthropic services you connect to it. It never invents
qualifications; check its output before you rely on it.
