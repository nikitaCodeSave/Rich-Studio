// table with colspan is not padded just by opening it
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, state, showJson, applyJson }) {
  const span = { rich_message: { blocks: [{ type: 'table', cells: [[{ text: 'Шапка', colspan: 2, is_header: true, align: 'center', valign: 'top' }], [{ text: 'a', align: 'left', valign: 'top' }, { text: 'b', align: 'left', valign: 'top' }]] }] } };
  await applyJson(span);
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForTimeout(300);
  await showJson();
  const t = (await state()).rich_message.blocks[0].cells;
  check('Table with colspan keeps its shape', t[0].length === 1 && t[1].length === 2, JSON.stringify(t.map((r) => r.length)));
}
