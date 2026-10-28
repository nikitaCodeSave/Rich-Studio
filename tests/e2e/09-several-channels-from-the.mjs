// several channels from the header field
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, setChat, applyJson }) {
  await applyJson({ rich_message: { html: '<p>Кросспост</p>' } });
  await setChat('@chan_a, @chan_b');
  await page.click('#btn-publish');
  await page.waitForTimeout(800);
  const sentTo = (await mockLog()).filter((c) => c.method === 'sendRichMessage').slice(-2).map((c) => c.body.chat_id);
  check('Publishing to two channels from one draft', JSON.stringify(sentTo) === '["@chan_a","@chan_b"]', JSON.stringify(sentTo));
  await setChat('@my_channel');
}
