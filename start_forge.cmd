@echo off
rem  Forge - double-click this to start everything Blender needs.
rem  The real work is in start_forge.ps1 next to this file; this wrapper only
rem  exists so the artist has something to double-click.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_forge.ps1"
if errorlevel 1 pause
