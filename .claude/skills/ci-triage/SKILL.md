---
name: ci-triage
description: Bring red CI on GitHub back to green: find what failed, tell a flake from a real break, fix each cause in its own PR, hand the merge to the human; use when runs or PR checks fail, and for every Dependabot PR.
---

# CI triage

Every failure gets a cause backed by evidence before anything is fixed: "new versions broke it" is where the work starts. The project rules applied here (areas and paths, `make check`, merge by a human) are in `AGENTS.md`.

## 1. Map the failures

```bash
gh run list --limit 30 --json databaseId,workflowName,headBranch,event,conclusion,displayTitle \
  --jq '.[] | "\(.databaseId) \(.workflowName) \(.event) \(.headBranch) \(.conclusion) | \(.displayTitle)"'
gh run view <id> --json jobs --jq '.jobs[] | "\(.conclusion) | \(.name) | \([.steps[] | select(.conclusion=="failure") | .name])"'
gh run view <id> --log-failed > /tmp/run-<id>.log && grep -nE "FAILED|FAIL |Error" /tmp/run-<id>.log
gh pr diff <n> --name-only
```

Done when every failed run has its job, step and failing test or line, you know its PR's diff, and you know whether `main` (push and schedule runs) is red too.

## 2. Classify each failure

| What the log shows | Cause | Fix |
|---|---|---|
| `test_lock.py`: `requirements.lock отстал от requirements.txt` | a pin in `requirements.txt` moved without `make lock` | 3a |
| `vendor_codemirror: app/static/vendor/ is not what package-lock.json gives` or `vendor_codemirror: … a second copy …` | `package.json` or the lock moved a package of the CodeMirror set; the copy in `app/static/vendor/codemirror-*` is made by `scripts/vendor_codemirror.mjs` | 3a, `make vendor` |
| `FAIL  <check>` in `e2e` of a PR whose diff cannot reach the editor (an image digest, docs, another job's deps) | flake: a fixed pause before reading a page that updates asynchronously | 3b |
| `supply-chain` or Trivy: a CVE in a package outside the diff | a fresh advisory | the CVE rule in `AGENTS.md` |
| anything else | a real break | reproduce locally (`make check-fast`, the single test), fix test-first (`tests/AGENTS.md`) |

Evidence for a flake, one is enough: the check's own output shows the action succeeded (the mock got the right call) while the page it read was stale; the same check fails in other runs (`gh run list --status failure --limit 60 --json databaseId --jq '.[].databaseId'`, then `gh run view <id> --log-failed | grep "FAIL  "` for each); `gh run rerun <id> --failed` goes green.

Done when every failure has a class and its evidence.

## 3. Fix, one cause per PR

**a. A Python package or CodeMirror moves** (a CVE, a planned bump): a PR of its own from `main`. Dependabot opens none of these (`docs/decisions/0020-dependabot-self-contained-prs.md`); a Dependabot PR that does is a config error, close it.

```bash
git switch -c deps/<what> origin/main
make lock LOCK_ARGS="--upgrade-package <name>"   # a transitive package (a CVE); after raising a pin in requirements*.txt plain `make lock`
.venv/bin/pip install -r requirements-dev.txt     # the bumped tools, so make check runs what CI runs
git diff --stat                                   # only the bumped packages and their dependencies move
make check-fast && git commit -am "requirements.lock: …" && git push   # the whole set runs in CI
```

CodeMirror (a CVE from `npm audit`, or a planned update): `make vendor-update` moves the whole set to its latest versions together and builds the copy again; commit `package.json`, the lock and `app/static/vendor/` (the old `codemirror-<hash>/` goes, a new one comes). After a hand edit of `package.json` or the lock, `make vendor` alone. If a version brought in a package the set did not have, `make vendor` names the `npm install --save-dev --save-exact …` to run first. `a second copy <pkg> <version> in node_modules/<other>/node_modules/<pkg>` means one package moved past what another one of the set accepts: `make vendor-update`, then `npm ls <pkg>` shows one version.

**b. Flaky browser check**: its own PR (area editor). Replace the pause before the read with a wait for the event, then the unchanged `check` (as in `tests/e2e/19-a-new-post-becomes.mjs`):

```js
await page.waitForFunction(() => /опрос/.test(document.querySelector('#posts li')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
```

For the mock's log or the API, `until` from the harness, counted from before the action (the log keeps the whole session):

```js
const before = (await mockLog()).filter((c) => c.method === 'sendPoll').length;
await page.click('#send');
const poll = await until(async () => (await mockLog()).filter((c) => c.method === 'sendPoll').slice(before).pop());
```

Two actions that each reload the same list (a tab, then a filter) go one after the other: wait for the first one's result, or the older reload may land last.

The same pause-then-read in other checks is a later PR: list those files in this PR's description.

**Browser checks locally**: `make e2e-local`, one check `make e2e-local ONLY=poll`. It brings up its own stand on free ports with an empty DATA_DIR. The working studio on 7424 is a container that talks to the real Telegram; the harness refuses to run against it, and the stand stops its own processes by PID. The proof of a flake fix is `E2E_SLOW=500 make e2e-local ONLY=<check>` (every request of the page is delayed, as on a slow runner): the old check goes red, the new one green.

Done when `gh pr checks <n> --watch` is green for every PR; locally `make check-fast` and the one check you fixed (a browser fix: the red-then-green proof with `E2E_SLOW=500 make e2e-local ONLY=<check>`).

## 4. Hand off

- Merging is the human's. The work ends with every PR green and mergeable, local branches pushed and deleted, and one line for the user to run: `! gh pr merge <a> --merge && gh pr merge <b> --merge && git pull --ff-only && git fetch --prune`. GitHub deletes the PR branches on merge.
- After the merge read `gh run list --branch main --event push`: the newest run covers every merge. An older run `cancelled` there is a queued run replaced by a newer push (`concurrency` in `ci.yml`), not a failure.
- A merge that changes what the image runs (`requirements.lock`, `Dockerfile`, `app/`) leaves the working studio on the old code until `docker compose up -d --build` in the repo folder. Tell the user why it matters (a security fix is not live yet) and rebuild after their yes.

Done when the user has the merge line and, after the merge, the newest push run on `main` is green.
