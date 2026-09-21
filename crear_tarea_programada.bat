@echo off
:: ============================================================================
:: REGISTRAR TAREA PROGRAMADA DE WINDOWS (WATCHDOG CADA 10 MINUTOS)
:: ============================================================================
:: Este script configura en Windows una tarea periódica que vigila el agente
:: de nodo cada 10 minutos.
::
:: GARANTÍAS:
:: 1. Si ya está corriendo, NO crea ningún proceso nuevo (Ignorado).
:: 2. Si se cayó o la PC se reinició, lo levanta automáticamente.
:: 3. Garantiza ESTRICTAMENTE 1 sola instancia (Singleton Lock a nivel de SO).
:: ============================================================================

chcp 65001 > nul
title Registrar Tarea Programada - Watchdog 10 Minutos
color 0B

cd /d "%~dp0"

echo ============================================================================
echo   CONFIGURANDO TAREA PROGRAMADA DE WINDOWS (CADA 10 MINUTOS)
echo ============================================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\register_watchdog_task.ps1"

echo.
echo Presiona cualquier tecla para salir...
pause > nul
