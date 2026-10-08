import {useEffect, useRef, useState} from 'react';
import {Check, ExternalLink, FileText, History, Link2, LoaderCircle, Sparkles, Upload, X} from 'lucide-react';
import {api, date, Obj, PanelProps} from '../api';
import {Button, Empty, Markdown, PageHead, Panel, Progress} from '../ui';

const NAMES: Obj = {resume: 'Resume PDF', latex: 'Overleaf source', linkedin: 'LinkedIn export', transcript: 'Transcript',
  cover_template: 'Cover-letter template', supporting: 'Supporting material'};
const SECTIONS = ['Identity', 'Education', 'Experience', 'Projects', 'Skills', 'Publications', 'Awards & Certifications',
  'Coursework & Training', 'Links', 'Answers', 'Notes'];

export function Profile({state, run, busy}: PanelProps) {
  const [doc, setDoc] = useState<Obj | null>(null), [text, setText] = useState(''), [view, setView] = useState<'edit' | 'preview'>('edit');
  const [kind, setKind] = useState('resume'), [portfolio, setPortfolio] = useState(''), [history, setHistory] = useState('');
  const editor = useRef<HTMLTextAreaElement>(null);
  const load = async () => {const d = await api('/profile/doc'); setDoc(d); setText(d.text); setHistory('');};
  useEffect(() => {load().catch(() => {});}, []);
  // Reload when the server-side version changes (append/ingest) and the editor has no unsaved edits.
  useEffect(() => {if (doc && state.profile.version !== doc.version && text === doc.text) load().catch(() => {});}, [state.profile.version]);
  const dirty = !!doc && text !== doc.text;
  const save = (verified?: boolean) => run('profile', async () => {
    const d = await api('/profile/doc', 'PUT', verified === undefined ? {text} : {text, verified});
    setDoc({...(doc || {}), ...d, text}); setHistory('');
  }, 'Profile saved.');
  const jump = (name: string) => {
    if (view === 'preview') {document.getElementById('sec-' + name.toLowerCase().replace(/[^a-z0-9]+/g, '-'))?.scrollIntoView({behavior: 'smooth', block: 'start'}); return;}
    const i = text.indexOf('## ' + name);
    if (i >= 0 && editor.current) {editor.current.focus(); editor.current.setSelectionRange(i, i); const lines = text.slice(0, i).split('\n').length; editor.current.scrollTop = Math.max(0, (lines - 2) * 20);}
  };
  const proposals: Obj[] = state.proposals || [];
  const running = (id: string) => state.progress?.['ingest:' + id];
  return <div className="page">
    <PageHead title="Profile" sub="Everything the assistant knows about you. Upload documents to grow it; edit the text directly to correct it.">
      <label className="check" title="Prepared applications use only a verified profile">
        <input type="checkbox" checked={!!state.profile.verified} onChange={e => save(e.target.checked)}/>Verified</label>
      <Button primary disabled={!dirty || !!busy} onClick={() => save()}><Check size={15}/>Save profile</Button>
    </PageHead>

    {proposals.map(p => <Proposal key={p.doc_id} p={p} run={run} busy={busy}/>)}

    <div className="profile-grid">
      <aside className="profile-side">
        <Panel title="Documents" sub="Each upload can be added to the profile once.">
          <div className="field-row">
            <label className="field">Type<select value={kind} onChange={e => setKind(e.target.value)}>{Object.entries(NAMES).map(([k, v]) => <option value={k} key={k}>{String(v)}</option>)}</select></label>
            <label className="btn btn-primary file-btn"><Upload size={15}/>Upload<input type="file" accept=".pdf,.tex,.zip,.txt,.md" disabled={!!busy} onChange={e => {
              const file = e.target.files?.[0];
              if (file) {const f = new FormData(); f.append('file', file); f.append('kind', kind); run('upload', () => api('/documents', 'POST', f), 'Document added. Click “Add to profile” to read it.');}
              e.target.value = '';
            }}/></label>
          </div>
          <div className="inline-form portfolio"><input placeholder="https://your-site.com/project" value={portfolio} onChange={e => setPortfolio(e.target.value)}/>
            <Button disabled={!portfolio || !!busy} onClick={() => run('portfolio', async () => {await api('/portfolio', 'POST', {url: portfolio}); setPortfolio('');}, 'Page saved as a document.')}><Link2 size={14}/>Add page</Button></div>
          {!state.documents.length ? <Empty title="No documents">Upload your resume, LinkedIn export, transcript, and project pages.</Empty>
            : <div className="docs">{state.documents.map((d: Obj) => <div className="doc" key={d.id}>
              <span className="doc-icon"><FileText size={17}/></span>
              <div className="doc-main"><strong>{d.name}</strong><small>{NAMES[d.kind] || d.kind} · {date(d.created_at)}{d.pages ? ` · ${d.pages} p.` : ''}</small>
                {running(d.id) && <Progress p={running(d.id)} compact/>}</div>
              {d.ingested ? <span className="tag green" title="Already added to your profile"><Check size={11}/>In profile</span>
                : !running(d.id) && <Button disabled={!!busy} title="Read this document and propose what to add" onClick={() => run('ingest', () => api(`/documents/${d.id}/ingest`, 'POST'))}><Sparkles size={13}/>Add to profile</Button>}
              {d.path.endsWith('.pdf') && <a className="icon-btn" href={'/api/file/' + d.path} target="_blank" rel="noreferrer" aria-label="Open PDF"><ExternalLink size={14}/></a>}
            </div>)}</div>}
        </Panel>
        <Panel title="Sections">
          <div className="jump">{SECTIONS.map(s => <button key={s} className="link-chip" onClick={() => jump(s)}>{s}</button>)}</div>
        </Panel>
      </aside>

      <section className="panel editor-panel">
        <header className="panel-head">
          <div className="segmented"><button className={view === 'edit' ? 'on' : ''} onClick={() => setView('edit')}>Edit</button><button className={view === 'preview' ? 'on' : ''} onClick={() => setView('preview')}>Preview</button></div>
          <div className="editor-meta">
            {dirty ? <span className="warn-text small">Unsaved changes</span> : doc?.updated_at ? <span className="muted small">Saved {date(doc.updated_at)}</span> : null}
            {doc && doc.history?.length > 0 && <label className="history"><History size={13}/><select value={history} onChange={e => {
              const name = e.target.value; setHistory(name);
              if (name) api(`/profile/history/${encodeURIComponent(name)}`).then(r => {setText(r.text); setView('preview');});
              else setText(doc.text);
            }}><option value="">Current version</option>{doc.history.map((h: string) => <option key={h} value={h}>{h.replace('.md', '').replace('T', ' ').replace('Z', '')}</option>)}</select></label>}
          </div>
        </header>
        {!doc ? <p className="muted"><LoaderCircle size={14} className="spin"/> Loading…</p>
          : view === 'edit' ? <textarea ref={editor} className="editor" spellCheck={false} value={text} onChange={e => setText(e.target.value)}/>
          : <Markdown text={text}/>}
        {history && <p className="hint">Viewing an older version. Click Save profile to restore it, or pick “Current version” to go back.</p>}
      </section>
    </div>
  </div>;
}

/** Review of what one document would add to the profile; nothing is written until Append is clicked. */
function Proposal({p, run, busy}: {p: Obj, run: PanelProps['run'], busy: string}) {
  const all = Object.entries(p.additions || {}).flatMap(([sec, lines]) => (lines as string[]).map((line, i) => `${sec}\u0000${i}`));
  const [picked, setPicked] = useState<Set<string>>(new Set(all));
  const [identity, setIdentity] = useState<Set<string>>(new Set(Object.keys(p.identity || {})));
  const dismiss = () => run('dismiss', () => api(`/profile/proposal/${p.doc_id}`, 'DELETE'));
  if (p.error) return <Panel className="proposal" title={<>Could not read {p.doc_name}</>} action={<button className="icon-btn" onClick={dismiss} aria-label="Dismiss"><X size={15}/></button>}><p className="warn-text">{p.error}</p></Panel>;
  const toggle = (k: string) => setPicked(s => {const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n;});
  const count = picked.size + identity.size;
  return <Panel className="proposal" title={<><Sparkles size={15}/> New from {p.doc_name}</>}
    sub={count ? `${count} addition${count > 1 ? 's' : ''} proposed. Untick anything that isn’t right, then append.` : 'Nothing new found in this document.'}
    action={<button className="icon-btn" onClick={dismiss} aria-label="Dismiss"><X size={15}/></button>}>
    {p.conflicts?.length > 0 && <div className="hint warn"><div><strong>Check these:</strong><ul>{p.conflicts.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul></div></div>}
    {Object.keys(p.identity || {}).length > 0 && <div className="proposal-section"><h3>Identity</h3>
      {Object.entries(p.identity).map(([k, v]) => <label key={k} className="check"><input type="checkbox" checked={identity.has(k)} onChange={() => setIdentity(s => {const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n;})}/><code>{k}</code>: {String(v)}</label>)}</div>}
    {Object.entries(p.additions || {}).map(([sec, lines]) => <div key={sec} className="proposal-section"><h3>{sec}</h3>
      {(lines as string[]).map((line, i) => <label key={i} className="check"><input type="checkbox" checked={picked.has(`${sec}\u0000${i}`)} onChange={() => toggle(`${sec}\u0000${i}`)}/><span className="md-line">{line.replace(/^\s*[-*]\s*/, '').replace(/\*\*/g, '')}</span></label>)}</div>)}
    <div className="row-actions">
      <Button primary disabled={!count || !!busy} onClick={() => run('append', () => api('/profile/append', 'POST', {
        doc_id: p.doc_id,
        additions: Object.fromEntries(Object.entries(p.additions || {}).map(([sec, lines]) => [sec, (lines as string[]).filter((_, i) => picked.has(`${sec}\u0000${i}`))])),
        identity: Object.fromEntries(Object.entries(p.identity || {}).filter(([k]) => identity.has(k))),
      }), 'Profile updated.')}><Check size={14}/>Append {count ? `${count} item${count > 1 ? 's' : ''}` : ''}</Button>
      <Button disabled={!!busy} onClick={dismiss}>Dismiss</Button>
    </div>
  </Panel>;
}
