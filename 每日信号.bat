@echo off
title A股 ETF 量化工具 - 每日信号
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (python --version >nul 2>&1 && set "PY=python")
if not defined PY if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PY (
    echo [错误] 未找到 Python，请先安装 Python 3.10+。
    pause
    exit /b 1
)

echo 正在更新行情数据并生成今日信号（约 1 分钟）...
%PY% run_daily.py %*
echo.
echo [注意] 信号由历史回测策略生成，不构成投资建议；回测收益不代表未来表现。
pause
