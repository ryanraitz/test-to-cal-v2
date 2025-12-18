@echo off
echo Generating progress report (patched project)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0generate_progress_report_TEST_TO_CAL_PATCHED.ps1"
pause
