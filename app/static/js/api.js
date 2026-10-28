// fetch wrapper for the studio API. On 401 it asks for the key or a token once and trades it for a session:
// the server sets an HttpOnly cookie, so no script here ever keeps the key (and <img src=/media/...> and
// /render pages work behind it too).
import { store } from './dom.js';

function authHeaders() {
  return { 'X-Studio-Client': 'ui' };
}

/** Log in with a key or a token; true when the studio took it. */
export async function login(key) {
  const r = await fetch('/api/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
  return r.ok;
}

/** The editor used to keep the key in localStorage: trade it for a session once and forget it. */
export async function migrateStoredKey() {
  const old = store.get('apiKey', '');
  if (!old) return;
  store.del('apiKey');
  document.cookie = 'rs_key=; path=/; max-age=0';
  await login(old).catch(() => false);
}

/** GET for background polling: never asks for the key, the caller decides what a 401 means. */
export function probe(path) { return fetch(path, { headers: authHeaders() }); }

/** @type {Promise<boolean> | null} */
let asking = null;
/** Several requests get 401 at once (the editor loads spec and config together): one prompt for all of them. */
function askOnce() {
  asking ||= (async () => {
    const k = prompt('Студия защищена. Введите ключ STUDIO_API_KEY или свой токен:');
    return !!k && await login(k);
  })().finally(() => { asking = null; });
  return asking;
}

export async function api(method, path, body, raw) {
  const headers = authHeaders();
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const r = await fetch(path, { method, headers, body: payload });
  if (r.status === 401 && path !== '/api/session' && await askOnce()) return api(method, path, body, raw);
  if (raw) return r;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    const msg = typeof d === 'string' ? d : d && d.message ? d.message + (d.errors ? ':\n• ' + d.errors.join('\n• ') : '') : JSON.stringify(d || data);
    throw Object.assign(new Error(msg), { status: r.status, detail: d });
  }
  return data;
}
