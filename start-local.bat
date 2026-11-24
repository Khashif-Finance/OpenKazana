@echo off
setlocal
title OpenKhazana - Local Launcher

echo ============================================================
echo   OpenKhazana - Pakistan Market Hub ^& Multi-Asset Tracker
echo ============================================================
echo.

cd /d "%~dp0"

if not defined DATABASE_URL set "DATABASE_URL=sqlite+aiosqlite:///./openkhazana_local.db"
if not defined AUTH_MODE set "AUTH_MODE=local"
if not defined NEXT_PUBLIC_API_BASE_URL set "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000"
if not defined INTERNAL_API_BASE_URL set "INTERNAL_API_BASE_URL=http://localhost:8000"
if not defined SCRAPERS_ENABLED set "SCRAPERS_ENABLED=true"

if not exist "apps\api\.venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment in apps\api\.venv...
    python -m venv "apps\api\.venv"
    "apps\api\.venv\Scripts\python.exe" -m pip install --upgrade pip
    "apps\api\.venv\Scripts\pip.exe" install -e "apps\api"
)

if not exist "apps\web\node_modules" (
    echo [2/3] Installing frontend npm dependencies in apps\web...
    pushd "apps\web"
    call npm install
    popd
)

echo [1/2] Starting OpenKhazana FastAPI Backend on http://localhost:8000 (AUTH_MODE=%AUTH_MODE%) ...
start "OpenKhazana API (Port 8000)" cmd /k "cd /d "%~dp0apps\api" && set "DATABASE_URL=%DATABASE_URL%" && set "AUTH_MODE=%AUTH_MODE%" && set "SCRAPERS_ENABLED=%SCRAPERS_ENABLED%" && .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

echo [2/2] Starting OpenKhazana Next.js Web App on http://localhost:3000 ...
start "OpenKhazana Web (Port 3000)" cmd /k "cd /d "%~dp0apps\web" && set "NEXT_PUBLIC_API_BASE_URL=%NEXT_PUBLIC_API_BASE_URL%" && set "INTERNAL_API_BASE_URL=%INTERNAL_API_BASE_URL%" && npm run dev"

echo.
echo ============================================================
echo   Both services have been launched in separate windows:
echo     - Web UI:   http://localhost:3000
echo     - API Docs: http://localhost:8000/docs
echo     - Mode:     AUTH_MODE=%AUTH_MODE% (Zero-login local mode)
echo ============================================================
echo.
pause
