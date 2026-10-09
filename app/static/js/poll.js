// Poll dialog (sendPoll).
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { act, h, store, toast } from './dom.js';
import { loadPosts } from './posts.js';
import { S, target, targetLabel } from './state.js';

// ---------- polls ----------
export function showPoll() {
  const P = store.get('pollDraft', null) || { question: '', options: ['', ''], members_only: false, countries: '', description: '', quiz: false, correct: 0, explanation: '', multiple: false, hide_results: false, close_hours: '', pin: false, extra: '' };
  const save = () => store.set('pollDraft', P);
  const inp = (key, attrs) => h('input', { type: 'text', value: P[key], oninput: (e) => { P[key] = e.target.value; save(); }, ...attrs });
  const field = (label, ...el) => h('div', { class: 'pf' }, h('label', { class: 'small muted', text: label }), ...el);
  const chk = (key, label, title) => h('label', { class: 'row', title: title || '', style: 'margin:2px 0' }, h('input', { type: 'checkbox', checked: P[key], onchange: (e) => { P[key] = e.target.checked; save(); drawQuiz(); } }), label);
  const opts = h('div');
  const drawOpts = () => {
    opts.replaceChildren(...P.options.map((o, i) => h('div', { class: 'row', style: 'margin:2px 0' },
      P.quiz ? h('input', { type: 'radio', name: 'poll-correct', checked: P.correct === i, title: 'Правильный ответ', onchange: () => { P.correct = i; save(); } }) : null,
      h('input', { type: 'text', value: o, placeholder: 'Вариант ' + (i + 1), oninput: (e) => { P.options[i] = e.target.value; save(); } }),
      P.options.length > 2 ? h('button', { class: 'ghost small-btn', text: '✕', onclick: () => { P.options.splice(i, 1); if (P.correct >= P.options.length) P.correct = 0; save(); drawOpts(); } }) : null)),
    P.options.length < 12 ? h('button', { class: 'ghost small-btn', text: '＋ вариант', onclick: () => { P.options.push(''); save(); drawOpts(); } }) : null);
  };
  const quizBox = h('div');
  const drawQuiz = () => { drawOpts(); quizBox.replaceChildren(...(P.quiz ? [field('Пояснение после ответа', inp('explanation', { placeholder: 'Почему этот ответ верный' }))] : [])); };
  drawQuiz();
  const when = h('input', { type: 'datetime-local' });
  const build = () => {
    const poll = { question: P.question.trim(), options: P.options.map((o) => o.trim()).filter(Boolean).map((text) => ({ text })) };
    if (P.description.trim()) poll.description = P.description.trim();
    if (P.members_only) poll.members_only = true;
    const cc = P.countries.toUpperCase().split(/[\s,;]+/).filter(Boolean);
    if (cc.length) poll.country_codes = cc;
    if (P.quiz) { poll.type = 'quiz'; poll.correct_option_ids = [P.correct]; if (P.explanation.trim()) poll.explanation = P.explanation.trim(); }
    else if (P.multiple) poll.allows_multiple_answers = true;
    if (P.hide_results) poll.hide_results_until_closes = true;
    const hrs = parseFloat(P.close_hours);
    if (hrs > 0) poll.close_date = Math.floor(Date.now() / 1000 + hrs * 3600);
    if (S.options.disable_notification) poll.disable_notification = true;
    if (P.extra.trim()) Object.assign(poll, JSON.parse(P.extra));
    return poll;
  };
  const go = async (scheduled) => {
    let poll;
    try { poll = build(); } catch (e) { toast('Доп. параметры: ' + e.message, 'err'); return; }
    /** @type {Json} */
    const body = { poll, pin: P.pin, ...target() };
    if (scheduled) { if (!when.value) { toast('Укажите время', 'err'); return; } body.at = Math.floor(new Date(when.value).getTime() / 1000); }
    if (!scheduled && !confirm('Отправить опрос в ' + targetLabel() + '?')) return;
    const r = await act(() => api('POST', '/api/poll', body), scheduled ? 'Опрос запланирован' : (x) => 'Опрос отправлен' + (x.warnings.length ? '\n' + x.warnings.join('; ') : ''));
    if (r) { closeDialog(d); loadPosts(); }
  };
  const d = openDialog([
    h('h3', { text: 'Опрос в канал' }),
    h('p', { class: 'muted small', text: 'Отдельное сообщение (в rich-пост опрос не вкладывается). Удобно отправить сразу после поста. Куда: ' + (targetLabel() || 'канал не указан') }),
    field('Вопрос', inp('question', { maxlength: '300', placeholder: 'Во сколько провести эфир?' })),
    field('Варианты (2–12)', opts),
    field('Описание под вопросом (до 1024 символов)', inp('description')),
    chk('quiz', 'Викторина (один правильный ответ)'), quizBox,
    chk('multiple', 'Несколько ответов (не для викторины)'),
    chk('members_only', 'Только подписчики старше 24 часов', 'members_only, только для каналов'),
    field('Страны, из которых можно голосовать (до 12, например RU, KZ; FT = анонимные номера)', inp('countries', { placeholder: 'пусто = все' })),
    chk('hide_results', 'Показать результаты только после закрытия'),
    field('Закрыть через, часов', inp('close_hours', { placeholder: 'не закрывать' })),
    chk('pin', 'Закрепить опрос'),
    h('details', {}, h('summary', { class: 'small', text: 'Дополнительные параметры sendPoll (JSON)' }),
      h('textarea', { class: 'code', style: 'min-height:60px', value: P.extra, placeholder: '{"allows_revoting": true, "shuffle_options": true}', oninput: (e) => { P.extra = e.target.value; save(); } })),
    field('Отложить до (необязательно)', when)],
  [h('button', { class: 'ghost', value: 'cancel', text: 'Закрыть' }),
    h('button', { class: 'ghost', text: 'Отложить', onclick: () => go(true) }),
    h('button', { class: 'primary', text: 'Отправить', onclick: () => go(false) })]);
}
