# Abogi as a Microsoft Foundry hosted agent (Responses protocol on port 8088).
# Build for the platform's architecture:
#   docker build --platform linux/amd64 -t abogi .
FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM --platform=linux/amd64 python:3.12-slim

ENV PYTHONNOUSERSITE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PORT=8088

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

# Dependencies first so code changes do not invalidate this layer.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8088

CMD ["/app/.venv/bin/python", "-m", "abogi.hosting.app"]
