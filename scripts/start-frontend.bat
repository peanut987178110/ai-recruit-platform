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
title AI 招聘与人才发展平台 - 前端

cd /d "%~dp0..\frontend"

echo ================================================================
echo   AI 招聘与人才发展平台 · 前端界面
echo ================================================================
echo.

if not exist "node_modules" (
    echo   [错误] 尚未安装前端依赖。
    echo   请先双击项目根目录的 install.bat 完成安装。
    echo.
    pause
    exit /b 1
)

echo   访问地址: http://127.0.0.1:5173
echo   停止服务: 按 Ctrl+C
echo.

call npm run dev
pause
