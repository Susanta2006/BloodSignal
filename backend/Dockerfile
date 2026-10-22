
# BloodSignal API (FastAPI + e-RaktKosh adapter)
# Build from repository root:
# docker build -f backend/Dockerfile -t bloodsignal-api ./backend

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=10000 \
    APP_ENV=production

WORKDIR /app

COPY requirements.txt ./requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m pip install google-genai \
    && groupadd --system --gid 10001 bloodsignal \
    && useradd --system --uid 10001 --gid bloodsignal \
       --home-dir /nonexistent \
       --shell /usr/sbin/nologin bloodsignal

COPY --chown=bloodsignal:bloodsignal app ./app

USER 10001:10001

EXPOSE 10000
STOPSIGNAL SIGTERM

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '10000') + '/health/live', timeout=2)"
