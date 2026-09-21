@echo off
chcp 65001 > nul
title Agente de Nodo LAN - Scraper Worker
color 0B

cd /d "%~dp0"

set "PY_CMD=python"
if exist "%~dp0venv\Scripts\python.exe" set "PY_CMD=%~dp0venv\Scripts\python.exe"

:RUN_LOOP
echo ============================================================================
echo [%DATE% %TIME%] Iniciando Agente de Nodo LAN en puerto 5555...
echo ============================================================================

"%PY_CMD%" cluster/node_agent.py %*

set EXIT_CODE=%ERRORLEVEL%
echo.
echo [%DATE% %TIME%] El agente se cerro con codigo: %EXIT_CODE%

if "%EXIT_CODE%"=="0" (
    echo [INFO] Cierre limpio confirmado por el usuario.
    goto END
)

echo [ALERTA] Caida inesperada. Reiniciando agente en 5 segundos...
timeout /t 5 /nobreak > nul
goto RUN_LOOP

:END
pause
