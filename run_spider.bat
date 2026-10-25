@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ==================== Windows Console Buffer Fix ====================
REM Fix: Output requires Ctrl+A to display (batch mode buffering issue)
REM Root cause: Windows cmd.exe buffers ~4KB output in batch mode
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8:replace
set PYTHONDONTWRITEBYTECODE=1

REM Force console mode settings (disable virtual terminal buffering)
mode con: cols=150 lines=50 >nul 2>&1
REM Use PowerShell to disable console line buffering and VT processing
powershell -NoProfile -Command "$console = [System.Console]::Out; $console.NewLine = '`r'n'" >nul 2>&1

echo ==========================================
echo   Game Feedback AI Agent - Automation Script
echo ==========================================
echo.

echo [INFO] Checking local proxy ports (17890/17891/4780)...
netstat -an | findstr ":17890.*LISTENING :17891.*LISTENING :4780.*LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Detected local proxy
) else (
    echo [WARN] No proxy detected on common ports.
    echo [WARN] International platforms may fail. Set PROXY_URL in .env if needed.
)

echo.
echo [INFO] Starting main crawler...
echo [INFO] Press Ctrl+C to interrupt at any time
echo.
echo ==========================================

REM Use unbuffered mode + explicit stdin redirect for real-time output
REM python -u ensures no buffering, ^< NUL prevents stdin blocking delay
REM main.py writes run_summary.txt (UTF-8) with per-platform status
python -u -X utf8 main.py < NUL
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% neq 0 (
    echo.
    echo ==========================================
    echo   [ERR] Task failed with exit code: %EXIT_CODE%
    echo   Please check the logs above for details
    echo ==========================================
)

echo.
echo ==========================================================
echo   Platform Crawl Summary
echo ==========================================================
if exist "run_summary.txt" (
    type run_summary.txt
) else (
    echo   [WARN] run_summary.txt not found
)
echo ==========================================================

echo.
echo [INFO] Task finished. Terminal stays open. Press any key to close...
pause >nul
