@echo off
chcp 65001 >nul
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
