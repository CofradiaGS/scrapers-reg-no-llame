# ============================================================================
# WATCHDOG CENTINELA: AGENTE DE NODO LAN (WORKER PC)
# ============================================================================
# Este script se ejecuta periódicamente (cada 10 minutos vía Tarea Programada).
# OBJETIVO ESTRICTO:
# 1. Comprueba si el Agente de Nodo (node_agent.py) está vivo y respondiendo.
# 2. Si YA está corriendo: NO HACE NADA y finaliza de inmediato.
# 3. Si NO está corriendo: Lo levanta en segundo plano de forma totalmente invisible.
# 4. PROTECCIÓN SINGLETON: Garantiza que bajo ninguna circunstancia se ejecuten
#    dos o más instancias al mismo tiempo.
# ============================================================================

$ErrorActionPreference = "SilentlyContinue"
$RepoRoot = (Get-Item $PSScriptRoot).Parent.FullName
$WatchdogLog = Join-Path $RepoRoot "watchdog.log"
$PythonExe = Join-Path $RepoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

$NodeScript = Join-Path $RepoRoot "cluster\node_agent.py"

function Log-Message {
    param([string]$Msg)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] [Watchdog] $Msg"
    Add-Content -Path $WatchdogLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

# ----------------------------------------------------------------------------
# COMPROBACIÓN 1: ¿EL PUERTO 5555 YA ESTÁ ESCUCHANDO?
# ----------------------------------------------------------------------------
$portOpen = $false
try {
    $tcp = Get-NetTCPConnection -LocalPort 5555 -State Listen -ErrorAction SilentlyContinue
    if ($tcp) {
        $portOpen = $true
    }
} catch {
    $portOpen = $false
}

# ----------------------------------------------------------------------------
# COMPROBACIÓN 2: VERIFICAR RESPUESTA HTTP HEALTHCHECK
# ----------------------------------------------------------------------------
$isHealthy = $false
if ($portOpen) {
    try {
        $req = [System.Net.WebRequest]::Create("http://127.0.0.1:5555/health")
        $req.Timeout = 2000
        $resp = $req.GetResponse()
        if ($resp.StatusCode -eq 200) {
            $isHealthy = $true
        }
        $resp.Close()
    } catch {
        $isHealthy = $false
    }
}

# ----------------------------------------------------------------------------
# COMPROBACIÓN 3: VERIFICAR PROCESO EXISTENTE EN WMI
# ----------------------------------------------------------------------------
$existingProcess = Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%node_agent.py%'" -ErrorAction SilentlyContinue

# Si el puerto 5555 ya está abierto O si ya responde HTTP O si ya existe el proceso:
if ($isHealthy -or $portOpen -or $existingProcess) {
    # El agente ya está corriendo. Salir de inmediato sin hacer nada.
    exit 0
}

# ----------------------------------------------------------------------------
# INICIAR EL AGENTE EN SEGUNDO PLANO (MODO SILENCIOSO / SIN VENTANA)
# ----------------------------------------------------------------------------
Log-Message "El Agente de Nodo no está activo. Levantando nuevo proceso singleton..."

try {
    $runNodeBat = Join-Path $RepoRoot "run_node.bat"
    $proc = Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "`"$runNodeBat`"") -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru
    Log-Message "Agente lanzado con éxito (PID: $($proc.Id))."
} catch {
    Log-Message "Error al lanzar el agente: $_"
    exit 1
}

exit 0
