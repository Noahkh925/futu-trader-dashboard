FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY config ./config
COPY fixtures ./fixtures
COPY src ./src

RUN pip install --no-cache-dir -e ".[dashboard]"

ENV DASHBOARD_READONLY=1
ENV REPORTS_DIR=fixtures/staging/promotion_ok
ENV PYTHONUNBUFFERED=1

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:${PORT:-8501}/_stcore/health || exit 1

CMD ["sh", "-c", "streamlit run src/dashboard/app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
