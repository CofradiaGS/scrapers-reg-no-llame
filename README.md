# Sistema Multi-Scraper Distribuido: Registro No Llame (Arquitectura Hexagonal)

Sistema industrial de scraping y enriquecimiento telefónico concurrente con arquitectura hexagonal, consumo transaccional mediante `FOR UPDATE SKIP LOCKED` sobre MySQL 8 VPS, y orquestación de cluster **Master-Worker en Red Local (LAN)** con sincronización automática vía Git.

---

## 📑 Tabla de Contenidos
1. [Arquitectura del Cluster LAN](#1-arquitectura-del-cluster-lan)
2. [🤖 Guía Rápida para Antigravity / IA en una Nueva PC Worker](#2--guía-rápida-para-antigravity--ia-en-una-nueva-pc-worker)
3. [Puesta en Marcha Manual de un Nodo Worker (PC Hija)](#3-puesta-en-marcha-manual-de-un-nodo-worker-pc-hija)
4. [Vinculación desde la PC Madre (Master)](#4-vinculación-desde-la-pc-madre-master)
5. [Flujos de Trabajo Habituales](#5-flujos-de-trabajo-habituales)
6. [Estructura del Proyecto y Documentación Técnica](#6-estructura-del-proyecto-y-documentación-técnica)

---

## 1. Arquitectura del Cluster LAN

El sistema opera sobre la misma red local (oficina/hogar) bajo un esquema **PC Madre (Master)** y **PCs Hijas (Workers/Nodos)**:

```mermaid
flowchart TD
    subgraph PC_Madre [PC Madre / Master (192.168.1.X)]
        MC[cluster/master_control.py / run_master.bat]
        UI[Dashboard Web http://localhost:5000]
        NODES[cluster/nodes.json]
    end

    subgraph RedLocal [Red Local LAN - Puerto TCP 5555]
        direction LR
        W1[PC Hija 1: node_agent.py]
        W2[PC Hija 2: node_agent.py]
        W3[PC Hija 3: node_agent.py]
    end

    subgraph GitHub [GitHub Remoto]
        ORIGIN[(origin/main)]
    end

    subgraph VPS [VPS MySQL 8 Central]
        DB[(queue_registro_no_llame)]
    end

    PC_Madre -->|1. git push| ORIGIN
    PC_Madre -->|2. Broadcast POST /update| RedLocal
    RedLocal -->|3. git reset --hard origin/main| ORIGIN
    PC_Madre -->|4. Orden de Scraping POST /start| RedLocal
    RedLocal -->|5. Consumo transaccional con SKIP LOCKED| DB
```

* **PC Madre**: Desarrolla, hace commits a GitHub y controla el cluster vía CLI o Panel Web.
* **PCs Hijas**: Ejecutan en segundo plano `cluster/node_agent.py` (puerto `5555`). Reciben órdenes de la Madre para auto-actualizarse (`git pull/reset`), iniciar scrapers (`claro`, `movistar`, `personal`, `iris`, etc.) y reportar logs/métricas.
* **VPS MySQL**: Todas las PCs consumen de la misma cola global sin colisiones gracias a bloqueos `FOR UPDATE SKIP LOCKED`.

---

## 2. 🤖 Guía Rápida para Antigravity / IA en una Nueva PC Worker

> **Si eres un agente de Antigravity (o cualquier asistente de IA) ejecutándote en una PC recién clonada:**  
> Ejecuta las siguientes 4 fases para dejar esta máquina completamente vinculada y lista para recibir órdenes.

### Opción 1: Instalación Automática en 1 Clic (Recomendada)
Simplemente haz doble clic en **[`setup_pc.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/setup_pc.bat)**.
El script detectará e instalará automáticamente:
- Python 3.11 (si la PC no lo tiene).
- Tor Portable oficial para rotación de IPs anónimas.
- Entorno virtual (`venv`) y librerías de `requirements.txt`.
- Navegador Chromium para Playwright.
- Plantilla de variables de entorno `.env`.
- Regla en el Firewall de Windows para el puerto `5555`.
- Configuración de inicio automático con Windows (`shell:startup`).

---

### Opción 2: Para Asistentes de IA (Antigravity / Gemini)
Si acabas de clonar este repositorio en otra máquina y abriste Antigravity, puedes pegarle este prompt directamente:

```text
Ejecuta el script setup_pc.bat (o powershell -ExecutionPolicy Bypass -File scripts\install_dependencies.ps1 -Unattended) para instalar automáticamente todas las dependencias, configurar el puerto 5555 en el firewall y levantar el agente con run_node.bat.
```

---

### Opción 3: Instalación Manual Paso a Paso

#### Fase A: Preparación del Entorno Python
```powershell
# 1. Crear y activar entorno virtual
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Instalar navegadores para Playwright (Chromium)
playwright install chromium
```

### Fase B: Configuración del Archivo `.env`
Crea el archivo `.env` en la raíz del proyecto con la conexión al VPS de base de datos (copiar los valores del `.env` de la PC Madre):
```env
DB_HOST=TU_IP_O_HOST_VPS
DB_PORT=3306
DB_USER=TU_USUARIO_DB
DB_PASSWORD=TU_PASSWORD_DB
DB_NAME=TU_NOMBRE_DB
```

### Fase C: Regla de Firewall para el Agente LAN (Puerto 5555)
Ejecutar en PowerShell con permisos de Administrador:
```powershell
New-NetFirewallRule -DisplayName "Scraper Node Agent LAN" -Direction Inbound -LocalPort 5555 -Protocol TCP -Action Allow
```

### Fase D: Identificar la IP Local y Probar el Agente
1. Obtener la IP local de esta PC para suministrarla a la PC Madre:
   ```powershell
   Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias 'Wi-Fi*','Ethernet*' | Select-Object IPAddress, InterfaceAlias
   ```
2. Ejecutar el agente de nodo:
   ```powershell
   .\run_node.bat
   ```
   *Debe mostrar: `AGENTE DE NODO LAN ACTIVO EN http://0.0.0.0:5555`*.

---

## 3. Puesta en Marcha y Centinela 24/7 (PC Hija)

### A. Tarea Programada cada 10 Minutos (Recomendado 24/7)
Para que Windows vigile el agente cada 10 minutos y lo levante en segundo plano si la PC se reinicia o se cierra el proceso:
1. Haz doble clic en **[`crear_tarea_programada.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/crear_tarea_programada.bat)**.
2. **Garantía Singleton Estricta**:
   - Si el agente ya está corriendo: El chequeo termina en milisegundos sin abrir nada nuevo.
   - Si se cerró o la PC encendió: Lo levanta en segundo plano de forma invisible.
   - Protegido por bloqueo de kernel en `node_agent.lock` y política `IgnoreNew` de Windows para imposibilitar procesos duplicados.

### B. Auto-Arranque Tradicional (shell:startup)
Como alternativa o complemento:
1. Presiona `Win + R` -> escribe `shell:startup` y presiona Enter.
2. Crea un acceso directo a [`run_node.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/run_node.bat) en esa carpeta.

---

## 4. Vinculación desde la PC Madre (Master)

Una vez que el agente está activo en la PC Hija, en la **PC Madre** puedes vincularla de dos formas:

### Opción 1: Auto-Descubrimiento en la Red (Más fácil)
En la PC Madre ejecuta:
```powershell
python cluster/master_control.py scan --auto-add
```
El script escaneará automáticamente la subred local (`192.168.1.X`), detectará la nueva PC y la guardará en `cluster/nodes.json`.

### Opción 2: Registro Manual
```powershell
python cluster/master_control.py add-node --name "PC-Oficina-2" --ip "192.168.1.52"
```

### Comprobación de Conectividad
```powershell
python cluster/master_control.py status
```
Verás la tabla con el nuevo nodo reportando estado `IDLE`, su commit de Git y uso de recursos.

---

## 5. Flujos de Trabajo Habituales

### A. Subir Cambios y Auto-Actualizar Todo el Cluster (Git Push & Sync)
Cuando hagas cambios en el código desde la PC Madre y quieras que **todas las PCs del cluster se actualicen en 3 segundos**:
```powershell
python cluster/master_control.py push-and-update
```
> **Comportamiento inteligente**: El agente ejecuta `git fetch origin main && git reset --hard origin/main`. Si alguna PC estaba scrapeando en ese momento, el agente detiene limpiamente el proceso, actualiza los archivos y lo reinicia de inmediato con el código nuevo.

### B. Enviar Órdenes de Scraping
```powershell
# Iniciar Claro con 8 workers en una PC específica:
python cluster/master_control.py start --pc "PC-Oficina-2" --scraper claro --workers 8 --tor

# Iniciar Movistar en otra:
python cluster/master_control.py start --pc "PC-Oficina-3" --scraper movistar --workers 8

# Iniciar el mismo scraper en TODO el cluster al unísono:
python cluster/master_control.py start --all --scraper personal --workers 8

# Detener el scraping en todo el cluster:
python cluster/master_control.py stop --all
```

### C. Dashboard Web Visual
Para controlar el cluster con clics desde el navegador web:
```powershell
python cluster/master_control.py ui
```
Abre `http://localhost:5000` con tarjetas para cada PC, botones de inicio/parada, selector de scraper y visualizador de logs en tiempo real.

---

## 6. Estructura del Proyecto y Documentación Técnica

* **[`cluster/node_agent.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/cluster/node_agent.py)**: Servidor HTTP multihilo del nodo worker (puerto 5555).
* **[`cluster/master_control.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/cluster/master_control.py)**: Orquestador central con CLI, escáner LAN y Dashboard Web.
* **[`cluster/nodes.json`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/cluster/nodes.json)**: Registro de inventario de PCs y sus direcciones IP.
* **[`run_node.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/run_node.bat)**: Lanzador del worker para PCs Hijas.
* **[`run_master.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/run_master.bat)**: Menú interactivo para la PC Madre.
* **[`docs/README.md`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/docs/README.md)**: Índice exhaustivo de la documentación técnica en 7 capas.
* **[`docs/07_operacion_y_runbooks/orquestacion_cluster_lan.md`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/docs/07_operacion_y_runbooks/orquestacion_cluster_lan.md)**: Especificación formal de la arquitectura Master-Worker, endpoints REST y runbooks.
* **[`AGENTS.md`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/AGENTS.md)**: Reglas inviolables de arquitectura hexagonal y verificación de enlaces.
