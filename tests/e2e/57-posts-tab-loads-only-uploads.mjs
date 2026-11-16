// «Посты» sends the browser only to /media/ and /api/ (#204, #213). Old records may hold any name after upload:
// (the API refuses such names since #210): here they go straight into the stand's database. A thumbnail the server
// would give all the same goes into <img> only as a file of the uploads (posts.js): such ones are put into the answer
// of GET /api/posts.
import { existsSync } from 'node:fs';

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, BASE }) {
  const origin = new URL(BASE).origin;
  // a real upload whose name has «..» in it, as free_name allows: its thumbnail stays, and its load is the event
  const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
  const form = new FormData();
  form.append('file', new Blob([Buffer.from(png, 'base64')], { type: 'image/png' }), 'e2e..thumb.png');
  const up = await fetch(BASE + '/api/media', { method: 'POST', body: form }).then((r) => r.json());
  const now = Math.floor(Date.now() / 1000);
  const run = Date.now().toString(36);
  const titles = [];

  // the stand's data folder, from scripts/e2e_local.sh: without it the old records would be left out unnoticed
  const dbFile = process.env.DATA_DIR + '/studio.db';
  // and it is this stand's: the file just uploaded through BASE lies in its media folder, so a DATA_DIR of another
  // studio never gets the fake «sent» records below
  const haveDb = !!process.env.DATA_DIR && existsSync(dbFile) && existsSync(process.env.DATA_DIR + '/media/' + up.name);
  check('The stand\'s database is there for old records', haveDb, `DATA_DIR=${process.env.DATA_DIR || ''} (scripts/e2e_local.sh gives it)`);
  if (haveDb) {
    const { DatabaseSync } = await import('node:sqlite');
    const db = new DatabaseSync(dbFile);
    db.exec('pragma busy_timeout = 5000');
    const add = db.prepare("insert into posts(id, chat_id, message_id, payload, status, sent_at, created_at, kind) values(?, '@test', ?, ?, 'sent', ?, ?, 'rich')");
    ['../api/export', 'a/../../api/export', '%2e%2e/api/export', '..', '/etc/passwd', 'missing.png', up.name].forEach((name, i) => {
      const title = `e2e-213 ${run} db ${i}`;
      const blocks = [{ type: 'heading', size: 1, text: title }, { type: 'photo', photo: { type: 'photo', media: 'upload:' + name } }];
      add.run(`e2e213-${run}-${i}`, 9000 + i, JSON.stringify({ rich_message: { blocks } }), now, now);
      titles.push(title);
    });
    db.close();
  }

  // what a server could give for such records: the browser resolves each to /api/export or to another site
  const given = ['/media/../api/export', '/media/%2e%2e/api/export', '/media/.%2E/api/export', '/media\\..\\api\\export',
    '/media/x/../../api/export', '/media/\t../api/export', '/api/export', '//evil.example/x.png', 'https://evil.example/x.png', up.url];
  const fakes = given.map((thumb, i) => {
    const title = `e2e-213 ${run} api ${i}`;
    titles.push(title);
    return { id: `e2e213-${run}-fake-${i}`, chat_id: '@test', status: 'sent', kind: 'rich', title, thumb, created_at: now + 1, sent_at: now + 1, buttons: {}, pinned: false };
  });
  /** @param {import('playwright').Route} route */
  const withFakes = async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, json: [...fakes, ...(await r.json())] });
  };
  const requests = [];
  /** @param {import('playwright').Request} r */
  const seen = (r) => requests.push(r.url());
  await page.route('**/api/posts', withFakes);
  page.on('request', seen);
  try {
    // every post, read once by the tab: the filter's own change event would load the list a second time
    await page.evaluate(() => { /** @type {HTMLSelectElement} */ (document.querySelector('#posts-filter')).value = ''; });
    await page.click('#side-tabs [data-tab=posts]');
    // every row is there and the upload's own thumbnail has loaded
    await page.waitForFunction(({ titles, url }) => {
      const text = document.querySelector('#posts')?.textContent || '';
      const good = [...document.querySelectorAll('#posts img.thumb')].filter((i) => i.getAttribute('src') === url);
      return titles.every((t) => text.includes(t)) && good.length > 0 && good.every((i) => i.complete && i.naturalWidth > 0);
    }, { titles, url: up.url }, { timeout: 8000 }).catch(() => {});
    // where every thumbnail goes, as the browser resolves its src: also a lazy one that has not loaded yet
    const srcs = await page.evaluate(() => [...document.querySelectorAll('#posts img')].map((i) => /** @type {HTMLImageElement} */ (i).src));
    const notUpload = srcs.filter((u) => { const x = new URL(u); return x.origin !== origin || !x.pathname.startsWith('/media/'); });
    const good = await page.locator(`#posts img[src="${up.url}"]`).count();
    check('«Посты» puts a thumbnail into <img> only as a file of the uploads', notUpload.length === 0 && good === (haveDb ? 2 : 1),
      JSON.stringify({ notUpload, good }));
    const outside = requests.filter((u) => { const x = new URL(u); return x.origin !== origin || !/^\/(media|api)\//.test(x.pathname); });
    check('«Посты» makes no requests outside /media/ and /api/', outside.length === 0, JSON.stringify(outside));
  } finally {
    page.off('request', seen);
    await page.unroute('**/api/posts', withFakes);
    await page.click('#side-tabs [data-tab=drafts]');
  }
}
