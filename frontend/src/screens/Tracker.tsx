import {useState} from 'react';
import {ArrowDownToLine, Check, Copy, ExternalLink, Link2, RefreshCw, Unplug} from 'lucide-react';
import {api, Obj, PanelProps} from '../api';
import {Badge, Button, PageHead, Panel} from '../ui';

const STEPS = [
  <>Signed in as <b>momran@utexas.edu</b>, create a blank sheet: <a className="text-link" href="https://sheets.new" target="_blank" rel="noreferrer">sheets.new <ExternalLink size={12}/></a></>,
  <>In the sheet, open <b>Extensions → Apps Script</b>. Delete what’s there, paste the script (button below), and press <b>Save</b>.</>,
  <>Back in the sheet, reload the page, then choose <b>JobApplier → Set up tracker</b>. Google asks you to authorize it once.</>,
  <>In Apps Script: <b>Deploy → New deployment</b>, type <b>Web app</b>. Set <b>Execute as: Me</b> and <b>Who has access: Anyone</b>, then Deploy.</>,
  <>In the sheet, choose <b>JobApplier → Show connection link</b>, copy it, and paste it below.</>,
];

export function Tracker({state, run, busy, embedded = false}: PanelProps & {embedded?: boolean}) {
  const [link, setLink] = useState(''), [copied, setCopied] = useState(false), [script, setScript] = useState('');
  const connected = state.capabilities.tracker_connected;
  const failures = state.jobs.filter((j: Obj) => j.sync_status === 'error');
  const copyScript = () => run('script', async () => {
    const r = await api('/tracker/script');
    try {await navigator.clipboard.writeText(r.script); setCopied(true); setTimeout(() => setCopied(false), 2500);}
    catch {setScript(r.script);}  // Clipboard blocked: show it to copy by hand.
  });
  return <div className={embedded ? '' : 'page narrow'}>
    {!embedded && <PageHead title="Tracker" sub="A Google Sheet kept in sync with your applications."/>}

    {connected ? <Panel title={<>Connected sheet <Badge status="submitted"/></>} sub={state.tracker?.sheet ? `“${state.tracker.sheet}”, tab Applications.` : 'Applications tab.'}
      action={<Button disabled={!!busy} onClick={() => run('disconnect', () => api('/tracker/disconnect', 'POST'), 'Tracker disconnected.')}><Unplug size={14}/>Disconnect</Button>}>
      <div className="row-actions">
        <Button primary disabled={!!busy} onClick={() => run('sync', async () => {
          const r = await api('/sheets/sync', 'POST');
          if (r.errors.length) throw new Error(`${r.synced} synced; ${r.errors.length} need attention.`);
        }, 'Tracker is up to date.')}><RefreshCw size={14}/>Sync now</Button>
        <span className="muted small">Syncs automatically after each preparation and submission.</span>
      </div>
      {failures.map((j: Obj) => <div className="hint warn" key={j.id}>{j.company}: {j.sync_error}</div>)}
      <div className="hint">Your notes, referral details, and formulas in the sheet are never overwritten. Marking a row
        <b>&nbsp;Pending referral</b> in the sheet holds that application; a status like Applied or Interview prevents a duplicate submission.</div>
    </Panel> : <Panel title="Set up your tracker" sub="About two minutes, once. No Google Cloud project or OAuth setup needed.">
      <ol className="steps">{STEPS.map((s, i) => <li key={i}><span>{i + 1}</span><div>{s}</div></li>)}</ol>
      <div className="row-actions"><Button disabled={!!busy} onClick={copyScript}>{copied ? <Check size={14}/> : <Copy size={14}/>}{copied ? 'Script copied' : 'Copy script'}</Button></div>
      {script && <pre className="script">{script}</pre>}
      <label className="field connect">Connection link
        <div className="inline-form"><input type="password" autoComplete="off" placeholder="https://script.google.com/macros/s/…/exec#…" value={link} onChange={e => setLink(e.target.value)}/>
          <Button primary disabled={!link.trim() || !!busy} onClick={() => run('connect', async () => {
            const r = await api('/tracker/connect', 'POST', {link}); setLink('');
            await api('/sheets/sync', 'POST').catch(() => {});
            return r;
          }, 'Tracker connected and synced.')}><Link2 size={14}/>Connect</Button></div></label>
      <p className="small muted">The link is stored in your Mac’s Keychain. Anyone with it can edit the sheet, so treat it like a password.
        If “Anyone” isn’t offered when deploying, UT may restrict it; the same steps work with a personal Google account.</p>
    </Panel>}

    <Panel title="Export" sub="Every application record, for backup or other tools.">
      <a className="text-link" href="/api/export" download="jobapplier-export.json"><ArrowDownToLine size={14}/>Download application records (JSON)</a>
    </Panel>
  </div>;
}
