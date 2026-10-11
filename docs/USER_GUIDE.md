# JobApplier user guide

Start it from any terminal with `jobapplier` (opens the dashboard; `jobapplier stop` stops it).
JobApplier runs on your Mac at http://127.0.0.1:8765. Everything it knows lives in `data/` on this computer;
passwords and the tracker link live in your Mac's Keychain.

## Stop button

While anything is running (a preparation, advice, a document being read, a networking run, the email check), a red
**Stop** button appears in the top bar. It cancels everything; the job is marked paused so you can resume later.
The only thing it does not interrupt is an application whose Submit button has already been clicked, so the
outcome gets recorded.

## Two modes

The switch at the top of the sidebar picks the mode. Both share the same jobs, profile, companies and sheet.

- **AI Applier**: give it job links. When adding links you choose how far it goes:
  - **Advisor**: reads the posting, researches the company, tailors your skills section (as Overleaf text and a
    one-page PDF), drafts a cover letter if one is asked for, and lists the questions the form will probably ask
    with answers from your profile. You apply yourself. Use **Get advice** in a job's drawer.
  - **AI Applier**: everything above, then it opens its own Chrome window, signs in (Google first, then a login
    you saved for that company), fills the form, and stops at **Review**. You approve; it submits.
  - **Autonomous**: same pipeline, no review step. It only ever changes the skills section. Anything it cannot
    settle on its own (an unanswered question, a fit flag, a resume that won't fit one page, a sign-in it can't
    complete) parks the job as *Needs attention* instead of submitting. Settings: a daily cap (default 10) and a
    wait before each submission (default 5 minutes) during which **Pause** cancels it.
- **Tracker**: for jobs you apply to yourself. **Log application** records the link, date, how you signed in,
  and referral details; the posting is read in the background to fill in the title, company and deadline.

## Questions

The Dashboard's **Questions** panel collects everything the assistant needs from you:

- **Company questions** (asked once per company): how you sign in there, and the password to save if you chose
  email and password. Answers go to the company record.
- **Application questions**: when a form asks something your profile cannot answer (relocation, start date,
  in-person availability...), the preparation pauses and the question appears here, with a suggested answer when
  one can be inferred. Your answer is saved under **Answers** in your profile, so every later application reuses
  it. Then click **Resume preparation** in the job's drawer.

## Resume tailoring rules

- Only the skills section changes. It is rebuilt in your resume's own category style, with everything relevant
  from your profile, and trimmed item by item until the PDF fits on one page (dropped items are listed).
- Experience wording suggestions are shown side by side. In AI Applier mode you can tick ones to apply; they are
  applied to that application's copy only. Your master resume source is never modified.

## Match score

Every tailored resume (Advisor, AI Applier, Autonomous) gets a **Match** tab in the job drawer with two parts:

- **HackerRank rubric**: the scoring prompt and rules from HackerRank's open-source resume scorer
  ([interviewstreet/hiring-agent](https://github.com/interviewstreet/hiring-agent), MIT) run once on the tailored
  PDF text, with public GitHub signals from the GitHub link in your profile when enabled. Categories: open source
  (35), self projects (30), production experience (25), technical skills (10), AI fluency (10), plus capped
  bonuses and deductions; the caps and bonus rules are enforced in code, not by the model. It is one company's
  intern rubric and the same resume can move about ten points between runs, so read the evidence, not just the
  number. The score is kept until the resume PDF changes.
- **Requirements mentioned**: which of the posting's extracted requirements the resume text actually contains,
  by plain keyword matching (repeatable, no AI). Missing terms are listed per requirement.

Settings → Applying turns the score and the GitHub lookup on or off. The score never blocks anything.

## Email monitor

The mail icon in the top bar reads recent replies from employers in the Gmail account that owns your tracker
sheet, through the same script (read-only: nothing is sent, moved or marked). Replies are classified as
confirmation, rejection, interview, assessment, offer, or action required, attached to the matching
application, and shown in the Dashboard's **Action required** panel with a link to the email. Rejections and
offers update the status. Settings → Email monitor turns on automatic checks (every 15/30/60 minutes while the
app runs) and optional texts to your own phone through your carrier's email-to-text address, with quiet hours.

## Networking

The **Networking** page builds a list of people worth talking to at the companies you apply to, records
evidence about each one, and drafts a first message you review and send yourself. It never sends anything,
never connects or follows anyone, and never schedules follow-ups.

1. **Setup**: your target role, a one-line introduction (the only thing about you that goes into a message),
   how many people to research per run (default 5), and whether LinkedIn may be read.
2. **Sources**: upload your LinkedIn **Connections.csv** (Settings & Privacy → Data privacy → Get a copy of your
   data → Connections; a whole archive is accepted, but only that one file is read and kept). Connections at
   your target companies join the list. Paste profile links of people you found yourself, or run one public web
   search per company (search engines often block automated lookups; an empty result just means nothing
   usable came back).
3. **Research** reads sources for people already on the list, at most 5 per run and 30 LinkedIn pages a day,
   with pauses: the profile and its recent activity in the app's own Chrome (after a one-time login via **Open
   LinkedIn login**), or public search snippets when LinkedIn reading is off. It records the current role,
   a recent trigger (move, promotion, open role) with its date, up to five samples of the person's own
   writing, a tentative writing style (Driver, Influencer, Steady, Analyst, or insufficient evidence), and one
   "hook" they wrote. Every claim keeps the URL it came from; anything the model states without a source among
   the pages read is dropped. A mutual connection stays *unverified* until you confirm it by name.
4. **Write drafts** for people whose research is *Ready*: 35-65 words, one easy question about something they
   actually wrote, no pitch, flattery or referral request. Each draft is checked in code (length, exactly one
   question, no banned asks, a source from the evidence, no unverified mutual) and lands as *Drafted* or
   *Draft needs review* with the reason. People without a usable source get no draft, only a note.
5. Open a person to see the evidence beside the draft, edit the text, **Copy** it, open the profile, and mark
   it **Reviewed** (you sent it) or **Close** it.

Every run freezes a versioned copy under `data/networking/versions/` (list CSV, evidence JSON, drafts
Markdown); the current `networking-list.csv` and `drafts.md` are also there, and the CSV can be downloaded
from the page. The list keeps a stable ID per person (`person-001`, ...), deduplicated by profile link.

## Profile

One document, in plain Markdown, with fixed sections (Identity, Education, Experience, Projects, Skills,
Publications, Awards & Certifications, Coursework & Training, Links, Answers, Notes). Every AI task reads it.

- **Upload** a document (resume PDF, LaTeX source, LinkedIn export, transcript, project pages), then click
  **Add to profile**. The assistant proposes what is new; untick anything wrong and click **Append**. Nothing is
  written until you click.
- **Edit** the text directly to correct or remove anything; **Save profile**. Every save is kept under
  History, so you can look at or restore an older version.
- **Identity**, **Links** and **Answers** use `- Key: value` lines; those are what the applier types into
  forms. Add reusable answers (sponsorship, relocation, availability) under Answers.
- **Verified**: tick it once you have checked the document. Applications are only prepared from a verified
  profile.

## Companies

One record per employer, built automatically from your jobs: portal (Greenhouse, Workday, ...), how you sign
in, whether you have an account, your notes, and the questions the assistant asked about that company.
Answers are remembered, so each question is asked once per company, not once per job.

**Saved login** (optional): an email and password the assistant may type on that company's own sign-in page.
It is stored in the Keychain and never sent to the AI. Verification codes and CAPTCHAs are always handed to you.

## Tracker sheet

Settings → Tracker sheet connects a Google Sheet through a small script you paste into it (no Google Cloud
setup). Every change syncs to the sheet. The sheet is also read: a row marked **Pending referral** holds that
application, and a status like **Applied** or **Interview** prevents a duplicate submission. Your notes,
referral details and formulas in the sheet are never overwritten.

## What the app never does without you

- Submit an application (in AI Applier mode; Autonomous mode is a separate, later feature).
- Change the experience section of your resume.
- Invent facts, eligibility or demographic answers; unknown answers are handed to you.
- Type passwords anywhere except the matching company's sign-in page, or enter verification codes/CAPTCHAs.
