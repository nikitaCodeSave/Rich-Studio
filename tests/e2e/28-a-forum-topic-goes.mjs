// a forum topic goes with its channel; the channel the editor used to remember moves into an old draft
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, setChat }) {
  const mk = (body) => page.evaluate((b) => fetch('/api/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then((r) => r.json()), body);
  const get = (id) => page.evaluate((x) => fetch('/api/drafts/' + x).then((r) => r.json()), id);
  const open = async (title) => {
    await page.click('#side-tabs [data-tab=drafts]');
    await page.click('#btn-new');
    await page.waitForTimeout(300);
    await page.locator('#drafts li', { hasText: title }).first().click();
    await page.waitForTimeout(500);
  };
  const post = { rich_message: { blocks: [{ type: 'paragraph', text: 'В тему' }] }, options: { message_thread_id: 7 } };
  const t = await mk({ title: 'Тема форума', post, chat_ids: ['@forum_a'] });
  await open('Тема форума');
  await setChat('@forum_a');
  await page.waitForTimeout(400);
  await page.click('#btn-save');
  await page.waitForTimeout(500);
  const kept = (await get(t.id)).payload.options || {};
  await setChat('@other_b');
  await page.waitForTimeout(400);
  await page.click('#btn-save');
  await page.waitForTimeout(500);
  const dropped = (await get(t.id)).payload.options || {};
  const old = await mk({ title: 'Старый черновик', post: { rich_message: { blocks: [{ type: 'paragraph', text: 'Старый' }] } } });
  await page.evaluate(() => localStorage.setItem('rs.chat', JSON.stringify('@legacy_chan')));
  await open('Старый черновик');
  const shown = await page.inputValue('#chat');
  await page.waitForTimeout(3200); // autosave
  const migrated = await get(old.id);
  const leftover = await page.evaluate(() => localStorage.getItem('rs.chat'));
  check('A topic is dropped with another channel; the old remembered channel moves into the draft',
    kept.message_thread_id === 7 && dropped.message_thread_id === undefined && shown === '@legacy_chan' && JSON.stringify(migrated.chat_ids) === '["@legacy_chan"]' && leftover === null,
    JSON.stringify({ kept, dropped, shown, migrated: migrated.chat_ids, leftover }));
}
