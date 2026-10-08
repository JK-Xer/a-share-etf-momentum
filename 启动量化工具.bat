@echo off
title A股 ETF 量化工具
cd /d "%~dp0"

rem ---- 查找 Python：优先 py 启动器，其次 python，最后已知安装路径 ----
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (python --version >nul 2>&1 && set "PY=python")
if not defined PY if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined PY (
    echo [错误] 未找到 Python，请先安装 Python 3.10+。
    pause
    exit /b 1
)

echo ============================================
echo   A股 ETF 量化工具  http://localhost:8501
echo   浏览器将自动打开；关闭本窗口即退出程序
echo ============================================
%PY% -m streamlit run app.py --browser.gatherUsageStats false %*
if errorlevel 1 (
    echo.
    echo [错误] 启动失败。若是首次使用，请先双击「安装依赖.bat」。
    pause
)
