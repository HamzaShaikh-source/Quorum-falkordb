@echo off
rem Judge mode: the guided demo in one command. Needs Python 3 and Docker Desktop.
where py >nul 2>nul && (py -3 "%~dp0scripts\judge.py" %* & goto :done)
python "%~dp0scripts\judge.py" %*
:done
pause
