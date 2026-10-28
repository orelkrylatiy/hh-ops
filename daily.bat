@echo off
setlocal

cd /d "%~dp0"
set "PYTHONUTF8=1"

rem daily.bat — run scripts/daily.sh (boost + apply + replies) from cmd.
rem Examples:
rem   daily.bat                          dry-run for all profiles
rem   daily.bat --live --with-boost      live full cycle for all profiles
rem   daily.bat --live --profiles 0555 1206 --with-boost

where bash >nul 2>nul
if %errorlevel%==0 (
    bash scripts/daily.sh %*
    goto :eof
)

if exist "%ProgramFiles%\Git\bin\bash.exe" (
    "%ProgramFiles%\Git\bin\bash.exe" scripts/daily.sh %*
    goto :eof
)

echo Git Bash is required to run daily.bat. Install Git for Windows. >&2
exit /b 1
