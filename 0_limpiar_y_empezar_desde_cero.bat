@echo off
chcp 65001 >nul
title Limpiar y comenzar scrapeo nacional desde cero
set "BASE=%~dp0"

echo Esta accion borrara solamente resultados y archivos de progreso del scraper.
echo Se conservaran el codigo, los lanzadores y municipalidades_portal_345.csv.
echo.
choice /M "Desea limpiar e iniciar desde cero"
if errorlevel 2 exit /b 0

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[IO.Path]::GetFullPath('%BASE%');" ^
  "$pidFile=Join-Path $root 'scraper_nacional.pid';" ^
  "if(Test-Path $pidFile){$p=[int](Get-Content $pidFile); Stop-Process -Id $p -Force -ErrorAction SilentlyContinue};" ^
  "$files=@('actas_descargadas_metadata.csv','estado_municipalidades.csv','fallas_scrapeo.txt','ejecucion_scraper.log','ejecucion_scraper_error.log','scraper_nacional.pid','municipalidades_pendientes_primera_pasada.csv','municipalidades_para_reintentar.csv');" ^
  "foreach($name in $files){$path=Join-Path $root $name; if(Test-Path -LiteralPath $path){Remove-Item -LiteralPath $path -Force}};" ^
  "$municipios=Import-Csv (Join-Path $root 'municipalidades_portal_345.csv') -Delimiter ';';" ^
  "$slugs=@{}; foreach($m in $municipios){$d=$m.municipalidad.Normalize([Text.NormalizationForm]::FormD); $chars=foreach($c in $d.ToCharArray()){if([Globalization.CharUnicodeInfo]::GetUnicodeCategory($c) -ne [Globalization.UnicodeCategory]::NonSpacingMark){$c}}; $slug=((-join $chars).ToLowerInvariant().Trim() -replace '\s+','_' -replace '[^a-z0-9_-]',''); $slugs[$slug]=$true};" ^
  "Get-ChildItem -LiteralPath $root -Directory | ForEach-Object {if($_.FullName.StartsWith($root+'\') -and ($slugs.ContainsKey($_.Name) -or $_.Name -like 'prueba*')){Remove-Item -LiteralPath $_.FullName -Recurse -Force}}"

echo.
echo Limpieza terminada. Listo para iniciar scrapeo nacional...

call "%BASE%ejecutar_scrapeo_nacional_firefox.bat"
