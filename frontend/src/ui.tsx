import {useEffect, useState} from 'react';
import type {ReactNode} from 'react';
import {LoaderCircle, Orbit} from 'lucide-react';
import {labels} from './api';
export function Badge({status}: {status: string}) {
  return <span className={'badge s-' + status}><i/>{labels[status] || status}</span>;
}

export function Button({children, onClick, disabled = false, primary = false, className = '', title}:
  {children: ReactNode, onClick?: () => void, disabled?: boolean, primary?: boolean, className?: string, title?: string}) {
  return <button title={title} disabled={disabled} onClick={onClick} className={`btn ${primary ? 'btn-primary' : ''} ${className}`}>{children}</button>;
}

export function Empty({title, children}: {title: string, children: ReactNode}) {
  return <div className="empty"><span className="empty-orb"><Orbit size={26}/></span><h3>{title}</h3><p>{children}</p></div>;
}

export function Panel({title, sub, action, children, className = ''}:
  {title?: ReactNode, sub?: ReactNode, action?: ReactNode, children?: ReactNode, className?: string}) {
  return <section className={`panel ${className}`}>
    {(title || action) && <header className="panel-head"><div>{title && <h2>{title}</h2>}{sub && <p>{sub}</p>}</div>{action}</header>}
    {children}
  </section>;
}

export function PageHead({kicker, title, sub, children}: {kicker?: string, title: string, sub?: string, children?: ReactNode}) {
  return <div className="page-head"><div>{kicker && <div className="kicker">{kicker}</div>}<h1>{title}</h1>{sub && <p>{sub}</p>}</div>{children && <div className="page-actions">{children}</div>}</div>;
}

export function SavedInput({label, value, save, disabled = false}: {label: string, value: string, save: (v: string) => Promise<any>, disabled?: boolean}) {
  const [v, setV] = useState(value || '');
  useEffect(() => setV(value || ''), [value]);
  return <label className="field">{label}<div className="inline-form"><input value={v} disabled={disabled} onChange={e => setV(e.target.value)}/>
    {v !== (value || '') && <Button disabled={disabled} onClick={() => save(v)}>Save</Button>}</div></label>;
}

function useNow(active: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  return Date.now();
}
const secs = (since: string, nowMs: number) => Math.max(0, Math.round((nowMs - new Date(since).getTime()) / 1000));
const clock = (s: number) => s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${pad2(s % 60)}s`;
const pad2 = (n: number) => n.toString().padStart(2, '0');
const PHASES: Record<string, string> = {
  thinking: 'Thinking', starting: 'Starting Ollama', loading: 'Loading model and reading input', generating: 'Generating',
};

/** Live status for a running job: stage stepper, what it's doing now, and what the model is doing. */
export function Progress({p, compact = false}: {p: Record<string, any>, compact?: boolean}) {
  const nowMs = useNow(true);
  const m = p.model;
  const modelName = m ? m.model : '';
  const bar = <div className="stepper" aria-hidden="true">{p.stages.map((s: string, i: number) =>
    <span key={s} className={i < p.stage_index ? 'done' : i === p.stage_index ? 'now' : ''}/>)}</div>;
  if (compact) return <div className="progress compact" title={`${p.stage}${p.detail ? ' · ' + p.detail : ''}`}>
    {bar}<small>{p.stage}{m ? ` · ${PHASES[m.phase] || m.phase}` : ''}</small></div>;
  return <div className="progress" role="status" aria-live="polite">
    <div className="progress-head"><strong>{p.stage}</strong><span>Step {p.stage_index + 1} of {p.stages.length} · {clock(secs(p.started, nowMs))}</span></div>
    {bar}
    <div className="progress-stages">{p.stages.map((s: string, i: number) => <span key={s} className={i < p.stage_index ? 'done' : i === p.stage_index ? 'now' : ''}>{s}</span>)}</div>
    {p.detail && <p className="progress-detail"><LoaderCircle size={13} className="spin"/>{p.detail}{p.step ? ` · form step ${p.step}` : ''}</p>}
    {m && <p className="progress-model"><span className="think-dots"><i/><i/><i/></span>
      <b>{modelName}</b> · {PHASES[m.phase] || m.phase}{m.tokens ? ` · ${m.tokens} tokens` : ''} · {clock(secs(m.since, nowMs))}
      {m.phase === 'loading' && secs(m.since, nowMs) > 8 && <em> First run can take a while on a laptop.</em>}</p>}
  </div>;
}

/** Minimal Markdown renderer for the profile preview: headings, bullets (nested), bold, links. No HTML injection. */
export function Markdown({text}: {text: string}) {
  const inline = (s: string, key: number): ReactNode => {
    const parts: ReactNode[] = [];
    const re = /\*\*(.+?)\*\*|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s<>)]+)/g;
    let last = 0, m: RegExpExecArray | null, i = 0;
    while ((m = re.exec(s))) {
      if (m.index > last) parts.push(s.slice(last, m.index));
      if (m[1]) parts.push(<strong key={`${key}-${i++}`}>{m[1]}</strong>);
      else if (m[2]) parts.push(<a key={`${key}-${i++}`} href={m[3]} target="_blank" rel="noreferrer">{m[2]}</a>);
      else parts.push(<a key={`${key}-${i++}`} href={m[4]} target="_blank" rel="noreferrer">{m[4]}</a>);
      last = m.index + m[0].length;
    }
    if (last < s.length) parts.push(s.slice(last));
    return parts;
  };
  const out: ReactNode[] = [];
  let list: {depth: number, node: ReactNode}[] = [];
  const flush = () => {
    if (!list.length) return;
    out.push(<ul key={'l' + out.length} className="md-list">{list.map((it, i) => <li key={i} style={{marginLeft: it.depth * 16}}>{it.node}</li>)}</ul>);
    list = [];
  };
  text.split('\n').forEach((raw, n) => {
    const h = raw.match(/^(#{1,3})\s+(.*)$/);
    const b = raw.match(/^(\s*)[-*]\s+(.*)$/);
    if (h) {flush(); const Tag = (`h${h[1].length + 1}` as 'h2' | 'h3' | 'h4'); out.push(<Tag key={n} id={'sec-' + h[2].toLowerCase().replace(/[^a-z0-9]+/g, '-')}>{inline(h[2], n)}</Tag>);}
    else if (b) list.push({depth: Math.floor(b[1].replace(/\t/g, '  ').length / 2), node: inline(b[2], n)});
    else if (raw.trim() === '') flush();
    else {flush(); out.push(<p key={n}>{inline(raw, n)}</p>);}
  });
  flush();
  return <div className="md">{out}</div>;
}

/** Company logo tinted to the page's palette; falls back to initials. Dark logos are inverted so they read on the dark background. */
export function Logo({company, name, size = 34}: {company?: Record<string, any> | null, name: string, size?: number}) {
  const [state, setState] = useState<'loading' | 'light' | 'dark' | 'none'>(company?.logo ? 'loading' : 'none');
  useEffect(() => setState(company?.logo ? 'loading' : 'none'), [company?.id, company?.logo]);
  const text = (name || '?').split(/[ .-]+/).filter(Boolean).slice(0, 2).map(x => x[0]).join('').toUpperCase();
  const analyze = (img: HTMLImageElement) => {
    try {  // mean luminance of opaque pixels decides whether to invert
      const c = document.createElement('canvas'); c.width = c.height = 24;
      const ctx = c.getContext('2d')!; ctx.drawImage(img, 0, 0, 24, 24);
      const d = ctx.getImageData(0, 0, 24, 24).data; let sum = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) {if (d[i + 3] > 40) {sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2]; n++;}}
      setState(n && sum / n < 110 ? 'dark' : 'light');
    } catch {setState('light');}
  };
  return <span className={'logo ' + state} style={{width: size, height: size}} aria-hidden="true">
    {state !== 'none' && <img src={`/api/logo/${company!.id}?v=${company!.logo}`} alt="" onLoad={e => analyze(e.currentTarget)} onError={() => setState('none')}/>}
    {(state === 'none' || state === 'loading') && <span className="initials">{text}</span>}
  </span>;
}
