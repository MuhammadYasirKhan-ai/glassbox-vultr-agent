#!/usr/bin/env bash
# Local run (Linux/macOS/WSL2). Needs Docker running and `.env` filled in.
set -euo pipefail
docker image inspect glassbox-sandbox:latest >/dev/null 2>&1 || docker build -t glassbox-sandbox:latest sandbox/
exec python -m uvicorn --factory app.main:get_app --env-file .env --host 127.0.0.1 --port 8000
