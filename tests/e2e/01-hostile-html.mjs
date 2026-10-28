// hostile HTML does not execute and is cleaned up in the preview
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson, alerts, csp, BASE }) {
  const evil = [
    '<a id="x1" href="java&#9;script:alert(1)">tab</a>',
    '<svg><a xlink:href="javascript:alert(2)"><text>svg</text></a><animate attributeName="href" to="javascript:alert(3)"/></svg>',
    '<img src=x onerror="alert(4)">', '<p style="position:fixed;inset:0;background:red">style</p>',
    '<map><area href="javascript:alert(5)"></map>', '<iframe srcdoc="<script>alert(6)</script>"></iframe>',
    '<svg><style>body{display:none}</style></svg>', '<tg-button type="url" url="javascript:alert(7)">b</tg-button>',
    '<form action="javascript:alert(8)"><button>f</button></form>', '<a href="data:text/html,<script>alert(9)</script>">d</a>',
  ].join('\n');
  // the Chromium of Playwright 1.64 reports the CSP block as soon as the sanitizer parses onerror= into its inert
  // <template> (render.js, the tpl.innerHTML line), before the attribute is dropped; DOMParser does the same.
  // Only reports from that very line are the policy working on this hostile input: any other, from inserting
  // a handler into the live page, still fails the final «No CSP violations» check
  const renderSrc = await (await fetch(BASE + '/static/render.js')).text();
  const parseLine = renderSrc.split('\n').findIndex((l) => l.includes('tpl.innerHTML'));
  const parseNoise = [];
  /** @param {import('playwright').ConsoleMessage} m */
  const onConsole = (m) => {
    const at = m.location();
    if (/inline event handler/i.test(m.text()) && at.url.endsWith('/static/render.js') && at.lineNumber === parseLine) parseNoise.push(m.text());
  };
  page.on('console', onConsole);
  const cspBefore = csp.length;
  await page.click('#mode-tabs [data-mode=html]');
  await page.fill('#src-html', evil);
  await page.waitForTimeout(700);
  await page.evaluate(() => document.querySelector('#preview').shadowRoot.querySelectorAll('a, .tg-btn').forEach((a) => a.click()));
  await page.waitForTimeout(500);
  const leftovers = await page.evaluate(() => {
    const p = document.querySelector('#preview').shadowRoot;
    const bad = [];
    p.querySelectorAll('*').forEach((n) => {
      if (n.namespaceURI !== 'http://www.w3.org/1999/xhtml') bad.push('svg:' + n.localName);
      for (const a of n.attributes) {
        if (/^on/i.test(a.name) || a.name === 'style' && !n.closest('.katex') && !n.matches('td,th,.tg-map,.tg-map-tiles') || /javascript:|data:text/i.test(a.value)) bad.push(n.localName + '[' + a.name + ']');
      }
      if (['iframe', 'form', 'area', 'style', 'script'].includes(n.localName)) bad.push(n.localName);
    });
    if (!p.querySelector('.tg-bubble')) bad.push('no preview rendered');
    return bad;
  });
  check('XSS: hostile HTML is neutralised in the preview', alerts() === 0 && leftovers.length === 0, leftovers.join(', '));
  page.off('console', onConsole);
  const fresh = csp.splice(cspBefore);
  for (const t of parseNoise) fresh.splice(fresh.indexOf(t), 1);
  csp.push(...fresh);
  check('CSP: the sanitizer still parses in render.js where the filter expects', parseLine >= 0, String(parseLine));
  const rtUrl = { rich_message: { blocks: [{ type: 'paragraph', text: { type: 'url', text: 'клик', url: 'javascript:alert(10)' } }] } };
  await applyJson(rtUrl);
  await page.click('#mode-tabs [data-mode=blocks]');
  const href = await page.evaluate(() => document.querySelector('#preview').shadowRoot.querySelector('a')?.getAttribute('href'));
  check('XSS: javascript: URL in a blocks link is dropped', !href, String(href));
}
