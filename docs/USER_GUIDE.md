# JobApplier user guide

Start it from any terminal with `jobapplier` (opens the dashboard; `jobapplier stop` stops it).
JobApplier runs on your Mac at http://127.0.0.1:8765. Everything it knows lives in `data/` on this computer;
passwords and the tracker link live in your Mac's Keychain.

## Stop button

While anything is running (a preparation, advice, a document being read, a contact search, the email check), a red
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

## Email monitor

The mail icon in the top bar reads recent replies from employers in the Gmail account that owns your tracker
sheet, through the same script (read-only: nothing is sent, moved or marked). Replies are classified as
confirmation, rejection, interview, assessment, offer, or action required, attached to the matching
application, and shown in the Dashboard's **Action required** panel with a link to the email. Rejections and
offers update the status. Settings → Email monitor turns on automatic checks (every 15/30/60 minutes while the
app runs) and optional texts to your own phone through your carrier's email-to-text address, with quiet hours.

## Find contacts (Tracker mode)

**Find contacts** searches the web and reads LinkedIn people-search results in the app's own Chrome window
(log in to LinkedIn there once) for the kind of person you describe, at the companies you select. It only
reads: it never connects, follows or messages. Each contact gets a short draft note in your voice to copy.
Limits: 2 result pages per job and 40 profiles a day, with pauses between pages. Contacts live on the
company record and in the job's **Contacts** tab, with a status you keep up to date.

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
