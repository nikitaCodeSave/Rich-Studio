// «Обновить пост» after an agent replaced the same post: the editor says so and replaces it only when confirmed
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, applyJson }) {
  const sent = await page.evaluate(async () => (await fetch('/api/send', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: '@race_me', post: { rich_message: { markdown: 'Исходный' } } }) })).json());
  const { id } = sent.post;
  const mid = sent.message.message_id;
  await page.click('#side-tabs [data-tab=drafts]');
  await page.click('#side-tabs [data-tab=posts]');
  await page.locator('#posts li', { hasText: '#' + mid }).getByRole('button', { name: 'Править' }).click();
  await page.waitForSelector('#edit-bar >> text=Правите пост', { timeout: 5000 }).catch(() => {});
  // the agent edits the post while it is open here
  await page.evaluate(async (pid) => fetch(`/api/posts/${pid}/edit`, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Studio-Client': 'mcp:agent' }, body: JSON.stringify({ rich_message: { markdown: 'От агента' } }) }), id);
  await applyJson({ rich_message: { markdown: 'Из редактора' } });
  const asked = [];
  const onDialog = (d) => asked.push(d.message());
  page.on('dialog', onDialog);
  await page.click('#edit-bar >> text=Обновить пост');
  let edits = [];
  for (let i = 0; i < 50 && edits.length < 2; i++) {
    await page.waitForTimeout(100);
    edits = (await mockLog()).filter((c) => c.method === 'editMessageText' && c.body.message_id === mid);
  }
  page.off('dialog', onDialog);
  const versions = await page.evaluate(async (pid) => (await fetch(`/api/posts/${pid}/versions`)).json(), id);
  check('«Обновить пост» does not overwrite an agent\'s edit silently',
    asked.length === 2 && /изменили в другом месте/.test(asked[1]) && edits.length === 2 && edits[1].body.rich_message.markdown === 'Из редактора'
      && versions.map((v) => v.payload.rich_message.markdown).join('|') === 'От агента|Исходный',
    JSON.stringify({ asked, edits: edits.map((e) => e.body.rich_message.markdown), versions: versions.map((v) => v.payload.rich_message.markdown) }));
}
