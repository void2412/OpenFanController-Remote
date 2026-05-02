FROM debian:13

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    HOST=127.0.0.1 \
    PORT=8088 \
    CONTROLLER_POLL_INTERVAL=2.0 \
    CURVE_POLL_INTERVAL=2.0 \
    POWER_SWITCH_STATE_INTERVAL=2.0 \
    LOG_LEVEL=info

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.getenv('PORT', '8088'), timeout=5).read()" || exit 1

CMD ["sh", "-c", "exec python -m uvicorn controller_app:app --host 0.0.0.0 --port ${PORT:-8088} --log-level ${LOG_LEVEL:-info}"]
