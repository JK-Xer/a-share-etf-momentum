@echo off
title A股 ETF 量化工具 - 自检
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

echo 运行引擎自检（回测/指标/策略数值与手算对照）...
%PY% selfcheck.py
if errorlevel 1 (
    echo.
    echo [失败] 自检未通过，请勿使用本工具做决策。
) else (
    echo.
    echo [通过] 引擎数值正确。
)
pause
