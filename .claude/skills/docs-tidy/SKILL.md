---
name: docs-tidy
description: Cut a doc down to the current state of the studio, short and plain: no history, nothing the code already says, no slop; use when a doc grew long or went stale after a change, or when asked to tidy the docs.
---

# Docs tidy

A doc tells its reader how the studio works **now** and what they must do. How it got there lives in git (commit messages, PR descriptions); a decision later work must follow lives in `docs/decisions/`. Anything else in a doc is sediment.

Docs: `README.md`, `docs/*.md`, `deploy/README.md`, `AGENTS.md`, `.claude/skills/*/SKILL.md`, `.claude/agents/*.md`, and the docstrings of routes and MCP tools (`make docs` turns them into `docs/reference/`). Not docs here: `docs/decisions/` (a record keeps its history; a new decision is a new record naming what it replaces), `docs/reference/` (generated), `docs/openapi.json`.

## 1. Every sentence against the code

One doc per PR; README and the telegram-rich-posts skill each get their own. Start with `pytest tests/test_docs_current.py`: it names the common phrasings of history, file names that are gone, and docs over their word budget. Then read the doc whole and put every sentence through, in order:

1. **True now?** Look it up in the code: grep the name, route, setting or error text. A command runs only on a stand of its own with the fake Telegram (`make e2e-local` brings one up on free ports), and anything that publishes only with `dry_run`: the studio on 7424 posts to real channels.
2. **History?** «раньше», «теперь», «больше не», «стало», «добавлено», «исправлено», "now", "no longer", a PR or issue number, the story of a fixed bug → keep only the current behaviour. A source is a file path (where the measurement or the rule lives), not a ticket.
3. **Derivable?** Lists that `docs/reference/`, `app/bundled/api.json` or a `--help` already hold; directory trees; a function retold step by step → delete, point to the source. A setting stays with its effect; only a bare list of names and defaults goes (`.env.example` holds it).
4. **Said elsewhere?** One meaning, one place, where its reader looks: README for the person running the studio, `docs/ARCHITECTURE.md` for whoever changes the code. The telegram-rich-posts skill is whole on its own: the studio serves it over MCP to agents that have no repository, so what a post author needs stays in it, and README links to the skill, never the reverse.
5. **Slop?** Filler, hedging, the same fact twice, a paragraph where a line does, an example that teaches nothing new → cut to the line.

What stays: steps a person performs, settings and their effect, limits measured on the real Telegram with where they live, what prevents a lost or doubled post, the reason behind a choice that looks wrong.

Done when every sentence of the doc passed all five, and for every sentence you removed you can name its reason: false, history, derivable, elsewhere, slop.

## 2. Shape and budget

- README: what the studio is, how to run it, what it does (a line or a short paragraph per feature; the details live in the skill, `docs/` or the reference), settings, where to read next.
- The telegram-rich-posts skill: what an agent needs to write, check and publish a post — rules and steps, not a tour of the editor.
- Headings a reader can jump to; order follows the reader's task, not the order things were built.

Done when the doc follows its shape, and, if it is in `BUDGET` (`tests/test_docs_current.py`) and got shorter, its cap is its new size plus about a tenth.

## 3. Hand over

`make docs` if a docstring changed, then `make check-fast`. The PR description groups what went and why (for example «список маршрутов: есть в docs/reference/»). The reviewer finds the reason of every removed sentence and checks every kept one against the code; a current fact lost or a false one kept blocks the merge.
