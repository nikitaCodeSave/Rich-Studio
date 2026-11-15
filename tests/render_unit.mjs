// Unit checks for the preview renderer and its HTML sanitizer (app/static/render.js).
// No studio needed: render.js is loaded into a blank page without CSP, so any handler
// that slips through the sanitizer really runs and flips window.__xss.
// Run: node tests/render_unit.mjs (CHROMIUM=/path/to/chromium to use a preinstalled browser)
/* global RichRender -- defined by render.js inside the page */
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const stat = (f) => fileURLToPath(new URL('../app/static/' + f, import.meta.url));
const results = [];
const check = (name, ok, info = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !info ? '' : '  ' + info}`); };

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message));
page.on('dialog', (d) => { pageErrors.push('dialog: ' + d.message()); d.dismiss(); });
// tg.css gives the slideshow its layout, so scrolling and dragging it can be checked
await page.setContent('<!doctype html><meta charset="utf-8"><body><div id="out" class="tg-preview"></div></body>');
await page.addStyleTag({ path: stat('tg.css') });
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
  const r = await render({ blocks: [{ type: 'paragraph', text: { type: 'url', text: 'клик', url: 'javascript:window.__xss=1' } }] });
  check('blocks: javascript: URL in a url entity has no href', !has(r.html, /href=/), r.html);
}
{
  // ton_address (Bot API 10.4): the reader sees the text as a link, the address goes only into the title
  const address = 'EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c" onclick="window.__xss=1';
  const r = await render({ blocks: [{ type: 'paragraph', text: ['TON ', { type: 'ton_address', text: 'EQAA…M9c', address }] }] });
  const clicked = await page.evaluate(async () => { document.querySelector('#out .tg-body .tg-link')?.click(); await new Promise((r) => setTimeout(r, 50)); return window.__xss; });
  const ok = has(r.html, /<p>TON <span class="tg-link" title="Адрес TON: EQAA[^"]*">EQAA…M9c<\/span><\/p>/) && !has(r.html, /tg-unknown|\son\w+="/);
  check('blocks: ton_address is link-coloured text with the address in its title, no handler', ok && !r.xss && !clicked, r.html);
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

// ---------- attributes ----------
{
  const r = await render({ html: '<p style="position:fixed" class="evil" id="x" data-x="1" title="ok">t</p><span class="tg-spoiler">s</span>' });
  check('drops style, id, data-*, foreign classes; keeps title', !has(r.html, /style=|id="x"|data-x|class="evil"/) && has(r.html, /title="ok"/), r.html);
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

// ---------- video / animation muted ----------
{
  await render({ blocks: [{ type: 'animation', animation: { type: 'animation', media: 'upload:a.gif' } }, { type: 'video', video: { type: 'video', media: 'upload:v.mp4' } }] });
  const m = await page.evaluate(() => [...document.querySelectorAll('#out video')].map((v) => [v.muted, v.defaultMuted]));
  check('video and animation are muted as a property, not only an attribute', m.length === 2 && m.every(([a, b]) => a === true && b === true), JSON.stringify(m));
  await render({ blocks: [{ type: 'slideshow', blocks: [photo('a.png')] }] });
  const one = await page.evaluate(() => { const t = document.querySelector('#out .tg-slides'); return [t.hasAttribute('tabindex'), t.hasAttribute('aria-label')]; });
  check('slideshow of one slide is not focusable and has no arrows hint', !one[0] && !one[1], one.join());
}

check('no script ran and no page errors', (await page.evaluate(() => window.__xss)) === 0 && pageErrors.length === 0, pageErrors.join(' | '));
await browser.close();
const failed = results.filter((x) => !x).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
