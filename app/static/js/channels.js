// Channels: titles instead of bare ids, the forum topic of the post, the dialog that checks the bot's rights.
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, act, h, toast } from './dom.js';
import { changed, renderPreview } from './editor.js';
import { channelOf, channelTitle, chatLabel, chats, S } from './state.js';

export { channelTitle };

const RIGHTS = { can_post_messages: 'публикация', can_edit_messages: 'правка', can_delete_messages: 'удаление', can_pin_messages: 'закреп' };

export async function loadChannels() {
  S.channels = await api('GET', '/api/channels');
  showTarget();
  renderPreview(); // the preview's header shows the channel's title
}

// the header: where the post goes, by name, and the forum topic when there is one channel
export function showTarget() {
  const list = chats();
  const btn = $('#btn-check');
  btn.textContent = (list.length ? list.map(chatLabel).join(', ') : 'Канал не выбран') + ' ▾';
  btn.title = (list.length ? 'Пост уйдёт в: ' + list.map(chatLabel).join(', ') + '\n' : '') + 'Выбрать каналы (id через запятую), проверить права бота, темы форума';
  const one = list.length === 1 ? channelOf(list[0]) : null;
  const sel = /** @type {HTMLSelectElement} */ ($('#topic'));
  let topic = S.options.message_thread_id;
  if (topic === undefined) S.topicChat = undefined;
  else if (S.topicChat === undefined && list.length === 1) S.topicChat = list[0];
  // a topic belongs to its chat: in another chat it would send the post nowhere or to a wrong thread
  else if (list.length !== 1 || list[0] !== S.topicChat) {
    delete S.options.message_thread_id; S.topicChat = undefined; topic = undefined; changed();
  }
  if (!one || !one.is_forum) { sel.hidden = true; return; }
  const topics = one.topics || [];
  sel.replaceChildren(h('option', { value: '', text: 'Тема: General' }),
    ...topics.map((t) => h('option', { value: String(t.id), text: 'Тема: ' + t.name, selected: t.id === topic ? true : null })),
    topic !== undefined && !topics.some((t) => t.id === topic) ? h('option', { value: String(topic), text: 'Тема №' + topic, selected: true }) : null,
    h('option', { value: '+', text: 'Темы…' }));
  sel.hidden = false;
}

export function bindTarget() {
  const sel = /** @type {HTMLSelectElement} */ ($('#topic'));
  sel.onchange = () => {
    if (sel.value === '+') { showChannels(); showTarget(); return; }
    if (sel.value) { S.options.message_thread_id = Number(sel.value); S.topicChat = chats()[0]; } else delete S.options.message_thread_id;
    changed();
  };
  $('#btn-check').onclick = showChannels;
}

function rightsLine(c) {
  const r = c.rights || {};
  if (!c.checked_at) return h('span', { class: 'muted small', text: 'ещё не проверен' });
  return h('span', { class: 'small' }, Object.entries(RIGHTS).map(([k, name]) => {
    // in a group the bot posts and edits its own messages without special rights; in a channel it needs them
    const own = c.type !== 'channel' && (k === 'can_post_messages' || k === 'can_edit_messages');
    const ok = r.status === 'creator' || !!r[k] || own || (k === 'can_post_messages' && !!c.can_post);
    return h('span', { class: ok ? 'ok' : 'err', text: (ok ? '✓ ' : '✕ ') + name + '  ' });
  }));
}

function topicsEditor(c, redraw) {
  const topics = [...(c.topics || [])];
  const save = () => act(async () => { await api('PUT', `/api/channels/${encodeURIComponent(c.chat_id)}/topics`, { topics }); await loadChannels(); redraw(); }, 'Темы сохранены');
  const id = h('input', { type: 'number', min: 1, placeholder: 'номер', style: 'width:80px' });
  const name = h('input', { type: 'text', placeholder: 'название темы' });
  return h('div', { class: 'topics' },
    h('p', { class: 'muted small', text: 'Темы форума. Telegram не даёт боту их список: новые темы студия запоминает сама, остальные добавьте вручную. Номер темы — второе число в ссылке на сообщение темы: t.me/c/…/номер/…' }),
    topics.map((t, i) => h('div', { class: 'row small' }, h('span', { text: `${t.name} · №${t.id}` }), h('button', { class: 'ghost small-btn', text: '✕', title: 'Убрать тему', onclick: () => { topics.splice(i, 1); save(); } }))),
    h('div', { class: 'row' }, id, name, h('button', { class: 'ghost small-btn', text: '＋ тема', onclick: () => {
      const n = Number(id.value);
      if (!(n >= 1)) { toast('Номер темы — целое число больше нуля', 'err'); return; }
      topics.push({ id: n, name: name.value.trim() }); save();
    } })));
}

/** @param {string} value */
function setTarget(value) {
  const inp = /** @type {HTMLInputElement} */ ($('#chat'));
  inp.value = value;
  inp.dispatchEvent(new Event('input'));
}

export function showChannels() {
  const box = h('div', { class: 'channels' });
  const use = (c) => { setTarget(c.chat_id); closeDialog(); };
  const check = (id) => act(async () => { const r = await api('POST', '/api/channels', { chat_id: id }); await loadChannels(); draw(); return r; },
    (r) => `«${r.channel.title}»: ` + (r.can_post ? 'бот может публиковать' : 'у бота нет права публиковать'));
  const draw = () => {
    const current = chats();
    // a channel typed in the field but never checked is listed too, so «Проверить» is one click away
    const typed = current.filter((id) => !channelOf(id)).map((id) => /** @type {Channel} */ ({ chat_id: id, title: null, is_forum: false, topics: [], checked_at: null, is_default: false }));
    box.replaceChildren(...[...S.channels, ...typed].map((c) => h('div', { class: 'channel' + (current.includes(c.chat_id) ? ' on' : '') },
      h('div', { class: 'row', style: 'margin:0' },
        h('b', { text: c.title || c.chat_id }), h('span', { class: 'muted small', text: c.chat_id + (c.type ? ' · ' + ({ channel: 'канал', supergroup: c.is_forum ? 'форум' : 'группа', group: 'группа', private: 'личка' }[c.type] || c.type) : '') + (c.is_default ? ' · по умолчанию' : '') }),
        h('span', { class: 'grow' }),
        h('button', { class: 'ghost small-btn', text: 'Сюда', title: 'Публиковать этот черновик в этот канал', onclick: () => use(c) }),
        h('button', { class: 'ghost small-btn', text: c.checked_at ? 'Проверить снова' : 'Проверить', onclick: () => check(c.chat_id) }),
        c.checked_at && !c.is_default ? h('button', { class: 'ghost small-btn', text: '✕', title: 'Убрать из списка (в Telegram ничего не меняется)', onclick: () => act(async () => { await api('DELETE', '/api/channels/' + encodeURIComponent(c.chat_id)); await loadChannels(); draw(); }) }) : null),
      rightsLine(c),
      c.is_forum ? topicsEditor(c, draw) : null)));
    if (!S.channels.length && !typed.length) box.append(h('p', { class: 'muted', text: 'Каналов пока нет: добавьте первый ниже.' }));
  };
  draw();
  const add = h('input', { type: 'text', placeholder: '@канал или -100…' });
  const where = h('input', { type: 'text', value: /** @type {HTMLInputElement} */ ($('#chat')).value, placeholder: '@канал, @другой', onchange: (e) => { setTarget(e.target.value); draw(); } });
  openDialog([h('h3', { text: 'Каналы' }),
    h('label', { class: 'small muted', text: 'Куда публиковать этот черновик (несколько через запятую: пост уйдёт в каждый)' }), where,
    box,
    h('div', { class: 'row' }, add, h('button', { text: 'Добавить и проверить', onclick: () => { if (add.value.trim()) check(add.value.trim()); } })),
    h('p', { class: 'muted small', text: 'Студия спрашивает у Telegram название и права бота. Бот должен быть админом канала с правом «Публикация сообщений».' })]);
  renderPreview();
}
