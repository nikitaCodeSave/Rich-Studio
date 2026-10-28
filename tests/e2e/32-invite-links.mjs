// invite links: a named link from the «Каналы» dialog, joins through it counted from chat_member
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, push }) {
  await page.click('#btn-check');
  await page.locator('dialog[open] .channel', { hasText: '@kept_chan' }).locator('button', { hasText: 'Ссылки' }).click();
  await page.waitForTimeout(400);
  const empty = await page.locator('dialog[open] .links').textContent();
  await page.fill('dialog[open] input[placeholder^="Название"]', 'Хабр');
  await page.fill('dialog[open] input[aria-label="Предел вступлений"]', '10');
  await page.locator('dialog[open] button', { hasText: 'Создать ссылку' }).click();
  await page.waitForTimeout(500);
  const sent = (await mockLog()).filter((c) => c.method === 'createChatInviteLink').pop();
  const link = sent && (await page.locator('dialog[open] .invite-link code').first().textContent());
  await push({
    chat_member: {
      chat: { id: -1001, type: 'channel' }, from: { id: 7 }, date: Math.floor(Date.now() / 1000),
      old_chat_member: { status: 'left', user: { id: 7 } }, new_chat_member: { status: 'member', user: { id: 7 } },
      invite_link: { invite_link: link, creator: { id: 42 }, is_primary: false, is_revoked: false },
    },
  });
  let counted = '';
  for (let i = 0; i < 20 && !/вступили: 1/.test(counted); i++) {
    await page.waitForTimeout(300);
    counted = await page.evaluate(() => fetch('/api/invite-links').then((r) => r.text()));
  }
  // the dialog shows the new count when opened again
  await page.keyboard.press('Escape');
  await page.locator('dialog[open] .channel', { hasText: '@kept_chan' }).locator('button', { hasText: 'Ссылки' }).click();
  await page.waitForTimeout(400);
  const row = await page.locator('dialog[open] .invite-link').first().textContent();
  await page.selectOption('dialog[open] select[aria-label="Вид ссылки"]', 'subscription');
  const priceShown = await page.locator('dialog[open] input[aria-label="Цена подписки"]').isVisible();
  const limitHidden = await page.locator('dialog[open] input[aria-label="Предел вступлений"]').isHidden();
  await page.selectOption('dialog[open] select[aria-label="Вид ссылки"]', 'invite');
  await page.screenshot({ path: 'test-results/ui/invite-links-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: 'test-results/ui/invite-links-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  check('Invite links: a named link is made and counts who joined',
    /Ссылок пока нет/.test(empty) && sent && sent.body.name === 'Хабр' && sent.body.member_limit === 10 && sent.body.chat_id === '@kept_chan'
      && /вступили: 1/.test(row) && /Хабр/.test(row) && /до 10 человек/.test(row) && priceShown && limitHidden,
    JSON.stringify({ empty, sent: sent && sent.body, row, priceShown, limitHidden }));
}
