/**
 * JobApplier tracker — paste into Extensions → Apps Script of a blank Google Sheet.
 *
 * The local JobApplier app talks to this script through its web-app link. The app decides which rows
 * to change; this script only reads the sheet, writes the cells it is told to, and refuses to write if
 * a row no longer matches what the app saw (for example after you sorted the sheet). Formula cells are
 * never overwritten.
 */
const SHEET_NAME = 'Applications';
const COLUMNS = [
  'Application ID', 'Company', 'Job title', 'Status', 'Date added', 'Submitted at', 'Last update', 'Deadline',
  'Location', 'Job URL', 'Application URL', 'Candidate portal', 'Application status URL', 'Requisition ID',
  'Sign-in method', 'Account email', 'Account created', 'Referral status', 'Referral contact', 'Referral link',
  'Referral review notes', 'Application notes', 'Skills used', 'Resume version', 'Confirmation',
  'Company LinkedIn URL', 'Contact profile URL', 'Outreach status', 'Last contact', 'Follow-up date',
  'Mode', 'Action required', 'Last email',
];
const STATUSES = ['Queued', 'Preparing', 'Needs attention', 'Advised', 'Ready to review', 'Approved', 'Submitting', 'Submitted',
  'Applied', 'Check outcome', 'Interview', 'Offer', 'Rejected', 'Withdrawn'];
const REFERRALS = ['Not sought', 'Pending referral', 'Referral received', 'Proceed without referral'];

function onOpen() {
  SpreadsheetApp.getUi().createMenu('JobApplier')
    .addItem('Set up tracker', 'setup')
    .addItem('Show connection link', 'showLink')
    .addToUi();
}

/** Creates the Applications tab with headers, formatting, and dropdowns. Safe to run again. */
function setup() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME, 0);
  ensureHeaders_(sheet, COLUMNS);
  const width = sheet.getLastColumn();
  sheet.setFrozenRows(1);
  sheet.setFrozenColumns(3);
  sheet.getRange(1, 1, 1, width).setFontWeight('bold').setBackground('#1f2329').setFontColor('#ffffff').setWrap(true);
  sheet.setRowHeight(1, 36);
  sheet.setColumnWidths(1, width, 150);
  sheet.setColumnWidth(1, 90);
  sheet.setColumnWidths(2, 2, 220);
  sheet.hideColumns(1); // Application ID is for matching; it stays in the sheet but out of the way.
  const headers = headers_(sheet);
  const rows = Math.max(sheet.getMaxRows() - 1, 1);
  dropdown_(sheet, headers.indexOf('Status') + 1, rows, STATUSES);
  dropdown_(sheet, headers.indexOf('Referral status') + 1, rows, REFERRALS);
  const blank = ss.getSheetByName('Sheet1');
  if (blank && blank.getLastRow() === 0 && ss.getSheets().length > 1) ss.deleteSheet(blank);
  token_();
  SpreadsheetApp.getUi().alert('Tracker is set up.\n\nNext: Deploy → New deployment → Web app\n' +
    '  • Execute as: Me\n  • Who has access: Anyone\nThen choose JobApplier → Show connection link.');
}

function showLink() {
  const url = ScriptApp.getService().getUrl();
  const ui = SpreadsheetApp.getUi();
  if (!url) {
    ui.alert('Deploy the script first: Deploy → New deployment → Web app (Execute as: Me, Who has access: Anyone).');
    return;
  }
  const link = url + '#' + token_();
  const html = HtmlService.createHtmlOutput(
    '<p style="font:13px sans-serif">Paste this into JobApplier → Tracker. Treat it like a password.</p>' +
    '<textarea style="width:100%;height:90px;font:12px monospace" onclick="this.select()" readonly>' + link + '</textarea>')
    .setWidth(520).setHeight(170);
  ui.showModalDialog(html, 'JobApplier connection link');
}

function doGet(e) {
  return respond_(() => {
    authorize_(e.parameter.token);
    const sheet = sheet_();
    return {sheet: sheet.getParent().getName(), rows: read_(sheet)};
  });
}

function doPost(e) {
  return respond_(() => {
    const body = JSON.parse(e.postData.contents);
    authorize_(body.token);
    if (body.action === 'mail') return mail_(body);
    if (body.action === 'text') return text_(body);
    const lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      return write_(sheet_(), body);
    } finally {
      lock.releaseLock();
    }
  });
}

// ---------- email monitor (read-only Gmail search; nothing is sent, moved or marked) ----------

const ATS_SENDERS = ['greenhouse.io', 'greenhouse-mail.io', 'lever.co', 'hire.lever.co', 'myworkday.com', 'myworkdayjobs.com', 'icims.com',
  'ashbyhq.com', 'smartrecruiters.com', 'taleo.net', 'workable.com', 'jobvite.com', 'bamboohr.com', 'successfactors.com',
  'applytojob.com', 'hirevue.com', 'codesignal.com', 'hackerrank.com', 'calendly.com', 'goodtime.io', 'modernhire.com'];

/** Recent messages from employers: {domains: [...], days: N, after: 'unix seconds'} -> {messages: [...]}. */
function mail_(body) {
  const domains = (body.domains || []).concat(ATS_SENDERS).filter(Boolean);
  const days = Math.min(Math.max(Number(body.days) || 7, 1), 60);
  const from = domains.map(d => 'from:' + d).join(' OR ');
  const query = '(' + from + ') newer_than:' + days + 'd -category:promotions';
  const threads = GmailApp.search(query, 0, 60);
  const after = Number(body.after) || 0;
  const out = [];
  threads.forEach(thread => {
    thread.getMessages().forEach(m => {
      const at = m.getDate().getTime() / 1000;
      if (at <= after) return;
      out.push({id: m.getId(), thread_id: thread.getId(), at: m.getDate().toISOString(), from: m.getFrom(),
        subject: m.getSubject(), snippet: m.getPlainBody().replace(/\s+/g, ' ').slice(0, 700),
        link: 'https://mail.google.com/mail/u/0/#all/' + thread.getId()});
    });
  });
  out.sort((a, b) => a.at < b.at ? -1 : 1);
  return {messages: out.slice(-80), query: query};
}

/** Sends a short text to the owner's own phone through the carrier's email-to-SMS address. Only to `to` given by the app. */
function text_(body) {
  const to = String(body.to || '');
  if (!/^\d{10}@(tmomail\.net|vtext\.com|txt\.att\.net|messaging\.sprintpcs\.com|msg\.fi\.google\.com)$/.test(to)) {
    throw new Error('Text address must be a 10-digit number at a known carrier gateway.');
  }
  MailApp.sendEmail({to: to, subject: '', body: String(body.message || '').slice(0, 300)});
  return {sent: true};
}

// ---------- internals ----------

function token_() {
  const props = PropertiesService.getScriptProperties();
  let token = props.getProperty('TOKEN');
  if (!token) {
    token = (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, '');
    props.setProperty('TOKEN', token);
  }
  return token;
}

function authorize_(given) {
  if (!given || given !== token_()) throw new Error('Invalid connection link. Copy it again from JobApplier → Show connection link.');
}

function respond_(fn) {
  let out;
  try {
    out = fn();
  } catch (err) {
    out = {error: String(err && err.message || err)};
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(ContentService.MimeType.JSON);
}

function sheet_() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) throw new Error('No "' + SHEET_NAME + '" tab. Choose JobApplier → Set up tracker in the sheet.');
  return sheet;
}

function headers_(sheet) {
  const width = sheet.getLastColumn();
  return width ? sheet.getRange(1, 1, 1, width).getDisplayValues()[0].map(String) : [];
}

function ensureHeaders_(sheet, labels) {
  const existing = headers_(sheet);
  const missing = labels.filter(label => existing.indexOf(label) < 0);
  if (missing.length) sheet.getRange(1, existing.length + 1, 1, missing.length).setValues([missing]);
  return headers_(sheet);
}

function dropdown_(sheet, column, rows, values) {
  if (column < 1) return;
  const rule = SpreadsheetApp.newDataValidation().requireValueInList(values, true).setAllowInvalid(true).build();
  sheet.getRange(2, column, rows, 1).setDataValidation(rule);
}

/** Each row as {header: raw}, where raw is the formula if the cell has one, otherwise the displayed text. */
function read_(sheet) {
  const last = sheet.getLastRow();
  const headers = headers_(sheet);
  if (last < 2 || !headers.length) return [];
  const range = sheet.getRange(2, 1, last - 1, headers.length);
  const shown = range.getDisplayValues(), formulas = range.getFormulas();
  return shown.map((row, r) => {
    const item = {_row: r + 2};
    headers.forEach((h, c) => { if (h) item[h] = formulas[r][c] || row[c]; });
    return item;
  });
}

function raw_(sheet, row, column) {
  const cell = sheet.getRange(row, column);
  return cell.getFormula() || cell.getDisplayValue();
}

function write_(sheet, body) {
  const headers = ensureHeaders_(sheet, body.headers || []);
  const col = label => headers.indexOf(label) + 1;
  let updated = 0, appended = 0;
  (body.updates || []).forEach(u => {
    // Refuse to write if the row moved or changed since the app read it.
    Object.keys(u.expect || {}).forEach(label => {
      if (col(label) < 1 || raw_(sheet, u.row, col(label)) !== u.expect[label]) {
        throw new Error('The sheet changed while syncing (row ' + u.row + '). Sync again.');
      }
    });
    Object.keys(u.values).forEach(label => {
      const c = col(label);
      if (c < 1) return;
      const cell = sheet.getRange(u.row, c);
      if (cell.getFormula()) return; // never replace a formula
      if (cell.getDisplayValue() !== String(u.values[label])) cell.setValue(u.values[label]);
    });
    updated++;
  });
  (body.appends || []).forEach(a => {
    const line = headers.map(h => (h in a.values ? a.values[h] : ''));
    sheet.appendRow(line);
    appended++;
  });
  return {updated: updated, appended: appended};
}
