@echo off
:: ============================================================================
:: INSTALADOR AUTOMÁTICO DE ENTORNO Y DEPENDENCIAS (TODO EN UNO)
:: ============================================================================
:: Ejecuta este archivo en cualquier PC recién clonada para instalar:
:: - Python 3.11 (si no está instalado)
:: - Tor Portable para rotación de IPs (si no está instalado)
:: - Entorno virtual (venv) y librerías de requirements.txt
:: - Navegador Chromium de Playwright
:: - Archivo .env
:: - Regla del Firewall de Windows en puerto 5555
:: - Acceso directo para inicio automático con Windows
:: ============================================================================

chcp 65001 > nul
title Instalador Automático de Dependencias - Scrapers Reg No Llame
color 0B

cd /d "%~dp0"

echo ============================================================================
echo   INICIANDO INSTALACIÓN AUTOMÁTICA DE DEPENDENCIAS DEL PROYECTO
echo ============================================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_dependencies.ps1" %*

set EXIT_CODE=%ERRORLEVEL%
echo.
if "%EXIT_CODE%"=="0" (
    echo [EXITO] Instalador finalizado correctamente.
) else (
    echo [AVISO] El script finalizó con código de salida: %EXIT_CODE%
)
echo.
echo Presiona cualquier tecla para salir...
pause > nul
