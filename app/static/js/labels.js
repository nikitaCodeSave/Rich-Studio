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
  style: 'Стиль', callback_data: 'callback_data', copy_text: 'Копировать текст', disabled: 'Неактивна',
  has_checkbox: 'Чекбокс', is_checked: 'Отмечен', value: 'Номер', latitude: 'Широта', longitude: 'Долгота',
  media: 'Файл', has_spoiler: 'Под спойлером', unix_time: 'Unix-время', date_time_format: 'Формат',
  custom_emoji_id: 'id эмодзи', alternative_text: 'Замена', user: 'Пользователь', anchor_name: 'Якорь',
  reference_name: 'Сноска', username: 'username', hashtag: 'Хэштег',
  photo: 'Фото', video: 'Видео', animation: 'GIF', audio: 'Аудио', document: 'Файл', voice_note: 'Голосовое',
};
// Inline types the rich text field edits as formatting (others become chips)
export const RT_WRAP = { bold: 'b', italic: 'i', underline: 'u', strikethrough: 's', spoiler: 'span.rt-spoiler', marked: 'mark', code: 'code', subscript: 'sub', superscript: 'sup' };
export const IGNORED_MEDIA_FIELDS = new Set(['caption', 'parse_mode', 'caption_entities', 'show_caption_above_media']);
