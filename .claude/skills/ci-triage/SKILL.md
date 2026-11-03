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
| `test_lock.py`: `requirements.lock отстал от requirements.txt` in a Dependabot pip PR | Dependabot bumps `requirements.txt` only, by design (`.github/dependabot.yml`) | 3a |
| `FAIL  <check>` in `e2e` of a PR whose diff cannot reach the editor (an image digest, docs, another job's deps) | flake: a fixed pause before reading a page that updates asynchronously | 3b |
| `supply-chain` or Trivy: a CVE in a package outside the diff | a fresh advisory | the CVE rule in `AGENTS.md` |
| anything else | a real break | reproduce locally (`make check-fast`, the single test), fix test-first (`tests/AGENTS.md`) |

Evidence for a flake, one is enough: the check's own output shows the action succeeded (the mock got the right call) while the page it read was stale; the same check fails in other runs (`gh run list --status failure --limit 60 --json databaseId --jq '.[].databaseId'`, then `gh run view <id> --log-failed | grep "FAIL  "` for each); `gh run rerun <id> --failed` goes green.

Done when every failure has a class and its evidence.

## 3. Fix, one cause per PR

**a. Dependabot pip PR**: the lock goes into the PR's own branch.

```bash
git switch -c <branch> --track origin/<branch>
.venv/bin/pip install -r requirements-dev.txt   # the bumped tools, so make check runs what CI runs
make lock && git diff --stat                     # only the bumped packages and their dependencies move
make check && git commit -am "requirements.lock: …" && git push
```

Once you pushed, Dependabot stops rebasing the PR; `@dependabot recreate` would throw the lock commit away, so rebase it yourself if `main` moves.

**b. Flaky browser check**: its own PR (area editor). Replace the pause before the read with a wait for the event, then the unchanged `check` (as in `tests/e2e/19-a-new-post-becomes.mjs`):

```js
await page.waitForFunction(() => /опрос/.test(document.querySelector('#posts li')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
```

The same pause-then-read in other checks is a later PR: list those files in this PR's description.

**Browser checks locally**: `make e2e-local`, one check `make e2e-local ONLY=poll` (three runs in a row for a flake fix, then the whole set). It brings up its own stand on free ports with an empty DATA_DIR. The working studio on 7424 is a container that talks to the real Telegram; the harness refuses to run against it, and the stand stops its own processes by PID.

Done when `make check` (and `make e2e-local` for a browser fix) is green locally and `gh pr checks <n> --watch` is green for every PR.

## 4. Hand off

- Merging is the human's. The work ends with every PR green and mergeable, local branches pushed and deleted, and one line for the user to run: `! gh pr merge <a> --merge && gh pr merge <b> --merge && git pull --ff-only && git fetch --prune`. GitHub deletes the PR branches on merge.
- After the merge read `gh run list --branch main --event push`: the newest run covers every merge. An older run `cancelled` there is a queued run replaced by a newer push (`concurrency` in `ci.yml`), not a failure.
- A merge that changes what the image runs (`requirements.lock`, `Dockerfile`, `app/`) leaves the working studio on the old code until `docker compose up -d --build` in the repo folder. Tell the user why it matters (a security fix is not live yet) and rebuild after their yes.

Done when the user has the merge line and, after the merge, the newest push run on `main` is green.
