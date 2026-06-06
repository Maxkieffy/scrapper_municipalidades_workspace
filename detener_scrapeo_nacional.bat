@echo off
chcp 65001 >nul
title Detener scrapeo nacional
set "BASE=%~dp0"

if not exist "%BASE%scraper_nacional.pid" (
  echo No se encontro el archivo con el PID del proceso.
  pause
  exit /b 1
)

set /p PID=<"%BASE%scraper_nacional.pid"
echo Se intentara detener el proceso de scrapeo con PID %PID%.
choice /M "Desea detenerlo ahora"
if errorlevel 2 exit /b 0

taskkill /PID %PID% /T /F
if errorlevel 1 (
  echo No se pudo detener el proceso. Puede que ya haya terminado.
) else (
  del /Q "%BASE%scraper_nacional.pid" 2>nul
  echo Proceso detenido.
)
pause
