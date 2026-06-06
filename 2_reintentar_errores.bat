@echo off
chcp 65001 >nul
title Reintentar municipalidades con errores

set "PYTHON=C:\Users\maxha\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "BASE=%~dp0"
set "SCRIPT=%BASE%transparencia_actas_scraper.py"
set "CSV=%BASE%municipalidades_para_reintentar.csv"

echo Se reintentaran las municipalidades que dieron error o quedaron parciales.
echo Ejecute este archivo despues de terminar la primera pasada.
echo.

"%PYTHON%" "%SCRIPT%" --input-csv "%CSV%" --output-dir "%BASE%" --years 2024 2025 --selenium --browser firefox --force

echo.
echo Reintento terminado o interrumpido.
pause
