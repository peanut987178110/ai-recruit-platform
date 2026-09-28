@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title AI 招聘与人才发展平台 - 安装

cd /d "%~dp0"

REM 前端产物是否已随包提供。离线包带 dist，因此不需要 Node.js；
REM 从源码仓库克隆下来时没有 dist，才需要 Node 去构建。
set HAVE_DIST=0
if exist "frontend\dist\index.html" set HAVE_DIST=1

echo ================================================================
echo   AI 招聘与人才发展平台 · 首次安装
echo ================================================================
echo.
echo   本脚本会：
echo     1. 检查运行环境
echo     2. 创建 Python 虚拟环境并安装后端依赖
echo     3. 准备前端界面
echo.
echo   需要联网，且与国内镜像源可达。整个过程约 3 至 8 分钟。
echo.
pause
echo.

REM ---------- 检查 Python ----------
echo [1/4] 检查 Python...
set PY=
where python >nul 2>&1 && set PY=python
if "!PY!"=="" (
    where py >nul 2>&1 && set PY=py
)
if "!PY!"=="" (
    echo.
    echo   [错误] 未检测到 Python。
    echo   请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/
    echo   安装时务必勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('!PY! --version 2^>^&1') do set PYVER=%%v
echo     Python !PYVER!  已就绪
echo.

REM ---------- 检查 Node（仅在需要构建前端时） ----------
echo [2/4] 检查前端...
if "!HAVE_DIST!"=="1" (
    echo     前端界面已随包提供，无需 Node.js。
    echo.
    goto :backend_deps
)
echo     未发现预构建的前端，需要从源码构建，因此要求 Node.js。
where node >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [错误] 未检测到 Node.js，且本包未附带已构建的前端界面。
    echo   请先安装 Node.js 18 或更高版本：https://nodejs.org/
    echo.
    echo   提示：如果你拿到的是离线包，正常情况下应自带前端界面。
    echo   出现这个提示，说明包不完整，建议重新获取。
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('node --version') do set NODEVER=%%v
echo     Node.js !NODEVER!  已就绪
echo.

:backend_deps
REM ---------- 后端依赖 ----------
echo [3/4] 安装后端依赖...
if not exist ".venv\Scripts\python.exe" (
    echo     创建虚拟环境...
    !PY! -m venv .venv
    if errorlevel 1 (
        echo   [错误] 虚拟环境创建失败。
        pause
        exit /b 1
    )
)
echo     安装 Python 包（使用清华镜像加速）...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple
".venv\Scripts\python.exe" -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r backendequirements.txt
if errorlevel 1 (
    echo     镜像源安装失败，改用官方源重试...
    ".venv\Scripts\python.exe" -m pip install -q -r backendequirements.txt
    if errorlevel 1 (
        echo   [错误] 后端依赖安装失败，请检查网络。
        pause
        exit /b 1
    )
)
echo     后端依赖安装完成
echo.

REM ---------- 前端依赖与构建 ----------
echo [4/4] 准备前端界面...
if "!HAVE_DIST!"=="1" (
    echo     使用包内已构建的界面，跳过 npm 安装与构建。
    echo.
    goto :done
)
pushd frontend
if not exist "node_modules" (
    echo     安装 npm 包...
    call npm install --registry=https://registry.npmmirror.com
    if errorlevel 1 (
        echo     镜像源失败，改用官方源重试...
        call npm install
        if errorlevel 1 (
            echo   [错误] 前端依赖安装失败，请检查网络。
            popd
            pause
            exit /b 1
        )
    )
)
echo     构建界面...
call npm run build
if errorlevel 1 (
    echo   [警告] 前端构建失败。开发模式下仍可运行，但建议排查后重试。
)
popd
echo.

:done
echo ================================================================
echo   安装完成
echo ================================================================
echo.
echo   下一步：
echo     1. 双击 scripts\start-all.bat 启动平台
echo     2. 浏览器访问 http://127.0.0.1:8000
echo     3. 用 admin / 123456 登录（初始超管账号）
echo.
echo   首次启动会自动建库并写入演示数据，浏览器会自动打开。
echo.
echo   如果用不了模型能力，登录后到「阈值与参数 → 模型网关」
echo   填入网关地址与密钥即可，无需改代码。
echo.
pause
