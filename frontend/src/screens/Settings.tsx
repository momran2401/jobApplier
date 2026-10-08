import {useState} from 'react';
import {Check, KeyRound, Play, RefreshCw} from 'lucide-react';
import {api, Obj, PanelProps} from '../api';
import {Button, PageHead, Panel} from '../ui';
import {Tracker} from './Tracker';

const KEYS = ['default_mode', 'always_letter', 'autonomous_daily_cap', 'autonomous_delay_minutes', 'monitor_minutes', 'text_phone', 'text_gateway', 'text_kinds', 'quiet_from', 'quiet_to', 'provider', 'model', 'effort', 'fallback_enabled', 'fallback_model', 'fast_model', 'ollama_context', 'ollama_timeout', 'ollama_url',
  'max_model_calls', 'max_output_tokens', 'tex_main', 'skills_start', 'skills_end'];

export function Settings({state, run, busy}: PanelProps) {
  const [s, setS] = useState<Obj>(state.settings), [key, setKey] = useState(''), [models, setModels] = useState<string[]>([]);
  const [fallbackModels, setFallbackModels] = useState<string[]>([]), [fallbackChecked, setFallbackChecked] = useState(false);
  const save = () => api('/settings', 'PUT', Object.fromEntries(KEYS.map(k => [k, s[k]])));
  const set = (k: string, v: any) => setS({...s, [k]: v});
  return <div className="page narrow">
    <PageHead title="Settings"/>

    <Panel title="Applying" sub="How new job links are handled by default.">
      <div className="form-grid">
        <label className="field">Default mode<select value={s.default_mode} onChange={e => set('default_mode', e.target.value)}>
          <option value="assisted">AI Applier · prepare and submit with my approval</option><option value="advisor">Advisor · advice only, I apply myself</option><option value="autonomous">Autonomous · submit without review</option></select></label>
        <label className="field">Autonomous: max submissions per day<input type="number" min={1} max={50} value={s.autonomous_daily_cap} onChange={e => set('autonomous_daily_cap', Number(e.target.value))}/></label>
        <label className="field">Autonomous: wait before submitting (minutes)<input type="number" min={0} max={120} value={s.autonomous_delay_minutes} onChange={e => set('autonomous_delay_minutes', Number(e.target.value))}/></label>
        <label className="check" style={{alignSelf: 'end'}}><input type="checkbox" checked={!!s.always_letter} onChange={e => set('always_letter', e.target.checked)}/>Always draft a cover letter, even if the posting doesn’t mention one</label>
      </div>
    </Panel>
    <Panel title="Email monitor" sub="Reads replies from employers in the Gmail account that owns your tracker sheet (read-only), flags what needs action, and can text you.">
      <div className="form-grid">
        <label className="field">Check automatically<select value={s.monitor_minutes} onChange={e => set('monitor_minutes', Number(e.target.value))}>
          <option value={0}>Only when I click the mail icon</option><option value={15}>Every 15 minutes while the app runs</option><option value={30}>Every 30 minutes</option><option value={60}>Every hour</option></select></label>
        <label className="field">Text me at (10-digit US number)<input value={s.text_phone} onChange={e => set('text_phone', e.target.value)} placeholder="optional"/></label>
        <label className="field">Carrier<select value={s.text_gateway} onChange={e => set('text_gateway', e.target.value)}>
          <option value="tmomail.net">T-Mobile</option><option value="vtext.com">Verizon</option><option value="txt.att.net">AT&amp;T</option><option value="msg.fi.google.com">Google Fi</option></select></label>
        <label className="field">Quiet hours (no texts)<div className="inline-form"><input type="number" min={0} max={23} value={s.quiet_from} onChange={e => set('quiet_from', Number(e.target.value))}/><span className="muted">to</span><input type="number" min={0} max={23} value={s.quiet_to} onChange={e => set('quiet_to', Number(e.target.value))}/></div></label>
        <div className="field full"><span>Text me about</span><div className="choices">{[['interview', 'Interviews'], ['assessment', 'Assessments'], ['action_required', 'Action required'], ['offer', 'Offers'], ['rejection', 'Rejections'], ['confirmation', 'Confirmations']].map(([k, t]) =>
          <label key={k} className={'link-chip' + ((s.text_kinds || []).includes(k) ? ' on' : '')}><input type="checkbox" style={{display: 'none'}} checked={(s.text_kinds || []).includes(k)} onChange={e => set('text_kinds', e.target.checked ? [...(s.text_kinds || []), k] : (s.text_kinds || []).filter((x: string) => x !== k))}/>{t}</label>)}</div></div>
      </div>
      <p className="small muted">Requires the tracker sheet's script to be updated to the latest version (Settings → Tracker sheet → Copy script, paste over the old one, Deploy → Manage deployments → Edit → New version) and authorized once for Gmail.</p>
    </Panel>
    <Panel title="AI connection" sub="Claude receives the relevant profile and page content. Ollama stays on this Mac.">
      <div className="form-grid">
        <label className="field">Provider<select value={s.provider} onChange={e => setS({...s, provider: e.target.value, model: {anthropic: 'claude-sonnet-5-5', codex: 'default'}[e.target.value] || s.fallback_model})}>
          <option value="codex">ChatGPT · Codex (Plus login)</option><option value="anthropic">Claude · API key</option><option value="ollama">Ollama · Local</option></select></label>
        <label className="field">Model<input list="model-list" value={s.model} onChange={e => set('model', e.target.value)} placeholder="Choose or enter a model"/>
          <datalist id="model-list">{models.map(m => <option key={m} value={m}/>)}</datalist></label>
        {s.provider !== 'ollama' && <label className="field">Effort<select value={s.effort} onChange={e => set('effort', e.target.value)}>
          <option value="low">Low · fastest, cheapest</option><option value="medium">Medium</option><option value="high">High · most careful</option></select></label>}
        {(s.provider === 'ollama' || s.fallback_enabled) && <label className="field">Ollama address<input value={s.ollama_url} onChange={e => set('ollama_url', e.target.value)}/></label>}
        <label className="field">Max AI calls per preparation<input type="number" min={1} max={100} value={s.max_model_calls} onChange={e => set('max_model_calls', Number(e.target.value))}/></label>
        <label className="field">Max output tokens per call<input type="number" min={256} max={16000} value={s.max_output_tokens} onChange={e => set('max_output_tokens', Number(e.target.value))}/></label>
      </div>
      <div className="row-actions"><Button disabled={!!busy} onClick={() => run('models', async () => {await save(); setModels(await api('/models'));})}><RefreshCw size={14}/>Find available models</Button>
        <span className="muted small">{models.length ? `${models.length} models available` : 'Saves your provider, then lists its models.'}</span></div>
      {s.provider === 'codex' && <div className="inset">
        <p className="small">{state.capabilities.codex ? <span className="ok-text">Codex CLI found.</span> : <span className="warn-text">Codex CLI not found. Install it with: brew install codex</span>}
          {' '}It uses your ChatGPT sign-in (run <code>codex login</code> once in Terminal) and draws from your Plus allowance. Model “default” is Codex’s own choice.
          If the plan limit runs out, text tasks continue on the local model and form filling pauses.</p>
      </div>}
      {s.provider === 'anthropic' && <div className="inset key-box">
        <label className="field"><span><KeyRound size={13}/> Anthropic API key {state.capabilities.anthropic_key && <b className="ok-text">· Stored</b>}</span>
          <input type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} placeholder="Stored securely in macOS Keychain"/></label>
        <Button disabled={!key || !!busy} onClick={() => run('key', async () => {await api('/secrets', 'POST', {name: 'anthropic_key', value: key}); setKey('');}, 'API key saved in Keychain.')}>Save key</Button>
        <p className="small muted">API usage is billed separately. A Claude.ai subscription is not an API key.</p>
      </div>}
      <div className="usage">
        <span><b>{(state.usage.calls || 0).toLocaleString()}</b>AI calls</span>
        <span><b>{(state.usage.input_tokens || 0).toLocaleString()}</b>Input tokens</span>
        <span><b>{(state.usage.output_tokens || 0).toLocaleString()}</b>Output tokens</span>
        <span><b>{(state.usage.cache_read_tokens || 0).toLocaleString()}</b>Cached input</span>
      </div>
    </Panel>

    <details className="advanced"><summary>Advanced: local models, limits, LaTeX markers</summary>
    <Panel title="Local fallback" sub="If Claude credits run out, research and drafting continue on the local model; form filling pauses until credits are added. Each new run tries Claude first.">
      <label className="check"><input type="checkbox" checked={s.fallback_enabled} onChange={e => set('fallback_enabled', e.target.checked)}/>Use Ollama for text tasks when Claude credits run out</label>
      <div className="form-grid">
        <label className="field">Fallback model<input list="fallback-models" value={s.fallback_model} onChange={e => set('fallback_model', e.target.value)}/>
          <datalist id="fallback-models">{fallbackModels.map(m => <option key={m} value={m}/>)}</datalist></label>
        <label className="field">Fast local model (reading postings)<input list="fallback-models" value={s.fast_model} placeholder="Empty: always use the main model" onChange={e => set('fast_model', e.target.value)}/></label>
        <label className="field">Local context window<select value={s.ollama_context} onChange={e => set('ollama_context', Number(e.target.value))}>
          {[4096, 8192, 16384, 32768].map(n => <option key={n} value={n}>{n.toLocaleString()} tokens</option>)}</select></label>
        <label className="field">Local timeout (seconds)<input type="number" min={120} max={1800} value={s.ollama_timeout} onChange={e => set('ollama_timeout', Number(e.target.value))}/></label>
      </div>
      <div className="row-actions"><Button disabled={!!busy} onClick={() => run('fallback-models', async () => {await save(); setFallbackModels(await api('/models?provider=ollama')); setFallbackChecked(true);})}>
        <RefreshCw size={14}/>Check installed local models</Button>
        <Button disabled={!!busy} onClick={() => run('ollama-start', async () => {const r = await api('/ollama/start', 'POST'); setFallbackModels(r.models); setFallbackChecked(true);}, 'Ollama is running.')}>
          <Play size={14}/>Start Ollama</Button>
        {fallbackChecked && <span className={fallbackModels.includes(s.fallback_model) ? 'ok-text small' : 'warn-text small'}>
          {fallbackModels.includes(s.fallback_model) ? 'The fallback model is installed.' : 'Fallback model not found. Start Ollama and download it.'}</span>}</div>
      <pre className="cmd">ollama pull {s.fallback_model}</pre>
      <p className="small muted">Ollama starts automatically when it’s needed. Invalid keys, rate limits, and network errors do not trigger fallback.</p>
      {state.last_inference && <p className="small muted">Last AI response: {state.last_inference.model} · {state.last_inference.fallback ? 'Ollama fallback' : state.last_inference.provider}</p>}
    </Panel>

    <Panel title="Resume (LaTeX)" sub="Only the text between your markers is replaced; the surrounding LaTeX stays intact."
      action={<span className={'badge ' + (state.capabilities.latex ? 's-submitted' : 's-needs_attention')}><i/>{state.capabilities.latex ? 'Compiler available' : 'Install Tectonic to compile'}</span>}>
      <div className="form-grid">
        <label className="field">Main .tex file<input placeholder="main.tex" value={s.tex_main} onChange={e => set('tex_main', e.target.value)}/></label><div/>
        <label className="field">Skills start marker<input placeholder="% JOBAPPLIER_SKILLS_START" value={s.skills_start} onChange={e => set('skills_start', e.target.value)}/></label>
        <label className="field">Skills end marker<input placeholder="% JOBAPPLIER_SKILLS_END" value={s.skills_end} onChange={e => set('skills_end', e.target.value)}/></label>
      </div>
      <div className="hint">Without configured markers, your uploaded resume PDF is used unchanged, and the review says so.</div>
    </Panel>

    </details>
    <Tracker state={state} run={run} busy={busy} embedded/>
    <div className="save-bar"><span className="muted small">Credentials stay in Keychain. Your workspace stays on this Mac.</span>
      <Button primary disabled={!!busy} onClick={() => run('settings', save, 'Settings saved.')}><Check size={15}/>Save settings</Button></div>
  </div>;
}
