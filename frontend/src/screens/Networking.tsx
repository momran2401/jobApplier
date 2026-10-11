import {useState} from 'react';
import {Check, Copy, Download, ExternalLink, FileUp, Link2, Play, Search, Trash2, X} from 'lucide-react';
import {api, Obj, PanelProps} from '../api';
import {Button, Empty, PageHead, Panel, Progress} from '../ui';

const RESEARCH: Obj = {pending: 'Pending', ready: 'Ready', needs_review: 'Needs review', unavailable: 'Unavailable'};
const DRAFT: Obj = {none: 'No draft', drafted: 'Drafted', needs_review: 'Draft needs review', reviewed: 'Reviewed', closed: 'Closed'};
const TRIGGER: Obj = {move: 'Recent move', promotion: 'Promotion', open_role: 'Open role', post: 'Recent post', none: 'No trigger', unknown: 'Trigger unknown'};
const FILTERS = [['all', 'All'], ['pending', 'Pending'], ['ready', 'Ready'], ['needs_review', 'To review'], ['drafted', 'Drafted'], ['unavailable', 'Unavailable']];
const host = (url: string) => {try {return new URL(url).hostname.replace(/^www\./, '') + new URL(url).pathname.slice(0, 40);} catch {return url;}};

/** People worth talking to at target companies. Research keeps a source for every claim; drafts are reviewed and sent by the user. */
export function Networking({state, run, busy}: PanelProps) {
  const n: Obj = state.networking || {};
  const people: Obj[] = n.people || [];
  const companies: Obj[] = state.companies || [];
  const [setup, setSetup] = useState<Obj>(n.setup || {});
  const [urls, setUrls] = useState(''), [urlCompany, setUrlCompany] = useState(''), [discover, setDiscover] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState('all'), [open, setOpen] = useState<string | null>(null), [searchNote, setSearchNote] = useState('');
  const progress = state.progress?.['networking:research'] || state.progress?.['networking:drafts'];
  const pending = people.filter(p => p.research_status === 'pending');
  const draftable = people.filter(p => p.research_status === 'ready' && p.draft?.status === 'none');
  const setupChanged = JSON.stringify(setup) !== JSON.stringify(n.setup || {});
  const shown = people.filter(p => filter === 'all' || (filter === 'drafted' ? ['drafted', 'reviewed'].includes(p.draft?.status) : filter === 'needs_review' ? (p.research_status === 'needs_review' || p.draft?.status === 'needs_review') : p.research_status === filter));
  const set = (k: string, v: any) => setSetup({...setup, [k]: v});
  const toggle = (id: string) => setDiscover(s => {const d = new Set(s); d.has(id) ? d.delete(id) : d.add(id); return d;});
  return <div className="page">
    <PageHead title="Networking" sub="People worth talking to at the companies you’re applying to. Every claim keeps its source; every message is yours to review and send.">
      <a className="btn" href="/api/networking/export?kind=csv" download><Download size={14}/>networking-list.csv</a>
      <Button disabled={!!busy || !!progress || !pending.length} title={pending.length ? `Up to ${setup.people_per_run} per run` : 'Nobody is waiting for research'}
        onClick={() => run('research', () => api('/networking/research', 'POST', {}), 'Researching. Progress shows below and on the Dashboard.')}><Play size={14}/>Research {Math.min(pending.length, setup.people_per_run || 5)} of {pending.length} pending</Button>
      <Button primary disabled={!!busy || !!progress || !draftable.length} onClick={() => run('drafts', () => api('/networking/drafts', 'POST', {}), 'Writing drafts for your review. Nothing is sent.')}><Play size={14}/>Write {draftable.length} draft{draftable.length === 1 ? '' : 's'}</Button>
    </PageHead>
    {progress && <Progress p={progress}/>}

    <Panel title="Setup" sub="What you’re looking for and how you introduce yourself. The introduction is the only thing about you that goes into a message.">
      <div className="form-grid">
        <label className="field">Target role<input value={setup.target_role || ''} onChange={e => set('target_role', e.target.value)} placeholder="e.g. firmware or embedded internships"/></label>
        <label className="field">Location filter<input value={setup.location || ''} onChange={e => set('location', e.target.value)} placeholder="optional, e.g. Austin or remote US"/></label>
        <label className="field full">One-line introduction<input value={setup.intro || ''} onChange={e => set('intro', e.target.value)} placeholder="e.g. ECE student at UT Austin, class of 2028, building embedded projects"/></label>
        <label className="field">People per research run<input type="number" min={1} max={10} value={setup.people_per_run || 5} onChange={e => set('people_per_run', Number(e.target.value))}/></label>
        <div className="field"><span>LinkedIn reading</span>
          <label className="check"><input type="checkbox" checked={!!setup.linkedin_reading} onChange={e => set('linkedin_reading', e.target.checked)}/>Read profile and recent activity for people on the list ({n.limits?.pages_per_day} pages a day; {n.quota_left} left today)</label>
          <label className="check"><input type="checkbox" checked={!!setup.recency_required} onChange={e => set('recency_required', e.target.checked)}/>Require a trigger from the last {n.limits?.window_days} days (otherwise flagged, not dropped)</label></div>
      </div>
      <div className="row-actions">
        <Button primary disabled={!!busy || !setupChanged} onClick={() => run('setup', () => api('/networking/setup', 'PUT', setup), 'Setup saved.')}><Check size={14}/>Save setup</Button>
        <Button disabled={!!busy} onClick={() => run('linkedin', () => api('/linkedin/open', 'POST'), 'LinkedIn opened in the app’s Chrome window. Log in there once, then come back.')}>Open LinkedIn login</Button>
        <span className="muted small">Reading only, for people already on your list, with pauses between pages. It never connects, follows or messages.</span>
      </div>
    </Panel>

    <Panel title="Sources" sub="Start with your own connections, add people you find yourself, and widen with public search.">
      <div className="sources">
        <div className="source">
          <h3>Your connections</h3>
          <p className="small muted">{n.connections ? `${n.connections.count.toLocaleString()} connections imported ${new Date(n.connections.imported_at).toLocaleDateString()}. Those at your target companies are on the list.`
            : 'LinkedIn → Settings & Privacy → Data privacy → Get a copy of your data → Connections. It arrives by email in minutes to hours. Upload only Connections.csv (a whole archive is accepted, but only that file is read).'}</p>
          <label className="btn file-btn"><FileUp size={14}/>{n.connections ? 'Replace Connections.csv' : 'Upload Connections.csv'}<input type="file" accept=".csv,.zip" disabled={!!busy} onChange={e => {
            const file = e.target.files?.[0]; e.target.value = '';
            if (file) {const f = new FormData(); f.append('file', file); run('connections', async () => {const r = await api('/networking/connections', 'POST', f); return r;}, 'Connections imported.');}}}/></label>
        </div>
        <div className="source">
          <h3>People you found</h3>
          <p className="small muted">Search LinkedIn yourself and paste profile links, one per line. This is the best source: you pick the targets.</p>
          <textarea rows={3} value={urls} onChange={e => setUrls(e.target.value)} placeholder={'https://www.linkedin.com/in/…'}/>
          <div className="inline-form"><select value={urlCompany} onChange={e => setUrlCompany(e.target.value)}><option value="">Company (optional)</option>{companies.map(c => <option key={c.id} value={c.name}>{c.name}</option>)}</select>
            <Button disabled={!urls.trim() || !!busy} onClick={() => run('add-people', async () => {const r = await api('/networking/people', 'POST', {urls, company: urlCompany}); setUrls(''); if (r.errors.length) throw new Error(`${r.added.length} added. ${r.errors.map((x: Obj) => x.error).join(' ')}`);}, 'Added to the list.')}><Link2 size={14}/>Add</Button></div>
        </div>
        <div className="source">
          <h3>Public search</h3>
          <p className="small muted">One web search per company for public profiles matching your target role. No LinkedIn access; names come from search titles.</p>
          <div className="choices">{companies.map(c => <label key={c.id} className={'link-chip' + (discover.has(c.id) ? ' on' : '')}><input type="checkbox" style={{display: 'none'}} checked={discover.has(c.id)} onChange={() => toggle(c.id)}/>{c.name}</label>)}</div>
          <div className="row-actions"><Button disabled={!discover.size || !!busy} onClick={() => run('discover', async () => {const r = await api('/networking/discover', 'POST', {company_ids: [...discover]}); setDiscover(new Set()); setSearchNote(r.note);}, 'Search finished.')}><Search size={14}/>{busy === 'discover' ? 'Searching…' : 'Search'}</Button>
            {searchNote && <span className="small muted">{searchNote}</span>}</div>
        </div>
      </div>
    </Panel>

    <Panel title={<>Networking list <span className="count">{people.length}</span></>} sub={n.last_version ? `Last frozen version ${n.last_version.version} (${n.last_version.reason}); files under data/networking.` : 'Each research run freezes a versioned copy under data/networking.'}
      action={<div className="filters">{FILTERS.map(([v, t]) => <button key={v} className={filter === v ? 'on' : ''} onClick={() => setFilter(v)}>{t}</button>)}</div>}>
      {!people.length ? <Empty title="Nobody on the list yet">Import your connections, paste profile links, or search a company above.</Empty>
        : !shown.length ? <p className="col-empty">No one matches this filter.</p>
        : <div className="people">{shown.map(p => <div key={p.id} className={'person' + (open === p.id ? ' open' : '')}>
          <div className="person-row" role="button" tabIndex={0} onClick={() => setOpen(open === p.id ? null : p.id)} onKeyDown={e => {if (e.key === 'Enter') setOpen(open === p.id ? null : p.id);}}>
            <span className="mono">{p.id}</span>
            <span className="row-main"><span className="serif">{p.name || host(p.profile_url)}</span><span className="sub">{[p.company, p.role || p.headline].filter(Boolean).join(' · ') || 'Not researched yet'}</span></span>
            <span className="person-tags">
              {p.connected && <span className="tag">Connected</span>}
              {p.trigger?.kind && !['none', 'unknown'].includes(p.trigger.kind) && <span className="tag green">{TRIGGER[p.trigger.kind]}</span>}
              {p.research_status !== 'pending' && p.style?.label !== 'insufficient evidence' && <span className="tag">{p.style.label}</span>}
              <span className={'tag ' + (p.research_status === 'ready' ? 'green' : p.research_status === 'needs_review' ? 'amber' : '')}>{RESEARCH[p.research_status]}</span>
              {p.draft?.status !== 'none' && <span className={'tag ' + (p.draft.status === 'needs_review' ? 'amber' : p.draft.status === 'reviewed' ? 'green' : '')}>{DRAFT[p.draft.status]}</span>}
            </span>
          </div>
          {open === p.id && <PersonDetail key={`${p.id}:${p.researched_at}:${p.draft?.drafted_at}`} p={p} run={run} busy={busy} running={!!progress} close={() => setOpen(null)}/>}
        </div>)}</div>}
    </Panel>
  </div>;
}

function PersonDetail({p, run, busy, running, close}: {p: Obj, run: PanelProps['run'], busy: string, running: boolean, close: () => void}) {
  const d = p.draft || {}, t = p.trigger || {}, s = p.style || {}, m = p.mutual || {};
  const [text, setText] = useState(d.text || ''), [mutual, setMutual] = useState(m.status === 'verified' ? m.name : ''), [copied, setCopied] = useState(false);
  const patch = (changes: Obj, msg?: string) => run('person', () => api(`/networking/people/${p.id}`, 'PATCH', changes), msg);
  const link = (url: string, label?: string) => url ? <a className="text-link" href={url} target="_blank" rel="noreferrer">{label || host(url)} <ExternalLink size={11}/></a> : <span className="muted">no source</span>;
  const final = ['reviewed', 'closed'].includes(d.status);
  return <div className="person-detail">
    <div className="row-actions" style={{marginTop: 0}}>
      {p.profile_url && <a className="btn" href={p.profile_url} target="_blank" rel="noreferrer">Open profile <ExternalLink size={13}/></a>}
      <Button disabled={!!busy || running} onClick={() => run('research', () => api('/networking/research', 'POST', {ids: [p.id]}), 'Researching this person.')}><Play size={13}/>{p.research_status === 'pending' ? 'Research' : 'Research again'}</Button>
      <Button disabled={!!busy || running || p.research_status !== 'ready' || final} onClick={() => run('drafts', () => api('/networking/drafts', 'POST', {ids: [p.id]}), 'Writing a draft for your review.')}><Play size={13}/>{d.status === 'none' ? 'Write draft' : 'Rewrite draft'}</Button>
      <Button disabled={!!busy} className="danger" onClick={() => {if (confirm(`Remove ${p.name || p.id} from the list?`)) run('person', async () => {await api(`/networking/people/${p.id}`, 'DELETE'); close();}, 'Removed.');}}><Trash2 size={13}/>Remove</Button>
    </div>
    {p.research_note && <p className="small muted">{p.research_note}{p.sample_gaps ? ` Gaps: ${p.sample_gaps}` : ''}</p>}
    {p.research_status !== 'pending' && <div className="evidence">
      <div><span>ROLE</span><p>{p.role || 'Not shown on the pages read'}{p.company ? ` at ${p.company}` : ''}</p><small>{link(p.role_source_url)}{p.role_checked_at ? ` · checked ${p.role_checked_at}` : ''}</small></div>
      <div><span>TRIGGER</span><p>{TRIGGER[t.kind] || 'Unknown'}{t.date ? ` · ${t.date}${t.date_precision === 'month' ? ' (month)' : ''}` : ''}</p><small>{t.note} {t.source_url && link(t.source_url)}</small></div>
      <div><span>WRITING STYLE</span><p>{s.label}{s.label !== 'insufficient evidence' ? ` · ${s.confidence} confidence` : ''}</p><small>{s.why} {s.patterns}</small></div>
      <div><span>MUTUAL</span><p>{m.status === 'verified' ? `${m.name} (confirmed by you)` : m.status === 'unverified' ? `${m.name} · unverified` : 'None recorded'}</p>
        <small className="inline-form"><input value={mutual} placeholder="Name someone you both know, if you’re sure" onChange={e => setMutual(e.target.value)}/>{mutual !== (m.status === 'verified' ? m.name : '') && <Button disabled={!!busy} onClick={() => patch({mutual_name: mutual}, mutual ? 'Mutual confirmed.' : 'Mutual cleared.')}>Save</Button>}</small></div>
    </div>}
    {p.samples?.length > 0 && <div className="sub-panel"><h3>What they wrote</h3>{p.samples.map((x: Obj, i: number) => <div className="sample" key={i}><p>“{x.snippet}”</p><small>{x.kind}{x.date ? ` · ${x.date}` : ''} · {link(x.url)}</small></div>)}</div>}
    {p.hook?.text && <p className="small"><b>Hook:</b> “{p.hook.text}” · {link(p.hook.url)}</p>}
    {p.flags?.length > 0 && <p className="small amber">{p.flags.join(' · ')}</p>}
    {d.status !== 'none' && <div className="sub-panel draft-box"><div className="sub-head"><h3>First message · {DRAFT[d.status]}</h3>
        <div className="row-actions" style={{margin: 0}}>
          <Button disabled={!text} onClick={() => navigator.clipboard.writeText(text).then(() => {setCopied(true); setTimeout(() => setCopied(false), 2000);}).catch(() => {})}><Copy size={13}/>{copied ? 'Copied' : 'Copy'}</Button>
          {text !== (d.text || '') && <Button primary disabled={!!busy} onClick={() => patch({draft_text: text}, 'Draft saved.')}><Check size={13}/>Save edit</Button>}
        </div></div>
      {d.review_note && <div className="hint warn"><span>{d.review_note}</span></div>}
      <textarea rows={4} value={text} disabled={final} onChange={e => setText(e.target.value)} placeholder="No draft yet: the evidence doesn’t support a specific question. Add a note or research again."/>
      <div className="mono-row"><span>{text.trim().split(/\s+/).filter(Boolean).length} words · {d.source_url ? <>source {link(d.source_url)}</> : 'no source'}</span>
        <span className="row-actions" style={{margin: 0}}>
          {d.status !== 'reviewed' && d.text && <Button disabled={!!busy} onClick={() => patch({draft_status: 'reviewed'}, 'Marked reviewed. Send it yourself from LinkedIn.')}>Reviewed</Button>}
          {d.status !== 'closed' && <Button disabled={!!busy} onClick={() => patch({draft_status: 'closed'}, 'Closed.')}><X size={13}/>Close</Button>}
          {final && <Button disabled={!!busy} onClick={() => patch({draft_status: 'drafted'})}>Reopen</Button>}
        </span></div>
      <p className="small muted">Change anything you would never say, then send it yourself. The app never sends messages or schedules follow-ups.</p>
    </div>}
  </div>;
}
