@echo off
cd /d "%~dp0"
title proj3 workbench - STOP

echo.
echo   Stopping proj3 workbench (port 8103)...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*uvicorn*server:app*' -or $_.CommandLine -like '*server.py*') }; if ($p) { $p | ForEach-Object { Write-Host ('  stopped PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force } } else { Write-Host '  no workbench process found' }; Start-Sleep -Milliseconds 1200; $b = Get-NetTCPConnection -LocalPort 8103 -State Listen -ErrorAction SilentlyContinue; if ($b) { Write-Host ('  WARN: port 8103 still in use (PID ' + $b.OwningProcess + ')') } else { Write-Host '  OK: port 8103 is now free' }"

echo.
echo   Done. You can close this window.
echo.
pause
