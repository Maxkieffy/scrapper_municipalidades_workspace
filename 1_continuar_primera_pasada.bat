@echo off
chcp 65001 >nul
title Continuar primera pasada del scrapeo nacional

set "PYTHON=C:\Users\maxha\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "BASE=%~dp0"
set "SCRIPT=%BASE%transparencia_actas_scraper.py"
set "CSV=%BASE%municipalidades_pendientes_primera_pasada.csv"

echo Se procesaran solamente las municipalidades aun no intentadas.
echo Puede detener el proceso con Ctrl+C y volver a ejecutar este archivo.
echo No cierre esta ventana mientras este trabajando.
echo.

"%PYTHON%" "%SCRIPT%" --input-csv "%CSV%" --output-dir "%BASE%" --years 2024 2025 --selenium --browser firefox

echo.
echo Primera pasada terminada o interrumpida.
pause
