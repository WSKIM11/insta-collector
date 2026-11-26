@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p PY=<python_path.txt
if not defined PY set "PY=python"
echo 1) 텔레그램에서 만든 봇에게 아무 메시지나 보낸 상태여야 합니다.
%PY% collector.py --get-chat-id
echo.
echo 위 chat_id 숫자를 config.json 의 "chat_id" 에 넣고 저장한 뒤 아무 키나 누르세요.
pause
%PY% collector.py --telegram-test
pause
