import {useCallback, useEffect, useRef, useState} from 'react';
import {ArrowUpRight, Check, CircleAlert, ClipboardCheck, Link2, LoaderCircle, Mail, Plus, RefreshCw, Search, ShieldCheck, Square, Users, X} from 'lucide-react';
import {ago, api, date, hasToken, labels, Obj, setToken} from './api';
import {Button, Logo} from './ui';
import {Profile} from './screens/Profile';
import {Companies} from './screens/Companies';
import {Settings} from './screens/Settings';
import {JobDetail} from './screens/JobDetail';
import {LogJob} from './screens/LogJob';
import {FindContacts} from './screens/FindContacts';

const NAV = [['applications', 'Dashboard'], ['profile', 'Profile'], ['companies', 'Companies'], ['settings', 'Settings']] as const;
const readMode = (): 'applier' | 'tracker' => {try {return localStorage.getItem('mode') === 'tracker' ? 'tracker' : 'applier';} catch {return 'applier';}};
// Time-of-day greeting for the dashboard heading; the name comes from the Identity section of profile.md.
const greeting = () => {const h = new Date().getHours(); return h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : h < 22 ? 'Good evening' : 'Working late';};
const dateLong = () => new Date().toLocaleDateString(undefined, {weekday: 'long', month: 'long', day: 'numeric'});
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
const pad = (n: number) => n.toString().padStart(2, '0');

export default function App() {
  const lastState = useRef('');
  const [state, setState] = useState<Obj | null>(null), [screen, setScreen] = useState('applications'), [filter, setFilter] = useState('all');
  const [mode, setModeState] = useState<'applier' | 'tracker'>(readMode);
  const setMode = (m: 'applier' | 'tracker') => {setModeState(m); try {localStorage.setItem('mode', m);} catch {}};
  const [query, setQuery] = useState(''), [selected, setSelected] = useState<string[]>([]), [detail, setDetail] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false), [logOpen, setLogOpen] = useState(false), [importMode, setImportMode] = useState(''), [contactsOpen, setContactsOpen] = useState(false), [links, setLinks] = useState(''), [busy, setBusy] = useState(''), [notice, setNotice] = useState(''), [error, setError] = useState('');
  // The dashboard polls every few seconds; only re-render when something actually changed.
  const refresh = useCallback(async () => {
    const next = await api('/state'), text = JSON.stringify(next);
    if (text !== lastState.current) {lastState.current = text; setState(next);}
  }, []);
  useEffect(() => {api('/bootstrap').then(b => {setToken(b.token); return refresh();}).catch(e => setError(e.message));}, [refresh]);
  // Poll every second while a job is running so its progress stays live; otherwise every few seconds.
  const running = !!state && (Object.keys(state.progress || {}).length > 0 || state.monitor?.running || state.batch_running);
  useEffect(() => {
    const id = setInterval(() => {if (hasToken()) refresh().catch(() => {});}, running ? 1000 : 3500);
    return () => clearInterval(id);
  }, [refresh, running]);
  async function run(name: string, action: () => Promise<any>, success?: string) {
    setBusy(name); setError('');
    try {const result = await action(); await refresh(); if (success) setNotice(success); return result;}
    catch (e: any) {setError(e.message);}
    finally {setBusy('');}
  }
  useEffect(() => {if (notice) {const id = setTimeout(() => setNotice(''), 6500); return () => clearTimeout(id);}}, [notice]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {if (e.key === 'Escape') {setImportOpen(false); setLogOpen(false); setContactsOpen(false); setDetail(null);}};
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, []);

  if (!state) return <div className="boot"><div className="boot-core"><img src="/favicon.svg" alt="" className="boot-mark"/><h2>Loading</h2>
    {error ? <p className="error-text">{error}</p> : <LoaderCircle className="spin"/>}</div></div>;

  const jobs: Obj[] = state.jobs;
  const tracker = mode === 'tracker';
  const s = state.settings, caps = state.capabilities, last = state.last_inference;
  const reviewJobs = jobs.filter(j => ['ready', 'approved'].includes(j.status));
  const actionJobs = jobs.filter(j => j.action_required);
  const held = jobs.filter(j => j.referral_status === 'pending' && !['submitted', 'applied'].includes(j.status));
  const questions = [
    ...(state.companies || []).flatMap((c: Obj) => c.questions.filter((q: Obj) => !q.answer).map((q: Obj) => ({...q, company: c}))),
    ...jobs.filter(j => j.status === 'needs_attention' && j.package?.unresolved?.length).map(j => ({id: 'job-' + j.id, question: j.package.unresolved[0],
      suggested: j.package.suggested_answer || '', job: j, company: {name: j.company, id: j.company_id}})),
  ];
  const progress: [string, Obj][] = Object.entries(state.progress || {});
  const runningNames = progress.map(([key, p]) => {const j = jobs.find(x => x.id === key); return j ? j.company : p.operation === 'ingest' ? 'Profile' : p.operation === 'contacts' ? 'Contact search' : key;});
  const filtered = jobs.filter(j => `${j.company} ${j.title}`.toLowerCase().includes(query.toLowerCase()) && (filter === 'all'
    || filter === 'review' && ['ready', 'approved', 'needs_attention'].includes(j.status)
    || filter === 'pending' && j.referral_status === 'pending'
    || filter === 'submitted' && ['submitted', 'applied'].includes(j.status)
    || filter === 'action' && j.action_required));
  const active = jobs.find(j => j.id === detail);
  const jobName = (id: string) => {const j = jobs.find(x => x.id === id); return j ? j.company || j.title : '';};
  const companyOf = (j: Obj) => (state.companies || []).find((c: Obj) => c.id === j.company_id);
  const submitOne = async (job: Obj) => {
    let j = job;
    if (j.status !== 'approved') j = await api(`/jobs/${j.id}/approve`, 'POST', {revision: j.revision});
    await api('/submit', 'POST', {ids: [j.id]});
  };
  const approveSelected = async () => {
    const approved = [];
    for (const id of selected) {
      let job = (await api('/state')).jobs.find((j: Obj) => j.id === id);
      if (job.status !== 'approved') job = await api(`/jobs/${id}/approve`, 'POST', {revision: job.revision});
      approved.push(job.id);
    }
    await api('/submit', 'POST', {ids: approved});
    setSelected([]);
  };
  const aiOk = s.provider === 'ollama' || (s.provider === 'codex' ? caps.codex : caps.anthropic_key);
  const aiLabel = s.provider === 'codex' ? (caps.codex ? 'ChatGPT · Codex' : 'Codex not installed') : s.provider === 'anthropic' ? (caps.anthropic_key ? s.model : 'API key needed') : s.model || 'Ollama';
  const firstName = state.profile.identity?.first_name;
  const summary = tracker
    ? <>You have <Em n={actionJobs.length} tone="amber"/> needing action, <Em n={questions.length} tone="amber"/> to answer, and <Em n={held.length} tone="amber"/> waiting on a referral.</>
    : <>You have <Em n={reviewJobs.length} tone="blue"/> ready to review, <Em n={questions.length} tone="amber"/> to answer, and <Em n={held.length} tone="amber"/> waiting on a referral.</>;
  const runningNote = runningNames.length ? ` ${runningNames[0]}${runningNames.length > 1 ? ` and ${runningNames.length - 1} more` : ''} ${runningNames.length > 1 ? 'are' : 'is'} being worked on right now.` : '';

  return <div className="app">
    <header className="topnav">
      <a className="brand" href="/" aria-label="JobApplier home"><img className="brand-mark" src="/favicon.svg" alt=""/>JobApplier</a>
      <nav>{NAV.map(([id, title]) => <button key={id} className={screen === id ? 'on' : ''} onClick={() => setScreen(id)} aria-current={screen === id ? 'page' : undefined}>{title}
        {id === 'companies' && questions.length > 0 && <em>{questions.length}</em>}{id === 'profile' && !state.profile.verified && <i className="dot" title="Profile not verified"/>}</button>)}</nav>
      <div className="mode-pill" role="tablist" aria-label="Mode">
        <button role="tab" aria-selected={!tracker} className={!tracker ? 'on' : ''} onClick={() => setMode('applier')}>AI Applier</button>
        <button role="tab" aria-selected={tracker} className={tracker ? 'on' : ''} onClick={() => setMode('tracker')}>Tracker</button>
      </div>
      <div className="iconbar">
        {running && <button className="pill stop" title="Stop every running operation" onClick={() => run('stop', async () => {const r = await api('/stop', 'POST'); setNotice(r.stopped ? `Stopped ${r.stopped} operation${r.stopped > 1 ? 's' : ''}.` : 'Nothing was running.');})}><Square size={12}/>Stop</button>}
        <button className="round" title={state.monitor?.last_run ? `Check email for employer replies (last: ${ago(state.monitor.last_run)})` : 'Check email for employer replies'} aria-label="Check email" disabled={!!busy || state.monitor?.running}
          onClick={() => run('monitor', async () => {const r = await api('/monitor/run', 'POST', {}); setNotice(`Email checked: ${plural(r.checked, 'new message')}, ${plural(r.updated, 'application')} updated.`);})}>
          <Mail size={15} className={busy === 'monitor' || state.monitor?.running ? 'spin' : ''}/></button>
        <button className="round" title="Refresh" aria-label="Refresh workspace" onClick={() => run('refresh', refresh)}><RefreshCw size={15} className={busy === 'refresh' ? 'spin' : ''}/></button>
      </div>
      {tracker && <button className="pill" disabled={!jobs.length} onClick={() => setContactsOpen(true)}><Users size={14}/>Find contacts</button>}
      {tracker ? <button className="cream" onClick={() => setLogOpen(true)}><ClipboardCheck size={15}/>Log application</button>
        : <button className="cream" onClick={() => setImportOpen(true)}><Plus size={15}/>Add job links</button>}
    </header>

    <main className="main">
      {error && <div className="alert" role="alert"><CircleAlert size={18}/><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><X size={15}/></button></div>}

      {screen === 'applications' && <div className="dash">
        <div className="masthead">
          <div className="kicker">{dateLong()} · {tracker ? 'Tracker' : 'AI Applier'}</div>
          <h1>{greeting()}{firstName ? <>, <em>{firstName}</em></> : ''}.</h1>
          <p className="lede">{summary}{runningNote}</p>
        </div>

        <div className="columns">
          <section className="col">
            <div className="col-head">{tracker ? 'ACTION REQUIRED' : 'TO REVIEW'} <span>{pad(tracker ? actionJobs.length : reviewJobs.length)}</span></div>
            {(tracker ? actionJobs : reviewJobs).length === 0 && <p className="col-empty">{tracker ? 'Nothing from employers needs you right now.' : 'Nothing is waiting for your review.'}</p>}
            {(tracker ? actionJobs : reviewJobs).slice(0, 4).map(j => <div className="col-row" key={j.id}>
              <Logo company={companyOf(j)} name={j.company} size={36}/><div className="col-main"><div className="serif">{j.company}</div><div className="sub">{j.title}{tracker ? <> · <span className="amber">{j.action_required.message}</span></> : j.referral_status === 'pending' ? <> · <span className="amber">referral hold</span></> : <> · {labels[j.status]}</>}</div></div>
              {tracker ? <button className="pill" onClick={() => j.action_required.link ? window.open(j.action_required.link, '_blank') : setDetail(j.id)}>{j.action_required.link ? 'Open email' : 'Open'}</button>
                : j.status === 'approved' && j.referral_status !== 'pending' ? <button className="cream small" disabled={!!busy} onClick={() => run('submit', () => submitOne(j), 'Submitting.')}>Submit</button>
                : <button className="pill" onClick={() => setDetail(j.id)}>Review</button>}
            </div>)}
          </section>

          <section className="col">
            <div className="col-head">TO ANSWER <span>{pad(questions.length)}</span></div>
            {questions.length === 0 ? <p className="col-empty">No open questions.</p> : <>
              <QuestionCard q={questions[0]} run={run} busy={busy}/>
              {questions.length > 1 && <button className="text-link" onClick={() => setScreen('companies')}>{questions.length - 1} more on the Companies page →</button>}
            </>}
          </section>

          <section className="col">
            <div className="col-head">IN PROGRESS {progress.length ? <span className="live">● LIVE</span> : <span>{pad(0)}</span>}</div>
            {progress.length === 0 && <p className="col-empty">Nothing running.{state.events[0] ? ` Last: ${state.events[0].message}` : ''}</p>}
            {progress.slice(0, 2).map(([key, p], i) => {const j = jobs.find(x => x.id === key); return <div className="col-row stack" key={key}>
              <div className="serif">{runningNames[i]}</div>
              <div className="sub">{j ? j.title : p.detail}</div>
              <Stepper p={p}/>
              <div className="mono-row"><span>{p.stage}{p.step ? ` · step ${p.step}` : ''}{p.model ? ` · ${p.model.phase}` : ''}</span><Elapsed since={p.started}/></div>
              <button className="pill danger" disabled={!!busy} onClick={() => run('stop', async () => {const r = await api('/stop', 'POST'); setNotice(r.stopped ? 'Stopped.' : 'Nothing was running.');})}>Stop</button>
            </div>;})}
          </section>
        </div>

        <div className="board-grid">
          <section className="board">
            <div className="rule-head">
              <h2>All applications</h2>
              <div className="filters">{(tracker ? [['all', 'All'], ['submitted', 'Applied'], ['pending', 'Referrals'], ['action', 'Action required']] : [['all', 'All'], ['review', 'To review'], ['pending', 'Referrals'], ['submitted', 'Submitted']]).map(([v, t]) =>
                <button key={v} className={filter === v ? 'on' : ''} onClick={() => setFilter(v)}>{t}{v === 'all' ? ` ${jobs.length}` : ''}</button>)}</div>
              <label className="search"><Search size={14}/><input placeholder="Search" aria-label="Search applications" value={query} onChange={e => setQuery(e.target.value)}/></label>
            </div>
            {selected.length > 0 && <div className="selection"><span>{selected.length} selected</span>
              <button className="cream small" disabled={!!busy} onClick={() => run('submit', approveSelected, 'Approved applications have entered the submission queue.')}>Approve & submit</button>
              <button className="text-link" onClick={() => setSelected([])}>Clear</button></div>}
            {filtered.length === 0 ? <p className="col-empty">{jobs.length ? 'No matches.' : tracker ? 'Log an application to get started.' : 'Add job links to get started.'}</p>
              : <div className="rows">{filtered.map(j => {
                const selectable = !tracker && ['ready', 'approved'].includes(j.status) && j.referral_status !== 'pending';
                return <div className={'row' + (tracker ? ' no-check' : '')} key={j.id} role="button" tabIndex={0} onClick={() => setDetail(j.id)} onKeyDown={e => {if (e.key === 'Enter') setDetail(j.id);}}>
                  {!tracker && <span className="row-check" onClick={e => e.stopPropagation()}>{selectable && <input type="checkbox" aria-label={`Select ${j.title}`} checked={selected.includes(j.id)} onChange={e => setSelected(e.target.checked ? [...selected, j.id] : selected.filter(x => x !== j.id))}/>}</span>}
                  <span className="serif company"><Logo company={companyOf(j)} name={j.company} size={30}/>{j.company}{j.mode === 'autonomous' && <span className="tag auto" title="Autonomous: submits without review">auto</span>}</span>
                  <span className="row-main"><span className="title">{j.title || 'Reading posting…'}</span><span className="sub">{j.location || (j.posting_kind === 'expression_of_interest' ? 'Expression of interest' : '')}</span></span>
                  {state.progress?.[j.id] ? <span className="status s-preparing"><i/>{state.progress[j.id].stage}</span> : <span className={'status s-' + j.status}><i/>{labels[j.status] || j.status}</span>}
                  <span className="row-ref">{j.referral_status === 'pending' ? <span className="amber">Referral hold</span> : j.referral_status === 'received' ? <span className="green">Referral</span> : j.contact_profile_url ? 'Contact found' : j.action_required ? <span className="amber">Action</span> : '—'}</span>
                  <span className="row-date" title={tracker ? 'Date applied' : 'Date added'}>{date(tracker ? (j.submitted_at || j.created_at) : j.created_at)}</span>
                  <ArrowUpRight size={15} className="row-go"/>
                </div>;
              })}</div>}
          </section>
          <aside className="log">
            <div className="rule-head"><h2 className="italic">Log</h2></div>
            {state.events.length === 0 ? <p className="col-empty">No activity yet.</p>
              : state.events.slice(0, 10).map((e: Obj) => <div className="log-row" key={e.id} onClick={() => e.job_id && setDetail(e.job_id)} role={e.job_id ? 'button' : undefined}>
                <div className="msg">{e.message}</div><div className="meta">{e.job_id && jobName(e.job_id) ? jobName(e.job_id) + ' · ' : ''}{ago(e.at)}</div></div>)}
          </aside>
        </div>
      </div>}
      {screen === 'profile' && <Profile state={state} run={run} busy={busy}/>}
      {screen === 'companies' && <Companies state={state} run={run} busy={busy} openJob={id => setDetail(id)}/>}
      {screen === 'settings' && <Settings state={state} run={run} busy={busy}/>}
    </main>

    <footer className="statusbar">
      <button onClick={() => setScreen('settings')}><i className={aiOk ? 'ok' : 'warn'}/>{aiLabel}</button>
      <button onClick={() => setScreen('settings')}><i className={s.fallback_enabled ? 'ok' : 'warn'}/>{s.fallback_enabled ? 'Local fallback ready' : 'Local fallback off'}</button>
      <button onClick={() => setScreen('settings')}><i className={caps.latex ? 'ok' : 'warn'}/>{caps.latex ? 'LaTeX' : 'LaTeX missing'}</button>
      <button className={s.sheet_sync_enabled ? '' : 'amber'} onClick={() => setScreen('settings')}><i className={s.sheet_sync_enabled ? 'ok' : 'warn'}/>{s.sheet_sync_enabled ? 'Tracker synced' : 'Tracker offline'}</button>
      <span>{(state.usage.calls || 0).toLocaleString()} AI calls{last ? ` · ${last.fallback ? 'local · ' : ''}${last.model}` : ''}</span>
      <span className="right"><ShieldCheck size={13}/>Nothing is submitted without your approval · 127.0.0.1</span>
    </footer>

    {notice && <div className="toast" role="status"><Check size={16}/>{notice}<button onClick={() => setNotice('')} aria-label="Dismiss notification"><X size={14}/></button></div>}

    {importOpen && <div className="overlay" onClick={() => setImportOpen(false)}><div className="modal" role="dialog" aria-modal="true" aria-label="Add job links" onClick={e => e.stopPropagation()}>
      <button className="round close" onClick={() => setImportOpen(false)} aria-label="Close"><X size={16}/></button>
      <span className="modal-orb"><Link2 size={20}/></span><h2>Add job links</h2>
      <p>Paste one or more job links, one per line. Duplicates are skipped.</p>
      <div className="mode-pick three">{[['advisor', 'Advisor', 'Tailored skills, cover letter and answers. You apply yourself.'], ['assisted', 'AI Applier', 'Tailors your resume, fills the form, waits for your approval, then submits.'], ['autonomous', 'Autonomous', 'Same, but submits without review. Skills only; anything uncertain is parked for you.']].map(([v, t, d]) =>
        <label key={v} className={'mode-opt' + ((importMode || s.default_mode) === v ? ' on' : '')}><input type="radio" name="mode" checked={(importMode || s.default_mode) === v} onChange={() => setImportMode(v)}/><strong>{t}</strong><small>{d}</small></label>)}</div>
      <textarea autoFocus rows={7} value={links} onChange={e => setLinks(e.target.value)} placeholder={'https://company.com/careers/job/…\nhttps://job-boards.greenhouse.io/…'}/>
      <div className="modal-foot"><span className="muted small">{links.split(/\s+/).filter(Boolean).length} link(s)</span>
        <div><Button onClick={() => setImportOpen(false)}>Cancel</Button>
          <Button primary disabled={!links.trim() || !!busy} onClick={() => run('import', async () => {
            const r = await api('/jobs', 'POST', {urls: links.split(/\s+/).filter(Boolean), mode: importMode || s.default_mode});
            if (r.errors.length) throw new Error(`${r.imported.length} added. ${r.errors.map((e: Obj) => e.error).join(' ')}`);
            setLinks(''); setImportOpen(false);
          }, 'Jobs added.')}><Plus size={15}/>Add</Button></div></div>
    </div></div>}
    {logOpen && <LogJob close={() => setLogOpen(false)} run={run} busy={busy}/>}
    {contactsOpen && <FindContacts jobs={selected.length ? jobs.filter(j => selected.includes(j.id)) : jobs} close={() => setContactsOpen(false)} run={run} busy={busy}/>}
    {active && <JobDetail job={active} state={state} close={() => setDetail(null)} run={run} busy={busy} goProfile={() => {setDetail(null); setScreen('profile');}}/>}
  </div>;
}

function Em({n, tone}: {n: number, tone: 'blue' | 'amber'}) {
  return <span className={'em ' + tone}>{n}</span>;
}

function Stepper({p}: {p: Obj}) {
  return <div className="stepper">{p.stages.map((s: string, i: number) => <span key={s} className={i < p.stage_index ? 'done' : i === p.stage_index ? 'now' : ''}/>)}</div>;
}

function Elapsed({since}: {since: string}) {
  const [, tick] = useState(0);
  useEffect(() => {const id = setInterval(() => tick(t => t + 1), 1000); return () => clearInterval(id);}, []);
  const s = Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 1000));
  return <span>{s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${(s % 60).toString().padStart(2, '0')}s`}</span>;
}

function QuestionCard({q, run, busy}: {q: Obj, run: (n: string, a: () => Promise<any>, s?: string) => Promise<any>, busy: string}) {
  const [v, setV] = useState(q.suggested || '');
  useEffect(() => setV(q.suggested || ''), [q.id]);
  const answer = (value: string) => q.job
    ? run('answer', () => api(`/jobs/${q.job.id}/answer`, 'POST', {answer: value}), 'Answer saved to your profile. Resume the application when ready.')
    : run('answer', () => api(`/companies/${q.company.id}/questions/${q.id}`, 'POST', {answer: value}), 'Answer saved.');
  return <div className="qcard">
    <div className="sub">{q.company.name}{q.job ? ` · ${q.job.title}` : ''} asks</div>
    <div className="serif q">“{q.question}”</div>
    {q.choices?.length > 0 ? <div className="choices">{q.choices.map((c: string) => <button key={c} className="pill" disabled={!!busy} onClick={() => answer(c)}>{c}</button>)}</div>
      : <div className="answer-row"><input value={v} placeholder="Your answer" onChange={e => setV(e.target.value)} onKeyDown={e => {if (e.key === 'Enter' && v.trim()) answer(v);}}/>
        <button className="pill" disabled={!v.trim() || !!busy} onClick={() => answer(v)}>Save</button></div>}
    {q.suggested && <div className="sub">Suggested from your profile. Check before saving.</div>}
  </div>;
}
