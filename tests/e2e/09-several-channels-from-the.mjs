// several channels from the header field
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, setChat, applyJson, until }) {
  const sends = async () => (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  await applyJson({ rich_message: { html: '<p>Кросспост</p>' } });
  await setChat('@chan_a, @chan_b');
  const before = await sends();
  await page.click('#btn-publish');
  await until(async () => (await sends()) >= before + 2);
  const sentTo = (await mockLog()).filter((c) => c.method === 'sendRichMessage').slice(-2).map((c) => c.body.chat_id);
  check('Publishing to two channels from one draft', JSON.stringify(sentTo) === '["@chan_a","@chan_b"]', JSON.stringify(sentTo));
  await setChat('@my_channel');
}
