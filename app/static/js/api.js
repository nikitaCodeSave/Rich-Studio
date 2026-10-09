// fetch wrapper for the studio API (asks for STUDIO_API_KEY on 401).
import { store } from './dom.js';

// the key also goes into a same-site cookie, so <img src=/media/...> and /render pages work behind it
export function setKeyCookie(k) { document.cookie = 'rs_key=' + encodeURIComponent(k) + '; path=/; SameSite=Strict'; }

export async function api(method, path, body, raw) {
  const headers = {};
  const key = store.get('apiKey', '');
  if (key) headers['X-API-Key'] = key;
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const r = await fetch(path, { method, headers, body: payload });
  if (r.status === 401) {
    const k = prompt('Сервис защищён ключом STUDIO_API_KEY. Введите ключ:');
    if (k) { store.set('apiKey', k); setKeyCookie(k); return api(method, path, body, raw); }
  }
  if (raw) return r;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = data.detail;
    const msg = typeof d === 'string' ? d : d && d.message ? d.message + (d.errors ? ':\n• ' + d.errors.join('\n• ') : '') : JSON.stringify(d || data);
    throw new Error(msg);
  }
  return data;
}
