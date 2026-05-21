# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

WORKDIR /app

# System deps for tree-sitter native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Builder ─────────────────────────────────────────────────────────────────
FROM base AS builder

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install hatchling

# Install project deps (no dev extras)
COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[standard]" 2>/dev/null || pip install --no-cache-dir -e .

# ── Runtime ─────────────────────────────────────────────────────────────────
FROM base AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app /app

# Create default data directory
RUN mkdir -p /data/chroma

ENV CODEPAL_SERVER__HOST=0.0.0.0
ENV CODEPAL_SERVER__PORT=8742
ENV CODEPAL_CHROMA__PERSIST_DIR=/data/chroma
ENV CODEPAL_INDEXER__STATE_DB=/data/index_state.db

EXPOSE 8742

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8742/v1/status || exit 1

CMD ["python", "-m", "codepal.main"]
