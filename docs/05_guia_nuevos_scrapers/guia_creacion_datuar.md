# Guía Técnica: Adaptador Scraper Datuar (Enriquecimiento de Identidad)

Este documento detalla el diseño, la especificación de protocolo, la arquitectura de integración y la guía operativa del motor de extracción y enriquecimiento de identidad para **Datuar Argentina** (`https://datuar.com`), integrado bajo los principios de la **Arquitectura Hexagonal (Puertos y Adaptadores)**.

---

## 1. Ciclo de Vida del Pipeline y Regla de No-Cortocircuito

En el sistema, Datuar opera como un **eslabón de enriquecimiento no-terminal** formalizado en [`ReglaPipeline`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L130-L195):

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
1. **Paso No-Terminal**: A diferencia de las empresas de telecomunicaciones (Claro, Movistar, Personal), [`DatuarAdapter.consultar_linea`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/datuar/datuar_adapter.py) **NUNCA cortocircuita** la línea a `finalizado`. Dé o no dé resultado positivo (encuentre el nombre o no), [`ReglaPipeline.resolver_siguiente_etapa`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L138) avanza siempre a `scraper_actual = 'claro'` y `estado = 'pendiente'`.
2. **Bypass Inteligente para Líneas sin DNI**: Cuando [`IrisHttpAdapter`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/iris_http_adapter.py) no encuentra Port Out ni datos de titular, la línea no posee DNI. Dado que tanto Datuar como Claro exigen DNI para consultar, [`ReglaPipeline`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L159) deriva automáticamente a `scraper_actual = 'movistar'`, saltando Datuar y Claro para ahorrar dos ciclos completos de transacciones en base de datos.
3. **Persistencia Acumulativa**: Los datos extraídos por Datuar se preservan de forma atómica en `datos_json["datuar"]` y enriquecen la entidad [`Titular`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L43-L67).

---

## 2. Especificación del Protocolo Datuar

### 2.1. Endpoints y Cabeceras

| Parámetro | Valor Predeterminado | Descripción |
| :--- | :--- | :--- |
| `base_url` | `https://datuar.com` | URL base del portal de consulta |
| `search_endpoint` | `/index2.php?busqueda={dni}` | Endpoint de búsqueda por DNI |
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
            Adapter->>API: GET /index2.php?busqueda={dni} (Referer: https://datuar.com/)
            alt 200 OK (Atributos completos: data-cdu, data-edad, data-genero, data-provincia, data-ciudad, data-municipio, data-nombre-completo)
                API-->>Adapter: HTML renderizado con atributos
                Note over Adapter: Extrae CUIL, Edad, Género, Provincia, Ciudad, Municipio, Apellidos y Nombres
                Adapter->>Cache: INSERT INTO cache (dni, nombre_completo, nombres, apellidos, cuil, edad, genero, provincia, ciudad, municipio)
                Adapter-->>CasoUso: ScrapeResult(COINCIDENCIA, origen='datuar_live')
            else 200 OK (Sin atributos / No registrado)
                API-->>Adapter: HTML vacío de resultados
                Adapter-->>CasoUso: ScrapeResult(SIN_COINCIDENCIA, 'DNI no registrado')
            else 429 Too Many Requests
                Adapter->>Adapter: Rotación de circuito Tor e intento con backoff 1.5s
            end
        end
    end
```

---

## 3. Caché Local SQLite de Alta Velocidad (`datuar_cache.sqlite`)

Para eliminar peticiones redundantes a la web cuando un mismo DNI aparece en múltiples líneas o reintentos, [`DatuarAdapter`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/datuar/datuar_adapter.py) implementa una base de datos local SQLite con soporte completo de atributos de identidad y demografía:

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

## 4. Arquitectura de Red y Tor Stream Isolation

[`DatuarAdapter`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/datuar/datuar_adapter.py) soporta tanto conexión directa ultrarrápida como enrutamiento seguro por **Tor Stream Isolation**:
* **Aislamiento por Worker**: Credenciales dinámicas `socks5h://w{slot}_{hash}:tor@127.0.0.1:9050` sobre el demonio único de Tor.
* **Mitigación Rate-Limit**: Ante un código HTTP 429, regenera las credenciales del socket SOCKS5h en 0 ms para que la siguiente petición salga por un nuevo circuito y dirección IP.

---

## 5. Registro y Factoría Central

El adaptador se encuentra registrado en [`ScraperRegistry`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py):

```python
from adapters.scrapers.datuar.datuar_adapter import DatuarAdapter
from adapters.scrapers.registry import ScraperRegistry

ScraperRegistry.registrar("datuar", DatuarAdapter)
```

Para más detalles sobre el registro, consultar:
- [Contrato Abstracto IScraperEnginePort](contrato_iscraper_engine.md)
- [Registro Centralizado y Factoría Dinámica](registro_y_factoria.md)

---

## 6. Guía de Ejecución y Pruebas CLI

### 6.1. Consulta Unitaria por DNI
Para consultar y enriquecer la identidad de un titular por su número de DNI:

```powershell
# 1. Conexión directa (latencia ~300 ms)
python main.py test-line 2604275327 --scraper datuar --dni 33517690 --no-tor

# 2. Modo Tor Stream Isolation (máximo anonimato)
python main.py test-line 2604275327 --scraper datuar --dni 33517690 --tor
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

### 6.2. Prueba de Micro-Lote en Memoria (Dry-Run)
Para verificar la orquestación del caso de uso y el avance hacia `claro:pendiente`:

```powershell
python main.py test-batch --scraper datuar --dry-run --batch-size 2 --no-tor
```

### 6.3. Lanzamiento del Supervisor Concurrente
Para ejecutar workers dedicados a la etapa de Datuar sobre la cola MySQL 8 VPS:

```powershell
# Conexión directa con caché local
python supervisor_vps.py --scraper datuar --workers 5 --batch-size 15

# Con Tor Stream Isolation
python supervisor_vps.py --scraper datuar --workers 5 --batch-size 15 --tor
```
