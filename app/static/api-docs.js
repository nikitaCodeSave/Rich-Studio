// Swagger UI on /docs (app/api/docs.py). Kept out of the HTML so the page's CSP can forbid inline scripts.
(function () {
  const w = /** @type {any} */ (window);
  w.ui = w.SwaggerUIBundle({
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    deepLinking: true,
    // the contract is never sent to validator.swagger.io (the badge under the title)
    validatorUrl: null,
    // ?url= or ?configUrl= in a link cannot load someone else's spec into the studio's origin
    queryConfigEnabled: false,
    // a key typed into «Authorize» lives only in this tab, not in localStorage; the editor's cookie works anyway
    persistAuthorization: false,
  });
})();
