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
    // a read by the editor's cookie without X-Studio-Client is refused under a name from ALLOWED_HOSTS over plain http
    // (security.py, #215); «Try it out» names itself unless the operation's own X-Studio-Client field is filled in
    requestInterceptor: (/** @type {{headers?: Record<string, string>}} */ req) => {
      const headers = (req.headers ||= {});
      if (!Object.keys(headers).some((k) => k.toLowerCase() === 'x-studio-client')) headers['X-Studio-Client'] = 'docs';
      return req;
    },
  });
})();
