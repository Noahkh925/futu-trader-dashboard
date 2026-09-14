FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY config ./config
COPY fixtures ./fixtures
COPY web ./web
COPY src ./src

RUN pip install --no-cache-dir -e ".[console]"

ENV DASHBOARD_READONLY=1
ENV DASHBOARD_HOSTING=cloud
ENV REPORTS_DIR=fixtures/staging/promotion_ok
ENV PYTHONUNBUFFERED=1

EXPOSE 8787

HEALTHCHECK CMD curl --fail http://localhost:${PORT:-8787}/health || exit 1

# T3 作战台：FastAPI + web/console（PROH-126/128/129）
# reports_dir 留空 → build_ops_snapshot 走 REPORTS_DIR / REPORTS_REMOTE_BASE
CMD ["sh", "-c", "uvicorn api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8787}"]
