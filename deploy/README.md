# Rich Studio на своём сервере

Три контейнера: студия (только внутри), Caddy (HTTPS снаружи) и Litestream (копия базы в S3 каждую секунду). Почему так: [docs/decisions/0010](../docs/decisions/0010-server-image-and-pinned-spec.md).

Нужно: сервер с Docker и Compose, домен с A/AAAA-записью на него, открытые 80 и 443, бакет S3 (AWS, Cloudflare R2, Backblaze B2 и другие). Если `main` ушёл вперёд от последней сборки, сначала соберите образ вручную (см. «Обновление»).

## Первый запуск

```sh
git clone … rich-studio && cd rich-studio/deploy
cp .env.example .env                      # домен, каналы; при желании DEADMAN_URL
cp litestream.env.example litestream.env  # бакет S3 и ключи: их видит только Litestream
chmod 600 .env litestream.env
mkdir -p secrets && printf '%s\n' '123:ABC…' > secrets/tg_bot_token
sudo chown 10001:10001 secrets/tg_bot_token && sudo chmod 400 secrets/tg_bot_token   # студия работает от uid 10001
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-login> --password-stdin  # образ приватный: PAT с read:packages
# без токена серверная студия не стартует: первый токен (печатается один раз)
docker compose run --rm --no-deps rich-studio python cli.py token add nikita --level admin
docker compose up -d
```

Редактор: `https://<домен>`, вход по этому токену. Агентам выдайте свои: `docker compose exec rich-studio python cli.py token add claude-laptop --level draft`, в окружении агента `STUDIO_URL=https://<домен>` (без `/` в конце) и `STUDIO_TOKEN`: их читают `.mcp.json` и хук здоровья, когда Claude Code открыт в папке студии; из другой папки подключение вручную ([README](../README.md#mcp-для-claude-и-других-агентов)).

Что включает серверный режим (`STUDIO_MODE=server`): без токена, `https://` в `STUDIO_URL` и токена бота студия не стартует и пишет, чего не хватает; спецификация Bot API берётся только из образа (`SPEC_PIN`); домен сам попадает в `ALLOWED_HOSTS`.

## Обновление

```sh
docker compose pull && docker compose up -d
```

Образ не собирается сам на каждый push в `main` (сборка arm64 через QEMU съедает минуты Actions). Перед обновлением соберите его: Actions → image → Run workflow на `main` (или `gh workflow run image.yml --ref main`) даёт `:main` и `:<sha>`, push тега `v*` даёт `:<версия>`.

`STUDIO_TAG` в `.env` закрепляет версию (`main`, sha коммита или версия из GHCR). Новая версия Bot API приходит так же: `make spec-update` → PR → ручная сборка образа.

## Восстановление базы

```sh
docker compose down
docker volume rm rich-studio_data
docker compose run --rm --no-deps rich-studio true                      # новый том с владельцем uid 10001
docker compose run --rm --no-deps litestream restore /data/studio.db    # последняя копия из S3
docker compose up -d
```

Загрузки (картинки, видео) Litestream не копирует: они в zip-бэкапе `GET /api/export` (уровень `admin`), храните его отдельно. CI каждый день повторяет эти шаги на копии в файле: [`scripts/restore_check.sh`](../scripts/restore_check.sh).

## Мониторинг

- `DEADMAN_URL` (healthchecks.io или похожий): студия пингует его раз в 5 минут, `…/fail`, если отчёт о состоянии `fail`. Нет пингов — сервис пишет вам. Подробнее: [0011](../docs/decisions/0011-dead-mans-switch.md).
- Внешний uptime-монитор на `https://<домен>/readyz` (без токена).
- Остальное как локально: `GET /api/health`, `GET /api/logs`, skill `studio-health`.
