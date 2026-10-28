// options cannot redirect a post to another chat
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  const r = await page.evaluate(async () => (await fetch('/api/send', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ post: { rich_message: { html: '<p>x</p>' }, options: { chat_id: '@other' } } }) })).status);
  check('options.chat_id is refused', r === 400, String(r));
}
