@echo off
schtasks /delete /tn "InstaCollector_Hourly" /f
schtasks /delete /tn "InstaCollector_DayEnd" /f
echo 예약 작업을 지웠습니다. (수집된 data 폴더는 그대로 남아 있습니다)
pause
