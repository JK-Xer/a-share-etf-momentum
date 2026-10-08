@echo off
title A股 ETF 量化工具 - 备份打包
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

set "PROJDIR=%~dp0"
set "STAGE=%TEMP%\etf_quant_stage"
rd /s /q "%STAGE%" >nul 2>&1
mkdir "%STAGE%" >nul 2>&1
if not exist "%STAGE%" (
    echo [错误] 创建临时目录失败。
    pause
    exit /b 1
)

echo 收集程序文件...
copy /y "%~dp0app.py" "%STAGE%\" >nul
copy /y "%~dp0config.py" "%STAGE%\" >nul
copy /y "%~dp0run_daily.py" "%STAGE%\" >nul
copy /y "%~dp0selfcheck.py" "%STAGE%\" >nul
copy /y "%~dp0*.md" "%STAGE%\" >nul
copy /y "%~dp0requirements.txt" "%STAGE%\" >nul
copy /y "%~dp0*.bat" "%STAGE%\" >nul
xcopy /e /i /y "%~dp0quant" "%STAGE%\quant\" >nul
rd /s /q "%STAGE%\quant\__pycache__" >nul 2>&1
if exist "%~dp0.streamlit" xcopy /e /i /y "%~dp0.streamlit" "%STAGE%\.streamlit\" >nul
if exist "%~dp0data" xcopy /e /i /y "%~dp0data" "%STAGE%\data\" >nul
if exist "%~dp0user_config.json" copy /y "%~dp0user_config.json" "%STAGE%\" >nul
if exist "%~dp0signals_log.csv" copy /y "%~dp0signals_log.csv" "%STAGE%\" >nul

for /f "delims=" %%i in ('%PY% -c "import os,time;print(os.path.join(os.environ['PROJDIR'],'\u91cf\u5316\u5de5\u5177_\u5907\u4efd_'+time.strftime('%%Y%%m%%d')+'.zip'))"') do set "ZIP=%%i"
set "ZIPSTAGE=%STAGE%"
set "ZIPDEST=%ZIP%"
echo 正在压缩备份包...
%PY% -c "import zipfile,os;root=os.environ['ZIPSTAGE'];z=zipfile.ZipFile(os.environ['ZIPDEST'],'w',zipfile.ZIP_DEFLATED);[z.write(os.path.join(dp,fn),os.path.relpath(os.path.join(dp,fn),root)) for dp,dns,fns in os.walk(root) for fn in fns];z.close()"
rd /s /q "%STAGE%" >nul 2>&1

if exist "%ZIP%" (
    echo.
    echo [完成] 已生成：%ZIP%
    echo 拷贝到其他电脑后：解压，装好 Python 3.10+，双击「安装依赖.bat」，再双击「启动量化工具.bat」。
) else (
    echo.
    echo [错误] 压缩失败。
)
pause
