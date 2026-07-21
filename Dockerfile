FROM python:3.12-slim

RUN groupadd -r coclaude && useradd -r -g coclaude -d /app coclaude
WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN mkdir -p /data && chown coclaude:coclaude /data
USER coclaude
EXPOSE 8788
CMD ["/app/.venv/bin/coclaude"]
