# Guía Técnica: Adaptador Scraper Claro (Cobro Express)

Este documento detalla el diseño, la especificación de protocolo, la arquitectura de integración y la guía operativa del motor de extracción para **Claro Argentina** a través de la pasarela **Cobro Express**, integrado bajo los principios de la **Arquitectura Hexagonal (Puertos y Adaptadores)**.

---

## 1. Ciclo de Vida del Pipeline y Regla de Cortocircuito

El pipeline opera bajo el patrón de **Cadena de Responsabilidad con Cortocircuito** formalizado en [`ReglaPipeline`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/core/domain/entities.py#L126-L172):

```text
CADENA_DEFAULT: ["iris", "claro", "movistar", "personal"]
```

```mermaid
flowchart TD
    Inicio([Línea en Cola: PENDIENTE]) --> IRIS[1. Motor IRIS Movistar]
    IRIS -->|Coincidencia Port Out| MovistarPos[Titular y DNI Identificados]
    IRIS -->|Sin Port Out / No Posee| NextClaro[Pasa a Claro: PENDIENTE]
    MovistarPos --> NextClaro
    NextClaro --> ClaroEngine[2. Motor Claro Cobro Express]
    ClaroEngine -->|Coincidencia Positiva| Cortocircuito[CORTOCIRCUITO: COMPLETADO / Finalizado]
    ClaroEngine -->|Sin Coincidencia / Sin Deuda| NextMovistar[Pasa a Movistar: PENDIENTE]
    NextMovistar --> MovistarEngine[3. Motor Movistar Directo]
    MovistarEngine -->|Coincidencia| Cortocircuito
    MovistarEngine -->|Sin Coincidencia| NextPersonal[Pasa a Personal: PENDIENTE]
    NextPersonal --> PersonalEngine[4. Motor Personal / Telecom]
    PersonalEngine -->|Coincidencia| Cortocircuito
    PersonalEngine -->|Sin Coincidencia| FinNoCoinc[Fin de Cadena: NO_COINCIDENCIA]
```

### Regla de Cortocircuito para Claro
Cuando [`ClaroAdapter.consultar_linea`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/claro/claro_adapter.py) retorna `StatusScraping.COINCIDENCIA`, [`ReglaPipeline.resolver_siguiente_etapa`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/core/domain/entities.py#L134) detiene de inmediato la evaluación de los siguientes operadores y salta la línea a `scraper_actual = 'finalizado'` y `estado = 'completado'`.

---

## 2. Especificación de Integración Cobro Express

### 2.1. Endpoints y Configuración de Conexión

| Parámetro | Valor Predeterminado | Variable de Entorno | Descripción |
| :--- | :--- | :--- | :--- |
| `base_url` | `https://pagosce.cobroexpress.com.ar` | `COBRO_EXPRESS_URL` | URL base de la pasarela de pagos |
| `proxy` | `None` | `COBRO_EXPRESS_PROXY` | Proxy HTTP o SOCKS5 para conexión manual |
| `timeout` | `20` | `COBRO_EXPRESS_TIMEOUT` | Timeout en segundos para solicitudes HTTP |
| `delay_min` | `1.0` | `COBRO_EXPRESS_DELAY_MIN` | Jitter mínimo en segundos entre peticiones |
| `delay_max` | `2.5` | `COBRO_EXPRESS_DELAY_MAX` | Jitter máximo en segundos entre peticiones |
| `TOR_ENABLED` | `True` | `TOR_ENABLED` | Habilitación global de enrutamiento por proxy Tor |
| `TOR_PATH` | `tor.exe` (Tor Browser) | `TOR_PATH` | Ruta al binario ejecutable del kernel de Tor |
| `TOR_SOCKS_PORT_BASE` | `9050` | `TOR_SOCKS_PORT_BASE` | Puerto base SOCKS5h para workers |
| `TOR_CONTROL_PORT_BASE` | `9051` | `TOR_CONTROL_PORT_BASE` | Puerto base de control stem (señal NEWNYM) |
| `TOR_ROTATE_EVERY` | `19` | `TOR_ROTATE_EVERY` | Frecuencia de rotación preventiva de IP pública |

### 2.2. Parámetros de la Entidad Claro

La pasarela de Cobro Express organiza los servicios mediante identificadores de empresa y modalidad:

| Campo | Valor | Tipo | Descripción |
| :--- | :--- | :--- | :--- |
| `IdEmpresa` | `20916` | `int` | Identificador de empresa para **Claro Telefonía** |
| `IdEmpresaModalidad` | `2876` | `int` | Modalidad de consulta de factura por Teléfono y DNI |
| `C11` | `ani` (10 dígitos) | `str` | Número de teléfono normalizado sin 0 ni 15 |
| `C12` | `dni` | `str` | Número de DNI del titular sin puntos |

### 2.3. Diagrama de Secuencia de Consulta

```mermaid
sequenceDiagram
    autonumber
    actor CasoUso as ProcesarLoteUseCase
    participant Adapter as ClaroAdapter
    participant Session as requests.Session
    participant API as Cobro Express API

    CasoUso->>Adapter: consultar_linea(linea)
    alt Sin DNI provisto ni heredado
        Adapter-->>CasoUso: ScrapeResult(SIN_COINCIDENCIA, 'Sin DNI disponible')
    else Con DNI válido
        Adapter->>Session: sleep_jitter(1.0, 2.5)
        Adapter->>API: POST /api/Servicios/DeudaFormulario (C11: linea, C12: dni)
        alt 200 OK (Comprobantes encontrados)
            API-->>Adapter: Array JSON de deudas
            Note over Adapter: Decodifica Base64 (integradorInfo, transaccionInfo)
            Adapter-->>CasoUso: ScrapeResult(COINCIDENCIA, Operador='Claro')
        else 400 Bad Request (Cliente inexistente)
            API-->>Adapter: Error 400 JSON
            Adapter-->>CasoUso: ScrapeResult(SIN_COINCIDENCIA, 'Cliente inexistente')
        else 429 Too Many Requests
            API-->>Adapter: Error 429
            Adapter-->>CasoUso: RuntimeError('Rate limit 429')
        end
    end
```

---

## 3. Captura Exhaustiva del 100% de la Información

Por directiva de negocio, **el 100% de los datos retornados por la API de Cobro Express se captura y preserva sin descartar ningún campo**:

1. **`raw`**: Contiene la respuesta original de la API (`raw["items"]`) con los objetos de comprobantes íntegros.
2. **`detalles`**:
   - `id_empresa`, `id_modalidad`, `id_cliente`, `codigo_barra_primario`, `deuda_total`, `cantidad_comprobantes`.
   - `comprobantes`: Lista enriquecida con `idEmpresa`, `nombreEmpresa`, `idEmpresaModalidad`, `codigoBarra`, `idCliente`, `primerImporte`, `segundoImporte`, `importeMin`, `importeMax`, `idTipoMonto`, `hash`, `detalles_item`.
   - **Metadatos Decodificados**: Se procesa el contenido embebido en Base64 de `integradorInfo` (`AmountType`, `ExpirationDate`, `Reference`, `SearchForm`, `Hash`) y `transaccionInfo` (`PrimerImporte`, `SegundoImporte`, `ImporteMin`, `ImporteMax`).
3. **`fechas`**: Contiene `fecha_consulta` (ISO UTC) y `fecha_vencimiento` extraída de los metadatos integradores.
4. **`servicio`**: Detalle del producto (`CLARO`), tecnología (`Móvil / Celular`) y modalidad (`Modalidad 2876 (Cobro Express)`).
5. **`titular`**: `nro_documento` normalizado con tipo `DNI`.

---

## 4. Arquitectura Tor Stream Isolation (`IsolateSOCKSAuth`) a Costo $0

Para maximizar la velocidad, eliminar contenciones de sockets en Windows y reducir el consumo de memoria RAM de 800 MB a solo **~45 MB**, el sistema consolida el proxy en una **única instancia de Tor Daemon** gestionada por [`TorController`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/network/tor_controller.py) y configurada con la directiva `IsolateSOCKSAuth`:

```mermaid
graph TD
    subgraph Supervisor["Supervisor Industrial (main.py / supervisor.py)"]
        TorDaemon["Demonio Tor Único (Puerto 9050 IsolateSOCKSAuth)<br/>Consumo: ~45 MB RAM"]
        W1["Worker Slot 1 (W1-G1)"]
        W2["Worker Slot 2 (W2-G1)"]
        WN["Worker Slot N (WN-G1)"]
    end

    subgraph StreamIsolation["Aislamiento de Flujo SOCKS5h (Circuitos Dinámicos)"]
        C1["socks5h://w1_a7b2:tor@127.0.0.1:9050<br/>➔ Circuito IP Alfa"]
        C2["socks5h://w2_c3d4:tor@127.0.0.1:9050<br/>➔ Circuito IP Beta"]
        CN["socks5h://wN_e5f6:tor@127.0.0.1:9050<br/>➔ Circuito IP Gamma"]
    end

    subgraph CobroExpress["API Cobro Express (Stateless)"]
        API["POST /api/Servicios/DeudaFormulario<br/>(Llamada directa sin GET previo)"]
    end

    Supervisor -->|Inicia y Monitorea| TorDaemon
    W1 -->|SOCKS Auth Dinámico| C1
    W2 -->|SOCKS Auth Dinámico| C2
    WN -->|SOCKS Auth Dinámico| CN

    C1 --> API
    C2 --> API
    CN --> API
```

### 4.1. Mecanismo de Stream Isolation por Credenciales Dinámicas
Bajo la directiva `SocksPort 9050 IsolateSOCKSAuth` en [`torrc`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/torrc), Tor garantiza que cada par de usuario/contraseña transmitido en el protocolo SOCKS5 sea mapeado internamente a un **circuito y nodo de salida completamente diferente**.
* [`ClaroAdapter._generar_proxy_aislado`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/claro/claro_adapter.py) genera una identidad única por worker y rotación: `socks5h://w{slot}_{hash}:tor@127.0.0.1:9050`.
* **Rotación Instantánea en 0 ms**: Cambiar de IP pública no requiere reiniciar subprocesos, ni esperar señales `NEWNYM` de stem ni pausar la ejecución con `time.sleep()`. Al regenerar las credenciales en la sesión, la siguiente petición sale automáticamente por un nuevo circuito.
* **Desacoplamiento Estricto del Ciclo de Vida**: En [`tor_controller.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/network/tor_controller.py), solo el proceso maestro (`SupervisorIndustrial` o raíz CLI) es propietario del subproceso (`is_owner=True`). Los workers efímeros nunca terminan el demonio `tor.exe` al rotar preventivamente a las 350 consultas, garantizando disponibilidad ininterrumpida.
* **Priorización Regional en `torrc`**: Incorpora `ExitNodes {ar},{cl},{uy},{br} StrictNodes 0`, reduciendo drásticamente las denegaciones WAF de Cobro Express al preferir nodos del Cono Sur, manteniendo tolerancia automática si no hay relays disponibles.
* **Optimización de Latencias**: Configuración de `CircuitBuildTimeout 10`, `KeepalivePeriod 60` y `MaxCircuitDirtiness 300` para reducir los tiempos de respuesta de Tor a 2.5s - 3.5s.

### 4.2. Supresión de Llamadas HTTP Redundantes
La pasarela `DeudaFormulario` es *stateless*. En lugar de ejecutar una petición `GET https://pagosce.cobroexpress.com.ar/` antes de cada consulta (lo que duplicaba la latencia por Tor), [`ClaroAdapter.autenticar`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/claro/claro_adapter.py) simplemente valida la sesión activa, enviando directamente el POST y reduciendo el RTT al 50%.

### 4.3. Jitter Adaptativo Ultrarrápido
Dado que las peticiones se distribuyen sobre IPs diferentes de forma natural por Stream Isolation, los retardos entre lotes se ajustan de forma segura a `0.2s - 0.6s` (configurados en [`config.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/config.py)), multiplicando el rendimiento por worker de 15 RPM a **60-120 RPM**.

### 4.4. Sub-sistema Alternativo de Alta Velocidad: Pool de Proxies Públicos Rotativos
Como alternativa de ultra-baja latencia (1.0s a 2.5s por consulta) y costo $0, el sistema incorpora [`ProxyPoolManager`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/network/proxy_pool.py):
* **Recolección Racionalizada sin Estampida de Hilos**: Solo el proceso supervisor o el Worker Slot 1 ejecuta el `ProxyPoolFeeder` en segundo plano (`allow_feeder=True`). Los workers secundarios consumen la caché ya validada, erradicando la sobrecarga destructiva de cientos de hilos concurrentes en Windows.
* **Persistencia Atómica en Disco (`live_proxies.txt`)**: La caché local se persiste mediante archivos temporales con reemplazo atómico (`os.replace`), evitando escrituras concurrentes truncadas entre subprocesos.
* **Reutilización de Conexiones HTTP y Connection Pooling**: [`ClaroAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/claro/claro_adapter.py) utiliza una sesión persistente con `HTTPAdapter(pool_connections=10, pool_maxsize=10)` eliminando el agotamiento de puertos efímeros en Windows (`TIME_WAIT`).
* **Mecanismo de Descarte Resiliente ante 429**: Si el pool dispone de pocos proxies (≤8), ante un código HTTP 429 aplica un backoff breve de 1.5s asumiendo colisión temporal de hilos antes de descartar el proxy de forma definitiva.

---

## 5. Registro y Factoría Central

El adaptador se registra en [`ScraperRegistry`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/registry.py) bajo tres alias:
- `"claro"`: Nombre canónico utilizado en la cadena del pipeline (modo por defecto según flags o configuración).
- `"claro_cobro_express"`: Alias explícito del motor de pasarela.
- `"claro_fast"`: Alias preconfigurado para forzar el modo de alta velocidad con [`ProxyPoolManager`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/network/proxy_pool.py).

```python
from adapters.scrapers.claro.claro_adapter import ClaroAdapter
from adapters.scrapers.registry import ScraperRegistry

ScraperRegistry.registrar("claro", ClaroAdapter)
ScraperRegistry.registrar("claro_cobro_express", ClaroAdapter)
ScraperRegistry.registrar("claro_fast", ClaroAdapter)
```

---

## 6. Guía de Ejecución y Pruebas CLI

### 6.1. Consulta Unitaria con DNI (Modo Directo, Tor o Proxy Pool)
Para consultar una línea individual conociendo el DNI del titular:

```powershell
# 1. Conexión directa (sin Tor ni proxies)
python main.py test-line 2604275327 --scraper claro --dni 33517690 --no-tor

# 2. Modo Tor Stream Isolation (Máximo anonimato, latencia 4-8s)
python main.py test-line 2604275327 --scraper claro --dni 33517690 --tor

# 3. Modo Proxy Pool Rotativo de Alta Velocidad (Latencia 1-2.5s, costo $0)
python main.py test-line 2604275327 --scraper claro --dni 33517690 --proxy-pool

# O usando directamente el alias claro_fast:
python main.py test-line 2604275327 --scraper claro_fast --dni 33517690
```

Salida esperada (en horario operativo 06:00 a 23:00):
```text
=======================================================
🎯 RESULTADO NORMALIZADO: 2604275327
=======================================================
  • Estatus:     coincidencia
  • Operador:    Claro
  • Descripción: Claro - Cliente: 1680219860 | Deuda: $11360.63 | Vto: 2026-09-20 | CB: 0821680219860260920011360632SF
  • Documento:   DNI 33517690
```

### 6.2. Prueba de Micro-Lote en Memoria (Dry-Run)
Para validar la orquestación del caso de uso sin persistir en la base de datos central:

```powershell
# Con Proxy Pool ultrarrápido
python main.py test-batch --scraper claro --dry-run --batch-size 2 --proxy-pool

# Con Tor Stream Isolation
python main.py test-batch --scraper claro --dry-run --batch-size 2 --tor
```

### 6.3. Lanzamiento del Supervisor Concurrente
Para ejecutar workers dedicados a la etapa de Claro escuchando la cola de producción en MySQL 8 VPS:

```powershell
# Modo Proxy Pool (Máximo rendimiento de RPM)
python main.py supervise --scraper claro --workers 5 --batch-size 15 --prioridad auto --proxy-pool

# Modo Tor Stream Isolation (Máxima evasión anti-bloqueo)
python main.py supervise --scraper claro --workers 5 --batch-size 15 --prioridad auto --tor
```
