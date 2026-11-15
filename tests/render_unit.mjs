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
  // Telegram decodes only &lt; &gt; &amp; &quot; and numeric entities with a small x: &quot without «;», &QUOT; and
  // &#X22; stay as typed, in text and in an address, so such a link is not dropped (measured, PR #173)
  const r = await render({ html: '<p>a &quot b &QUOT; c &#34; d &#x41; <a href="https://example.com/?q=&QUOT;x">раз</a> <a href="https://example.com/?q=&#X22;x">два</a></p>' });
  const ok = has(r.html, /^<p>a &amp;quot b &amp;QUOT; c " d A <a href="https:\/\/example\.com\/\?q=&amp;QUOT;x"[^>]*>раз<\/a> <a href="https:\/\/example\.com\/\?q=&amp;#X22;x"[^>]*>два<\/a><\/p>$/);
  check('only the entities Telegram knows are decoded in HTML', ok, r.html);
}
{
  // all 2125 names of HTML5 sent to Telegram (PR #173): it decodes exactly 16 of them, in html and markdown alike
  const src = '1&nbsp;2 3&mdash;4 5&laquo;6 7&#X22;8 &amp;&apos;&copy;&gt;&hellip;&ldquo;&lsquo;&lt;&ndash;&quot;&rdquo;&reg;&rsquo;&trade; &euro; &shy; &NBSP; &nbsp &#160;&#x2014;';
  const want = '1 2 3—4 5&laquo;6 7&#X22;8 &\'©>…“‘<–"”®’™ &euro; &shy; &NBSP; &nbsp  —';
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
  // Telegram finds a TON address in HTML and Markdown text by itself: EQ…/UQ… of 48 characters, not a card number
  // with spaces (tests/telegram_parse/captured.json: html-detected-card-ton); not in code, links and formulas
  const eq = 'EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c';
  const uq = 'UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKZ';
  const found = async (rm) => { await render(rm); return page.evaluate(() => [...document.querySelectorAll('#out .tg-ton')].map((n) => n.textContent)); };
  const html = await found({ html: `<p>Карта 4242 4242 4242 4242, адрес TON ${eq} и <b>${uq}</b>.</p>` });
  check('html: TON addresses in text are link-coloured, a card number with spaces stays text', JSON.stringify(html) === JSON.stringify([eq, uq]), JSON.stringify(html));
  const md = await found({ markdown: `Адрес ${eq}, ещё **${uq}**` });
  check('markdown: TON addresses in text are found the same way', JSON.stringify(md) === JSON.stringify([eq, uq]), JSON.stringify(md));
  const not = await found({ html: `<p><code>${eq}</code> <a href="https://t.me/x">${eq}</a> <tg-math>${eq}</tg-math> x${eq} ${eq}x ${eq.slice(0, -1)} kQ${eq.slice(2)} 0:${'a'.repeat(64)}</p><pre>${uq}</pre>` });
  check('html: no TON address in code, links, formulas, glued to other letters, of another length or prefix', not.length === 0, JSON.stringify(not));
  const off = await found({ html: `<p>${eq}</p>`, skip_entity_detection: true });
  check('html: skip_entity_detection leaves a TON address as text', off.length === 0, JSON.stringify(off));
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
    { lang: null, copy: true, text: 'без языка', label: 'none', bar: '3px', pre: null },
  ]);
  await render({ html: '<pre><code class="language-python">def f():\n    return 1</code></pre><pre>без языка</pre>' });
  const html = JSON.stringify(await look());
  check('html: a code block has a header with its language and a copy icon, and a bar', html === want, html);
  await render({ blocks: [{ type: 'pre', language: 'python', text: 'def f():\n    return 1' }, { type: 'pre', text: 'без языка' }] });
  // from blocks a line break is a <br>, so textContent has no \n there
  const blocks = JSON.stringify(await look()).replace('def f():    return 1', 'def f():\\n    return 1');
  check('blocks: a code block looks the same', blocks === want, blocks);
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (t) => { window.__copied = t; } } }));
  await page.click('#out .tg-pre-copy');
  const copied = await page.evaluate(() => window.__copied);
  check('the copy icon copies the code with its line breaks', copied === 'def f():\n    return 1', JSON.stringify(copied));
  const outline = await page.evaluate(() => [RichRender.outline({ rich_message: { html: '<pre><code class="language-python">x</code></pre>' } }), RichRender.outline({ rich_message: { blocks: [{ type: 'pre', language: 'python', text: 'x' }] } })]);
  check('outline: a code block is still <pre:language> with its text', outline[0] === '<pre:python>x</pre>' && outline[1] === outline[0], outline.join(' | '));
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

check('no script ran and no page errors', (await page.evaluate(() => window.__xss)) === 0 && pageErrors.length === 0, pageErrors.join(' | '));
await browser.close();
const failed = results.filter((x) => !x).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
