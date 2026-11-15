#!/usr/bin/env node
// CodeMirror 6 for the editor's HTML, Markdown and JSON tabs, vendored without a bundler (docs/decisions/0016).
// From ROOTS it follows the static imports and copies the ES module entry of every package it reaches as npm ships it,
// byte for byte, except that bare imports become relative ('@codemirror/state' -> './state.js'), plus a one-line header.
// Everything goes to app/static/vendor/ under names that start with "codemirror" (the script owns them all):
//   codemirror-<hash>/  a file per package, manifest.json (versions, sha256, imports) and LICENSE; <hash> is of the
//                       content, so a new set of versions gets new URLs and the browser cache never mixes two sets
//   codemirror.js       the one module the editor imports: each root package as a namespace (cm.view.EditorView)
//   codemirror.d.ts     its types for tsc, from the same packages in node_modules
//
//   node scripts/vendor_codemirror.mjs           write, from node_modules as package-lock.json pins it (make vendor)
//   node scripts/vendor_codemirror.mjs --check   build in memory and compare byte for byte, extra files too (make check)
//   node scripts/vendor_codemirror.mjs --update  newest version of every package of the set, then write (make vendor-update)
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

const REPO = fileURLToPath(new URL('..', import.meta.url));
const NODE_MODULES = join(REPO, 'node_modules');
const VENDOR = join(REPO, 'app/static/vendor');
const OWNED = 'codemirror'; // every entry of VENDOR whose name starts with it is written (and removed) by this script

// what the editor imports, as namespaces of codemirror.js; whatever they import comes along
const ROOTS = [
  '@codemirror/state', '@codemirror/view', '@codemirror/commands', '@codemirror/language',
  '@codemirror/autocomplete', '@codemirror/search',
  '@codemirror/lang-html', '@codemirror/lang-markdown', '@codemirror/lang-json',
  '@lezer/common', '@lezer/highlight',
];
const LICENSE = 'MIT'; // LICENSE in the directory carries the notices; another license is a decision, not an update
const HEADER = (pj, from) => `// ${pj.name}@${pj.version} ${from} (${pj.license}) as npm ships it, imports made relative by scripts/vendor_codemirror.mjs: do not edit, run make vendor\n`;

// import … from '…', export … from '…' and import '…' at the start of a statement: rollup output and the hand-written
// helper packages keep them on top. LEFTOVER is looser (anything up to the next ';' before from) and runs after the
// rewrite: whatever it still finds is an import IMPORT missed. tests/codemirror_vendor.mjs then loads the set in Node
// with every bare import refused, so V8's own parser has the last word.
const IMPORT = /(^|[;}\n])(\s*(?:import\s*|(?:import|export)\b[\s\w$*{},]*?\bfrom\s*))(['"])([^'"\n]*)\3/g;
const LEFTOVER = /(^|[;}\n])\s*(?:import\s*|(?:import|export)\b[^;]*?\bfrom\s*)(['"])([^'"\n]*)\2/g;
const DYNAMIC = /\bimport\s*\(/;

function die(message) {
  console.error(`vendor_codemirror: ${message}`);
  process.exit(1);
}
const readJson = (path) => JSON.parse(readFileSync(path, 'utf8'));
const sha256 = (text) => createHash('sha256').update(text).digest('hex');

// file in the flat directory: @codemirror/view -> view.js, @lezer/common -> lezer-common.js, crelt -> crelt.js
const fileOf = (name) => (name.startsWith('@lezer/') ? 'lezer-' : '') + name.replace(/^@[^/]+\//, '') + '.js';
// namespace in codemirror.js: @codemirror/lang-html -> langHtml, @lezer/highlight -> lezerHighlight
const namespaceOf = (name) => fileOf(name).slice(0, -3).replace(/-(\w)/g, (_, c) => c.toUpperCase());
const packageOf = (spec) => spec.split('/').slice(0, spec.startsWith('@') ? 2 : 1).join('/');

// the ES module entry of package.json: exports['.'] by the conditions a browser meets, then "module"
const CONDITIONS = ['browser', 'import', 'module', 'default'];
function pick(target) {
  if (typeof target === 'string') return target;
  if (!target || typeof target !== 'object' || Array.isArray(target)) return undefined;
  for (const [condition, sub] of Object.entries(target)) {
    const found = CONDITIONS.includes(condition) && pick(sub);
    if (found) return found;
  }
  return undefined;
}
function entryOf(pj) {
  let exp = pj.exports;
  if (exp && typeof exp === 'object' && Object.keys(exp).some((k) => k.startsWith('.'))) exp = exp['.'];
  const from = (pick(exp) ?? pj.module ?? '').replace(/^\.\//, '');
  if (!/\.m?js$/.test(from)) die(`${pj.name}: no ES module entry in its package.json (exports or module)`);
  return from;
}

// every package reachable from ROOTS: name -> {pj, dir, from, body, imports}
function collect() {
  if (!existsSync(NODE_MODULES)) die('no node_modules: run npm ci');
  const pkgs = new Map();
  const queue = [...ROOTS];
  while (queue.length) {
    const name = queue.shift();
    if (pkgs.has(name)) continue;
    const dir = join(NODE_MODULES, name);
    if (!existsSync(join(dir, 'package.json'))) die(`${name} is not in node_modules: run npm ci`);
    const pj = readJson(join(dir, 'package.json'));
    const from = entryOf(pj);
    const raw = readFileSync(join(dir, from));
    const src = raw.toString('utf8');
    if (!Buffer.from(src, 'utf8').equals(raw)) die(`${name}/${from} is not UTF-8: it would not be copied byte for byte`);
    if (DYNAMIC.test(src)) die(`${name}/${from} has a dynamic import(): copying its entry would not bring what it loads`);
    const imports = [];
    const body = src.replace(IMPORT, (match, start, head, quote, spec) => {
      if (packageOf(spec) !== spec || spec.startsWith('.') || spec.includes(':')) {
        die(`${name}/${from} imports '${spec}': only whole packages can be vendored, its entry must be one file`);
      }
      if (!imports.includes(spec)) imports.push(spec);
      queue.push(spec);
      return start + head + quote + './' + fileOf(spec) + quote;
    });
    pkgs.set(name, { pj, dir, from, body: HEADER(pj, from) + body, imports });
  }
  const files = new Map([...pkgs].map(([name]) => [fileOf(name), name]));
  if (files.size !== pkgs.size) die(`two packages map to one file name: ${[...pkgs.keys()].join(', ')}`);
  for (const [name, p] of pkgs) {
    for (const [, , , spec] of p.body.matchAll(LEFTOVER)) {
      if (!spec.startsWith('./') || !files.has(spec.slice(2))) die(`${name}/${p.from}: import '${spec}' is left as it was: the browser cannot load it`);
    }
  }
  return new Map([...pkgs].sort(([a], [b]) => (a < b ? -1 : 1)));
}

// what keeps the copy honest: one version per package, the one package-lock.json pins and package.json names exactly, MIT
function verify(pkgs) {
  const lock = readJson(join(REPO, 'package-lock.json')).packages;
  const dev = readJson(join(REPO, 'package.json')).devDependencies ?? {};
  const problems = [];
  const pin = [];
  for (const [name, { pj, dir }] of pkgs) {
    if (lock[`node_modules/${name}`]?.version !== pj.version) {
      problems.push(`${name}: node_modules has ${pj.version}, package-lock.json ${lock[`node_modules/${name}`]?.version ?? 'nothing'}: run npm ci`);
    }
    for (const [path, entry] of Object.entries(lock)) {
      if (path.endsWith(`/node_modules/${name}`)) problems.push(`${name}: a second copy ${entry.version} in ${path}: the page would load two, align the versions`);
    }
    if (dev[name] !== pj.version) pin.push(`${name}@${pj.version}`);
    if (pj.license !== LICENSE) problems.push(`${name}: license ${pj.license}, only ${LICENSE} is vendored`);
    if (!licenseFile(dir)) problems.push(`${name}: no LICENSE file to copy`);
    if (ROOTS.includes(name) && !(pj.types || pj.typings)) problems.push(`${name}: no types for tsc (codemirror.d.ts)`);
  }
  if (pin.length) problems.push(`not exact devDependencies: npm install --save-dev --save-exact ${pin.join(' ')}`);
  if (problems.length) die(problems.join('\n  '));
}

function licenseFile(dir) {
  const file = readdirSync(dir).find((f) => /^licen[cs]e(\.md|\.txt)?$/i.test(f));
  return file && readFileSync(join(dir, file), 'utf8').trim();
}

// the generated files, path relative to VENDOR -> content
function render(pkgs) {
  const set = new Map();
  const manifest = { generator: 'scripts/vendor_codemirror.mjs', roots: ROOTS, packages: {} };
  const notices = new Map(); // license text -> packages under it
  for (const [name, p] of pkgs) {
    set.set(fileOf(name), p.body);
    manifest.packages[name] = { version: p.pj.version, license: p.pj.license, file: fileOf(name), from: p.from, sha256: sha256(p.body), imports: p.imports };
    const text = licenseFile(p.dir);
    notices.set(text, [...(notices.get(text) ?? []), `${name}@${p.pj.version}`]);
  }
  set.set('manifest.json', JSON.stringify(manifest, null, 2) + '\n');
  set.set('LICENSE', `CodeMirror 6 and the packages it imports, copied from npm by scripts/vendor_codemirror.mjs. All are ${LICENSE} licensed.\n`
    + [...notices].map(([text, names]) => `\n${'-'.repeat(72)}\n${names.join(', ')}\n\n${text}\n`).join(''));
  const hash = sha256([...set].map(([f, c]) => `${f}\0${c}\0`).join('')).slice(0, 8);
  const dir = `${OWNED}-${hash}`;
  const roots = ROOTS.map((name) => `export * as ${namespaceOf(name)} from './${dir}/${fileOf(name)}';\n`).join('');
  const rest = [...pkgs.keys()].filter((name) => !ROOTS.includes(name)).map((name) => `import './${dir}/${fileOf(name)}';\n`).join('');
  const files = new Map([...set].map(([f, c]) => [`${dir}/${f}`, c]));
  files.set(`${OWNED}.js`, `// Generated by scripts/vendor_codemirror.mjs (make vendor), do not edit. CodeMirror 6, docs/decisions/0016.\n`
    + `// The files are in a directory named after a hash of their content: a new set of versions gets new URLs, so the browser\n`
    + `// cache never mixes two sets. Import this module, not the directory: const cm = await import('../vendor/codemirror.js').\n`
    + roots
    + `// the rest of the set: requested here so the browser fetches every file at once, not one import level after another\n`
    + rest);
  files.set(`${OWNED}.d.ts`, `// Generated by scripts/vendor_codemirror.mjs: types of codemirror.js for tsc, from the same versions in node_modules.\n`
    + ROOTS.map((name) => `export * as ${namespaceOf(name)} from '${name}';\n`).join(''));
  return { files, dir };
}

// files under VENDOR that belong to the script, path relative to VENDOR
function owned() {
  const out = [];
  const walk = (rel) => {
    const path = join(VENDOR, rel);
    if (statSync(path).isDirectory()) for (const f of readdirSync(path)) walk(`${rel}/${f}`);
    else out.push(rel);
  };
  for (const f of readdirSync(VENDOR)) if (f.startsWith(OWNED)) walk(f);
  return out.sort();
}

function summary(files, dir) {
  const code = [...files].filter(([f]) => f.startsWith(dir + '/') && f.endsWith('.js')).map(([, c]) => Buffer.from(c));
  const kb = (n) => (n / 1024).toFixed(0) + ' KB';
  const raw = code.reduce((s, b) => s + b.length, 0);
  const gzip = code.reduce((s, b) => s + gzipSync(b).length, 0);
  return `app/static/vendor/${dir}/: ${code.length} packages, ${kb(raw)}, gzip ${kb(gzip)}`;
}

function npm(...args) {
  console.log(`npm ${args.join(' ')}`);
  execFileSync('npm', args, { cwd: REPO, stdio: 'inherit' });
}

// newest versions: every package of the set to @latest, then the ones the new versions brought in or dropped
function update() {
  const before = [...collect().keys()];
  npm('install', '--save-dev', '--save-exact', '--no-fund', '--no-audit', ...before.map((name) => `${name}@latest`));
  const after = collect();
  const added = [...after].filter(([name]) => !before.includes(name)).map(([name, p]) => `${name}@${p.pj.version}`);
  if (added.length) npm('install', '--save-dev', '--save-exact', '--no-fund', '--no-audit', ...added);
  const dropped = before.filter((name) => !after.has(name));
  if (dropped.length) npm('uninstall', '--no-fund', '--no-audit', ...dropped);
}

const mode = process.argv[2];
if (mode && !['--check', '--update'].includes(mode)) die('usage: node scripts/vendor_codemirror.mjs [--check | --update]');
if (mode === '--update') update();
const pkgs = collect();
verify(pkgs);
const { files, dir } = render(pkgs);
if (mode === '--check') {
  const have = owned();
  const bad = [
    ...[...files.keys()].filter((f) => !have.includes(f)).map((f) => `missing: ${f}`),
    ...have.filter((f) => !files.has(f)).map((f) => `extra: ${f}`),
    ...have.filter((f) => files.has(f) && !readFileSync(join(VENDOR, f)).equals(Buffer.from(files.get(f)))).map((f) => `differs: ${f}`),
  ];
  if (bad.length) die(`app/static/vendor/ is not what package-lock.json gives:\n  ${bad.join('\n  ')}\nrun make vendor (after npm ci) and commit the result`);
  console.log(`vendor ok, ${summary(files, dir)}`);
} else {
  for (const f of readdirSync(VENDOR)) if (f.startsWith(OWNED)) rmSync(join(VENDOR, f), { recursive: true });
  mkdirSync(join(VENDOR, dir));
  for (const [f, content] of files) writeFileSync(join(VENDOR, f), content);
  console.log(`wrote ${summary(files, dir)}`);
}
