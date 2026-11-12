@echo off
rem One-click setup. Needs Python 3 and Docker Desktop.
where py >nul 2>nul && (py -3 "%~dp0scripts\setup.py" %* & goto :done)
python "%~dp0scripts\setup.py" %*
:done
pause
