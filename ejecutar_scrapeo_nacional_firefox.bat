@echo off
chcp 65001 >nul
title Scrapeo nacional de actas municipales 2024-2025

set "PYTHON=C:\Users\maxha\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "BASE=%~dp0"
set "SCRIPT=%BASE%transparencia_actas_scraper.py"
set "CSV=%BASE%municipalidades_portal_345.csv"
set "SALIDA=%BASE%."
set "LOG=%BASE%ejecucion_scraper.log"

echo Inicio: %date% %time%
echo El proceso usa Firefox en segundo plano y puede tardar varias horas.
echo Puede detenerlo con Ctrl+C y volver a ejecutar este archivo para reanudar.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%PYTHON%' '%SCRIPT%' --input-csv '%CSV%' --output-dir '%SALIDA%' --years 2024 2025 --selenium --browser firefox 2>&1 | Tee-Object -FilePath '%LOG%' -Append; exit $LASTEXITCODE"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Fin: %date% %time%
echo Codigo de salida: %EXIT_CODE%
echo Revise "%LOG%" y "%BASE%estado_municipalidades.csv".
pause
exit /b %EXIT_CODE%
