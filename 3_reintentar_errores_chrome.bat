@echo off
chcp 65001 >nul
title Reintentar municipalidades con errores usando Chrome

set "PYTHON=C:\Users\maxha\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "BASE=%~dp0"
set "SCRIPT=%BASE%transparencia_actas_scraper.py"
set "CSV=%BASE%municipalidades_para_reintentar.csv"

echo Se reintentaran con Chrome las municipalidades que siguen con error o parciales.
echo Use este archivo como alternativa despues del reintento con Firefox.
echo.

if not exist "%CSV%" (
  echo No existe "%CSV%".
  echo Ejecute primero el scrapeo nacional para crear la lista.
  pause
  exit /b 1
)

"%PYTHON%" "%SCRIPT%" --input-csv "%CSV%" --output-dir "%BASE%." --years 2024 2025 --selenium --browser chrome --force
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Reintento con Chrome terminado o interrumpido.
pause
exit /b %EXIT_CODE%
