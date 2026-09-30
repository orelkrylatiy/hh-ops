FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/usr/local/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    cron \
    curl \
    dos2unix \
    gettext-base \
    # pip ставит humanizer-framework из pinned git-коммита
    git \
    procps \
    tzdata \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
RUN groupadd --gid "$GID" docker \
    && useradd --uid "$UID" --gid docker --create-home --shell /bin/bash docker

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY admin/requirements.txt /app/admin/requirements.txt
COPY src /app/src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e '.[playwright,pillow]' \
    && python -m pip install --no-cache-dir -r /app/admin/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY admin /app/admin
COPY config /app/config
COPY prompts /app/prompts
COPY rules /app/rules
COPY scripts /app/scripts
COPY crontab /app/crontab
COPY container-entrypoint.sh /app/container-entrypoint.sh
COPY startup.sh /app/startup.sh

RUN dos2unix /app/crontab /app/container-entrypoint.sh /app/startup.sh /app/scripts/*.sh \
    && chmod +x /app/container-entrypoint.sh /app/startup.sh /app/scripts/*.sh \
    && chmod 0644 /app/crontab \
    && crontab -u docker /app/crontab \
    && mkdir -p /app/logs /app/config /var/log \
    && touch /var/log/cron.log \
    && chown -R docker:docker /app /ms-playwright /var/log/cron.log

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

EXPOSE 8000
ENTRYPOINT ["/app/container-entrypoint.sh"]
