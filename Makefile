# Одна команда проверки для людей и агентов: `make check`. Вывод короткий, ошибки в конце.
# Инструменты запускаются тем же Python, куда ставили requirements-dev.txt.
PY ?= python3

.PHONY: check check-py check-js fmt test openapi e2e mock dev spec-update

check: check-py check-js

check-py:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy
	PYTHONPATH=app lint-imports --no-cache
	$(PY) -m pytest -q

check-js:
	npx eslint .
	npx tsc -p .
	npm run --silent test:render

fmt:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
	npx eslint --fix .

test:
	$(PY) -m pytest -q

# После намеренного изменения API: обновить docs/openapi.json и закоммитить его.
openapi:
	UPDATE_OPENAPI=1 $(PY) -m pytest -q tests/test_openapi_contract.py

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

# newer Bot API spec into app/bundled/api.json; exit 10 = updated, then make tests/test_spec_coverage.py green
spec-update:
	$(PY) scripts/update_spec.py
