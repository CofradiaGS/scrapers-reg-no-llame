# ============================================================================
# INSTALADOR AUTOMÁTICO DE ENTORNO Y DEPENDENCIAS (PC WORKER / NODO SCRAPER)
# ============================================================================
# Este script se encarga de instalar y configurar TODO lo necesario para que la PC
# quede lista como nodo del cluster:
# 1. Verifica/Instala Python 3.11 si la máquina no lo tiene.
# 2. Verifica/Instala Git si no está presente.
# 3. Detecta/Descarga Tor Portable para rotación de IPs (Claro, Movistar, Personal).
# 4. Crea el entorno virtual (venv).
# 5. Instala las librerías de requirements.txt.
# 6. Instala el navegador Chromium para Playwright.
# 7. Crea el archivo .env a partir de .env.example.
# 8. Configura la regla de Firewall de Windows para el puerto 5555 (Agente LAN).
# 9. Ofrece configurar el arranque automático con Windows (shell:startup).
# ============================================================================

[CmdletBinding()]
param (
    [switch]$Unattended = $false,
    [switch]$SkipTor = $false,
    [switch]$CreateStartupShortcut = $false
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Get-Item $PSScriptRoot).Parent.FullName

function Write-Step {
    param([string]$Message)
    Write-Host "`n============================================================================" -ForegroundColor Cyan
    Write-Host " [PASO] $Message" -ForegroundColor Cyan
    Write-Host "============================================================================" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
}

function Write-WarningMsg {
    param([string]$Message)
    Write-Host "  [AVISO] $Message" -ForegroundColor Yellow
}

function Write-ErrMsg {
    param([string]$Message)
    Write-Host "  [ERROR] $Message" -ForegroundColor Red
}

function Test-IsAdmin {
    $currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ----------------------------------------------------------------------------
# COMPROBACIÓN DE ELEVACIÓN DE PRIVILEGIOS
# ----------------------------------------------------------------------------
$IsAdmin = Test-IsAdmin
if (-not $IsAdmin) {
    Write-WarningMsg "No estás ejecutando como Administrador."
    Write-WarningMsg "La regla de firewall y la instalación automática de software podrían requerir permisos."
    if (-not $Unattended) {
        $elevateChoice = Read-Host "¿Deseas relanzar el instalador como Administrador? (S/N) [S]"
        if ($elevateChoice -eq "" -or $elevateChoice -eq "s" -or $elevateChoice -eq "S") {
            Write-Host "Relanzando con permisos elevados..." -ForegroundColor Yellow
            Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
            exit 0
        }
    }
}

Write-Host @"
============================================================================
   INSTALADOR DE REQUERIMIENTOS Y DEPENDENCIAS - CLUSTER DE SCRAPING
============================================================================
 Directorio del Proyecto: $RepoRoot
 Modo: $(if ($Unattended) { 'Desatendido / Automático' } else { 'Interactivo' })
 Permisos: $(if ($IsAdmin) { 'Administrador' } else { 'Usuario Estándar' })
============================================================================
"@ -ForegroundColor Magenta

# ----------------------------------------------------------------------------
# 1. VERIFICAR O INSTALAR PYTHON
# ----------------------------------------------------------------------------
Write-Step "1/8: Verificando Python en el sistema..."

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$pythonInstalled = $false

if ($pythonCmd) {
    try {
        $pyVersionStr = (& python --version 2>&1).ToString()
        Write-Success "Python detectado: $pyVersionStr"
        $pythonInstalled = $true
    } catch {
        $pythonInstalled = $false
    }
}

if (-not $pythonInstalled) {
    Write-WarningMsg "Python no fue encontrado en el PATH. Procediendo a instalar Python 3.11..."
    
    $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
    $installedViaWinget = $false

    if ($wingetCmd) {
        try {
            Write-Host "Instalando Python 3.11 mediante winget..." -ForegroundColor Yellow
            & winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
            $installedViaWinget = $true
        } catch {
            Write-WarningMsg "Winget falló. Probando descarga directa del instalador oficial..."
        }
    }

    if (-not $installedViaWinget) {
        $pyInstallerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        $installerPath = Join-Path $env:TEMP "python-3.11.9-installer.exe"
        Write-Host "Descargando Python 3.11 desde python.org..." -ForegroundColor Yellow
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $pyInstallerUrl -OutFile $installerPath -UseBasicParsing
        
        Write-Host "Ejecutando instalador silencioso de Python..." -ForegroundColor Yellow
        $installProc = Start-Process -FilePath $installerPath -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait -PassThru
        if ($installProc.ExitCode -eq 0) {
            Write-Success "Python 3.11 instalado correctamente."
        } else {
            Write-ErrMsg "El instalador de Python finalizó con código: $($installProc.ExitCode)"
        }
        Remove-Item $installerPath -ErrorAction SilentlyContinue
    }

    # Refrescar variables de entorno PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

# ----------------------------------------------------------------------------
# 2. VERIFICAR O DESCARGAR TOR PORTABLE
# ----------------------------------------------------------------------------
if (-not $SkipTor) {
    Write-Step "2/8: Verificando / Descargando Tor Portable..."

    $torCandidates = @(
        (Join-Path $RepoRoot "tor\tor.exe"),
        (Join-Path $RepoRoot "tor\Tor\tor.exe"),
        (Join-Path $RepoRoot "Tor\tor.exe"),
        (Join-Path $env:USERPROFILE "Desktop\Tor Browser\Browser\TorBrowser\Tor\tor.exe"),
        (Join-Path $env:LOCALAPPDATA "Tor Browser\Browser\TorBrowser\Tor\tor.exe"),
        (Join-Path $env:ProgramFiles "Tor Browser\Browser\TorBrowser\Tor\tor.exe")
    )

    $torFound = $false
    foreach ($cand in $torCandidates) {
        if (Test-Path $cand) {
            Write-Success "Tor detectado en: $cand"
            $torFound = $true
            break
        }
    }

    if (-not $torFound) {
        $torCmd = Get-Command tor -ErrorAction SilentlyContinue
        if ($torCmd) {
            Write-Success "Tor detectado en PATH del sistema: $($torCmd.Source)"
            $torFound = $true
        }
    }

    if (-not $torFound) {
        Write-WarningMsg "Tor no fue detectado. Descargando Tor Windows Expert Bundle portátil..."
        $torDestDir = Join-Path $RepoRoot "tor"
        if (-not (Test-Path $torDestDir)) {
            New-Item -ItemType Directory -Path $torDestDir | Out-Null
        }

        $torUrl = "https://archive.torproject.org/tor-package-archive/torbrowser/14.0.7/tor-expert-bundle-windows-x86_64-14.0.7.tar.gz"
        $torArchive = Join-Path $env:TEMP "tor-expert-bundle.tar.gz"

        try {
            Write-Host "Descargando paquete oficial de Tor (~21 MB)..." -ForegroundColor Yellow
            Invoke-WebRequest -Uri $torUrl -OutFile $torArchive -UseBasicParsing
            
            Write-Host "Extrayendo en $torDestDir..." -ForegroundColor Yellow
            & tar -xzf $torArchive -C $torDestDir

            if (Test-Path (Join-Path $torDestDir "tor.exe") -or (Test-Path (Join-Path $torDestDir "Tor\tor.exe"))) {
                Write-Success "Tor Portable instalado correctamente en $torDestDir."
            } else {
                Write-WarningMsg "Se extrajo el archivo, pero tor.exe se ubica en un subdirectorio."
            }
            Remove-Item $torArchive -ErrorAction SilentlyContinue
        } catch {
            Write-ErrMsg "No se pudo descargar Tor automáticamente: $_"
            Write-WarningMsg "Puedes instalar Tor Browser manualmente en tu PC si vas a usar rotación de Tor."
        }
    }
} else {
    Write-Step "2/8: Omitiendo verificación de Tor (--SkipTor activado)."
}

# ----------------------------------------------------------------------------
# 3. CREAR ENTORNO VIRTUAL (VENV)
# ----------------------------------------------------------------------------
Write-Step "3/8: Configurando entorno virtual Python (venv)..."

$venvPath = Join-Path $RepoRoot "venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvPip = Join-Path $venvPath "Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Creando entorno virtual en $venvPath..." -ForegroundColor Yellow
    & python -m venv $venvPath
    if (-not (Test-Path $venvPython)) {
        Write-ErrMsg "Fallo al crear el entorno virtual venv."
        exit 1
    }
    Write-Success "Entorno virtual creado exitosamente."
} else {
    Write-Success "Entorno virtual existente detectado en $venvPath."
}

# ----------------------------------------------------------------------------
# 4. INSTALAR DEPENDENCIAS DE REQUIREMENTS.TXT
# ----------------------------------------------------------------------------
Write-Step "4/8: Instalando dependencias de Python (requirements.txt)..."

$reqFile = Join-Path $RepoRoot "requirements.txt"
if (Test-Path $reqFile) {
    Write-Host "Actualizando pip..." -ForegroundColor Yellow
    & $venvPython -m pip install --upgrade pip --quiet

    Write-Host "Instalando paquetes desde requirements.txt..." -ForegroundColor Yellow
    & $venvPip install -r $reqFile
    Write-Success "Todas las dependencias de Python han sido instaladas."
} else {
    Write-WarningMsg "No se encontró requirements.txt en la raíz."
}

# ----------------------------------------------------------------------------
# 5. INSTALAR NAVEGADOR CHROMIUM PARA PLAYWRIGHT
# ----------------------------------------------------------------------------
Write-Step "5/8: Instalando navegadores de Playwright (Chromium)..."

$playwrightExe = Join-Path $venvPath "Scripts\playwright.exe"
if (Test-Path $playwrightExe) {
    Write-Host "Descargando e instalando Chromium para Playwright..." -ForegroundColor Yellow
    & $playwrightExe install chromium
    Write-Success "Navegador Chromium de Playwright instalado con éxito."
} else {
    Write-WarningMsg "Playwright CLI no encontrada en el venv."
}

# ----------------------------------------------------------------------------
# 6. CONFIGURAR ARCHIVO .ENV
# ----------------------------------------------------------------------------
Write-Step "6/8: Verificando archivo de variables de entorno (.env)..."

$envFile = Join-Path $RepoRoot ".env"
$envExample = Join-Path $RepoRoot ".env.example"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Success "Archivo .env creado a partir de .env.example."
        Write-WarningMsg "RECUERDA: Edita el archivo .env con las credenciales de tu base de datos VPS."
    } else {
        Write-WarningMsg "No se encontró .env ni .env.example."
    }
} else {
    Write-Success "Archivo .env detectado correctamente."
}

# ----------------------------------------------------------------------------
# 7. CONFIGURAR REGLA DE FIREWALL DE WINDOWS (PUERTO 5555)
# ----------------------------------------------------------------------------
Write-Step "7/8: Configurando regla del Firewall de Windows para el Agente LAN (Puerto 5555)..."

$ruleName = "Scraper Node Agent LAN"
$existingRule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue

if ($existingRule) {
    Write-Success "Regla de Firewall '$ruleName' ya existe y está activa."
} else {
    if ($IsAdmin) {
        try {
            New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort 5555 -Protocol TCP -Action Allow | Out-Null
            Write-Success "Regla de Firewall '$ruleName' creada en el puerto 5555 TCP."
        } catch {
            Write-ErrMsg "No se pudo crear la regla de firewall: $_"
        }
    } else {
        Write-WarningMsg "Se requieren permisos de Administrador para abrir el puerto 5555 en el Firewall."
        Write-Host "  Ejecuta más tarde en PowerShell de Administrador:" -ForegroundColor DarkGray
        Write-Host "  New-NetFirewallRule -DisplayName `"$ruleName`" -Direction Inbound -LocalPort 5555 -Protocol TCP -Action Allow" -ForegroundColor Yellow
    }
}

# ----------------------------------------------------------------------------
# 8. AUTO-ARRANQUE CON WINDOWS (OPCIONAL)
# ----------------------------------------------------------------------------
Write-Step "8/8: Configuración de Inicio Automático..."

$startupFolder = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Startup)
$shortcutPath = Join-Path $startupFolder "ScraperNodeAgent.lnk"
$runNodeBat = Join-Path $RepoRoot "run_node.bat"

$createShortcut = $CreateStartupShortcut
if (-not $Unattended -and -not $createShortcut) {
    $ans = Read-Host "¿Deseas que este Agente inicie automáticamente al encender la PC? (S/N) [S]"
    if ($ans -eq "" -or $ans -eq "s" -or $ans -eq "S") {
        $createShortcut = $true
    }
}

if ($createShortcut) {
    try {
        $wshShell = New-Object -ComObject WScript.Shell
        $shortcut = $wshShell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $runNodeBat
        $shortcut.WorkingDirectory = $RepoRoot
        $shortcut.Description = "Agente de Nodo LAN para Scraping"
        $shortcut.Save()
        Write-Success "Acceso directo de inicio automático creado en shell:startup."
    } catch {
        Write-WarningMsg "No se pudo crear el acceso directo de inicio: $_"
    }
} else {
    Write-Host "  Inicio automático omitido." -ForegroundColor DarkGray
}

# ----------------------------------------------------------------------------
# RESUMEN FINAL Y DIRECCIÓN IP
# ----------------------------------------------------------------------------
Write-Host "`n============================================================================" -ForegroundColor Green
Write-Host " ¡INSTALACIÓN Y CONFIGURACIÓN COMPLETADA CON ÉXITO!" -ForegroundColor Green
Write-Host "============================================================================" -ForegroundColor Green

$ips = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias 'Wi-Fi*','Ethernet*' -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" }

Write-Host "`n[INFO] Datos para registrar este nodo en la PC Madre:" -ForegroundColor Cyan
Write-Host "   Nombre del Equipo: $env:COMPUTERNAME" -ForegroundColor White
foreach ($ip in $ips) {
    Write-Host "   Dirección IP Local: $($ip.IPAddress) ($($ip.InterfaceAlias))" -ForegroundColor Yellow
}
Write-Host "`nComando para registrar esta PC desde la PC Madre:" -ForegroundColor DarkGray
if ($ips) {
    $cmdExample = 'python cluster/master_control.py add-node --name "{0}" --ip "{1}"' -f $env:COMPUTERNAME, $ips[0].IPAddress
    Write-Host $cmdExample -ForegroundColor Green
} else {
    $cmdExample = 'python cluster/master_control.py add-node --name "{0}" --ip "<IP-DE-ESTA-PC>"' -f $env:COMPUTERNAME
    Write-Host $cmdExample -ForegroundColor Green
}

Write-Host "`nPara iniciar el agente ahora:" -ForegroundColor Cyan
Write-Host "   Haz doble clic en: run_node.bat" -ForegroundColor White
Write-Host "============================================================================`n" -ForegroundColor Green

if (-not $Unattended) {
    $startNow = Read-Host "¿Deseas levantar el Agente de Nodo en esta PC ahora mismo? (S/N) [S]"
    if ($startNow -eq "" -or $startNow -eq "s" -or $startNow -eq "S") {
        Write-Host "Iniciando run_node.bat..." -ForegroundColor Green
        Start-Process -FilePath $runNodeBat
    }
}
