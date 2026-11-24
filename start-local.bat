@echo off
setlocal
title OpenKhazana - Local Launcher

echo ============================================================
echo   OpenKhazana - Pakistan Market Hub ^& Multi-Asset Tracker
echo ============================================================
echo.

cd /d "%~dp0"

set "WEB_MODE=prod"
set "FORCE_REBUILD=0"
for %%A in (%*) do (
    if /I "%%~A"=="--dev" set "WEB_MODE=dev"
    if /I "%%~A"=="--rebuild" set "FORCE_REBUILD=1"
)

if not defined DATABASE_URL set "DATABASE_URL=sqlite+aiosqlite:///./openkhazana_local.db"
if not defined AUTH_MODE set "AUTH_MODE=local"
if not defined NEXT_PUBLIC_API_BASE_URL set "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000"
if not defined INTERNAL_API_BASE_URL set "INTERNAL_API_BASE_URL=http://localhost:8000"
if not defined SCRAPERS_ENABLED set "SCRAPERS_ENABLED=true"

if not exist "apps\api\.venv\Scripts\python.exe" (
    echo [Setup 1/3] Creating Python virtual environment in apps\api\.venv...
    python -m venv "apps\api\.venv"
    "apps\api\.venv\Scripts\python.exe" -m pip install --upgrade pip
    "apps\api\.venv\Scripts\pip.exe" install -e "apps\api"
)

if not exist "apps\web\node_modules" (
    echo [Setup 2/3] Installing frontend npm dependencies in apps\web...
    pushd "apps\web"
    call npm install
    popd
)

if /I "%WEB_MODE%"=="prod" (
    if "%FORCE_REBUILD%"=="1" goto :do_build
    if not exist "apps\web\.next\BUILD_ID" goto :do_build
    goto :start_services
:do_build
    echo [Setup 3/3] Building fast compiled Next.js production bundle...
    pushd "apps\web"
    call npm run build
    popd
)

:start_services
echo [1/2] Starting OpenKhazana FastAPI Backend on http://localhost:8000 (AUTH_MODE=%AUTH_MODE%) ...
start "OpenKhazana API (Port 8000)" cmd /k "cd /d "%~dp0apps\api" && set "DATABASE_URL=%DATABASE_URL%" && set "AUTH_MODE=%AUTH_MODE%" && set "SCRAPERS_ENABLED=%SCRAPERS_ENABLED%" && .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000"

if /I "%WEB_MODE%"=="dev" (
    echo [2/2] Starting OpenKhazana Next.js Dev Server on http://localhost:3000 ...
    start "OpenKhazana Web Dev (Port 3000)" cmd /k "cd /d "%~dp0apps\web" && set "NEXT_PUBLIC_API_BASE_URL=%NEXT_PUBLIC_API_BASE_URL%" && set "INTERNAL_API_BASE_URL=%INTERNAL_API_BASE_URL%" && npm run dev"
) else (
    echo [2/2] Starting OpenKhazana Next.js Fast Production Server on http://localhost:3000 ...
    start "OpenKhazana Web (Port 3000)" cmd /k "cd /d "%~dp0apps\web" && set "NEXT_PUBLIC_API_BASE_URL=%NEXT_PUBLIC_API_BASE_URL%" && set "INTERNAL_API_BASE_URL=%INTERNAL_API_BASE_URL%" && npx next start -H 0.0.0.0 -p 3000"
)

echo.
echo ============================================================
echo   Both services have been launched in separate windows:
echo     - Web UI:   http://localhost:3000 (%WEB_MODE% mode)
echo     - API Docs: http://localhost:8000/docs
echo     - Mode:     AUTH_MODE=%AUTH_MODE% (Zero-login local mode)
echo     - Tip:      Pass --dev for hot-reload or --rebuild after code edits
echo ============================================================
echo.
pause
