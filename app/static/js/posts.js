// Published and scheduled posts.
import { api } from './api.js';
import { $, h, toast } from './dom.js';
import { buildPost, callbacksFor, loadPost, updatesLabel } from './editor.js';
import { S } from './state.js';

// ---------- posts ----------
export async function loadPosts() {
  S.posts = await api('GET', '/api/posts');
  S.updates = await api('GET', '/api/updates').catch(() => S.updates);
  const ul = $('#posts');
  const u = S.updates;
  $('#updates-state').replaceChildren(h('span', { class: u && u.state === 'ok' ? 'ok' : 'w', title: (u && u.error) || '', text: 'Приём событий: ' + updatesLabel() }),
    h('span', { class: 'muted', text: ' · нажатия кнопок, первые комментарии, реакции, итоги опросов' }));
  const label = { sent: 'опубликован', scheduled: 'отложен', failed: 'ошибка', cancelled: 'отменён', deleted: 'удалён', sending: 'отправляется' };
  ul.replaceChildren(...S.posts.map((p) => {
    const when = p.status === 'scheduled' ? p.scheduled_at : p.sent_at || p.created_at;
    const poll = p.kind === 'poll';
    const sent = p.status === 'sent';
    const pr = p.poll_results;
    const stats = [
      p.pinned ? '📌 закреплён' : null,
      p.reactions && Object.keys(p.reactions).length ? Object.entries(p.reactions).map(([k, v]) => (k.startsWith('custom:') ? '★' : k === 'paid' ? '⭐' : k) + ' ' + v).join('  ') : null,
      p.clicks && Object.keys(p.clicks).length ? 'нажатия: ' + Object.entries(p.clicks).map(([k, v]) => k + ' ' + v).join(', ') : null,
      p.comment_status ? 'комментарий: ' + ({ waiting: 'ждёт пересылки в обсуждение', sent: 'опубликован', failed: 'ошибка' }[p.comment_status] || p.comment_status) : null,
      pr ? `голосов: ${pr.total}${pr.is_closed ? ' (закрыт)' : ''} · ` + pr.options.map((o) => `${o.text} ${o.voter_count}`).join(' / ') : null,
    ].filter(Boolean);
    const call = async (method, path, body, ok) => { try { await api(method, path, body); toast(ok, 'ok'); } catch (e) { toast(e.message, 'err'); } loadPosts(); };
    return h('li', { title: p.error || '' },
      h('span', { class: 'meta' }, h('span', { class: 'status ' + p.status, text: label[p.status] || p.status }), poll ? h('b', { text: 'опрос' }) : null, p.chat_id, p.message_id ? '#' + p.message_id : ''),
      poll && p.poll_question ? h('span', { class: 'small', text: p.poll_question }) : null,
      h('span', { class: 'meta', text: new Date(when * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) + (p.error ? ' · ' + p.error : '') }),
      stats.length ? h('span', { class: 'meta stats', text: stats.join(' · ') }) : null,
      h('span', { class: 'acts' },
        poll ? null : h('button', { class: 'ghost small-btn', text: 'Открыть', title: 'Загрузить содержимое в редактор', onclick: async () => { const full = await api('GET', '/api/posts/' + p.id); S.draftId = null; $('#title').value = 'Копия поста ' + (p.message_id || ''); loadPost(full.payload); } }),
        !poll && ['sent', 'scheduled'].includes(p.status) ? h('button', { class: 'ghost small-btn', text: 'Заменить', title: 'Заменить этот пост содержимым редактора', onclick: () => replacePost(p) }) : null,
        !poll && ['sent', 'scheduled'].includes(p.status) ? h('button', { class: 'ghost small-btn', text: 'Кнопки', title: 'Заменить только кнопки под постом кнопками из редактора; текст не меняется', onclick: () => {
          const kb = S.keyboard.filter((r) => r.length);
          if (!confirm(kb.length ? `Поставить под пост ${kb.flat().length} кнопок из редактора?` : 'В редакторе нет кнопок. Убрать кнопки из поста?')) return;
          call('POST', `/api/posts/${p.id}/buttons`, { reply_markup: kb.length ? { inline_keyboard: kb } : null, callbacks: callbacksFor(kb) }, 'Кнопки обновлены');
        } }) : null,
        sent ? h('button', { class: 'ghost small-btn', text: p.pinned ? 'Открепить' : 'Закрепить', onclick: () => p.pinned ? call('DELETE', `/api/posts/${p.id}/pin`, undefined, 'Откреплено') : call('POST', `/api/posts/${p.id}/pin`, { disable_notification: true }, 'Закреплено без уведомления') }) : null,
        poll && sent && !(pr && pr.is_closed) ? h('button', { class: 'ghost small-btn', text: 'Закрыть опрос', onclick: () => { if (confirm('Закрыть опрос? Голосовать больше будет нельзя.')) call('POST', `/api/posts/${p.id}/stop-poll`, undefined, 'Опрос закрыт'); } }) : null,
        ['sent', 'scheduled'].includes(p.status) ? h('button', { class: 'danger small-btn', text: p.status === 'sent' ? 'Удалить' : 'Отменить', onclick: async () => {
          if (!confirm(p.status === 'sent' ? 'Удалить пост из канала? Это нельзя отменить.' : 'Отменить отложенную публикацию?')) return;
          try { await api('DELETE', '/api/posts/' + p.id); toast('Готово', 'ok'); } catch (e) { toast(e.message, 'err'); }
          loadPosts();
        } }) : null));
  }));
  if (!S.posts.length) ul.append(h('li', { class: 'muted small', text: 'Здесь появятся опубликованные и отложенные посты.' }));
}

export async function replacePost(p) {
  if (!confirm(`Заменить пост ${p.message_id ? '#' + p.message_id + ' ' : ''}в ${p.chat_id} содержимым редактора?`)) return;
  try { await api('POST', `/api/posts/${p.id}/edit`, buildPost()); toast('Пост обновлён', 'ok'); loadPosts(); } catch (e) { toast(e.message, 'err'); }
}
