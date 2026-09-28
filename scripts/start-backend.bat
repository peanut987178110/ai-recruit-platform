@echo off
chcp 65001 >nul
title AI 招聘与人才发展平台 - 后端

cd /d "%~dp0.."

echo ================================================================
echo   AI 招聘与人才发展平台 · 后端服务
echo ================================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo   [错误] 尚未安装运行环境。
    echo   请先双击项目根目录的 install.bat 完成安装。
    echo.
    pause
    exit /b 1
)

echo   接口文档: http://127.0.0.1:8000/docs
echo   停止服务: 按 Ctrl+C
echo.

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd backend
"..\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
