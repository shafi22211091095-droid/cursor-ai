# LabelLedger — minimal production image.
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[ai]" && rm -rf /root/.cache
USER app
ENV LABELLEDGER_DB=/data/labelledger.db
VOLUME ["/data"]
EXPOSE 8000
# Binding beyond loopback requires LABELLEDGER_BASIC_AUTH=user:password (enforced by the CLI).
CMD ["labelledger", "serve", "--host", "0.0.0.0", "--port", "8000"]
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
