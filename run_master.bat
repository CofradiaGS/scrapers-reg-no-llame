@echo off
:: ============================================================================
:: CONSOLA DE MANDO DE CLUSTER LAN - PC MADRE (MASTER)
:: ============================================================================
:: Abre el menú interactivo para controlar todas las máquinas de la red.
:: ============================================================================

chcp 65001 > nul
title Consola de Mando - Cluster LAN Master
color 0A

cd /d "%~dp0"

:MENU
cls
echo ============================================================================
echo        CONSOLA DE MANDO DE CLUSTER LAN - SCRAPING MULTI-MÁQUINA
echo ============================================================================
echo [1] Ver Estado de todas las PCs (status)
echo [2] Git Push y Actualizar Todo el Cluster (push-and-update)
echo [3] Iniciar Scraper en TODO el Cluster (start-all)
echo [4] Detener Scraper en TODO el Cluster (stop-all)
echo [5] Abrir Panel Web en Navegador (Dashboard UI)
echo [6] Escanear Red Local para Descubrir PCs (scan)
echo [7] Salir
echo ============================================================================
set /p OPCION="Selecciona una opción [1-7]: "

if "%OPCION%"=="1" (
    python cluster/master_control.py status
    pause
    goto MENU
)
if "%OPCION%"=="2" (
    python cluster/master_control.py push-and-update
    pause
    goto MENU
)
if "%OPCION%"=="3" (
    set /p SCRAPER="Scraper a ejecutar (claro, movistar, personal, iris_http, datuar) [claro]: "
    if "%SCRAPER%"=="" set SCRAPER=claro
    set /p WORKERS="Cantidad de workers por PC [8]: "
    if "%WORKERS%"=="" set WORKERS=8
    python cluster/master_control.py start --all --scraper %SCRAPER% --workers %WORKERS%
    pause
    goto MENU
)
if "%OPCION%"=="4" (
    python cluster/master_control.py stop --all
    pause
    goto MENU
)
if "%OPCION%"=="5" (
    echo Iniciando Panel Web... Abre http://localhost:5000 en tu navegador.
    python cluster/master_control.py ui
    pause
    goto MENU
)
if "%OPCION%"=="6" (
    python cluster/master_control.py scan --auto-add
    pause
    goto MENU
)
if "%OPCION%"=="7" (
    exit /b
)

echo Opción inválida.
pause
goto MENU
