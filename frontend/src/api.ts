export type Obj = Record<string, any>;
export type Run = (name: string, action: () => Promise<any>, success?: string) => Promise<any>;
export type PanelProps = {state: Obj, run: Run, busy: string};

let token = '';
export const setToken = (value: string) => {token = value;};
export const hasToken = () => !!token;

export async function api(path: string, method = 'GET', body?: unknown, retry = true): Promise<any> {
  const form = body instanceof FormData;
  const r = await fetch('/api' + path, {
    method,
    headers: {...(!form && body !== undefined ? {'Content-Type': 'application/json'} : {}), 'X-Workspace-Token': token},
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (r.status === 401 && retry && path !== '/bootstrap') {
    // The server was restarted and issued a new token: fetch it and retry once instead of failing.
    const b = await (await fetch('/api/bootstrap')).json();
    token = b.token;
    return api(path, method, body, false);
  }
  if (!r.ok) {
    let message = r.statusText;
    try {const b = await r.json(); message = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail);} catch {}
    throw new Error(message);
  }
  return r.json();
}

export const labels: Obj = {
  new: 'Queued', preparing: 'Preparing', needs_attention: 'Needs attention', ready: 'Ready to review',
  approved: 'Approved', submitting: 'Submitting', submitted: 'Submitted', uncertain: 'Check outcome', applied: 'Applied', saved: 'To apply', advised: 'Advised', rejected: 'Rejected', offer: 'Offer',
  pending: 'Pending referral', not_sought: 'Not sought', received: 'Referral received', proceed_without: 'Proceed without referral',
};
export const initials = (name: string) => (name || '?').split(/[ .-]+/).filter(Boolean).slice(0, 2).map(x => x[0]).join('').toUpperCase();
export const date = (value: string) => value ? new Date(value).toLocaleDateString(undefined, {month: 'short', day: 'numeric'}) : '—';
export const ago = (value: string) => {
  const s = (Date.now() - new Date(value).getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
export const pad = (n: number) => n.toString().padStart(2, '0');
