@echo off
REM Local run on Windows. Needs Docker Desktop running and .env filled in. Do NOT add --reload (breaks subprocesses on Windows).
docker image inspect glassbox-sandbox:latest >nul 2>&1 || docker build -t glassbox-sandbox:latest sandbox
python -m uvicorn --factory app.main:get_app --env-file .env --host 127.0.0.1 --port 8000
