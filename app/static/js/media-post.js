// Live Photo (sendLivePhoto) and paid media for stars (sendPaidMedia): messages of their own, not rich posts.
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { act, h, toast } from './dom.js';
import { loadPosts } from './posts.js';
import { S, target, targetLabel } from './state.js';
import { whenPicker } from './when.js';

const ITEM_KINDS = { photo: 'Фото', video: 'Видео', live_photo: 'Live Photo' };

/**
 * A select of uploaded files of one kind (photo or video), «…файл» first.
 * @param {string} kind
 * @param {string} value
 * @param {(ref: string) => void} onPick
 * @param {string} label
 */
function filePick(kind, value, onPick, label) {
  const files = S.uploads.filter((u) => u.kind === kind);
  return h('select', { 'aria-label': label, onchange: (e) => onPick(e.target.value) },
    h('option', { value: '', text: files.length ? '…' + label.toLowerCase() : `нет загруженных: ${kind === 'video' ? 'видео' : 'фото'} во вкладке «Файлы»` }),
    files.map((u) => h('option', { value: u.ref, text: u.name, selected: u.ref === value ? true : null })));
}

export function showMediaPost() {
  /** @type {{kind: string, clip: string, still: string, caption: string, stars: string, items: {type: string, media: string, photo: string}[], pin: boolean}} */
  const M = { kind: 'live_photo', clip: '', still: '', caption: '', stars: '', items: [{ type: 'photo', media: '', photo: '' }], pin: false };
  const body = h('div');
  const items = h('div');
  const drawItems = () => {
    items.replaceChildren(...M.items.map((it, i) => h('div', { class: 'row', style: 'margin:2px 0' },
      h('select', { 'aria-label': 'Что в №' + (i + 1), onchange: (e) => { it.type = e.target.value; it.media = ''; it.photo = ''; drawItems(); } },
        Object.entries(ITEM_KINDS).map(([k, t]) => h('option', { value: k, text: t, selected: it.type === k ? true : null }))),
      filePick(it.type === 'photo' ? 'photo' : 'video', it.media, (v) => { it.media = v; }, it.type === 'live_photo' ? 'Видео' : 'Файл'),
      it.type === 'live_photo' ? filePick('photo', it.photo, (v) => { it.photo = v; }, 'Кадр') : null,
      M.items.length > 1 ? h('button', { class: 'ghost small-btn', text: '✕', title: 'Убрать', onclick: () => { M.items.splice(i, 1); drawItems(); } }) : null)),
    M.items.length < 10 ? h('button', { class: 'ghost small-btn', text: '＋ ещё файл', onclick: () => { M.items.push({ type: 'photo', media: '', photo: '' }); drawItems(); } }) : null);
  };
  const field = (label, ...el) => h('div', { class: 'pf' }, h('label', { class: 'small muted', text: label }), ...el);
  const draw = () => {
    body.replaceChildren(...(M.kind === 'live_photo'
      ? [h('p', { class: 'muted small', text: 'Live Photo — короткое видео и кадр из него: в Telegram оно оживает при нажатии. В rich-посте таких блоков нет, поэтому это отдельное сообщение.' }),
          field('Видео', filePick('video', M.clip, (v) => { M.clip = v; }, 'Видео')),
          field('Кадр (фото)', filePick('photo', M.still, (v) => { M.still = v; }, 'Кадр'))]
      : [h('p', { class: 'muted small', text: 'Фото и видео под размытием: подписчик открывает их за звёзды. В канале должна быть разрешена публикация платного контента.' }),
          field('Цена, звёзд', h('input', { type: 'number', min: 1, value: M.stars, 'aria-label': 'Цена в звёздах', style: 'width:140px', oninput: (e) => { M.stars = e.target.value; } })),
          field('Файлы (до 10)', items)]));
    if (M.kind === 'paid_media') drawItems();
  };
  /** @returns {Json | null} */
  const build = () => {
    /** @type {Json} */
    const params = {};
    if (M.caption.trim()) params.caption = M.caption.trim();
    if (S.options.disable_notification) params.disable_notification = true;
    if (M.kind === 'live_photo') {
      if (!M.clip || !M.still) { toast('Выберите видео и кадр', 'err'); return null; }
      return { ...params, live_photo: M.clip, photo: M.still };
    }
    if (!(Number(M.stars) >= 1)) { toast('Цена — от 1 звезды', 'err'); return null; }
    const media = M.items.map((it) => (it.type === 'live_photo' ? { type: it.type, media: it.media, photo: it.photo } : { type: it.type, media: it.media }));
    if (media.some((m) => !m.media || (m.type === 'live_photo' && !m.photo))) { toast('Выберите файл для каждого пункта', 'err'); return null; }
    return { ...params, star_count: Number(M.stars), media };
  };
  const pick = whenPicker(Date.now() + 3600e3);
  const go = async (scheduled) => {
    const params = build();
    if (!params) return;
    /** @type {Json} */
    const req = { kind: M.kind, params, pin: M.pin, ...target() };
    if (scheduled) { const at = pick.value(); if (!at || at * 1000 < Date.now()) { toast('Выберите время в будущем', 'err'); return; } req.at = at; }
    const what = M.kind === 'live_photo' ? 'Live Photo' : `платный контент за ${M.stars} ⭐`;
    if (!scheduled && !confirm(`Отправить ${what} в ${targetLabel()}?`)) return;
    const r = await act(() => api('POST', '/api/media-post', req), scheduled ? 'Запланировано' : (x) => 'Отправлено' + (x.warnings.length ? '\n' + x.warnings.join('; ') : ''));
    if (r) { closeDialog(d); loadPosts(); }
  };
  const kind = h('select', { 'aria-label': 'Что отправить', onchange: (e) => { M.kind = e.target.value; draw(); } },
    h('option', { value: 'live_photo', text: 'Live Photo' }), h('option', { value: 'paid_media', text: 'Платный контент за звёзды' }));
  draw();
  const d = openDialog([
    h('h3', { text: 'Live Photo и платный контент' }),
    h('p', { class: 'muted small', text: 'Куда: ' + (targetLabel() || 'канал не указан') + '. Файлы сначала загрузите во вкладке «Файлы».' }),
    kind, body,
    field('Подпись (до 1024 символов)', h('textarea', { rows: 3, maxlength: 1024, 'aria-label': 'Подпись', oninput: (e) => { M.caption = e.target.value; } })),
    h('label', { class: 'row', style: 'margin:2px 0' }, h('input', { type: 'checkbox', onchange: (e) => { M.pin = e.target.checked; } }), 'Закрепить'),
    h('details', {}, h('summary', { class: 'small', text: 'Время для «Отложить»' }), pick.el)],
  [h('button', { class: 'ghost', value: 'cancel', text: 'Закрыть' }),
    h('button', { class: 'ghost', text: 'Отложить', onclick: () => go(true) }),
    h('button', { class: 'primary', text: 'Отправить', onclick: () => go(false) })]);
}
