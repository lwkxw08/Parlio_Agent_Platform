FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/voice-worker/pyproject.toml apps/voice-worker/
COPY apps/api/pyproject.toml apps/api/
COPY tools/latency-harness/pyproject.toml tools/latency-harness/
COPY tools/load-test/pyproject.toml tools/load-test/
RUN uv sync --frozen --no-dev --package parlio-voice --no-install-workspace

COPY apps/voice-worker apps/voice-worker
RUN uv sync --frozen --no-dev --package parlio-voice

# pre-download Silero VAD + turn-detector weights so cold starts don't hit the network
RUN uv run --package parlio-voice python -m parlio_voice.agent download-files

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8081
CMD ["parlio-voice", "start"]
