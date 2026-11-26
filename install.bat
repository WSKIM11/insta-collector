@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 이슈·밈 수집기 설치 ===

set "PY="
if exist "%USERPROFILE%\anaconda3\python.exe" set "PY=%USERPROFILE%\anaconda3\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
  echo [오류] 파이썬을 찾지 못했습니다. https://www.python.org 에서 설치 후 다시 실행하세요.
  pause & exit /b 1
)
echo 파이썬: %PY%
"%PY%" -m pip install -q requests beautifulsoup4 lxml

set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"
echo "%PY%"> python_path.txt

echo 예약 작업 등록: 매시간 정각 + 매일 23:55
schtasks /create /tn "InstaCollector_Hourly" /tr "\"%PYW%\" \"%~dp0collector.py\"" /sc hourly /mo 1 /st 00:00 /f
schtasks /create /tn "InstaCollector_DayEnd" /tr "\"%PYW%\" \"%~dp0collector.py\"" /sc daily /st 23:55 /f

echo.
echo 설치 완료. config.json 에 텔레그램 봇 토큰과 chat_id 를 넣은 뒤 run_now.bat 으로 바로 테스트해 보세요.
pause
