@echo off
chcp 65001 >nul
title Estado del scrapeo nacional
set "BASE=%~dp0"

powershell -NoProfile -Command "$root=[IO.Path]::GetFullPath('%BASE%'); $path=Join-Path $root 'estado_municipalidades.csv'; if(Test-Path $path){$rows=Import-Csv $path -Delimiter ';'; $latest=@{}; foreach($row in $rows){$latest[$row.codigo_portal]=$row}; $current=@($latest.Values); Write-Host ('Municipalidades con estado: ' + $current.Count + ' de 345'); $current | Group-Object estado | Select-Object Name,Count | Format-Table -AutoSize}; foreach($name in @('municipalidades_pendientes_primera_pasada.csv','municipalidades_para_reintentar.csv')){$p=Join-Path $root $name; if(Test-Path $p){$count=@(Import-Csv $p -Delimiter ';').Count; Write-Host ($name + ': ' + $count)}}; Write-Host 'Ultimas lineas:'; Get-Content (Join-Path $root 'ejecucion_scraper.log') -Tail 20 -ErrorAction SilentlyContinue"

pause
