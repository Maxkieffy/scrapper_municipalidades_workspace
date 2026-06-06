@echo off
chcp 65001 >nul
title Prueba del scraper de actas municipales

set "PYTHON=C:\Users\maxha\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "BASE=%~dp0"
set "SCRIPT=%BASE%transparencia_actas_scraper.py"

"%PYTHON%" "%SCRIPT%" --municipalidad "Linares" --codigo-portal MU140 --output-dir "%BASE%prueba_scraper" --years 2025 --selenium --browser firefox --max-actas 1 --force
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Prueba terminada. Revise %BASE%prueba_scraper
pause
exit /b %EXIT_CODE%
