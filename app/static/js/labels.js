// Cosmetic Russian names for spec types and fields; unknown names fall back to the spec.

// ---------- labels (cosmetic) ----------
export const BLOCK_LABELS = {
  paragraph: 'Абзац', heading: 'Заголовок', pre: 'Код', footer: 'Подвал', divider: 'Разделитель',
  mathematical_expression: 'Формула', anchor: 'Якорь', list: 'Список', blockquote: 'Цитата',
  expandable_blockquote: 'Свёрнутая цитата', pullquote: 'Акцентная цитата', collage: 'Коллаж',
  slideshow: 'Слайдшоу', table: 'Таблица', details: 'Раскрывашка', map: 'Карта', buttons: 'Кнопки',
  animation: 'GIF', audio: 'Аудио', document: 'Файл', photo: 'Фото', video: 'Видео',
  voice_note: 'Голосовое', thinking: 'Думаю… (только личка)',
};
export const RT_LABELS = {
  bold: 'Жирный', italic: 'Курсив', underline: 'Подчёркнутый', strikethrough: 'Зачёркнутый', spoiler: 'Спойлер',
  date_time: 'Дата и время', text_mention: 'Упоминание по id', subscript: 'Нижний индекс', superscript: 'Верхний индекс',
  marked: 'Маркер', code: 'Код', custom_emoji: 'Кастомный эмодзи', mathematical_expression: 'Формула', url: 'Ссылка',
  email_address: 'E-mail', phone_number: 'Телефон', bank_card_number: 'Номер карты', mention: '@упоминание',
  hashtag: '#хэштег', cashtag: '$кэштег', bot_command: '/команда', button: 'Кнопка в тексте', anchor: 'Якорь',
  anchor_link: 'Ссылка на якорь', reference: 'Сноска', reference_link: 'Ссылка на сноску',
};
export const FIELD_LABELS = {
  text: 'Текст', size: 'Размер (1–6)', language: 'Язык', expression: 'LaTeX', name: 'Имя', items: 'Пункты',
  blocks: 'Содержимое', credit: 'Автор', caption: 'Подпись', cells: 'Ячейки', is_bordered: 'Рамки',
  is_striped: 'Полосы', is_compact: 'Компактно', summary: 'Заголовок', is_open: 'Открыто', location: 'Точка',
  zoom: 'Масштаб', width: 'Ширина', height: 'Высота', buttons: 'Кнопки', align: 'Выравнивание', url: 'URL',
  style: 'Стиль', callback_data: 'Данные для бота', copy_text: 'Копировать текст', disabled: 'Неактивна',
  has_checkbox: 'Чекбокс', is_checked: 'Отмечен', value: 'Номер', latitude: 'Широта', longitude: 'Долгота',
  media: 'Файл', has_spoiler: 'Под спойлером', unix_time: 'Unix-время', date_time_format: 'Формат',
  custom_emoji_id: 'id эмодзи', alternative_text: 'Замена', user: 'Пользователь', anchor_name: 'Якорь',
  reference_name: 'Сноска', username: 'username', hashtag: 'Хэштег',
  photo: 'Фото', video: 'Видео', animation: 'GIF', audio: 'Аудио', document: 'Файл', voice_note: 'Голосовое',
};
// Placeholders in Russian; the English description from the spec stays in the field's tooltip
export const FIELD_HINTS = {
  text: 'Текст', summary: 'Виден всегда, остальное раскрывается по нажатию', caption: 'Подпись под медиа',
  credit: 'Автор или источник', url: 'https://example.com', callback_data: 'Что получит бот при нажатии, до 64 байт',
  copy_text: 'Что скопируется в буфер', expression: 'E = mc^2', language: 'python', name: 'латиницей, без пробелов',
  alternative_text: 'Эмодзи, если кастомный не загрузится', username: 'без @', hashtag: 'без #',
  anchor_name: 'Имя якоря', reference_name: 'Имя сноски', switch_inline_query: 'Текст запроса к боту',
};
export const STATUS_LABELS = { sent: 'опубликован', scheduled: 'отложен', failed: 'ошибка', cancelled: 'отменён', deleted: 'удалён', sending: 'отправляется', missed: 'пропущен' };
/** «1 блок», «2 блока», «5 блоков» */
export function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  const w = m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
  return `${n} ${w}`;
}
// Inline types the rich text field edits as formatting (others become chips)
export const RT_WRAP = { bold: 'b', italic: 'i', underline: 'u', strikethrough: 's', spoiler: 'span.rt-spoiler', marked: 'mark', code: 'code', subscript: 'sub', superscript: 'sup' };
export const IGNORED_MEDIA_FIELDS = new Set(['caption', 'parse_mode', 'caption_entities', 'show_caption_above_media']);
