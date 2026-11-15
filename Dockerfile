# base image by digest: a moved tag cannot change what is built; Dependabot (docker) bumps tag and digest together
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DATA_DIR=/data
WORKDIR /app
# every package with its hash (make lock): a changed or swapped file on PyPI stops the build
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock
COPY app/ .
# the skill agents read over MCP (mcp_tools/skill.py): next to the app, as in the repository; without it no start
COPY .claude/skills/telegram-rich-posts/SKILL.md /.claude/skills/telegram-rich-posts/SKILL.md
# an unprivileged user that owns /data (the server's named volume); the local compose runs as the host user
RUN useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin studio \
    && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
# the studio writes its own request log (logs.py, ./data/logs); uvicorn's would only duplicate it
# one process: the background loops run under a lease (services/loops.py); a stop waits for sends under way
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--timeout-graceful-shutdown", "10"]
