// The studio's own theme chosen in the header (#ui-theme), set on <html> before the page is drawn: a reload does not
// flash the system's theme first. The editor keeps the choice under the same key (store 'uiTheme', main.js);
// without one the page follows the system's (app.css).
(() => {
  try {
    const theme = JSON.parse(localStorage.getItem('rs.uiTheme') || 'null');
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  } catch { /* no storage: the system's theme */ }
})();
