@echo off
:: ============================================================================
:: DEMONIO DE ARRANQUE PERMANENTE 24/7 - SUPERVISOR INDUSTRIAL DATUAR (TOR MULTI-INSTANCE)
:: ============================================================================
:: Mantiene 20 workers concurrentes con Tor Multi-Instance particionado (máx 3 workers/puerto),
:: aislamiento de circuitos por stream y umbral de efectividad garantizado > 95%.
:: Auto-recuperación continua ante reinicios o cortes.
::
:: Para detener: Cerrar ventana de consola o presionar Ctrl+C.
:: ============================================================================

chcp 65001 > nul
title Supervisor Industrial 24/7 - DATUAR TOR (20 Workers ^| Efectividad > 95%%)
color 0B

cd /d "%~dp0"

:RUN_LOOP
echo ============================================================================
echo [%DATE% %TIME%] Iniciando Supervisor Industrial DATUAR (20 Workers ^| Tor Pool ^| Efectividad > 95%%)...
echo ============================================================================

venv\Scripts\python.exe main.py supervise --scraper datuar --queue-type cola_automatizacion --auto-id scraper_datuar --tor --workers 20 --batch-size 50 --min-success-rate 95.0 %*

set EXIT_CODE=%ERRORLEVEL%
echo.
echo [%DATE% %TIME%] El proceso supervisor finalizó con código: %EXIT_CODE%

if "%EXIT_CODE%"=="0" (
    echo [INFO] Cierre limpio confirmado por el usuario. Finalizando demonio.
    goto END
)

echo [ALERTA] Caída o interrupción detectada. Reiniciando supervisor en 10 segundos...
timeout /t 10 /nobreak > nul
goto RUN_LOOP

:END
echo Presiona cualquier tecla para cerrar.
pause > nul
