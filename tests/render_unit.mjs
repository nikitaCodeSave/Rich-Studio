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
await page.setContent('<!doctype html><meta charset="utf-8"><body><div id="out"></div></body>');
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
  const r = await render({ blocks: [{ type: 'paragraph', text: { type: 'url', text: 'клик', url: 'javascript:window.__xss=1' } }] });
  check('blocks: javascript: URL in a url entity has no href', !has(r.html, /href=/), r.html);
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

check('no script ran and no page errors', (await page.evaluate(() => window.__xss)) === 0 && pageErrors.length === 0, pageErrors.join(' | '));
await browser.close();
const failed = results.filter((x) => !x).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
