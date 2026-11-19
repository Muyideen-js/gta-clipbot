@echo off
echo This makes your PC download new clips into Desktop\GTA Clips every 10 minutes
echo (only while the PC is on). Run "Turn off auto-download.bat" to stop it.
echo.
schtasks /Create /F /SC MINUTE /MO 10 /TN "GTA Clips - get new clips" /TR "\"C:\Users\DELL\Desktop\clibot\.venv\Scripts\pythonw.exe\" \"C:\Users\DELL\Desktop\clibot\tools\get_clips.py\""
echo.
echo Done. New clips will appear by themselves.
pause
