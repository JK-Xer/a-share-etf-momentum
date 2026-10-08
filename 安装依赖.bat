@echo off
title A股 ETF 量化工具 - 安装依赖
cd /d "%~dp0"

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (python --version >nul 2>&1 && set "PY=python")
if not defined PY if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PY (
    echo [错误] 未找到 Python。请先到 python.org 安装 Python 3.10 或更新版本
    echo （安装时勾选 Add to PATH），然后重新运行本脚本。
    pause
    exit /b 1
)

echo 正在安装依赖（使用阿里云镜像，首次约 3-5 分钟）...
%PY% -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
if errorlevel 1 (
    echo.
    echo [失败] 安装出错，请检查网络后重试。
) else (
    echo.
    echo [完成] 依赖已就绪，现在可以双击「启动量化工具.bat」。
)
pause
