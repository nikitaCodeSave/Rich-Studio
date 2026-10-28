# Одна команда проверки для людей и агентов: `make check`. Вывод короткий, ошибки в конце.
# Инструменты запускаются тем же Python, куда ставили requirements-dev.txt (.venv, если он есть).
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

LINT_IMPORTS = $(if $(wildcard .venv/bin/lint-imports),.venv/bin/lint-imports,lint-imports)
UV = $(if $(wildcard .venv/bin/uv),.venv/bin/uv,uv)

.PHONY: check check-fast check-py check-js fmt test openapi docs e2e mock dev spec-update token tokens token-revoke lock

check: check-py check-js

# Быстрая проверка между шагами, без браузера. Перед PR всё равно нужен `make check`.
check-fast:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy
	PYTHONPATH=app $(LINT_IMPORTS) --no-cache
	$(PY) -m pytest -q -x
	npx eslint .
	npx tsc -p .

check-py:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy
	PYTHONPATH=app $(LINT_IMPORTS) --no-cache
	$(PY) -m pytest -q

check-js:
	npx eslint .
	npx tsc -p .
	npm run --silent test:render
	npm run --silent test:parity
	npm run --silent test:shots

fmt:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
	npx eslint --fix .

test:
	$(PY) -m pytest -q

# После намеренного изменения API: обновить docs/openapi.json и закоммитить его.
openapi: docs

# docs/openapi.json and docs/reference/ (HTTP routes and MCP tools) from the code
docs:
	UPDATE_OPENAPI=1 UPDATE_DOCS=1 $(PY) -m pytest -q tests/test_openapi_contract.py tests/test_reference_docs.py

# Именные токены доступа (README → Безопасность). База — ./data, как у docker compose; другая: DATA=путь.
# В контейнере то же: docker compose exec rich-studio python cli.py token add NAME --level draft
DATA ?= $(CURDIR)/data
LEVEL ?= draft
token:
	DATA_DIR=$(DATA) $(PY) app/cli.py token add $(NAME) --level $(LEVEL)
tokens:
	DATA_DIR=$(DATA) $(PY) app/cli.py token list
token-revoke:
	DATA_DIR=$(DATA) $(PY) app/cli.py token revoke $(NAME)

# Студия на подставном Telegram: http://localhost:8080, без токена и сети.
mock:
	cd tests && uvicorn mock_telegram:app --port 8081

dev:
	mkdir -p /tmp/rs-data
	cd app && DATA_DIR=/tmp/rs-data TG_BOT_TOKEN=1:x TG_API_BASE=http://127.0.0.1:8081 \
	  TG_DEFAULT_CHAT=@test TG_PREVIEW_CHAT=777 SPEC_URL=http://127.0.0.1:9/off \
	  uvicorn main:app --port 8080 --reload

# Браузерные проверки: сначала `make mock` и `make dev` в соседних терминалах.
e2e:
	npm run --silent test:e2e

# requirements.lock: every runtime package with hashes, for the image. After a change in requirements.txt
# (by hand or Dependabot) run it and commit the lock; tests/test_lock.py checks they agree.
lock:
	$(UV) pip compile requirements.txt --universal --python-version 3.12 --generate-hashes \
	  --annotation-style line --custom-compile-command "make lock" -o requirements.lock

# newer Bot API spec into app/bundled/api.json; exit 10 = updated, then make tests/test_spec_coverage.py green
spec-update:
	$(PY) scripts/update_spec.py
