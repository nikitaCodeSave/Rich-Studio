// the header stays one row on a wide screen: «Опубликовать» is not pushed to a second line
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.fill('#title', 'Довольно длинное название черновика');
  const header = await page.evaluate(() => {
    const at = (sel) => document.querySelector(sel).getBoundingClientRect();
    return { pub: Math.round(at('#btn-publish').top), brand: Math.round(at('.brand').top), right: Math.round(at('#btn-publish').right), width: innerWidth };
  });
  check('Header fits one row at 1440px', Math.abs(header.pub - header.brand) < 12 && header.right <= header.width, JSON.stringify(header));
}
