@echo off
setlocal

cd /d "%~dp0"
set "CONFIG_DIR=%APPDATA%\hh-applicant-tool"
set "PYTHONUTF8=1"

call "%~dp0.venv\Scripts\hh-applicant-tool.exe" %*
