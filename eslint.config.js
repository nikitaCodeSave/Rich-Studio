// ESLint for the editor (ES modules), the classic preview scripts and the Node test scripts.
// Catches what the browser only reports at runtime: undefined names, missing imports, unused code.
import js from '@eslint/js';
import globals from 'globals';

const rules = {
  ...js.configs.recommended.rules,
  'no-unused-vars': ['error', { args: 'none', caughtErrors: 'none', ignoreRestSiblings: true }],
  eqeqeq: ['error', 'smart'],
  'no-var': 'error',
  'prefer-const': ['error', { destructuring: 'all' }],
  'no-implicit-globals': 'error',
};

export default [
  { ignores: ['app/static/vendor/**', 'node_modules/**', 'data/**', '.venv/**'] },
  {
    files: ['app/static/js/**/*.js'],
    languageOptions: {
      sourceType: 'module',
      globals: { ...globals.browser, RichRender: 'readonly' },
    },
    rules: {
      ...rules,
      // user data reaches the DOM only through h()/textContent or RichRender's sanitizer
      'no-restricted-properties': ['error',
        { property: 'innerHTML', message: 'Use h() or RichRender (sanitized).' },
        { property: 'outerHTML', message: 'Use h() or RichRender (sanitized).' }],
      'no-restricted-syntax': ['error', { selector: "CallExpression[callee.property.name='insertAdjacentHTML']", message: 'Use h().' }],
    },
  },
  {
    // one place talks to the server: headers, API key, CSRF and errors are handled in api.js
    files: ['app/static/js/**/*.js'],
    ignores: ['app/static/js/api.js'],
    rules: {
      'no-restricted-globals': ['error', { name: 'fetch', message: "Use api() from './api.js'." }, { name: 'XMLHttpRequest', message: "Use api() from './api.js'." }],
    },
  },
  {
    files: ['app/static/*.js'],
    languageOptions: {
      sourceType: 'script',
      globals: { ...globals.browser, RichRender: 'writable', katex: 'readonly', markdownit: 'readonly' },
    },
    rules,
  },
  {
    files: ['tests/**/*.mjs', 'scripts/**/*.mjs', 'eslint.config.js'],
    // page.evaluate() callbacks in the browser checks run in the page
    languageOptions: { sourceType: 'module', globals: { ...globals.node, ...globals.browser } },
    rules,
  },
];
