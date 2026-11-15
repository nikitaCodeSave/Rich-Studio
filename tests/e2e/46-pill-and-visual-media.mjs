// The check pill next to the mode tabs scrolls to the check and leaves the mode alone; a visual post goes to Telegram
// without the media list of the HTML/Markdown tabs (InputRichMessage.media is for tg:// links in html/markdown), and
// the list stays with the draft for those tabs; a GIF and a voice note go into HTML by the documented tg://video and
// tg://audio links, their kind stays in the media entry
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, until, state, showJson, applyJson }) {
  const quick = { timeout: 5000 };
  const sends = async () => (await mockLog()).filter((c) => c.method === 'sendRichMessage');
  const draft = () => page.evaluate(async () => {
    const id = new URLSearchParams(location.search).get('draft');
    return id ? (await fetch('/api/drafts/' + id)).json() : null;
  });
  const sendToMe = async () => {
    const before = (await sends()).length;
    await page.click('#btn-me');
    return until(async () => (await sends()).slice(before).pop()?.body);
  };

  // ---------- the pill ----------
  await page.click('#btn-new');
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'Пост' }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForSelector('#check-pill:not([hidden])', quick).catch(() => {});
  await page.evaluate(() => scrollTo(0, 0));
  await page.click('#check-pill');
  await page.waitForFunction(() => { const r = document.querySelector('#validation').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }, null, quick).catch(() => {});
  const after = await page.evaluate(() => ({
    on: [...document.querySelectorAll('#mode-tabs button.on')].map((b) => /** @type {HTMLElement} */ (b).dataset.mode || b.id),
    blocksShown: !document.querySelector('#pane-blocks').hidden,
    hint: document.querySelector('#mode-hint').textContent,
    inView: (() => { const r = document.querySelector('#validation').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })(),
  }));
  check('The check pill scrolls to the check and keeps the mode',
    JSON.stringify(after.on) === '["blocks"]' && after.blocksShown && after.hint === 'отправляется как rich_message.blocks' && after.inView, JSON.stringify(after));

  // ---------- media of the HTML tab stay out of a visual post ----------
  const media = [{ id: 'photo1', media: { type: 'photo', media: 'AgACphoto1' } }];
  await applyJson({ rich_message: { html: '<p>С фото</p><img src="tg://photo?id=photo1"/>', media }, _studio: { buffers: { blocks: [{ type: 'paragraph', text: 'Визуальный пост' }] } } });
  await page.click('#mode-tabs [data-mode=blocks]');
  const visual = await sendToMe();
  await showJson();
  const shown = (await state()).rich_message;
  check('A visual post goes without the media list of the HTML tab, and the JSON tab shows it so',
    !!visual && JSON.stringify(visual.rich_message.blocks) === JSON.stringify([{ type: 'paragraph', text: 'Визуальный пост' }]) && !('media' in visual.rich_message)
      && !!shown.blocks && !('media' in shown),
    JSON.stringify({ sent: visual?.rich_message, shown }));

  // the list stays with the draft: after a reload the HTML tab has it and sends it
  await page.click('#mode-tabs [data-mode=blocks]');
  await until(async () => JSON.stringify((await draft())?.payload ?? '').includes('AgACphoto1'));
  await page.reload();
  await page.waitForSelector('.block');
  await page.click('#mode-tabs [data-mode=html]');
  const html = await sendToMe();
  check('The HTML tab keeps its media through a visual draft and a reload, and sends them',
    !!html && html.rich_message.html === '<p>С фото</p><img src="tg://photo?id=photo1"/>' && JSON.stringify(html.rich_message.media) === JSON.stringify(media),
    JSON.stringify(html?.rich_message));

  // ---------- «в пост» for a GIF and a voice note ----------
  await page.fill('#src-html', '');
  await page.click('#side-tabs [data-tab=uploads]');
  await page.setInputFiles('#upload', [
    { name: 'loop.gif', mimeType: 'image/gif', buffer: Buffer.from('GIF89a fake') },
    { name: 'note.ogg', mimeType: 'audio/ogg', buffer: Buffer.from('OggS fake') },
  ]);
  await page.waitForFunction(() => { const n = [...document.querySelectorAll('#uploads .name')].map((x) => x.textContent); return n.includes('loop.gif') && n.includes('note.ogg'); }, null, { timeout: 8000 }).catch(() => {});
  for (const name of ['loop.gif', 'note.ogg']) {
    await page.locator('#uploads li', { has: page.locator('.name', { hasText: name }) }).getByRole('button', { name: 'в пост' }).click(quick).catch(() => {});
  }
  const src = await page.inputValue('#src-html');
  await showJson();
  const rm = (await state()).rich_message;
  const kinds = Object.fromEntries((rm.media || []).map((m) => [m.id, m.media.type]));
  check('«в пост» puts a GIF and a voice note by tg://video and tg://audio links, the kind stays in media',
    src.includes('<video src="tg://video?id=animation1"></video>') && src.includes('<audio src="tg://audio?id=voice1"></audio>') && !/tg:\/\/(animation|voice)/.test(src)
      && kinds.animation1 === 'animation' && kinds.voice1 === 'voice_note',
    JSON.stringify({ src, media: rm.media }));
  await page.click('#side-tabs [data-tab=drafts]');
}
