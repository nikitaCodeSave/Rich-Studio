// Post preview inside an MCP host (Claude chat): the view of the preview_post tool, served as ui://rich-studio/preview.
// Talks to the host over postMessage JSON-RPC (MCP Apps). Has no network access: upload:<name> media shows as a stub.
(function () {
  // both are in the page preview_app.py builds
  const root = /** @type {HTMLElement} */ (document.getElementById('root'));
  const notes = /** @type {HTMLElement} */ (document.getElementById('notes'));
  const pending = new Map();
  let nextId = 1;

  function send(msg) {
    window.parent.postMessage({ jsonrpc: '2.0', ...msg }, '*');
  }

  function request(method, params) {
    const id = nextId++;
    send({ id, method, params });
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }

  function applyTheme(ctx) {
    if (ctx && ctx.theme) root.classList.toggle('tg-dark', ctx.theme === 'dark');
  }

  function reportSize() {
    // the content's own size, not the frame's: the host grows or shrinks the frame to it
    const box = document.documentElement.getBoundingClientRect();
    send({ method: 'ui/notifications/size-changed', params: { width: Math.ceil(box.width), height: Math.ceil(box.height) } });
  }

  function toolData(result) {
    if (result.structuredContent) return result.structuredContent;
    const text = (result.content || []).find((c) => c.type === 'text');
    try {
      return text ? JSON.parse(text.text) : null;
    } catch {
      return null;
    }
  }

  function line(cls, text) {
    const div = document.createElement('div');
    div.className = cls;
    div.textContent = text;
    return div;
  }

  function show(result) {
    const data = toolData(result);
    root.replaceChildren();
    notes.replaceChildren();
    if (result.isError || !data || !data.post) {
      const text = (result.content || []).map((c) => c.text || '').join('\n');
      notes.append(line('note error', text || 'Нет поста для предпросмотра'));
    } else {
      root.append(RichRender.render(data.post, {}));
      const v = data.validation || {};
      for (const e of v.errors || []) notes.append(line('note error', e));
      for (const w of v.warnings || []) notes.append(line('note warning', w));
    }
    requestAnimationFrame(reportSize);
  }

  window.addEventListener('message', (e) => {
    if (e.source !== window.parent) return;
    const msg = e.data;
    if (!msg || msg.jsonrpc !== '2.0') return;
    if (msg.id != null && pending.has(msg.id) && !msg.method) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(msg.error);
      else p.resolve(msg.result);
      return;
    }
    if (msg.method === 'ui/notifications/tool-result') show(msg.params || {});
    else if (msg.method === 'ui/notifications/host-context-changed') applyTheme(msg.params);
  });

  request('ui/initialize', {
    protocolVersion: '2025-06-18',
    appInfo: { name: 'rich-studio-preview', version: '1' },
    appCapabilities: {},
  }).then((res) => {
    applyTheme(res && res.hostContext);
    send({ method: 'ui/notifications/initialized', params: {} });
    new ResizeObserver(reportSize).observe(document.body);
  });
})();
