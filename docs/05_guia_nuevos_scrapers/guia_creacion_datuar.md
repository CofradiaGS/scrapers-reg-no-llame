# Guía Técnica: Adaptador Scraper Datuar (Enriquecimiento de Identidad)

Este documento detalla el diseño, la especificación de protocolo, la arquitectura de integración y la guía operativa del motor de extracción y enriquecimiento de identidad para **Datuar Argentina** (`https://datuar.com`), integrado bajo los principios de la **Arquitectura Hexagonal (Puertos y Adaptadores)**.

---

## 1. Ciclo de Vida del Pipeline y Regla de No-Cortocircuito

En el sistema, Datuar opera como un **eslabón de enriquecimiento no-terminal** formalizado en [`ReglaPipeline`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L130-L195):

```text
CADENA_DEFAULT: ["iris", "datuar", "claro", "movistar", "personal"]
```

```mermaid
flowchart TD
    Inicio([1. Línea en Cola: PENDIENTE]) --> IRIS[2. Motor IRIS Movistar]
    
    IRIS -->|Extrajo DNI del Titular| DatuarEngine[3. Motor Datuar Enriquecimiento]
    IRIS -->|Sin DNI / No Posee Port Out| BypassDNI[Bypass Directo sin DNI]
    
    DatuarEngine -->|Nombre y CUIL Enriquecidos| NextClaro[4. Motor Claro Cobro Express]
    DatuarEngine -->|DNI sin registro en Datuar| NextClaro
    
    NextClaro -->|Coincidencia Positiva| CortoClaro[CORTOCIRCUITO: COMPLETADO / Finalizado]
    NextClaro -->|Sin Coincidencia Claro| NextMovistar[5. Motor Movistar Cobro Express]
    
    BypassDNI --> NextMovistar
    
    NextMovistar -->|Coincidencia Positiva| CortoMovi[CORTOCIRCUITO: COMPLETADO / Finalizado]
    NextMovistar -->|Sin Coincidencia Movistar| NextPersonal[6. Motor Personal / Telecom]
    
    NextPersonal -->|Coincidencia Positiva| CortoPersonal[CORTOCIRCUITO: COMPLETADO / Finalizado]
    NextPersonal -->|Sin Coincidencia| FinCadena[Fin de Cadena: NO_COINCIDENCIA]
```

### Reglas de Pipeline para Datuar
1. **Paso No-Terminal**: A diferencia de las empresas de telecomunicaciones (Claro, Movistar, Personal), [`DatuarAdapter.consultar_linea`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/datuar/datuar_adapter.py) **NUNCA cortocircuita** la línea a `finalizado`. Dé o no dé resultado positivo (encuentre el nombre o no), [`ReglaPipeline.resolver_siguiente_etapa`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L138) avanza siempre a `scraper_actual = 'claro'` y `estado = 'pendiente'`.
2. **Bypass Inteligente para Líneas sin DNI**: Cuando [`IrisHttpAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/iris_http_adapter.py) no encuentra Port Out ni datos de titular, la línea no posee DNI. Dado que tanto Datuar como Claro exigen DNI para consultar, [`ReglaPipeline`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L159) deriva automáticamente a `scraper_actual = 'movistar'`, saltando Datuar y Claro para ahorrar dos ciclos completos de transacciones en base de datos.
3. **Persistencia Acumulativa**: Los datos extraídos por Datuar se preservan de forma atómica en `datos_json["datuar"]` y enriquecen la entidad [`Titular`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L43-L67).

---

## 2. Especificación del Protocolo Datuar

### 2.1. Endpoints y Cabeceras

| Parámetro | Valor Predeterminado | Descripción |
| :--- | :--- | :--- |
| `base_url` | `https://datuar.com` | URL base del portal de consulta |
| `search_endpoint` | `/indext.php?busqueda={dni}` | Endpoint de búsqueda oficial por DNI |
| `Referer` | `https://datuar.com/` | Cabecera obligatoria de navegación |
| `User-Agent` | Navegador moderno (Chrome/Edge/Firefox) | User-Agent aleatorio |
| `timeout` | `15` segundos | Límite de espera para conexión HTTP |
| `delay_min` | `0.2` segundos | Jitter mínimo entre peticiones |
| `delay_max` | `0.6` segundos | Jitter máximo entre peticiones |

### 2.2. Diagrama de Secuencia de Consulta

```mermaid
sequenceDiagram
    autonumber
    actor CasoUso as ProcesarLoteUseCase
    participant Adapter as DatuarAdapter
    participant Cache as datuar_cache.sqlite
    participant API as datuar.com

    CasoUso->>Adapter: consultar_linea(linea con DNI)
    alt DNI ausente o menor a 6 dígitos
        Adapter-->>CasoUso: ScrapeResult(SIN_COINCIDENCIA, 'Sin DNI disponible')
    else DNI válido presente
        Adapter->>Cache: SELECT FROM cache WHERE dni = ?
        alt DNI ya en caché local
            Cache-->>Adapter: (nombre_completo, nombres, apellidos, cuil, edad, genero, provincia, ciudad, municipio)
            Adapter-->>CasoUso: ScrapeResult(COINCIDENCIA, origen='datuar_cache')
        else DNI no registrado en caché
            Adapter->>API: GET /indext.php?busqueda={dni} (Referer: https://datuar.com/)
            alt 200 OK (Atributos completos: data-cdu, data-edad, data-genero, data-provincia, data-ciudad, data-municipio, data-nombre-completo)
                API-->>Adapter: HTML renderizado con atributos
                Note over Adapter: Extrae CUIL, Edad, Género, Provincia, Ciudad, Municipio, Apellidos y Nombres
                Adapter->>Cache: INSERT INTO cache (dni, nombre_completo, nombres, apellidos, cuil, edad, genero, provincia, ciudad, municipio)
                Adapter-->>CasoUso: ScrapeResult(COINCIDENCIA, origen='datuar_live')
            else 200 OK (Sin atributos / No registrado)
                API-->>Adapter: HTML vacío de resultados
                Adapter-->>CasoUso: ScrapeResult(SIN_COINCIDENCIA, 'DNI no registrado')
            else 200 OK con '0 resultados' (Bloqueo IP de Tor)
                Adapter->>Adapter: Rotación inmediata de circuito Tor y reintento
            else 429 Too Many Requests
                Adapter->>Adapter: Rotación de circuito Tor e intento con backoff 1.5s
            end
        end
    end
```

---

## 3. Protocolo de Evasión de Límites: ProxyPoolManager y Tor Multi-Instancia

Datuar impone un límite perimétrico estricto de **~15 consultas continuas por dirección IP**. Al superar este umbral, el servidor responde con el desafío Cloudflare Turnstile:
> *"Has superado el límite de búsquedas gratuitas continuas. Es obligatorio completar la verificación de seguridad para continuar."*

[`DatuarAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/datuar/datuar_adapter.py) ofrece dos arquitecturas complementarias de transporte para operar de forma indefinida 24/7 sin bloqueos:

### 3.1. Modalidad A: ProxyPoolManager (`datuar_fast` o `--proxy-pool`)
* **Rotación Continua de IP Externa**: Cada petición a Datuar se despacha a través de una dirección IP pública diferente provista por [`ProxyPoolManager`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/proxy_pool.py).
* **Evasión Definitiva del Límite de 15**: Dado que las peticiones se distribuyen entre decenas de proxies vivos, ninguna IP acumula 15 consultas continuas, eliminando por completo la aparición del captcha de Turnstile.
* **Auto-Depuración**: Si un proxy reporta latencia o bloqueo, el gestor lo penaliza inmediatamente vía `report_failure(proxy)` y conmuta al siguiente proxy sano sin interrumpir el lote.

### 3.2. Modalidad B: Tor Multi-Instancia con Rotación No-Destructiva y Failover Resiliente (`--tor`)
1. **Particionamiento en Pool de 3 Workers por Puerto (`WORKERS_PER_TOR_PORT = 3`)**:
   - Cada grupo de 3 workers se enruta deterministamente a su propio demonio Tor local (`9050`, `9052`, `9054`, `9056`, `9058`, `9060`, `9062`) con directorios de datos totalmente aislados (`tor_data/port_{socks_port}`).
   - Si una instancia de Tor sufre congelamiento o saturación de circuitos, **únicamente** se ven afectados sus 3 workers asignados; los demás workers continúan extrayendo datos a máxima velocidad sin enterarse.
2. **Aislamiento de Flujo Determinista (`IsolateSOCKSAuth`)**:
   - Cada worker dispone de credenciales deterministas `socks5h://w{slot}_r{circuit_id}:tor@127.0.0.1:{socks_port}`.
   - Tor mantiene un circuito de salida independiente para cada slot sin incurrir en colisiones cruzadas.
3. **Reutilización Óptima de Circuito Caliente (`DATUAR_TOR_ROTATE_EVERY = 30`)**:
   - Se mantiene el circuito caliente mientras continúe saludable, garantizando latencias ultrabajas de 1.1s - 1.8s por consulta.
   - Dado que una IP limpia soporta hasta 43 consultas continuas en Datuar antes del límite, se permite amortizar el circuito hasta **30 consultas consecutivas** antes de forzar rotación proactiva, evitando caer prematuramente en nodos de salida congestionados.
4. **Failover Resiliente Automático Tor ➔ ProxyPool**:
   - Ante la presencia de Cloudflare Turnstile en los circuitos Tor (común por listas públicas de salidas Tor) o timeouts de red, el adaptador conmuta inmediatamente a [`ProxyPoolManager`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/proxy_pool.py), el cual es precalentado por el supervisor con proxies rápidos con `verify=False`.
   - Esto erradica por completo la caída de lotes a `pendiente` y garantiza el cumplimiento estricto del umbral $\ge 95\%$ de éxito.
5. **Métricas de Efectividad y Calidad de Servicio (`--min-success-rate 95.0`)**:
   - El supervisor monitorea continuamente la tasa de efectividad de las consultas:
     $$\text{tasa\_efectividad} = \frac{\text{coincidencias} + \text{sin\_coincidencias}}{\text{total\_procesados}} \times 100$$
   - Garantiza que más del **95.0%** de las consultas sobre Datuar concluyan exitosamente sin registrar fallos transitorios ni reencolamientos redundantes.
6. **Endpoint Oficial de Producción (`/indext.php`)**:
   - La extracción se ejecuta exclusivamente contra `/indext.php?busqueda={dni}`, el endpoint oficial de búsqueda del portal web que procesa los formularios de consulta.
7. **Protocolo de Triple Confirmación para `SIN_COINCIDENCIA` (`DATUAR_SIN_COINCIDENCIA_CONFIRMACIONES = 3`)**:
   - Cuando una consulta retorna 0 resultados, el adaptador no acepta inmediatamente el resultado negativo.
   - Rota de forma independiente el circuito Tor (o conmuta de proxy) y reintenta la consulta hasta **3 veces** con IPs de salida distintas.
   - Si en cualquiera de los 3 intentos se detectan registros de la persona, se retorna de inmediato `COINCIDENCIA`.
   - Si y solo si los 3 intentos independientes ratifican la ausencia de registros (`total_cnt == 0`), se concluye de forma fidedigna `SIN_COINCIDENCIA`.

---

## 4. Caché Local SQLite de Alta Velocidad (`datuar_cache.sqlite`)

Para eliminar peticiones redundantes a la web cuando un mismo DNI aparece en múltiples líneas o reintentos, [`DatuarAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/datuar/datuar_adapter.py) implementa una base de datos local SQLite con soporte completo de atributos de identidad y demografía:

```sql
CREATE TABLE IF NOT EXISTS cache (
    dni TEXT PRIMARY KEY,
    nombre_completo TEXT,
    nombres TEXT,
    apellidos TEXT,
    cuil TEXT,
    edad TEXT,
    genero TEXT,
    provincia TEXT,
    ciudad TEXT,
    municipio TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

* **Latencia en Caché**: `< 1 ms` (0 consumo de ancho de banda).
* **Persistencia Atómica**: Manejo de transacciones con `with sqlite3.connect(...)` garantizando consistencia ante interrupciones.
* **Migración No-Destructiva**: Se asegura compatibilidad hacia atrás ejecutando `ALTER TABLE cache ADD COLUMN ...` para cualquier base de datos preexistente.

---

## 5. Arquitectura de Red y Resiliencia Concurrente

[`DatuarAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/datuar/datuar_adapter.py) soporta tres modos de transporte:
* **ProxyPoolManager**: Rotación de IPs públicas mediante [`ProxyPoolManager`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/proxy_pool.py), ideal para 20+ workers sin contención de nodos Tor.
* **Tor Stream Isolation Multi-Instancia**: Asignación determinista mediante [`TorController.resolve_ports_for_worker`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/tor_controller.py#L133).
* **Conexión Directa**: Para pruebas unitarias rápidas con `--no-tor`.
* **Timeout Dual y Conexiones Resistentes**: Configura `timeout = (5.0, 10.0)` (5s connect, 10s read) con `max_retries = 0` en el pool HTTPAdapter, eliminando bloqueos prolongados ante relés de salida lentos.

---

## 6. Registro y Factoría Central

El adaptador se encuentra registrado en [`ScraperRegistry`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py) bajo los alias:
* `datuar`: Instancia estándar con soporte Tor o ProxyPool configurable.
* `datuar_fast`: Instancia preconfigurada con `use_proxy_pool=True` y `use_tor=False`.

```python
from adapters.scrapers.datuar.datuar_adapter import DatuarAdapter
from adapters.scrapers.registry import ScraperRegistry

ScraperRegistry.registrar("datuar", DatuarAdapter)
```

Para más detalles sobre el registro, consultar:
- [Contrato Abstracto IScraperEnginePort](contrato_iscraper_engine.md)
- [Registro Centralizado y Factoría Dinámica](registro_y_factoria.md)

---

## 7. Guía de Ejecución y Pruebas CLI

### 7.1. Consulta Unitaria por DNI
Para consultar y enriquecer la identidad de un titular por su número de DNI:

```powershell
# 1. Modo ProxyPool (alta velocidad, cero bloqueos de 15 consultas)
python main.py test-line 2604275327 --scraper datuar_fast --dni 33517690

# 2. Modo Tor Stream Isolation
python main.py test-line 2604275327 --scraper datuar --dni 33517690 --tor

# 3. Conexión directa
python main.py test-line 2604275327 --scraper datuar --dni 33517690 --no-tor
```

Salida esperada:
```text
=======================================================
🎯 RESULTADO NORMALIZADO: 2604275327
=======================================================
  • Estatus:     coincidencia
  • Operador:    
  • Descripción: Datuar - NIEVA, Rodrigo Matias | CUIL: 20335176902
  • Titular:     Rodrigo Matias NIEVA
  • Documento:   DNI 33517690
```

### 7.2. Prueba de Micro-Lote en Memoria (Dry-Run)
Para verificar la orquestación del caso de uso y el enriquecimiento de la entidad:

```powershell
python main.py test-batch --scraper datuar_fast --dry-run --batch-size 2
```

### 7.3. Lanzamiento del Supervisor Concurrente Industrial 24/7
Para ejecutar 20 workers dedicados a la etapa de Datuar sobre la cola VPS:

```powershell
# Opción 1 (Recomendada): Modo ProxyPool (alta velocidad, 0 bloqueos por IP)
venv\Scripts\python main.py supervise --scraper datuar --queue-type cola_automatizacion --auto-id scraper_datuar --proxy-pool --workers 20

# Opción 2: Modo Tor Multi-Instancia (4 instancias de Tor, 8 consultas/rotación)
venv\Scripts\python main.py supervise --scraper datuar --queue-type cola_automatizacion --auto-id scraper_datuar --tor --workers 20
```
