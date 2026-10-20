import {useState} from 'react';
import {ArrowRight, Check, CheckCheck, CircleAlert, Copy, ExternalLink, Lightbulb, Mail, Pause, Play, Search, Trash2, Users, X} from 'lucide-react';
import {api, Obj, PanelProps} from '../api';
import {Badge, Button, Empty, Logo, Progress, SavedInput} from '../ui';

export function JobDetail({job, state, close, run, busy, goProfile}: PanelProps & {job: Obj, close: () => void, goProfile: () => void}) {
  const [view, setView] = useState(job.status === 'advised' ? 'advice' : 'overview');
  const [copied, setCopied] = useState('');
  const p = job.package;
  const company = (state.companies || []).find((c: Obj) => c.id === job.company_id);
  const copy = (key: string, text: string) => {navigator.clipboard.writeText(text).then(() => {setCopied(key); setTimeout(() => setCopied(''), 2000);}).catch(() => {});};
  const m = p?.match;
  const tabs = [...(p?.advice_at ? ['advice'] : []), 'overview', ...(job.fit ? ['fit'] : []), 'materials', ...(m ? ['match'] : []), 'answers', 'activity'];
  const cadence = (state.followups || []).find((f: Obj) => f.job_id === job.id);
  const mutate = (changes: Obj) => api(`/jobs/${job.id}`, 'PATCH', {revision: job.revision, ...changes});
  const locked = ['preparing', 'submitting'].includes(job.status);
  const canReview = !!p && ['ready', 'needs_attention'].includes(job.status);
  const events = state.events.filter((e: Obj) => e.job_id === job.id);
  const nextStep = job.referral_status === 'pending' ? 'Find your referral, then release the hold when you’re ready.'
    : job.status === 'ready' ? 'Review the exact documents and all answers, then approve.'
    : job.status === 'submitted' ? 'Your application is recorded. Revisit it in the candidate portal.'
    : 'Prepare the application, resolving any profile or sign-in questions along the way.';
  return <div className="overlay drawer-overlay" onClick={close}>
    <section className="drawer" role="dialog" aria-modal="true" aria-label={job.title} onClick={e => e.stopPropagation()}>
      <header className="drawer-head">
        <Logo company={company} name={job.company} size={48}/>
        <div><h2>{job.title || 'Untitled role'}</h2><p>{job.company}{job.location ? ' · ' + job.location : ''}</p></div>
        <button className="icon-btn" onClick={close} aria-label="Close application"><X size={19}/></button>
      </header>
      <div className="drawer-status"><Badge status={job.status}/>{job.referral_status === 'pending' && <Badge status="pending"/>}{['closed', 'expired'].includes(job.posting_status) && <span className="badge s-rejected"><i/>Posting {job.posting_status}</span>}
        <a className="text-link" href={job.url} target="_blank" rel="noreferrer">View posting <ExternalLink size={12}/></a></div>
      <div className="segmented drawer-tabs">{tabs.map(t =>
        <button className={view === t ? 'on' : ''} key={t} onClick={() => setView(t)}>{t[0].toUpperCase() + t.slice(1)}{t === 'activity' && events.length ? ` · ${events.length}` : ''}</button>)}</div>

      <div className="drawer-body">
        {state.progress?.[job.id] && <Progress p={state.progress[job.id]}/>}
        {job.error && <div className="hint warn"><CircleAlert size={16}/><span>{job.error}</span></div>}

        {view === 'overview' && <>
          {job.action_required && <div className="hint warn"><CircleAlert size={16}/><span><strong>Action required:</strong> {job.action_required.message}{job.action_required.link && <> · <a href={job.action_required.link} target="_blank" rel="noreferrer">open email</a></>}</span></div>}
          {job.last_email && !job.action_required && <div className="hint"><span><strong>Last email ({job.last_email.kind}):</strong> {job.last_email.summary}{job.last_email.link && <> · <a href={job.last_email.link} target="_blank" rel="noreferrer">open</a></>}</span></div>}
          {job.status_suggestion && <div className="hint warn"><CircleAlert size={16}/><span><strong>An email suggests this is now {job.status_suggestion.status}:</strong> {job.status_suggestion.summary} {job.status_suggestion.reason}
            <span className="row-actions"><Button disabled={!!busy} onClick={() => run('suggestion', () => api(`/jobs/${job.id}/status-suggestion`, 'POST', {accept: true}), 'Status updated.')}>Yes, that’s this one</Button>
              <Button disabled={!!busy} onClick={() => run('suggestion', () => api(`/jobs/${job.id}/status-suggestion`, 'POST', {accept: false}))}>Not this application</Button></span></span></div>}
          <div className="brief">
            <div><span>ROLE</span><p>{job.summary || 'Read the posting to extract its requirements and check your fit.'}</p></div>
            {job.fit && <div><span>FIT</span><p>{job.fit.score != null ? <><b className={'fit-' + job.fit.decision}>{job.fit.score}/5 · {job.fit.label}</b>{job.fit.summary ? ` · ${job.fit.summary}` : ''} · <button className="text-link" onClick={() => setView('fit')}>details</button></> : `Could not score this posting${job.fit.error ? ': ' + job.fit.error : ''}.`}</p></div>}
            {company?.history?.label && <div><span>THIS EMPLOYER</span><p>{company.history.applied} application{company.history.applied === 1 ? '' : 's'} so far, {company.history.label}{company.history.last_applied ? `; last applied ${company.history.last_applied}` : ''}.</p></div>}
            <div><span>TAILORING</span><p>{p?.tailoring_summary || 'Your experience stays intact. Skills are tailored once your profile is ready.'}</p></div>
            {m && <div><span>MATCH</span><p>{m.error ? `Scoring failed: ${m.error}` : <>{m.total}/{m.max} on HackerRank's rubric{m.coverage?.percent != null ? ` · ${m.coverage.percent}% of requirements mentioned` : ''} · <button className="text-link" onClick={() => setView('match')}>details</button></>}</p></div>}
            <div><span>NEXT STEP</span><p>{nextStep}</p></div>
          </div>
          {job.requirements?.length > 0 && <div className="sub-panel"><h3>Requirements</h3><div className="skill-cloud">{job.requirements.map((r: string, i: number) => <span key={i}>{r}</span>)}</div></div>}
          {['closed', 'expired'].includes(job.posting_status) && <div className="hint warn"><CircleAlert size={16}/><span>This posting looked {job.posting_status} when it was last read ({job.posting_checked_at}). Check the page before spending time on it.</span></div>}
          {job.fit_flags?.length > 0 && <div className="sub-panel warn-panel"><h3>Fit flags</h3><ul>{job.fit_flags.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul>
            {!job.fit_acknowledged && <Button disabled={locked || !!busy} onClick={() => run('fit', () => mutate({fit_acknowledged: true}), 'Fit concerns acknowledged.')}>Continue despite flags</Button>}</div>}
          {cadence && <FollowupPanel job={job} cadence={cadence} run={run} busy={busy} progress={state.progress?.[job.id]} enabled={state.settings.followups_enabled !== false}/>}
          <div className="sub-panel"><h3><Users size={15}/>Referral</h3><p className="small muted">A pending referral blocks submission, even when everything else is approved.</p>
            <label className="field">Referral status<select disabled={locked || job.status === 'submitted'} value={job.referral_status} onChange={e => run('referral', () => mutate({referral_status: e.target.value}), 'Referral status updated.')}>
              <option value="not_sought">Not sought</option><option value="pending">Pending referral · hold submission</option>
              <option value="received">Referral received · review before applying</option><option value="proceed_without">Proceed without a referral</option></select></label>
            <SavedInput label="Referral link" value={job.referral_url} disabled={locked} save={v => run('referral', () => mutate({referral_url: v}))}/>
            <SavedInput label="Referral contact" value={job.referral_contact} disabled={locked} save={v => run('referral', () => mutate({referral_contact: v}))}/>
            <SavedInput label="Referral notes" value={job.referral_notes} disabled={locked} save={v => run('referral', () => mutate({referral_notes: v}))}/>
          </div>
          <div className="sub-panel"><h3>Account & links</h3>
            <label className="field">Sign-in method<select disabled={locked} value={job.signin_method} onChange={e => run('signin', () => mutate({signin_method: e.target.value}))}>
              <option value="unknown">Not recorded yet</option><option value="google">Sign in with Google</option><option value="apple">Sign in with Apple</option>
              <option value="email_password">Email and password</option><option value="other">Other</option></select></label>
            <SavedInput label="Account email" value={job.account_email} disabled={locked} save={v => run('email', () => mutate({account_email: v}))}/>
            <label className="check"><input type="checkbox" disabled={locked} checked={job.account_created} onChange={e => run('account', () => mutate({account_created: e.target.checked}))}/>An account was created for this company</label>
            <div className="links">{[['Application', job.application_url], ['Candidate portal', job.portal_url], ['Application status', job.status_url]].filter(([, url]) => url)
              .map(([label, url]) => <a className="link-chip" key={label} href={url} target="_blank" rel="noreferrer">{label}<ExternalLink size={12}/></a>)}</div>
            <p className="small muted">Passwords and login tokens are never sent to your sheet.</p>
          </div>
          {job.status === 'uncertain' && <div className="sub-panel warn-panel"><h3>Check before retrying</h3><p>Open the candidate portal and verify whether the application arrived.</p>
            <div className="row-actions">
              <Button onClick={() => run('outcome', () => api(`/jobs/${job.id}/resolve-outcome`, 'POST', {revision: job.revision, outcome: 'submitted'}))}>It was submitted</Button>
              <Button onClick={() => run('outcome', () => api(`/jobs/${job.id}/resolve-outcome`, 'POST', {revision: job.revision, outcome: 'not_submitted'}))}>It was not submitted</Button>
            </div></div>}
        </>}

        {view === 'advice' && p?.advice_at && <>
          <div className="sub-panel"><div className="sub-head"><h3>Skills section for Overleaf</h3>
            <Button onClick={() => copy('skills', p.resume?.skills_block || '')}><Copy size={13}/>{copied === 'skills' ? 'Copied' : 'Copy LaTeX'}</Button></div>
            <p className="small muted">{p.tailoring_summary}{p.resume?.dropped?.length ? ` Trimmed to one page by dropping: ${p.resume.dropped.join('; ')}.` : ''}</p>
            <pre className="tex">{p.resume?.skills_block}</pre>
            <div className="row-actions">{p.resume?.tex_path && <a className="btn" href={'/api/file/' + p.resume.tex_path} download="resume.tex">Download full .tex</a>}
              <a className="btn" href={'/api/file/' + p.resume.path} target="_blank" rel="noreferrer">Open tailored PDF <ExternalLink size={13}/></a></div>
            {p.resume?.warning && <div className="hint warn">{p.resume.warning}</div>}</div>
          <SkillGaps gaps={p.skill_gaps}/>
          {p.suggested_edits?.length > 0 && <EditsPanel job={job} p={p} run={run} busy={busy} locked={locked}/>}
          {p.letter && <div className="sub-panel"><div className="sub-head"><h3>Cover letter</h3><div className="row-actions" style={{margin: 0}}>
            <Button onClick={() => copy('letter', p.letter)}><Copy size={13}/>{copied === 'letter' ? 'Copied' : 'Copy text'}</Button>
            {p.letter_pdf && <a className="btn" href={'/api/file/' + p.letter_pdf.path} target="_blank" rel="noreferrer">Open PDF <ExternalLink size={13}/></a>}</div></div>
            <FactCheck c={p.letter_check}/>
            <div className="letter">{p.letter}</div></div>}
          {p.likely_questions?.length > 0 && <div className="sub-panel"><h3>Likely application questions</h3>
            {p.likely_questions.map((q: Obj, i: number) => <div className="qa" key={i}><strong>{q.question}</strong>
              <p className={String(q.answer).startsWith('NEEDS:') ? 'warn-text' : ''}>{q.answer || 'Not in your profile yet.'}</p>
              <small>{q.confidence ? `Confidence: ${q.confidence}` : ''}{q.check ? <span className="amber"> · Fact-check: {q.check}</span> : ''}</small></div>)}</div>}
          {company?.research?.summary && <div className="sub-panel"><h3>About {company.name}</h3><p>{company.research.summary}</p>
            {company.research.products?.length > 0 && <div className="skill-cloud">{company.research.products.map((x: string) => <span key={x}>{x}</span>)}</div>}
            {company.research.hiring_notes?.length > 0 && <ul>{company.research.hiring_notes.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
            <small className="muted">{[company.research.hq, company.research.size].filter(Boolean).join(' · ')}</small></div>}
          {job.fit_flags?.length > 0 && <div className="sub-panel warn-panel"><h3>Fit flags</h3><ul>{job.fit_flags.map((f: string, i: number) => <li key={i}>{f}</li>)}</ul></div>}
        </>}

        {view === 'materials' && (!p ? <Empty title="No materials yet">Prepare this opportunity after saving your verified profile.</Empty> : <>
          <div className="sub-panel"><div className="sub-head"><h3>Exact resume PDF</h3><a className="text-link" href={'/api/file/' + p.resume.path} target="_blank" rel="noreferrer">Open PDF <ExternalLink size={12}/></a></div>
            {p.resume.warning && <div className="hint warn">{p.resume.warning}</div>}
            <iframe title="Prepared resume preview" className="pdf" src={'/api/file/' + p.resume.path}/>
            <div className="small muted">{p.resume.pages} page(s) · Version {p.resume.sha256.slice(0, 10)}</div></div>
          {p.selected_skills?.length > 0 && <div className="sub-panel"><h3>Skills selected for this role</h3><div className="skill-cloud">{p.selected_skills.map((s: string) => <span key={s}>{s}</span>)}</div></div>}
          <SkillGaps gaps={p.skill_gaps}/>
          {p.resume.diff && <div className="sub-panel"><h3>What changed</h3><pre className="diff">{p.resume.diff}</pre></div>}
          {p.suggested_edits?.length > 0 && <EditsPanel job={job} p={p} run={run} busy={busy} locked={locked}/>}
          {p.letter && <div className="sub-panel"><h3>Cover-letter draft</h3><FactCheck c={p.letter_check}/><div className="letter">{p.letter}</div></div>}
          {canReview && !p.visual_reviewed && <Button primary disabled={!!busy} onClick={() => run('review', () => api(`/jobs/${job.id}/review`, 'POST', {revision: job.revision}), 'Document review recorded.')}><CheckCheck size={15}/>I’ve reviewed the PDF and its layout</Button>}
          {p.visual_reviewed && <span className="ok-text"><CheckCheck size={16}/> Document review complete</span>}
        </>)}

        {view === 'fit' && job.fit && <FitPanel f={job.fit}/>}

        {view === 'match' && m && <MatchPanel m={m}/>}

        {view === 'answers' && <>
          <div className="sub-panel"><h3>Prepared answers</h3><p className="small muted">Values filled by the assistant. Check the final screenshot for manual entries or site defaults.</p>
            {p?.answers?.length ? p.answers.map((a: Obj, i: number) => <div className="qa" key={i}><strong>{a.question}</strong><p>{String(a.answer)}</p><small>Source: {a.source}</small></div>)
              : <Empty title="No answers yet">The assistant fills questions from your verified answer bank.</Empty>}</div>
          {p?.knockouts?.length > 0 && <div className="sub-panel warn-panel"><h3>Questions that can reject automatically</h3>
            <p className="small muted">Found on the form before anything was filled. Decide how to answer these, or whether to apply at all.</p>
            {p.knockouts.map((k: Obj, i: number) => <div className="qa" key={i}><strong>{k.label}</strong><p className={k.kind === 'warning' || !k.answered ? 'warn-text' : ''}>{k.note}</p></div>)}</div>}
          {p?.unresolved?.length > 0 && <div className="sub-panel warn-panel"><h3>Needs your answer</h3>{p.unresolved.map((q: string) => <p key={q}>{q}</p>)}
            {p.suggested_answer && <div className="inset"><strong>Suggested draft · verify before use</strong><p>{p.suggested_answer}</p></div>}
            <Button onClick={goProfile}>Update my answer bank <ArrowRight size={14}/></Button></div>}
          {p?.screenshot && <div className="sub-panel"><h3>Final browser review</h3><a href={'/api/file/' + p.screenshot} target="_blank" rel="noreferrer"><img className="shot" alt="Application form before submission" src={'/api/file/' + p.screenshot}/></a></div>}
          {job.receipt && <a className="btn" href={'/api/file/' + job.receipt} target="_blank" rel="noreferrer">View submission receipt <ExternalLink size={14}/></a>}
        </>}

        {view === 'activity' && (events.length ? <ol className="timeline">{events.map((e: Obj) => <li key={e.id}><i/><div><p>{e.message}</p><small>{new Date(e.at).toLocaleString()}</small></div></li>)}</ol>
          : <Empty title="No activity yet">Preparation and submission events are recorded here.</Empty>)}
      </div>

      <footer className="drawer-foot"><span className="small muted">{!locked && <button className="text-link danger-link" disabled={!!busy} title="Delete this job from JobApplier (and your other Macs). Its tracker sheet row stays."
          onClick={() => {if (confirm(`Delete ${job.company || 'this job'} · ${job.title || job.url}? This can't be undone. Its row in the tracker sheet stays.`)) run('delete', async () => {await api(`/jobs/${job.id}`, 'DELETE'); close();}, 'Job deleted.');}}><Trash2 size={13}/>Delete</button>}
        {' '}{job.sync_status === 'synced' ? 'Synced to your tracker' : job.sync_status === 'error' ? 'Tracker sync needs attention' : 'Saved locally'}</span>
        <div className="row-actions">{locked ? <Button onClick={() => run('pause', () => api(`/jobs/${job.id}/pause`, 'POST'))} disabled={job.status === 'submitting'}><Pause size={14}/>Pause</Button>
          : !['submitted', 'uncertain', 'applied'].includes(job.status) && <>
            {job.status === 'saved' && <Button primary disabled={!!busy} onClick={() => run('applied', () => api(`/jobs/${job.id}/applied`, 'POST'), 'Marked as applied.')}><Check size={15}/>Mark as applied</Button>}
            <Button disabled={!!busy} onClick={() => run('research', () => api(`/jobs/${job.id}/research`, 'POST'))}><Search size={14}/>Read job</Button>
            <Button disabled={!!busy || !state.profile.verified} title={state.profile.verified ? 'Tailored skills, cover letter and answers — you apply yourself' : 'Verify your profile first'} onClick={() => run('advise', () => api(`/jobs/${job.id}/advise`, 'POST'))}><Lightbulb size={14}/>{p?.advice_at ? 'Refresh advice' : 'Get advice'}</Button>
            <Button disabled={!!busy || !state.profile.verified} title={state.profile.verified ? undefined : 'Verify your profile first'} onClick={() => run('prepare', () => api(`/jobs/${job.id}/prepare`, 'POST'))}><Play size={14}/>{p ? 'Resume preparation' : 'Prepare'}</Button>
            {(job.mode === 'autonomous' || state.settings.default_mode === 'autonomous') && <Button disabled={!!busy || !state.profile.verified} title="Prepare and submit without review; parks on anything uncertain" onClick={() => run('autonomous', () => api(`/jobs/${job.id}/autonomous`, 'POST'))}><Play size={14}/>Run autonomously</Button>}
            {['ready', 'approved'].includes(job.status) && <Button primary disabled={!!busy || job.referral_status === 'pending' || !p?.visual_reviewed}
              onClick={() => run('approve', () => api(`/jobs/${job.id}/approve`, 'POST', {revision: job.revision}), 'Application approved. Select it in the queue to submit.')}><Check size={15}/>Approve</Button>}
          </>}</div></footer>
    </section>
  </div>;
}

/** Experience wording suggestions with accept/reject. Accepted ones are applied to the tailored copy (never the master) on the next preparation. */
function EditsPanel({job, p, run, busy, locked}: {job: Obj, p: Obj, run: PanelProps['run'], busy: string, locked: boolean}) {
  const accepted = new Set((p.accepted_edits || []).map((e: Obj) => e.original));
  const [picked, setPicked] = useState<Set<number>>(new Set(p.suggested_edits.map((e: Obj, i: number) => accepted.has(e.original) ? i : -1).filter((i: number) => i >= 0)));
  const changed = JSON.stringify([...picked].sort()) !== JSON.stringify(p.suggested_edits.map((e: Obj, i: number) => accepted.has(e.original) ? i : -1).filter((i: number) => i >= 0).sort());
  const manual = job.mode === 'advisor' || job.status === 'advised';
  return <div className="sub-panel"><h3>Experience wording suggestions</h3>
    <p className="small muted">{manual ? 'Optional rewording for you to apply in Overleaf. Facts unchanged.' : 'Tick a suggestion to apply it to this application’s resume copy. Your master resume is never changed.'}</p>
    {p.suggested_edits.map((e: Obj, i: number) => <div className="compare" key={i}>
      <div><span>ORIGINAL</span><p>{e.original}</p></div><div><span>SUGGESTED</span><p>{e.proposed}</p></div>
      <small>{e.reason}</small>
      {!manual && <label className="check accept"><input type="checkbox" disabled={locked} checked={picked.has(i)} onChange={() => setPicked(s => {const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n;})}/>Apply this change</label>}
    </div>)}
    {!manual && changed && <div className="row-actions"><Button primary disabled={!!busy || locked} onClick={() => run('edits', () => api(`/jobs/${job.id}/edits`, 'POST', {accepted: [...picked], revision: job.revision}), 'Edits saved. Resume preparation to rebuild the resume.')}><Check size={14}/>Save accepted edits</Button></div>}
  </div>;
}

/** The match report: HackerRank's open-source intern rubric (one run, so noisy) and a deterministic keyword-coverage check. */
function MatchPanel({m}: {m: Obj}) {
  if (m.error) return <div className="hint warn"><CircleAlert size={16}/><span>Scoring failed: {m.error}. Prepare or get advice again to retry.</span></div>;
  const pct = (a: number, b: number) => b ? Math.round(100 * a / b) : 0;
  return <>
    <div className="sub-panel"><div className="sub-head"><h3>HackerRank rubric · {m.total} / {m.max}</h3><span className="small muted">{new Date(m.scored_at).toLocaleDateString()}</span></div>
      <p className="small muted">{m.caveat}</p>
      <div className="score-bars">{m.categories.map((c: Obj) => <div className="score-row" key={c.key}>
        <div className="score-head"><strong>{c.label}</strong><span className="mono">{c.score} / {c.max}</span></div>
        <div className="bar"><i style={{width: pct(c.score, c.max) + '%'}}/></div>
        <p className="small">{c.evidence}</p></div>)}</div>
      <div className="score-row"><div className="score-head"><strong>Bonus</strong><span className="mono">+{m.bonus.total} / {m.bonus.max}</span></div>
        <ul className="small">{m.bonus.items.filter((b: Obj) => b.points > 0).map((b: Obj) => <li key={b.key}>{b.label}: +{b.points}{b.rank ? ` (rank ${b.rank})` : ''} · {b.evidence}</li>)}
          {!m.bonus.items.some((b: Obj) => b.points > 0) && <li className="muted">No bonus items apply.</li>}</ul></div>
      {m.deductions.total > 0 && <div className="score-row"><div className="score-head"><strong>Deductions</strong><span className="mono">-{m.deductions.total}</span></div><p className="small">{m.deductions.reasons}</p></div>}
      <div className="two-col">
        <div><h4>Strengths</h4><ul className="small">{m.strengths.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
        <div><h4>To improve</h4><ul className="small">{m.improvements.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
      </div>
      <p className="small muted">GitHub: {m.github?.used ? `${m.github.username}, ${m.github.total_projects} repos with your commits (${m.github.open_source} with other contributors)` : m.github?.note || 'not used'}.</p>
    </div>
    {m.coverage && <div className="sub-panel"><h3>Requirements mentioned in the resume · {m.coverage.covered} of {m.coverage.total}{m.coverage.percent != null ? ` (${m.coverage.percent}%)` : ''}</h3>
      <p className="small muted">Plain text matching against the tailored PDF, the way a recruiter's keyword search works. Repeatable; no AI involved.</p>
      <ul className="coverage">{m.coverage.requirements.map((r: Obj, i: number) => <li key={i} className={r.level === 'present' || (!r.level && r.covered) ? 'ok' : r.level === 'thin' ? 'thin' : 'miss'}>{r.covered ? <Check size={13}/> : <X size={13}/>}<span>{r.text}</span>{r.missing.length > 0 && <small>{r.level === 'thin' ? 'thin · ' : ''}missing: {r.missing.join(', ')}</small>}</li>)}</ul>
      {m.coverage.thin > 0 && <p className="small muted">Thin: at least half of the requirement’s terms appear. Known spellings count (JS for JavaScript, k8s for Kubernetes); “Java” never matches “JavaScript”.</p>}</div>}
  </>;
}

/** Result of the deterministic fact-check on a generated text: unsupported numbers block, unverified names warn. */
function FactCheck({c}: {c?: Obj}) {
  if (!c) return null;
  const voice = c.voice?.length > 0 && <p className="small warn-text">Reads like machine writing in places: {c.voice.join('; ')}. Worth a pass by hand.</p>;
  if (!c.blocking?.length && !c.warnings?.length) return <><p className="small ok-text">Fact-check passed: every number and name appears in your profile or the posting.</p>{voice}</>;
  return <><div className="hint warn"><CircleAlert size={16}/><span>{c.blocking?.length > 0 && <><strong>Not in your sources:</strong> {c.blocking.join(', ')}. </>}{c.warnings?.length > 0 && <><strong>Check these names:</strong> {c.warnings.join(', ')}.</>}</span></div>{voice}</>;
}

/** Skills the tailoring pass wanted to show but the Skills section does not support. Nothing is added on its own. */
function SkillGaps({gaps}: {gaps?: Obj}) {
  if (!gaps || (!gaps.in_profile?.length && !gaps.gap?.length)) return null;
  return <div className="sub-panel"><h3>Skills the posting wants that your resume doesn’t show</h3>
    {gaps.in_profile?.length > 0 && <p className="small"><strong>In your profile, but not under Skills:</strong> {gaps.in_profile.join(', ')}. Add them to the Skills section of your profile and they can go on the resume.</p>}
    {gaps.gap?.length > 0 && <p className="small"><strong>No trace in your profile:</strong> {gaps.gap.join(', ')}. Either you have them and the profile is missing them, or this is a real gap to mention honestly in the letter.</p>}
  </div>;
}

/** The job-fit evaluation: a score and verdict decided before any tailoring, with the requirements that drove it. */
function FitPanel({f}: {f: Obj}) {
  const tier = (r: Obj) => r.tier === 'stated' ? 'stated' : r.tier === 'structural' ? 'from the posting’s structure' : 'inferred';
  return <>
    <div className="sub-panel"><div className="sub-head"><h3>Fit · {f.score != null ? `${f.score} / 5 · ${f.label}` : 'not scored'}</h3><span className="small muted">{f.scored_at ? new Date(f.scored_at).toLocaleDateString() : ''}</span></div>
      {f.summary && <p>{f.summary}</p>}
      <p className="small muted">One reading of the posting against your profile. {f.apply_at}+ means apply, {f.consider_at}+ consider, below that skip. How much a requirement matters was rated from the posting before the profile was read, and an inferred importance is never critical or high.</p>
      {f.hard_stops?.length > 0 && <div className="hint warn"><CircleAlert size={16}/><span><strong>Hard stops:</strong> {f.hard_stops.join(' · ')}</span></div>}
      {f.error && <div className="hint warn">{f.error}</div>}
    </div>
    {f.requirements?.length > 0 && <div className="sub-panel"><h3>Requirements · what matters and what you bring</h3>
      <table className="fit-table"><thead><tr><th>Requirement</th><th>Matters</th><th>You</th></tr></thead><tbody>
        {f.requirements.map((r: Obj, i: number) => <tr key={i} className={'m-' + r.match}>
          <td>{r.text}{r.quote && <small>“{r.quote}”</small>}{r.evidence && <small>profile: {r.evidence}</small>}</td>
          <td><b>{r.importance}</b><small>{tier(r)}</small></td>
          <td className="match">{r.match}</td></tr>)}
      </tbody></table>
      {f.dropped > 0 && <p className="small muted">{f.dropped} lower-importance requirement{f.dropped === 1 ? '' : 's'} not listed.</p>}
    </div>}
    {f.gaps?.length > 0 && <div className="sub-panel"><h3>Gaps an interviewer will probe</h3>
      {f.gaps.map((g: Obj, i: number) => <div className="qa" key={i}><strong>{g.requirement}</strong><p><b>Risk:</b> {g.risk}</p><p><b>What you can say:</b> {g.mitigation}</p></div>)}</div>}
    {f.soft_gaps?.length > 0 && <div className="sub-panel"><h3>Smaller gaps</h3><div className="skill-cloud">{f.soft_gaps.map((x: string) => <span key={x}>{x}</span>)}</div></div>}
  </>;
}

const URGENCY: Obj = {urgent: 'Reply today', overdue: 'Follow-up due', waiting: 'On track', cold: 'Gone quiet'};

/** Follow-up cadence for an application that is in: when the next nudge is due, a draft to review, and what you sent. */
function FollowupPanel({job, cadence, run, busy, progress, enabled}: {job: Obj, cadence: Obj, run: PanelProps['run'], busy: string, progress?: Obj, enabled: boolean}) {
  const d = job.followup_draft;
  const drafting = progress?.operation === 'followup';
  return <div className={'sub-panel followup u-' + cadence.urgency}><h3><Mail size={15}/>Follow-up · <span className="urgency">{URGENCY[cadence.urgency] || cadence.urgency}</span></h3>
    <p className="small muted">{cadence.reason}{cadence.next_at && cadence.urgency !== 'cold' ? ` Next: ${cadence.next_at}.` : ''} Drafts are written here; you send them from your own email.</p>
    {d && <div className="inset"><strong>{d.subject}</strong><div className="letter">{d.body}</div><FactCheck c={d.check}/></div>}
    {enabled && <div className="row-actions">
      <Button disabled={!!busy || drafting || cadence.urgency === 'cold'} onClick={() => run('followup', () => api(`/jobs/${job.id}/followup/draft`, 'POST'))}>{drafting ? 'Drafting…' : d ? 'Redraft' : `Draft follow-up ${cadence.count + 1}`}</Button>
      <Button primary disabled={!!busy} onClick={() => run('followup-sent', () => api(`/jobs/${job.id}/followup/sent`, 'POST', {}), 'Recorded. The next date moved.')}><Check size={14}/>{d ? 'I sent this' : 'I sent a follow-up'}</Button>
    </div>}
    {job.follow_ups?.length > 0 && <ul className="small muted">{job.follow_ups.map((f: Obj, i: number) => <li key={i}>Follow-up {i + 1} sent {f.at}{f.text ? `: ${f.text.slice(0, 90)}${f.text.length > 90 ? '…' : ''}` : ''}</li>)}</ul>}
  </div>;
}
