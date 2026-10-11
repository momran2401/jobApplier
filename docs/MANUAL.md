# JobApplier manual

JobApplier is a job search assistant that runs on your Mac. It keeps one profile about you, reads job postings,
tailors your resume, fills application forms in its own Chrome window, tracks everything in a Google Sheet,
watches your inbox for replies, builds a list of people worth talking to, and keeps all of that the same across
your Macs. It never submits, sends or posts anything without you, except in Autonomous mode, which you turn on
per job.

This manual goes screen by screen. If you only read one thing, read "The rules it follows" at the end.

## Contents

1. Starting and stopping
2. The two modes
3. Dashboard
4. Profile
5. Jobs: Advisor, AI Applier, Autonomous
6. Match score
7. Companies
8. Networking
9. Tracker sheet and email monitor
10. New postings
11. Sync between your Macs
12. Settings
13. Where your data lives
14. The rules it follows
15. When something goes wrong

## 1. Starting and stopping

Open a terminal and type `jobapplier`. The first run installs what it needs (a local Node runtime, a Python
environment, Chromium for automation) and builds the dashboard; after that it starts in a second. It opens
http://127.0.0.1:8765 in your browser. `jobapplier stop` shuts it down. Control-C in the terminal does the same.

The server only listens on this Mac. Nobody on your network can reach it.

Things it expects on the Mac: Python 3.11 or newer, Google Chrome (the applier drives real Chrome, not the
bundled Chromium), Tectonic for LaTeX (`brew install tectonic`), and the Codex CLI if you use ChatGPT as the
model (`brew install codex`, then `codex login`). The launcher tells you which of these is missing; the app
still starts without them, with that part disabled.

## 2. The two modes

The pill at the top switches between **AI Applier** and **Tracker**. Same data, different front page.

- **AI Applier**: you add job links, it does the work, you review.
- **Tracker**: for jobs you apply to yourself. You log them, it keeps the sheet and inbox in sync, and the
  front page shows what needs action.

## 3. Dashboard

Three columns at the top:

- **To review** (AI Applier) or **Action required** (Tracker): applications waiting on you.
- **To answer**: questions the assistant could not answer from your profile. Each answer is saved to your
  profile, so you answer once.
- **In progress**: what is running right now, with a step bar and a Stop button.

Below that, **All applications** with filters and search. Click a row to open its drawer. The **Log** on the
right is a plain record of everything the app did.

The red **Stop** button in the top bar cancels every running operation. The one thing it will not interrupt is
an application whose Submit button has already been clicked, so the outcome gets recorded.

## 4. Profile

One Markdown document about you, with fixed sections: Identity, Education, Experience, Projects, Skills,
Publications, Awards and Certifications, Coursework and Training, Links, Answers, Notes. Every AI task reads it.
Nothing goes into a form or a letter that is not in here.

Three ways to fill it:

- **Upload** a document (resume PDF, LaTeX source, LinkedIn export, transcript, portfolio page), then click
  **Add to profile**. The assistant proposes new lines; untick anything wrong; click **Append**. It only ever
  adds. It never deletes or rewrites what is there.
- **Edit** the text directly. Every save is kept under **History**, so you can look at or restore an older
  version.
- **Answers**: when a form asks something the profile cannot answer, the question lands on the Dashboard; your
  answer is saved here and reused.

**Identity**, **Links** and **Answers** use `- Key: value` lines. Those are what gets typed into forms. Put
reusable answers there: sponsorship, relocation, start date, in-person availability.

Tick **Verified** once you have read the document. Nothing is prepared from an unverified profile.

The LaTeX resume: upload your `.tex` project as a zip, set the main file and the two markers around your skills
block in Settings. Only the text between the markers is ever replaced.

## 5. Jobs: Advisor, AI Applier, Autonomous

**Add job links** on the Dashboard, one per line, and pick how far the app should go for this batch:

**Advisor.** Reads the posting, researches the company, rewrites your skills section for this role (as LaTeX to
paste into Overleaf, and as a one-page PDF), drafts a cover letter if the posting mentions one, and lists the
questions the form will probably ask with answers from your profile. You apply yourself. The drawer's **Advice**
tab has all of it, with Copy buttons.

**AI Applier.** Everything Advisor does, then it opens its own Chrome window (separate from your normal Chrome),
signs in (Google button first, then a login you saved for that company), fills the form, and stops at
**Review**. You read the exact PDF, the answers and a screenshot of the finished form, click **Approve**, and
only then does it click Submit. If it hits a verification code, a CAPTCHA or a question it cannot answer, it
pauses and tells you what it needs. Finish that step in the Chrome window or answer on the Dashboard, then click
**Resume preparation**.

**Autonomous.** Same pipeline with no review stop. It only ever changes the skills section (never experience
wording), and it parks the job as *Needs attention* instead of submitting when anything is off: an unanswered
question, a fit flag, a resume that will not fit on one page, a sign-in it cannot finish, a posting that looks
closed. Two settings: a daily cap (default 10) and a wait before each submission (default 5 minutes) during
which **Pause** cancels it.

**Resume rules.** Only the skills section changes. It is rebuilt in your resume's own category style with
everything relevant from your profile, then trimmed item by item until the PDF fits on one page; dropped items
are listed. Wording suggestions for experience bullets are shown side by side. In AI Applier mode you can tick
ones to apply; they go into that application's copy only. Your master resume is never modified.

**Cover letters** are written when the posting mentions one, required or optional. Settings → "Always draft a
cover letter" overrides that.

**Fact-check.** Every generated letter, answer and wording suggestion is checked in code against your profile,
the posting and the company research. A number, percentage, dollar amount, date, email or link that appears in
none of them is blocking: the model gets one rewrite, and a letter that still fails is not attached (the job
pauses with the reason). Capitalized names that appear nowhere are listed as "check these names". No model is
involved in the check itself, so it cannot be talked around.

**Posting status.** Read job uses the page's own structured data when the site publishes it (title, company,
location, deadline). A page that returns 404 or says "no longer accepting applications" marks the job *closed*;
a past deadline marks it *expired*. Both show as a tag on the row. Autonomous mode refuses those.

**The drawer**, tab by tab: Advice (Advisor output), Overview (summary, requirements, fit flags, referral,
account details), Materials (the exact PDF, selected skills, what changed, cover letter), Match (next section),
Answers (every value it typed and where it came from, plus the final screenshot), Activity (the log for this
job).

**Referrals.** Set a job's referral status to *Pending* and it will not submit until you release it, however
approved it is. *Received* with a referral link makes the applier open that link instead of the posting.

## 6. Match score

Every tailored resume gets a Match tab with two parts.

**HackerRank rubric.** HackerRank open-sourced the resume scorer they used to triage intern applications
(interviewstreet/hiring-agent). The app runs its rubric, unchanged, on your tailored PDF plus public signals
from the GitHub link in your profile: open source (35 points), self projects (30), production experience (25),
technical skills (10), AI fluency (10), plus capped bonuses and deductions. The caps and bonus rules are enforced
in code, not by the model. Read it as one reviewer's opinion: it weighs open source heavily, and the same
resume can move about ten points between runs. The evidence under each bar is the useful part.

**Requirements mentioned.** Which of the posting's requirements the resume text actually contains, by plain
keyword matching. No AI, repeatable. Missing terms are listed per requirement. Eligibility lines ("must be a
U.S. person") naturally show as missing; that is fine.

The score never blocks anything. Settings → Applying turns it and the GitHub lookup off.

## 7. Companies

One record per employer, built automatically from your jobs: the portal (Greenhouse, Workday, ...), how you
sign in, whether you have an account, your notes, the questions it asked you about this company, the research
it did, the logo, and the people on your networking list there.

The first time it needs to sign in somewhere it asks one question: how do you sign in at this company? Answer
once; every later application there reuses it.

**Saved login** (optional): an email and password the assistant may type on that company's own sign-in page.
It is stored in your Mac's Keychain and never sent to the model. It is typed by code, on that company's page
only. Verification codes and CAPTCHAs are always handed to you.

## 8. Networking

The Networking page builds a list of people worth talking to at the companies you apply to, records evidence
about each one, and drafts a first message that you review and send yourself. It never sends anything, never
connects or follows anyone, and never schedules follow-ups.

**Setup.** Your target role, a one-line introduction (the only thing about you that goes into a message), how
many people to research per run (default 5), and whether LinkedIn may be read.

**Sources**, best first:

- **People you found.** Search LinkedIn yourself and paste profile links. You pick the targets; the app does
  the reading.
- **Your connections.** LinkedIn → Settings & Privacy → Data privacy → Get a copy of your data → Connections.
  Upload the `Connections.csv` (the whole archive zip works too, but only that one file is read and kept).
  Connections at your target companies join the list, marked Connected.
- **Public search.** One web search per company. Search engines often block automated lookups, so an empty
  result just means nothing usable came back.

**Research** reads sources for people already on the list, at most 5 per run and 30 LinkedIn pages a day, with
pauses between pages: the profile and recent activity in the app's own Chrome (log in there once with **Open
LinkedIn login**), or public search snippets when LinkedIn reading is off. It records the current role, a
recent trigger (a move, a promotion, an open role on their team) with its date, up to five samples of the
person's own writing, a tentative writing style (Driver, Influencer, Steady, Analyst, or "insufficient
evidence"), and one "hook" they wrote that could support a real question. Every claim keeps the URL it came
from. Anything the model states without a source among the pages it read is thrown away. A mutual connection
stays *unverified* until you confirm it by name.

**Drafts** for people whose research is *Ready*: 35 to 65 words, one easy question about something they
actually wrote, no pitch, no flattery, no referral request. Each draft is checked in code (length, exactly one
question, no banned asks, a source from the evidence, no unverified mutual, no unsupported numbers) and lands as
*Drafted* or *Draft needs review* with the reason. People without a usable source get no draft, only a note.

Open a person to see the evidence beside the draft. Edit the text, **Copy** it, open the profile, send it from
LinkedIn yourself, then mark it **Reviewed** or **Close** it.

Every run writes `networking-list.csv`, `drafts.md` and a frozen, versioned copy of the list and its evidence
under `data/networking/`. Person IDs (`person-001`, ...) are stable and never reused.

## 9. Tracker sheet and email monitor

**Sheet.** Settings → Tracker sheet connects a Google Sheet through a small script you paste into it (no Google
Cloud setup). Every change syncs to the sheet. The sheet is also read: a row marked *Pending referral* holds that
application, and a status like *Applied* or *Interview* prevents a duplicate submission. Your own notes and
formulas in the sheet are never overwritten.

**Email.** The mail icon in the top bar reads recent replies from employers in the Gmail account that owns your
sheet, through the same script, read-only. Replies are classified (confirmation, rejection, interview,
assessment, offer, action required), attached to the matching application, and shown under **Action
required** with a link to the email. Rejections and offers update the status. Settings → Email monitor turns on
automatic checks and optional texts to your phone, with quiet hours.

**Log application** (Tracker mode): for jobs you applied to yourself. Link, date, how you signed in, referral
details, optional password for the company (goes to the Keychain). "Store · apply later" keeps a job as *To
apply* without a date; **Mark as applied** later sets the date.

**How it's going** (Tracker mode): applied, replies, interviews, offers, rejections, and how referrals and
each mode are converting.

## 10. New postings

The Dashboard's "New postings at companies you track" panel checks the public job-board feeds (Greenhouse,
Lever, Ashby) of companies already on your Companies page, filtered by the keywords in Settings (default:
intern, internship, co-op, new grad, early career). No AI tokens, no scraping. **Add** turns a posting into a
job in your default mode; **Dismiss** hides it for good. Companies that run their own career site are not
covered. **Check now** runs it; it does not poll on its own.

## 11. Sync between your Macs

The problem: everything lives in `data/` on one Mac. Sync keeps that the same on every Mac you run JobApplier
on, without putting your data on GitHub or in anyone's database.

**How it works.** It uses a folder you already keep in sync, iCloud Drive by default (any Dropbox or Syncthing
folder works too). The live database never goes in that folder; putting a SQLite file in iCloud is how people
corrupt it. Instead, every minute and on startup and shutdown, each Mac:

1. Writes one encrypted snapshot of its records (`<machine-id>.json.enc`): jobs, companies, people, settings,
   networking setup and connections, your profile text with its history, and the log.
2. Writes encrypted copies of the files those records point to (`files/<sha256>.bin`): uploaded documents,
   built resumes and letters, screenshots, logos. Each file is written once.
3. Reads the other Macs' snapshots and merges them, record by record. The record with the newer edit wins.
   A person you removed stays removed (deletions travel as tombstones). If both Macs handed out the same person
   ID to different people while apart, the local one gets a new ID. If both Macs added the same job link, the
   local copy stays.

Everything in the folder is ciphertext (AES-GCM). The key comes from a passphrase you type once on each Mac; it
lives in that Mac's Keychain, never in the folder. Without the passphrase the folder is noise.

**What does not travel:** passwords, the Anthropic key, the tracker link (enter them once per Mac), Chrome
sign-ins (log in once per Mac), and per-machine usage counters. If both Macs edit the same job while offline,
the later edit wins and the earlier one is in the Log.

**Setup**, on each Mac: Settings → Sync between your Macs. Save settings, enter the same passphrase, tick the
switch. **Sync now** forces a round. The panel shows this Mac's name, the last push and pull, and the other Macs
it has seen.

## 12. Settings

- **Applying**: default mode, autonomous cap and delay, always draft a letter, match score on/off, GitHub
  lookup, new-posting keywords.
- **Email monitor**: automatic checks, phone number and carrier for texts, quiet hours.
- **Sync between your Macs**: see above.
- **AI connection**: provider and model. **Codex** uses your ChatGPT Plus sign-in (run `codex login` once).
  **Claude** uses an API key stored in the Keychain. **Ollama** runs a local model on this Mac. If the paid plan
  runs out mid-run, text tasks continue on the local fallback model and form filling pauses.
- **Advanced**: local fallback model, fast local model for reading postings, context window, LaTeX main file and
  markers.
- **Tracker sheet**: connect, test, copy the script.

## 13. Where your data lives

Everything is in `data/` next to the app, on this Mac:

- `workspace.sqlite`: jobs, companies, people, settings, events.
- `profile/profile.md` and `profile/history/`: your profile and every saved version.
- `documents/`: uploads. `artifacts/`: built PDFs, letters, screenshots, receipts.
- `networking/`: the list, drafts, and frozen versions. `logos/`: company logos.
- `browser/`: the app's Chrome profiles, one per site, with your sign-ins.
- `reports/`: reports written for you.

Passwords, API keys, the tracker link and the sync passphrase are in your Mac's Keychain under "JobApplier".

The `data/` folder is in `.gitignore`. Nothing personal is ever committed.

## 14. The rules it follows

- Nothing is submitted without your approval, except in Autonomous mode, and then only under its guardrails.
- Nothing is sent: no messages, no emails, no connection requests. Drafts are for you to send.
- It never invents facts. Unknown answers are handed to you. Generated text is fact-checked against your
  sources before it is used.
- It never changes your experience wording automatically, and never edits your master resume.
- Passwords are typed by code on the matching company's sign-in page only, and never shown to the model.
  Verification codes and CAPTCHAs are always yours.
- LinkedIn is read only, for people already on your list, a few per run, with a daily cap.
- The server listens on this Mac only. Your data stays in `data/` and in the Keychain, encrypted when it
  travels between your Macs.

## 15. When something goes wrong

- **Needs attention** on a job: open it; the red note says what happened. Usually: answer the question on the
  Dashboard, finish a sign-in in the Chrome window, or acknowledge a fit flag. Then **Resume preparation**.
- **Check outcome**: Submit was clicked and no confirmation was seen. Open the candidate portal, then click
  "It was submitted" or "It was not submitted".
- **Posting closed / expired**: read the page yourself before spending time on it.
- **Tracker offline**: Settings → Tracker sheet → Test. The script may need redeploying (Deploy → Manage
  deployments → Edit → New version).
- **LinkedIn asks for a login**: Networking → Open LinkedIn login, sign in once in the app's Chrome window, run
  research again.
- **Sync says the passphrase does not match**: it has to be the same on every Mac. Fix it on the Mac that got
  it wrong; nothing in the folder is harmed.
- **Everything is slow**: check Activity Monitor for memory pressure. The app itself is light; Chrome and the
  model are not.
- **Start over on one Mac**: stop the app, delete `data/`, start it, let sync pull everything back.

Tests: `.venv/bin/python -m pytest -q`. Lint: `.venv/bin/ruff check jobapplier tests`.
