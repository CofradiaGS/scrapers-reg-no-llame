@echo off
:: ============================================================================
:: DEMONIO DE ARRANQUE PERMANENTE 24/7 - SCRAPER IRIS VPS CENTRAL
:: ============================================================================
:: Este script mantiene el supervisor activo de forma indefinida. Si el proceso
:: principal se cierra de forma inesperada o la máquina se reinicia, el demonio
:: espera 10 segundos y vuelve a levantarlo automáticamente.
::
:: Para detenerlo por completo: Cierra esta ventana de consola o presiona Ctrl+C.
:: ============================================================================

chcp 65001 > nul
title Scraper IRIS Movistar - Supervisor 24/7 Industrial
color 0A

cd /d "%~dp0"

:RUN_LOOP
echo ============================================================================
echo [%DATE% %TIME%] Levantando Supervisor 24/7 para IRIS...
echo ============================================================================

python supervisor_vps.py %*

set EXIT_CODE=%ERRORLEVEL%
echo.
echo [%DATE% %TIME%] El proceso supervisor se detuvo con código: %EXIT_CODE%

:: Si el usuario detuvo el proceso con Ctrl+C (código de salida común 0 o 3221225786),
:: o si se desea reiniciar siempre ante fallas:
if "%EXIT_CODE%"=="0" (
    echo [INFO] Cierre limpio confirmado por el usuario. Finalizando demonio.
    goto END
)

echo [ALERTA] Caída inesperada detectada. Reiniciando supervisor en 10 segundos...
timeout /t 10 /nobreak > nul
goto RUN_LOOP

:END
echo Presiona cualquier tecla para cerrar.
pause > nul
