// Live Photo and paid media: the «Опубликовать ▾» dialog sends sendLivePhoto and sendPaidMedia with uploaded files
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, setChat }) {
  await page.click('#side-tabs [data-tab=uploads]');
  await page.setInputFiles('#upload', [
    { name: 'clip.mp4', mimeType: 'video/mp4', buffer: Buffer.from('fake mp4') },
    { name: 'still.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('fake jpg') },
  ]);
  await page.waitForTimeout(800);
  await setChat('@media_chan');
  await page.click('#btn-more');
  await page.click('#btn-media-post');
  await page.selectOption('dialog[open] select[aria-label="Видео"]', 'upload:clip.mp4');
  await page.selectOption('dialog[open] select[aria-label="Кадр"]', 'upload:still.jpg');
  await page.fill('dialog[open] textarea[aria-label="Подпись"]', 'Закат над морем');
  await page.screenshot({ path: 'test-results/ui/live-photo-1400.png' }).catch(() => {});
  await page.locator('dialog[open] button', { hasText: 'Отправить' }).click();
  await page.waitForTimeout(800);
  const live = (await mockLog()).filter((c) => c.method === 'sendLivePhoto').pop();
  await page.click('#btn-more');
  await page.click('#btn-media-post');
  await page.selectOption('dialog[open] select[aria-label="Что отправить"]', 'paid_media');
  await page.fill('dialog[open] input[aria-label="Цена в звёздах"]', '25');
  await page.selectOption('dialog[open] select[aria-label="Файл"]', 'upload:still.jpg');
  await page.locator('dialog[open] button', { hasText: '＋ ещё файл' }).click();
  await page.selectOption('dialog[open] select[aria-label="Что в №2"]', 'live_photo');
  await page.selectOption('dialog[open] .pf select[aria-label="Видео"]', 'upload:clip.mp4');
  await page.selectOption('dialog[open] .pf select[aria-label="Кадр"]', 'upload:still.jpg');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: 'test-results/ui/paid-media-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.locator('dialog[open] button', { hasText: 'Отправить' }).click();
  await page.waitForTimeout(800);
  const paid = (await mockLog()).filter((c) => c.method === 'sendPaidMedia').pop();
  const listed = await page.locator('#posts li', { hasText: 'Закат над морем' }).count().catch(() => 0);
  await setChat('@test');
  const media = paid && JSON.parse(typeof paid.body.media === 'string' ? paid.body.media : JSON.stringify(paid.body.media));
  check('Live Photo and paid media go out with the uploaded files',
    live && live.body.chat_id === '@media_chan' && /^attach:\/\//.test(live.body.live_photo) && /^attach:\/\//.test(live.body.photo) && live.body.caption === 'Закат над морем'
      && paid && paid.body.star_count === '25' && media.length === 2 && media[0].type === 'photo' && media[1].type === 'live_photo' && /^attach:\/\//.test(media[1].photo)
      && listed >= 1,
    JSON.stringify({ live: live && live.body, paid: paid && paid.body, listed }));
}
