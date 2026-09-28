@echo off
REM ============================================================
REM ASCII-only bootstrap -- do not put non-ASCII bytes above the
REM marker line below. cmd.exe mis-parses multi-byte UTF-8 lines
REM when the code page changes while the script is already running
REM (Chinese Windows starts at 936). So: switch code page, then
REM re-enter this file in a NEW cmd process, now decoded as UTF-8.
REM A same-process `call` does NOT work -- the parser snapshots its
REM code page at process start. Verified by experiment.
REM ============================================================
if not "%~1"=="__utf8" (
    chcp 65001 >nul
    cmd /d /c call "%~f0" __utf8
    exit /b %errorlevel%
)
REM ==================== end of bootstrap =====================
title AI 招聘与人才发展平台

cd /d "%~dp0.."

echo ================================================================
echo   AI 招聘与人才发展平台
echo ================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   [错误] 尚未安装运行环境。
    echo   请先双击本目录上层的 install.bat 完成安装。
    echo.
    pause
    exit /b 1
)

echo   正在启动服务...
echo   访问地址: http://127.0.0.1:8000
echo   登录账号: admin / 123456
echo   停止服务: 按 Ctrl+C
echo.

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd backend
start "" http://127.0.0.1:8000
"..\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
