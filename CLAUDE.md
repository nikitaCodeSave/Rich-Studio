@AGENTS.md

## Claude Code

- Запускайте сессию из корня репозитория: запреты в `.claude/settings.json` (`.env`, секреты `deploy/`, `api.json`, `requirements.lock`, force push) читаются только оттуда.
- Агент в git worktree (`.claude/worktrees/…`) не имеет своего `.venv`. Перед `make check` выполните `ln -sfn "$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/.venv" .venv`. `node_modules` берётся из корня выше по дереву.
- Несколько `make e2e-local` одновременно: каждому свои порты, `STUDIO_PORT=75NN MOCK_PORT=81NN make e2e-local`.
- Авто-память выключена. То, что должна знать следующая работа, записывайте в `AGENTS.md`, `docs/ARCHITECTURE.md` или `docs/decisions/`.
