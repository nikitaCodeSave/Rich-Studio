// «В HTML» in the visual mode: the post becomes HTML source in the HTML tab; text already there is replaced only
// after the studio's own question; a post HTML cannot hold whole is refused and the HTML tab stays as it was
import { readFileSync } from 'node:fs';
/* global RichRender -- defined by render.js inside the page */

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, state, showJson, applyJson, until }) {
  const captured = JSON.parse(readFileSync(new URL('../telegram_parse/captured.json', import.meta.url), 'utf8'));
  const quick = { timeout: 3000 };
  const visual = () => page.click('#mode-tabs [data-mode=blocks]');
  const html = () => page.inputValue('#src-html');
  const htmlTab = () => page.locator('#mode-tabs [data-mode=html].on').count();
  const button = page.locator('#to-html');
  const dialog = page.locator('dialog.dlg');
  const body = () => page.evaluate(() => document.querySelector('#preview').shadowRoot.querySelector('.tg-body')?.textContent?.replace(/\s+/g, '') || '');
  const draft = () => page.evaluate(async () => {
    const id = new URLSearchParams(location.search).get('draft');
    return id ? (await fetch('/api/drafts/' + id)).json() : null;
  });
  const shot = async (name) => {
    await page.screenshot({ path: `test-results/ui/${name}-1400.png` }).catch(() => {});
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `test-results/ui/${name}-390.png` }).catch(() => {});
    await page.setViewportSize({ width: 1400, height: 900 });
  };

  await page.click('#btn-new');
  // toasts of the checks before would cover the screenshots
  await page.evaluate(() => document.querySelector('#toasts')?.replaceChildren());
  // the browser's own alert/confirm never appear on the way
  let native = 0;
  const onDialog = () => { native++; };
  page.on('dialog', onDialog);
  const post = {
    rich_message: {
      blocks: [
        { type: 'heading', size: 2, text: 'Экспорт' },
        { type: 'paragraph', text: ['Текст с ', { type: 'bold', text: 'жирным' }, ' и ', { type: 'url', text: 'ссылкой', url: 'https://t.me/telegram' }] },
        { type: 'table', is_bordered: true, cells: [[{ text: 'A', is_header: true, align: 'left', valign: 'top' }, { text: 'B', is_header: true, align: 'left', valign: 'top' }], [{ text: '1', align: 'left', valign: 'top' }, { text: '2', align: 'left', valign: 'top' }]] },
        { type: 'photo', photo: { type: 'photo', media: 'AgACAgIAAxkBAAIBfile' }, caption: { text: 'Фото' } },
        { type: 'buttons', buttons: [{ text: 'Канал', url: 'https://t.me/telegram' }] },
      ],
    },
  };
  await applyJson(post);
  const hiddenInJson = await button.isHidden();
  await visual();
  const before = await body();
  await page.waitForFunction(() => !document.querySelector('.toolbar.on'), null, quick).catch(() => {});
  await shot('to-html-button');
  await button.click();
  await page.waitForFunction(() => document.querySelector('#mode-tabs [data-mode=html]').classList.contains('on'), null, quick).catch(() => {});
  const src = await html();
  await page.waitForFunction((t) => document.querySelector('#preview').shadowRoot.querySelector('.tg-body')?.textContent?.replace(/\s+/g, '') === t, before, quick).catch(() => {});
  const after = await body();
  await shot('to-html-result');
  await showJson();
  const sent = (await state()).rich_message;
  check('«В HTML» puts the visual post into the HTML tab as HTML, the preview stays the same',
    hiddenInJson && src.startsWith('<h2>Экспорт</h2>\n<p>Текст с <b>жирным</b>') && src.includes('<th align="left" valign="top">A</th>')
      && src.includes('<figure><img src="tg://photo?id=photo1"/><figcaption>Фото</figcaption></figure>') && src.includes('<tg-button type="url" url="https://t.me/telegram">Канал</tg-button>')
      && sent.html === src && JSON.stringify(sent.media) === JSON.stringify([{ id: 'photo1', media: { type: 'photo', media: 'AgACAgIAAxkBAAIBfile' } }]) && !sent.blocks
      && before.length > 10 && after === before,
    JSON.stringify({ hiddenInJson, src, sent, before, after }).slice(0, 1500));

  // text of its own in the HTML tab: the studio asks first; «Отмена» keeps it
  await page.click('#mode-tabs [data-mode=html]');
  const mine = src + '\n<p>моя правка</p>';
  await page.fill('#src-html', mine);
  await until(async () => JSON.stringify((await draft())?.payload ?? '').includes('моя правка')); // saved: its own undo step
  await visual();
  await button.click();
  const asked = await dialog.textContent(quick).catch(() => '');
  await shot('to-html-confirm');
  await dialog.getByRole('button', { name: 'Отмена' }).click(quick).catch(() => {});
  await page.click('#mode-tabs [data-mode=html]');
  const kept = await html();
  check('Own text in the HTML tab: the studio\'s question, «Отмена» keeps the text',
    /Заменить текст во вкладке HTML/.test(asked || '') && kept === mine && (await dialog.count()) === 0, JSON.stringify({ asked, kept }));

  // «Заменить» replaces it, and ↶ brings it back
  await visual();
  await button.click();
  await dialog.getByRole('button', { name: 'Заменить' }).click(quick).catch(() => {});
  await page.waitForFunction(() => document.querySelector('#mode-tabs [data-mode=html]').classList.contains('on'), null, quick).catch(() => {});
  const replaced = await html();
  await page.click('#btn-undo');
  await page.waitForFunction(() => /моя правка/.test(/** @type {HTMLTextAreaElement} */ (document.querySelector('#src-html')).value), null, quick).catch(() => {});
  const undone = await html();
  check('«Заменить» puts the HTML of the visual post there, ↶ brings the own text back',
    replaced === src && undone === mine, JSON.stringify({ replaced, undone }));

  // a field HTML has no place for: refused with the list, the HTML tab is not touched, the visual mode stays
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'Карта' }, { type: 'map', location: { latitude: 55.75, longitude: 37.62 }, width: 600, height: 400 }] } });
  await page.click('#mode-tabs [data-mode=html]');
  await page.fill('#src-html', mine);
  await visual();
  await button.click();
  const refusal = await dialog.textContent(quick).catch(() => '');
  await shot('to-html-refused');
  await dialog.getByRole('button', { name: 'Закрыть' }).click(quick).catch(() => {});
  const stayed = await page.locator('#mode-tabs [data-mode=blocks].on').count();
  await page.click('#mode-tabs [data-mode=html]');
  const untouched = await html();
  check('A field HTML cannot hold refuses the export with the list; the HTML tab is not changed',
    /не переведён в HTML/.test(refusal || '') && /блок 2 \(«Карта»\): поле width/.test(refusal || '') && /поле height/.test(refusal || '') && stayed === 1 && untouched === mine,
    JSON.stringify({ refusal, stayed, untouched }));

  // the whole way: HTML → «Мне в личку» → «В визуальный» (Telegram's blocks) → «В HTML»: the same post as Telegram's
  const showcase = captured['example-post-showcase'];
  await page.click('#mode-tabs [data-mode=html]');
  await page.fill('#src-html', showcase.source);
  await page.click('#btn-me');
  await page.waitForSelector('#parse-check >> text=В визуальный', quick).catch(() => {});
  await page.click('#parse-check >> text=В визуальный', quick).catch(() => {});
  await page.waitForSelector('#mode-tabs [data-mode=blocks].on', quick).catch(() => {});
  await button.click();
  await dialog.getByRole('button', { name: 'Заменить' }).click(quick).catch(() => {}); // the tab still holds the source
  await page.waitForFunction(() => document.querySelector('#mode-tabs [data-mode=html]').classList.contains('on'), null, quick).catch(() => {});
  const back = await html();
  // without the line breaks between blocks, which the outline would count as spaces (tests/html_export.mjs)
  const same = await page.evaluate(([h, blocks]) => RichRender.outline({ rich_message: { html: h.replace(/\n */g, '') } }) === RichRender.outline({ rich_message: { blocks } }), [back, showcase.blocks]);
  check('HTML → Telegram\'s blocks → «В HTML» gives the post Telegram made of it', same && back !== showcase.source && (await htmlTab()) === 1, back.slice(0, 300));

  page.off('dialog', onDialog);
  check('«В HTML» uses the studio\'s dialogs, never alert or confirm', native === 0, String(native));
}
