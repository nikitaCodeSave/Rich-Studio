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

Редактор: `https://<домен>`, вход по этому токену. Агентам выдайте свои: `docker compose exec rich-studio python cli.py token add claude-laptop --level draft`, в окружении агента `STUDIO_URL=https://<домен>` (без `/` в конце) и `STUDIO_TOKEN`: их читают `.mcp.json` и хук здоровья, когда Claude Code открыт в папке студии; из другой папки подключение вручную ([README](../README.md#mcp-для-claude-и-других-агентов)). Skill `telegram-rich-posts` такой агент получает по MCP от самой студии, копировать его не нужно ([README](../README.md#skill-для-claude-code)).

Что включает серверный режим (`STUDIO_MODE=server`): без токена, `https://` в `STUDIO_URL` и токена бота студия не стартует и пишет, чего не хватает; `STUDIO_API_KEY` (ключ уровня `admin`, необязателен, именные токены удобнее) не короче 32 символов, иначе студия тоже не стартует (неудачные попытки ключа не считаются, от перебора защищает только длина); спецификация Bot API берётся только из образа (`SPEC_PIN`); домен сам попадает в `ALLOWED_HOSTS`. Caddy передаёт студии `Host` как есть; другой прокси перед студией должен делать так же (у nginx `proxy_set_header Host $http_host`), иначе запись из редактора получит 403 «запрос с другого сайта отклонён»: `Origin` браузера сверяется с `Host`.

## Обновление

```sh
docker compose pull && docker compose up -d
```

Образ не собирается сам на каждый push в `main` (сборка arm64 через QEMU съедает минуты Actions). Перед обновлением соберите его: Actions → image → Run workflow на `main` (или `gh workflow run image.yml --ref main`) даёт `:main` и `:<sha>`, push тега `v*` даёт `:<версия>`.

`STUDIO_TAG` в `.env` закрепляет версию (`main`, sha коммита или версия из GHCR). Новая версия Bot API приходит так же: `make spec-update` → PR → ручная сборка образа.

Если в `.env` задан `STUDIO_API_KEY` короче 32 символов, обновлённая студия не стартует (`docker compose logs rich-studio` скажет почему). Замените ключ до обновления или после:

```sh
openssl rand -hex 32   # новый ключ; или python3 -c 'import secrets;print(secrets.token_urlsafe(32))'
# в .env: STUDIO_API_KEY=<новый ключ> (или новое содержимое файла из STUDIO_API_KEY_FILE)
docker compose up -d
```

Caddy не трогайте: ключ идёт через него заголовком, в `Caddyfile` его нет. Новый ключ нужен всем, кто ходил со старым: `STUDIO_API_KEY` в окружении агентов (его читают `.mcp.json` и хук здоровья), скрипты с `X-API-Key`. Редактор спросит ключ заново: сессия по старому ключу закрывается сама. Можно и совсем без ключа: уберите `STUDIO_API_KEY` из `.env`, затем, если токенов ещё нет, выдайте себе `admin` (команда из «Первого запуска»; с коротким ключом в `.env` она тоже не запустится) и `docker compose up -d`.

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
