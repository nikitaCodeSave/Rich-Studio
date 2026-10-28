// MCP App: the ui:// preview page renders a preview_post result inside a sandboxed host frame
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ browser, check, BASE }) {
  const mcp = async (method, params) => (await (await fetch(BASE + '/mcp', {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', 'mcp-protocol-version': '2025-06-18' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
  })).json()).result;
  const appPost = { rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Из чата' }, { type: 'paragraph', text: ['Привет, ', { type: 'bold', text: 'мир' }] }] } };
  const toolResult = await mcp('tools/call', { name: 'preview_post', arguments: { post: appPost } });
  const appHtml = (await mcp('resources/read', { uri: 'ui://rich-studio/preview' })).contents[0].text;
  // the sandbox a host gives the view: inline code only, no network
  const hostCsp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'";
  const host = await browser.newPage();
  const appErrors = [];
  host.on('pageerror', (e) => appErrors.push(e.message));
  host.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource|font/i.test(m.text())) appErrors.push(m.text()); });
  await host.setContent('<iframe id="app" sandbox="allow-scripts" style="width:420px;height:300px;border:0"></iframe>');
  const hostLog = await host.evaluate(({ html, csp, result }) => new Promise((resolve) => {
    const frame = document.getElementById('app');
    const log = [];
    window.addEventListener('message', (e) => {
      if (e.source !== frame.contentWindow) return;
      const m = e.data;
      log.push(m.method || 'response');
      const reply = (msg) => frame.contentWindow.postMessage({ jsonrpc: '2.0', ...msg }, '*');
      if (m.method === 'ui/initialize') {
        reply({ id: m.id, result: { protocolVersion: '2025-06-18', hostInfo: { name: 'e2e', version: '1' }, hostCapabilities: {}, hostContext: { theme: 'dark' } } });
      } else if (m.method === 'ui/notifications/initialized') {
        reply({ method: 'ui/notifications/tool-result', params: result });
      } else if (m.method === 'ui/notifications/size-changed' && log.includes('ui/notifications/initialized') && m.params.height > 0) {
        resolve({ log, size: m.params });
      }
    });
    frame.srcdoc = html.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="${csp}">`);
    setTimeout(() => resolve({ log, timeout: true }), 5000);
  }), { html: appHtml, csp: hostCsp, result: toolResult });
  const appFrame = host.frames().find((f) => f !== host.mainFrame());
  const appView = await appFrame.evaluate(() => ({
    dark: document.getElementById('root').classList.contains('tg-dark'),
    bubble: document.querySelectorAll('.tg-bubble').length,
    text: document.getElementById('root').textContent,
    notes: document.getElementById('notes').textContent,
  }));
  check('MCP App preview renders the tool result in a sandboxed frame',
    !hostLog.timeout && hostLog.log[0] === 'ui/initialize' && hostLog.size.height < 300 && appView.dark && appView.bubble > 0 && /Из чата/.test(appView.text) && /мир/.test(appView.text) && !appView.notes && appErrors.length === 0,
    JSON.stringify({ hostLog, appView, appErrors }));
  await host.close();
}
