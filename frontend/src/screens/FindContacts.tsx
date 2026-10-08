import {useState} from 'react';
import {Users, X} from 'lucide-react';
import {api, Obj, Run} from '../api';
import {Button} from '../ui';

/** Tracker mode: who to contact for a referral at the selected companies. Read-only web + LinkedIn search. */
export function FindContacts({jobs, close, run, busy}: {jobs: Obj[], close: () => void, run: Run, busy: string}) {
  const [picked, setPicked] = useState<Set<string>>(new Set(jobs.slice(0, 3).map(j => j.id)));
  const [description, setDescription] = useState('University recruiters, early-career hiring managers, or engineers on the team this role is on');
  const toggle = (id: string) => setPicked(s => {const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n;});
  return <div className="overlay" onClick={close}><div className="modal wide" role="dialog" aria-modal="true" aria-label="Find contacts" onClick={e => e.stopPropagation()}>
    <button className="icon-btn close" onClick={close} aria-label="Close"><X size={18}/></button>
    <span className="modal-orb"><Users size={22}/></span><h2>Find referral contacts</h2>
    <p>Searches the web and reads LinkedIn people results in the background. It only reads; it never connects or messages anyone. Each contact comes with a draft note you can copy.</p>
    <div className="hint"><span><strong>One-time:</strong> log in to LinkedIn in the window this opens (separate from your normal Chrome; the login is kept and later searches run hidden). </span>
      <Button disabled={!!busy} onClick={() => run('linkedin', () => api('/linkedin/open', 'POST'), 'LinkedIn opened in the app’s Chrome window. Log in there, then come back.')}>Open LinkedIn login</Button></div>
    <label className="field">Who should it look for?<textarea rows={2} value={description} onChange={e => setDescription(e.target.value)}/></label>
    <div className="field" style={{marginTop: 12}}><span>Jobs</span>
      <div className="pick-list">{jobs.map(j => <label key={j.id} className={'check' + (picked.has(j.id) ? ' on' : '')}><input type="checkbox" checked={picked.has(j.id)} onChange={() => toggle(j.id)}/>{j.company} · {j.title}</label>)}</div></div>
    <div className="modal-foot"><span className="muted small">At most 2 LinkedIn result pages per job and 40 profiles a day.</span>
      <div><Button onClick={close}>Cancel</Button>
        <Button primary disabled={!picked.size || !description.trim() || !!busy} onClick={() => run('contacts', async () => {await api('/contacts/find', 'POST', {job_ids: [...picked], description}); close();}, 'Looking for contacts. Results appear on each job and company.')}><Users size={15}/>Find contacts</Button></div></div>
  </div></div>;
}

export function ContactList({company, run, busy}: {company: Obj, run: Run, busy: string}) {
  const [copied, setCopied] = useState('');
  const contacts: Obj[] = company?.contacts || [];
  if (!contacts.length) return <p className="muted small">No contacts found yet. Use Find contacts in Tracker mode.</p>;
  const key = (c: Obj) => c.url || c.name;
  return <div className="contacts">{contacts.map(c => <div className="contact" key={key(c)}>
    <div className="contact-head"><div><strong>{c.url ? <a href={c.url} target="_blank" rel="noreferrer">{c.name}</a> : c.name}</strong><small>{c.headline}{c.source ? ` · ${c.source}` : ''}</small></div>
      <select value={c.status || 'found'} onChange={e => run('contact', () => api(`/companies/${company.id}/contacts`, 'PATCH', {key: key(c), status: e.target.value}))}>
        {['found', 'contacted', 'replied', 'referred', 'skip'].map(s => <option key={s} value={s}>{s}</option>)}</select></div>
    {c.why && <p className="small muted">{c.why}</p>}
    {c.note && <div className="note"><p>{c.note}</p><Button disabled={!!busy} onClick={() => {navigator.clipboard.writeText(c.note).then(() => {setCopied(key(c)); setTimeout(() => setCopied(''), 2000);}).catch(() => {});}}>{copied === key(c) ? 'Copied' : 'Copy note'}</Button></div>}
  </div>)}</div>;
}
