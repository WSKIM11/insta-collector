@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p PY=<python_path.txt
if not defined PY set "PY=python"
%PY% collector.py %*
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd')"') do set TODAY=%%d
if exist "data\%TODAY%\index.html" start "" "data\%TODAY%\index.html"
pause
