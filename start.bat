@echo off
rem Start Polyglot Agent with an installed Python 3.10+ (PolyglotAgent.exe does the same without this window).
cd /d "%~dp0"
where py >nul 2>nul && (py -3 start.py %* & goto :eof)
python start.py %*
if errorlevel 1 pause
