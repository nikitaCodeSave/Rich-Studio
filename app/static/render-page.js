// Standalone preview page (/render/..., /api/render). Kept out of the HTML so the page's CSP can forbid inline scripts.
(function () {
  const data = document.getElementById('payload');
  const theme = data.dataset.theme;
  const root = document.getElementById('root');
  const dark = theme === 'dark' || (theme === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
  root.classList.toggle('tg-dark', dark);
  root.append(RichRender.render(JSON.parse(data.textContent), { mediaBase: location.origin }));
})();
