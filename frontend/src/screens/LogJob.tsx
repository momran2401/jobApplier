import {useState} from 'react';
import {ClipboardCheck, X} from 'lucide-react';
import {api, Obj, Run} from '../api';
import {Button} from '../ui';

const SIGNIN = [['unknown', 'Not sure / not needed'], ['google', 'Sign in with Google'], ['apple', 'Sign in with Apple'], ['email_password', 'Email and password'], ['other', 'Other']];
const REFERRAL = [['not_sought', 'No referral'], ['pending', 'Waiting on a referral'], ['received', 'Had a referral'], ['proceed_without', 'Applied without one']];

/** Tracker mode: record an application you submitted yourself. The posting is read in the background to fill the rest. */
export function LogJob({close, run, busy}: {close: () => void, run: Run, busy: string}) {
  const [f, setF] = useState<Obj>({url: '', applied_at: new Date().toISOString().slice(0, 10), signin_method: 'unknown', account_email: '',
    account_created: false, referral_status: 'not_sought', referral_contact: '', notes: '', title: '', company: ''});
  const set = (k: string, v: any) => setF({...f, [k]: v});
  const valid = /^https?:\/\/\S+$/.test(f.url.trim());
  return <div className="overlay" onClick={close}><div className="modal wide" role="dialog" aria-modal="true" aria-label="Log an application" onClick={e => e.stopPropagation()}>
    <button className="icon-btn close" onClick={close} aria-label="Close"><X size={18}/></button>
    <span className="modal-orb"><ClipboardCheck size={22}/></span><h2>Log an application</h2>
    <p>For jobs you apply to yourself. The posting is read automatically to fill in the title, company, and deadline.</p>
    <div className="form-grid">
      <label className="field full">Job link<input autoFocus placeholder="https://…" value={f.url} onChange={e => set('url', e.target.value)}/></label>
      <label className="field">Date applied<input type="date" value={f.applied_at} onChange={e => set('applied_at', e.target.value)}/></label>
      <label className="field">How did you sign in?<select value={f.signin_method} onChange={e => set('signin_method', e.target.value)}>{SIGNIN.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
      <label className="field">Account email used<input placeholder="optional" value={f.account_email} onChange={e => set('account_email', e.target.value)}/></label>
      <label className="check" style={{alignSelf: 'end'}}><input type="checkbox" checked={f.account_created} onChange={e => set('account_created', e.target.checked)}/>I created an account for this company</label>
      <label className="field">Referral<select value={f.referral_status} onChange={e => set('referral_status', e.target.value)}>{REFERRAL.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
      <label className="field">Referral contact<input placeholder="optional" value={f.referral_contact} onChange={e => set('referral_contact', e.target.value)}/></label>
      <label className="field">Job title<input placeholder="optional — read from the posting" value={f.title} onChange={e => set('title', e.target.value)}/></label>
      <label className="field">Company<input placeholder="optional — read from the posting" value={f.company} onChange={e => set('company', e.target.value)}/></label>
      <label className="field full">Notes<textarea rows={2} placeholder="Anything worth remembering" value={f.notes} onChange={e => set('notes', e.target.value)}/></label>
    </div>
    <div className="modal-foot"><span className="muted small">Saved to the dashboard and your tracker sheet.</span>
      <div><Button onClick={close}>Cancel</Button>
        <Button primary disabled={!valid || !!busy} onClick={() => run('log', async () => {await api('/jobs/log', 'POST', {...f, url: f.url.trim()}); close();}, 'Application logged.')}><ClipboardCheck size={15}/>Log application</Button></div></div>
  </div></div>;
}
