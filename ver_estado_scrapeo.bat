@echo off
chcp 65001 >nul
title Estado del scrapeo nacional
set "BASE=%~dp0"

powershell -NoProfile -Command "$root=[IO.Path]::GetFullPath('%BASE%'); $path=Join-Path $root 'estado_municipalidades.csv'; if(Test-Path $path){$rows=Import-Csv $path -Delimiter ';'; Write-Host ('Registros de estado: ' + $rows.Count); $rows | Group-Object estado | Select-Object Name,Count | Format-Table -AutoSize}; Write-Host 'Ultimas lineas:'; Get-Content (Join-Path $root 'ejecucion_scraper.log') -Tail 20 -ErrorAction SilentlyContinue"

pause
