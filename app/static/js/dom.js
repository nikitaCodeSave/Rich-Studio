// DOM helpers, localStorage wrapper, toasts.

// ---------- helpers ----------
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'value') e.value = v;
    else if (k === 'checked') e.checked = !!v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat(Infinity)) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
export const clone = (x) => JSON.parse(JSON.stringify(x));
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
export const store = { get(k, d) { try { const v = localStorage.getItem('rs.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } }, set(k, v) { try { localStorage.setItem('rs.' + k, JSON.stringify(v)); } catch { /* ignore */ } }, del(k) { try { localStorage.removeItem('rs.' + k); } catch { /* ignore */ } } };

export function toast(msg, kind) {
  const t = h('div', { class: 'toast ' + (kind || ''), text: msg });
  $('#toasts').append(t);
  setTimeout(() => t.remove(), kind === 'err' ? 9000 : 4500);
}

/** What a send to one or several channels did: «<done> в N из M», where it did not go and the warnings. */
export function sentReport(done, r) {
  const bad = r.results.filter((x) => !x.ok);
  return (r.results.length > 1 ? `${done} в ${r.results.length - bad.length} из ${r.results.length}` : done)
    + (bad.length ? '\nНе ушло: ' + bad.map((x) => x.chat_id + ' (' + x.error + ')').join('; ') : '')
    + (r.warnings.length ? '\nПредупреждения: ' + r.warnings.join('; ') : '');
}

export async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(typeof okMsg === 'function' ? okMsg(r) : okMsg, 'ok'); return r; } catch (e) { toast(e.message, 'err'); }
}
