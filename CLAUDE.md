@AGENTS.md

## Claude Code

- Сессию запускайте из корня репозитория: запреты в `.claude/settings.json` (`.env`, секреты `deploy/`, `api.json`, `requirements.lock`, force push) читаются только оттуда.
- В git worktree (`.claude/worktrees/…`) нет своих `.venv` и `node_modules`. Перед `make check` выполните `ln -sfn "$(git rev-parse --path-format=absolute --git-common-dir)/../.venv" .venv` и `npm ci`: `eslint` и `tsc` находят и корневой `node_modules`, а проверка вендора CodeMirror читает только свой.
- Несколько `make e2e-local` одновременно: каждому свои порты, `STUDIO_PORT=75NN MOCK_PORT=81NN make e2e-local`.
- Авто-память выключена: что должна знать следующая работа, записывайте в `AGENTS.md`, `docs/ARCHITECTURE.md` или `docs/decisions/`.
- `origin/main` общий для всех worktree, его двигают чужие `git fetch`: свежий main подтягивайте `git merge origin/main`, а не `git reset` по нему (force push запрещён).
