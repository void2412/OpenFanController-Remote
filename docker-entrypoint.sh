#!/usr/bin/env bash
set -o pipefail

mkdir -p /app/runtime/logs

python -m uvicorn controller_app:app \
    --host "${HOST:-0.0.0.0}" \
    --port "${PORT:-8088}" \
    --log-level "${LOG_LEVEL:-info}" 2>&1 | tee -a /app/runtime/logs/controller_app.log
