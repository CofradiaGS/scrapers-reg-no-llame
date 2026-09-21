# Orquestación y Control de Cluster LAN (Master-Worker)

> **Capa 07: Operación, Despliegue y Runbooks**  
> **Subsistema**: Control Distribuido y Sincronización Automática de Scrapers en Red Local (LAN).

Este documento describe la arquitectura, protocolo de comunicación y runbooks operativos del sistema de orquestación distribuida para granjas de scraping telefónico. Permite operar una flota de computadoras de escritorio y servidores conectados a la misma red local bajo un esquema **Master-Worker**, logrando sincronización instantánea de código vía Git y control total de ejecución desde una única máquina central.

---

## 1. Arquitectura y Topología de Red

El cluster se compone de dos tipos de nodos que interactúan sobre la red local mediante peticiones HTTP REST ligeras (JSON RPC) sin requerir servicios en la nube ni apertura de puertos hacia Internet:

```mermaid
flowchart TD
    subgraph PC_Madre [PC Madre / Master (Desarrollo y Control)]
        GIT_REPO[Repositorio Local Git]
        CLI[Consola CLI master_control.py]
        UI[Dashboard Web http://localhost:5000]
        NODES_CFG[cluster/nodes.json]
    end

    subgraph RedLocal [Red Local LAN - Puerto 5555]
        direction LR
        N1[PC Hija 1: 192.168.1.51]
        N2[PC Hija 2: 192.168.1.52]
        N3[PC Hija 3: 192.168.1.53]
    end

    subgraph GitHub_Remote [GitHub Remoto]
        GH_ORIGIN[(origin/main)]
    end

    subgraph VPS_MySQL [VPS MySQL 8 Central]
        QUEUE[(queue_registro_no_llame)]
    end

    GIT_REPO -->|1. git push| GH_ORIGIN
    CLI -->|2. Orden broadcast: POST /update| RedLocal
    UI -->|Control Interactivo| RedLocal
    CLI -.->|Lectura / Guardado| NODES_CFG

    N1 & N2 & N3 -->|3. git fetch & reset hard| GH_ORIGIN
    N1 -->|Ejecuta: Claro| QUEUE
    N2 -->|Ejecuta: Movistar| QUEUE
    N3 -->|Ejecuta: Personal| QUEUE
```

### Componentes de la Arquitectura

1. **PC Madre (Master)**:
   - Archivo ejecutable: [`cluster/master_control.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/cluster/master_control.py).
   - Acceso rápido: [`run_master.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/run_master.bat).
   - Registro de nodos: [`cluster/nodes.json`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/cluster/nodes.json).
   - Funciones: Emite comandos broadcast (`push-and-update`, `start-all`, `stop-all`), monitorea métricas en tiempo real y hospeda el Dashboard Web local.

2. **PCs Hijas (Workers / Nodos)**:
   - Archivo ejecutable: [`cluster/node_agent.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/cluster/node_agent.py).
   - Acceso rápido: [`run_node.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/run_node.bat).
   - Proceso supervisado: [`supervisor_vps.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/supervisor_vps.py).
   - Funciones: Servidor HTTP multihilo escuchando en el puerto `5555`, encargado del ciclo de vida del proceso de scraping local y la sincronización con Git.

---

## 2. Especificación de Endpoints REST del Agente de Nodo

Cada PC Hija expone los siguientes endpoints HTTP en el puerto `5555`:

| Método | Endpoint | Parámetros / Body JSON | Código Respuesta | Descripción Técnica |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/status` o `/health` | Ninguno | `200 OK` | Devuelve el estado operativo (`idle` / `running`), PID, scraper activo, rama y commit Git, uso de CPU/RAM y uptime. |
| `POST` | `/start` | Objeto con parámetros de scraping | `200 OK` / `400 Bad Request` | Lanza `supervisor_vps.py` en un nuevo grupo de procesos del SO (`CREATE_NEW_PROCESS_GROUP`). |
| `POST` | `/stop` | Ninguno | `200 OK` | Mata limpiamente el árbol completo de procesos (supervisor + workers) vía `taskkill /F /T /PID`. |
| `POST` | `/update` | `{"restart_if_running": true}` | `200 OK` / `500 Error` | Ejecuta `git fetch origin main` y `git reset --hard origin/main`. Si el scraper estaba corriendo, lo reinicia con el nuevo código. |
| `GET` | `/logs` | Query param: `?lines=50` | `200 OK` | Devuelve las últimas N líneas del archivo `supervisor_247.log`. |

### Esquema del Payload para `/start`

```json
{
  "scraper": "claro",
  "workers": 8,
  "batch_size": 12,
  "prioridad": "auto",
  "tor": false,
  "proxy_pool": true,
  "solo_sin_coincidencia": false,
  "forzar_horario": false,
  "max_queries_worker": 350
}
```

---

## 3. Comandos de la Consola Maestra (`master_control.py`)

La PC Madre administra todo el parque de máquinas mediante la CLI o el Dashboard Web:

### Tabla de Subcomandos

| Subcomando | Parámetros Principales | Ejemplo de Uso | Propósito |
| :--- | :--- | :--- | :--- |
| `status` | Ninguno | `python cluster/master_control.py status` | Tabla visual del estado de todas las PCs (Online, Offline, Scraper, Commit, Uptime). |
| `push-and-update` | Ninguno | `python cluster/master_control.py push-and-update` | Hace `git push` a `origin/main` y ordena actualización inmediata a todo el cluster. |
| `update` | `--pc <nombre>` o `--all`, `--no-restart` | `python cluster/master_control.py update --all` | Actualiza el código Git en los nodos sin requerir push previo. |
| `start` | `--pc <nombre>` o `--all`, `--scraper <nombre>`, `--workers <N>`, `--tor`, `--proxy-pool` | `python cluster/master_control.py start --all --scraper personal --workers 9` | Inicia el scraping en el cluster con los parámetros deseados. |
| `stop` | `--pc <nombre>` o `--all` | `python cluster/master_control.py stop --all` | Detiene el scraping en una o todas las máquinas. |
| `scan` | `--subnet <prefijo>`, `--auto-add` | `python cluster/master_control.py scan --auto-add` | Escaneo concurrente de 254 IPs para descubrir agentes en la LAN. |
| `logs` | `--pc <nombre>`, `--lines <N>` | `python cluster/master_control.py logs --pc PC-01` | Lee el log en vivo de un nodo remoto. |
| `ui` | `--port <5000>`, `--no-browser` | `python cluster/master_control.py ui` | Levanta el Panel Web interactivo con auto-refresco en tiempo real. |

---

## 4. Runbook de Puesta en Marcha en Nuevas PCs

### Paso 1: Clonar el Repositorio en la PC Hija
```powershell
cd C:\Users\Usuario\Documents\GitHub
git clone https://github.com/CofradiaGS/scrapers-reg-no-llame.git
cd scrapers-reg-no-llame
```

### Paso 2: Habilitar el Puerto en el Firewall de Windows (Solo una vez)
Abrir PowerShell como Administrador en la PC Hija y ejecutar:
```powershell
New-NetFirewallRule -DisplayName "Scraper Node Agent LAN" -Direction Inbound -LocalPort 5555 -Protocol TCP -Action Allow
```

### Paso 3: Configurar el Centinela Watchdog (Tarea Programada cada 10 minutos)
Para garantizar alta disponibilidad 24/7 y que el agente se levante solo ante reinicios o cierres imprevistos:
Hacer doble clic en **[`crear_tarea_programada.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/crear_tarea_programada.bat)**.

#### Triple Capa de Protección contra Procesos Duplicados:
1. **Windows Task Scheduler (`MultipleInstances = IgnoreNew`)**: Si la tarea previa se encuentra en curso, Windows rechaza la creación de una segunda tarea paralela.
2. **Pre-Flight Check en [`scripts/watchdog_node_agent.ps1`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/scripts/watchdog_node_agent.ps1)**: Comprueba si el puerto TCP 5555 está activo o si el endpoint `/health` responde 200 OK. Si está vivo, termina en <200ms sin hacer nada.
3. **Lockfile Singleton a Nivel de Kernel en [`cluster/node_agent.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/cluster/node_agent.py)**: Utiliza `msvcrt.locking` exclusivo sobre `node_agent.lock`. Si cualquier usuario o proceso intenta levantar una segunda instancia, el kernel de Windows bloquea la solicitud y el nuevo proceso se auto-termina inmediatamente.

### Paso 4: Registrar la PC en la PC Madre
En la PC Madre, ejecutar:
```powershell
python cluster/master_control.py scan --auto-add
```
O agregarla manualmente:
```powershell
python cluster/master_control.py add-node --name "PC-Oficina-2" --ip "192.168.1.52"
```

---

## 5. Protocolo de Resolución de Incidentes

### Nodo Marcado como OFFLINE en el Dashboard
1. Verificar que la PC Hija esté encendida y conectada a la misma red WiFi/Ethernet.
2. Comprobar que [`run_node.bat`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/run_node.bat) esté en ejecución en la PC Hija.
3. Probar conectividad desde la PC Madre con `Test-NetConnection -ComputerName <IP> -Port 5555`.

### Conflicto de Git durante la Actualización
El agente de nodo utiliza deliberadamente `git reset --hard origin/main`, lo que garantiza que cualquier modificación accidental de archivos locales en la PC Hija sea descartada en favor del commit oficial de la rama `main`.

---

## 6. Enlaces Relacionados en la Documentación

* [31. Comandos de la CLI Principal (`main.py`)](comandos_cli_main.md)
* [32. Supervisor de Producción (`supervisor_vps.py`)](supervisor_produccion.md)
* [33. Demonio Permanente de Windows (`run_daemon.bat`)](demonio_windows_bat.md)
* [34. Runbook de Resolución de Problemas e Incidentes](resolucion_problemas.md)
* [Índice Maestro de la Documentación](../../docs/README.md)
