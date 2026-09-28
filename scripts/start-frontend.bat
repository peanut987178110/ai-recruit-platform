@echo off
chcp 65001 >nul
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
