import {useState} from 'react';
import {Building2, Check, KeyRound, MessageCircleQuestion, Plus, Trash2} from 'lucide-react';
import {api, Obj, PanelProps} from '../api';
import {Badge, Button, Empty, Logo, PageHead, Panel, SavedInput} from '../ui';
import {ContactList} from './FindContacts';

const SIGNIN: Obj = {unknown: 'Not recorded', none: 'No account needed', google: 'Sign in with Google', apple: 'Sign in with Apple', email_password: 'Email and password', other: 'Other'};

export function Companies({state, run, busy, openJob}: PanelProps & {openJob: (id: string) => void}) {
  const companies: Obj[] = state.companies || [];
  const [selected, setSelected] = useState<string>(companies[0]?.id || '');
  const company = companies.find(c => c.id === selected) || companies[0];
  const jobsOf = (c: Obj) => state.jobs.filter((j: Obj) => j.company_id === c.id);
  const open = (c: Obj) => c.questions.filter((q: Obj) => !q.answer).length;
  return <div className="page">
    <PageHead title="Companies" sub="What the assistant has learned about each employer: portal, sign-in, your answers, notes."/>
    {!companies.length ? <Empty title="No companies yet">Companies appear here as you add job links.</Empty> : <div className="companies-grid">
      <aside className="panel company-list">
        {companies.map(c => <button key={c.id} className={'company-item' + (company?.id === c.id ? ' on' : '')} onClick={() => setSelected(c.id)}>
          <Logo company={c} name={c.name} size={34}/>
          <span className="row-main"><strong>{c.name}</strong><small>{c.portal !== 'other' ? c.portal : c.domain || '—'} · {jobsOf(c).length} job{jobsOf(c).length === 1 ? '' : 's'}</small></span>
          {open(c) > 0 && <span className="tag amber">{open(c)}?</span>}
        </button>)}
      </aside>
      {company && <CompanyDetail key={company.id} c={company} jobs={jobsOf(company)} run={run} busy={busy} openJob={openJob}/>}
    </div>}
  </div>;
}

function CompanyDetail({c, jobs, run, busy, openJob}: {c: Obj, jobs: Obj[], run: PanelProps['run'], busy: string, openJob: (id: string) => void}) {
  const [email, setEmail] = useState(c.account_email || ''), [password, setPassword] = useState(''), [question, setQuestion] = useState('');
  const [answers, setAnswers] = useState<Obj>({});
  const patch = (changes: Obj, msg?: string) => run('company', () => api(`/companies/${c.id}`, 'PATCH', changes), msg);
  return <div className="company-detail">
    <Panel title={<><Building2 size={16}/>{c.name}</>} sub={<>{c.portal !== 'other' && <span className="tag">{c.portal}</span>} {c.domain && <span className="muted">{c.domain}</span>}{c.aliases?.length > 0 && <span className="muted"> · also known as {c.aliases.join(', ')}</span>}</>}>
      <div className="form-grid">
        <SavedInput label="Company name" value={c.name} save={v => patch({name: v})}/>
        <label className="field">Sign-in method<select value={c.signin_method} onChange={e => patch({signin_method: e.target.value})}>{Object.entries(SIGNIN).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}</select></label>
        <label className="check"><input type="checkbox" checked={!!c.has_account} onChange={e => patch({has_account: e.target.checked})}/>I already have an account here</label>
        <label className="check"><input type="checkbox" checked={!!c.google_signin} onChange={e => patch({google_signin: e.target.checked})}/>Use “Sign in with Google” when offered</label>
      </div>
    </Panel>

    <Panel title={<><KeyRound size={15}/>Saved login</>} sub={c.credential ? 'A password is saved in your Mac’s Keychain for this company. The assistant types it only on this company’s sign-in page; it is never sent to the AI.' : 'Optional. Lets the assistant sign in or create the account for you. Stored in your Mac’s Keychain, never sent to the AI.'}>
      <div className="form-grid">
        <label className="field">Login email<input value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com"/></label>
        <label className="field">Password<input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} placeholder={c.credential ? '•••••••• (saved)' : ''}/></label>
      </div>
      <div className="row-actions">
        <Button primary disabled={!!busy || !email || (!password && !c.credential)} onClick={() => run('credential', async () => {await api(`/companies/${c.id}/credential`, 'POST', {email, password: password || undefined}); setPassword('');}, 'Login saved in Keychain.')}><Check size={14}/>{c.credential ? 'Update login' : 'Save login'}</Button>
        {c.credential && <Button disabled={!!busy} onClick={() => run('credential', () => api(`/companies/${c.id}/credential`, 'POST', {email, password: ''}), 'Saved password removed.')}><Trash2 size={14}/>Remove password</Button>}
      </div>
    </Panel>

    <Panel title={<><MessageCircleQuestion size={15}/>Questions & answers</>} sub="Things the assistant needed to know about this company. Answered once, reused for every application here.">
      {c.questions.length === 0 && <p className="muted small">No questions yet.</p>}
      {c.questions.map((q: Obj) => <div key={q.id} className="qa">
        <strong>{q.question}</strong>
        {q.answer ? <p>{q.answer}</p> : q.choices?.length > 0 ? <div className="choices">{q.choices.map((ch: string) => <button key={ch} className="btn" disabled={!!busy} onClick={() => run('answer', () => api(`/companies/${c.id}/questions/${q.id}`, 'POST', {answer: ch}), 'Answer saved.')}>{ch}</button>)}</div>
        : <div className="inline-form"><input placeholder="Your answer" value={answers[q.id] || ''} onChange={e => setAnswers({...answers, [q.id]: e.target.value})}/>
          <Button disabled={!answers[q.id] || !!busy} onClick={() => run('answer', () => api(`/companies/${c.id}/questions/${q.id}`, 'POST', {answer: answers[q.id]}), 'Answer saved.')}>Save</Button></div>}
        <small>{q.answered_at ? 'Answered' : 'Asked'} {new Date(q.answered_at || q.asked_at).toLocaleDateString()}</small>
      </div>)}
      <div className="inline-form"><input placeholder="Add a note the assistant should know as a question, e.g. “Which email did I use here?”" value={question} onChange={e => setQuestion(e.target.value)}/>
        <Button disabled={!question.trim() || !!busy} onClick={() => run('question', async () => {await api(`/companies/${c.id}/questions`, 'POST', {question}); setQuestion('');})}><Plus size={14}/>Add</Button></div>
    </Panel>

    <Panel title={<>Contacts <span className="count">{(c.contacts || []).length}</span></>} sub="Referral contacts found for this company. Statuses are yours to keep up to date."><ContactList company={c} run={run} busy={busy}/></Panel>

    <Panel title="Notes"><NotesEditor value={c.notes || ''} save={v => patch({notes: v}, 'Notes saved.')} busy={busy}/></Panel>

    <Panel title={<>Jobs <span className="count">{jobs.length}</span></>}>
      {jobs.map(j => <button key={j.id} className="row" onClick={() => openJob(j.id)}><span className="row-main"><strong>{j.title}</strong><small>{j.location || ''}</small></span><Badge status={j.status}/></button>)}
    </Panel>
  </div>;
}

function NotesEditor({value, save, busy}: {value: string, save: (v: string) => void, busy: string}) {
  const [v, setV] = useState(value);
  return <><textarea rows={4} value={v} onChange={e => setV(e.target.value)} placeholder="Anything useful: referral contacts, deadlines, how their portal behaves…"/>
    {v !== value && <div className="row-actions"><Button disabled={!!busy} onClick={() => save(v)}>Save notes</Button></div>}</>;
}
