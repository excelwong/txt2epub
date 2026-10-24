@echo off
chcp 65001 >nul
cd /d "%~dp0"
python txt2epub.py
if errorlevel 1 pause
