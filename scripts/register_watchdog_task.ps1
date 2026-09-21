# ============================================================================
# REGISTRADOR DE TAREA PROGRAMADA DE WINDOWS: WATCHDOG DE NODO SCRAPER
# ============================================================================
# Registra una tarea programada que corre cada 10 minutos de forma silenciosa.
# Si el proceso ya está corriendo, no hace nada y finaliza en milisegundos.
# Si el proceso no está corriendo, lo levanta en segundo plano.
# ============================================================================

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Get-Item $PSScriptRoot).Parent.FullName
$WatchdogScript = Join-Path $RepoRoot "scripts\watchdog_node_agent.ps1"
$TaskName = "ScraperNodeAgent_Watchdog"

Write-Host "`n============================================================================" -ForegroundColor Cyan
Write-Host " CONFIGURANDO TAREA PROGRAMADA EN WINDOWS: $TaskName" -ForegroundColor Cyan
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host " Repositorio: $RepoRoot" -ForegroundColor DarkGray
Write-Host " Script de Verificación: $WatchdogScript" -ForegroundColor DarkGray
Write-Host " Frecuencia de chequeo: Cada 10 minutos (24/7)" -ForegroundColor DarkGray
Write-Host " Regla de concurrencia: IgnoreNew (Máximo 1 instancia garantizada)`n" -ForegroundColor DarkGray

if (-not (Test-Path $WatchdogScript)) {
    Write-Error "No se encontró el archivo centinela en: $WatchdogScript"
    exit 1
}

$WatchdogBat = Join-Path $RepoRoot "scripts\run_watchdog.bat"

# ----------------------------------------------------------------------------
# 1. REGISTRAR O ACTUALIZAR LA TAREA (COMPATIBLE CON CUALQUIER USUARIO)
# ----------------------------------------------------------------------------
$registered = $false

# Intento A: schtasks.exe con run_watchdog.bat (funciona tanto en usuario estándar como en admin)
try {
    $schArgs = @("/create", "/tn", $TaskName, "/tr", "`"$WatchdogBat`"", "/sc", "minute", "/mo", "10", "/f")
    $proc = Start-Process -FilePath "schtasks.exe" -ArgumentList $schArgs -NoNewWindow -Wait -PassThru
    if ($proc.ExitCode -eq 0) {
        Write-Host "  [OK] Tarea programada registrada exitosamente con schtasks.exe." -ForegroundColor Green
        $registered = $true
    }
} catch {
    Write-Warning "Aviso con schtasks: $_"
}

# Intento B: Si schtasks falló, usar Register-ScheduledTask nativo
if (-not $registered) {
    try {
        $action = New-ScheduledTaskAction -Execute $WatchdogBat
        $triggerInterval = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggerInterval -Settings $settings -Force | Out-Null
        Write-Host "  [OK] Tarea programada registrada con Register-ScheduledTask." -ForegroundColor Green
        $registered = $true
    } catch {
        Write-Error "No se pudo registrar la tarea programada: $_"
        exit 1
    }
}


# ----------------------------------------------------------------------------
# 5. EJECUTAR UNA PRIMERA COMPROBACIÓN INMEDIATA
# ----------------------------------------------------------------------------
Write-Host "`nEjecutando comprobación inicial..." -ForegroundColor Yellow
Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Host @"
============================================================================
 [EXITO] TAREA PROGRAMADA LISTA Y ACTIVA
============================================================================
- Windows revisará cada 10 minutos si el Agente de Nodo está activo.
- Si ya está corriendo: NO abrirá ningún proceso adicional.
- Si se cerró o la PC se reinició: Lo levantará solo en segundo plano.
- Protegido por Singleton Lock en node_agent.py y política IgnoreNew.
============================================================================
"@ -ForegroundColor Green
