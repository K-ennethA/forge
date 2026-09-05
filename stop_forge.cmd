@echo off
rem  Forge - double-click this to stop the background programs again.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_forge.ps1"
if errorlevel 1 pause
