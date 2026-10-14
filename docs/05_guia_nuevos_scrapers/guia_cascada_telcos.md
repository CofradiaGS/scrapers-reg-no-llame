# Guía de Adaptador Compuesto: Cascada Telcos con Cortocircuito

> **Capa 05: Guía de Nuevos Scrapers** | Adaptador Compuesto `TelcoCascadeAdapter`

El adaptador [`TelcoCascadeAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/telcos/telco_cascade_adapter.py) orquesta la resolución secuencial de líneas exclusivamente en las tres principales compañías telefónicas de Argentina (Claro, Personal y Movistar), aplicando la regla innegociable de cortocircuito (*short-circuit*).

Está registrado en [`ScraperRegistry`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py) bajo los alias:
- `'telcos'` (alias principal)
- `'telco_cascade'`
- `'claro_personal_movistar'`

---

## 1. Diagrama de Flujo de la Cascada

```mermaid
flowchart TD
    Inicio([Línea a consultar]) --> CheckDNI{¿Posee DNI\nen la tarea?}

    CheckDNI -- Sí --> Claro[Paso 1: Claro Cobro Express\nConsulta por DNI]
    CheckDNI -- No --> Personal[Paso 2: Telecom Personal\nConsulta por ANI]

    Claro --> ResClaro{¿Coincidencia\nen Claro?}
    ResClaro -- Sí --> ShortClaro[🎯 Cortocircuito Claro\nstatus = COINCIDENCIA\noperador = Claro]
    ResClaro -- No --> Personal

    Personal --> ResPersonal{¿Coincidencia\nen Personal?}
    ResPersonal -- Sí --> ShortPersonal[🎯 Cortocircuito Personal\nstatus = COINCIDENCIA\noperador = Telecom Personal]
    ResPersonal -- No --> Movistar[Paso 3: Movistar\nConsulta por Código Área + Línea]

    Movistar --> ResMovistar{¿Coincidencia\nen Movistar?}
    ResMovistar -- Sí --> ShortMovistar[🎯 Cortocircuito Movistar\nstatus = COINCIDENCIA\noperador = Movistar]
    ResMovistar -- No --> SinCoincidencias[Paso 4: Sin Coincidencias\nstatus = SIN_COINCIDENCIA\nConserva namespaces de las 3]

    ShortClaro --> Fin([Fin del Ciclo Local])
    ShortPersonal --> Fin
    ShortMovistar --> Fin
    SinCoincidencias --> Fin
```

---

## 2. Invariantes de Negocio y Reglas de Cascada

### A. Estrategia Exclusiva de Telcos: Selección y Prioridad por DNI
Para el motor de cascada Telcos, rige una estrategia estricta de dos fases tanto en la cola como en la ejecución:

1. **Prioridad 1: Procesamiento Primero de Registros CON DNI (`dni IS NOT NULL AND dni != ''`)**:
   - La cola (MySQL VPS, Staging SQLite y sincronizador) entrega prioritariamente todos los registros que poseen DNI registrado.
   - **Evaluación completa de los 3 operadores**: Se ejecuta en orden secuencial:
     1. **Claro**: Consulta por DNI. Si arroja `StatusScraping.COINCIDENCIA`, se produce cortocircuito inmediato a finalizado.
     2. **Telecom Personal**: Si Claro dio sin coincidencia, consulta por ANI (10 dígitos). Si arroja coincidencia, cortocircuito inmediato.
     3. **Movistar**: Si Personal dio sin coincidencia, consulta por Código de Área + Línea. Si arroja coincidencia, finaliza.
     4. Si ninguno arroja deuda o línea activa, retorna `StatusScraping.SIN_COINCIDENCIA` acumulando los 3 namespaces.

2. **Prioridad 2: Procesamiento Posterior de Registros SIN DNI (`dni IS NULL OR dni = ''`)**:
   - Una vez agotados los registros con DNI (o cuando el lote se rellena con remanentes), se procesan las líneas que no poseen DNI.
   - **Omisión estricta de Claro**: Dado que Claro Cobro Express requiere DNI mandatorio, Claro se omite por completo (no se realiza ninguna petición HTTP hacia Claro).
   - En el namespace de Claro se deja constancia transparente:
     ```json
     "claro": {
       "status": "sin_coincidencia",
       "fuente": "claro",
       "operador": "Claro",
       "detalles": {
         "motivo": "Omitido: Claro Cobro Express exige DNI disponible y la línea no posee DNI",
         "ani_consultado": "11XXXXXXXX"
       }
     }
     ```
   - **Evaluación exclusiva en Personal y Movistar**:
     1. **Telecom Personal**: Consulta por ANI. Cortocircuito si hay coincidencia.
     2. **Movistar**: Consulta por Área + Línea. Cortocircuito si hay coincidencia.
     3. Si ninguna halla coincidencia, retorna `StatusScraping.SIN_COINCIDENCIA` reflejando solo las consultas ejecutadas.

3. **Alcance Estricto y Exclusivo a Telcos**:
   - Esta priorización de DNI aplica **única y exclusivamente al motor Telcos** (`canon == "telcos"` o `scraper_nombre == "telcos"`).
   - Los demás scrapers del ecosistema (`iris`, `claro`, `personal`, `movistar`, `datuar`, etc.) conservan su orden habitual de reclamo por `id ASC` / `id_vps ASC` sin distinción de DNI.

### B. Cortocircuito Inmediato (Short-Circuit)
En cuanto cualquier compañía retorna `StatusScraping.COINCIDENCIA`, se interrumpe inmediatamente la evaluación de las compañías restantes y se retorna el `ScrapeResult`, evitando consultas redundantes y optimizando el consumo de red.

### C. Acumulación No Destructiva
Los datos de cada motor se estructuran bajo su propio namespace (`datos_acumulados["claro"]`, `datos_acumulados["personal"]`, `datos_acumulados["movistar"]`), preservando montos adeudados, comprobantes y fechas.

---

## 3. Ejemplo de Salida JSON Acumulada

En caso de no coincidencia en ninguna telco (con DNI disponible):

```json
{
  "claro": {
    "status": "sin_coincidencia",
    "fuente": "claro",
    "operador": "Claro",
    "ultima_modificacion": "2026-09-30 09:04:30"
  },
  "personal": {
    "status": "sin_coincidencia",
    "fuente": "personal",
    "operador": "Personal",
    "ultima_modificacion": "2026-09-30 09:04:45"
  },
  "movistar": {
    "status": "sin_coincidencia",
    "fuente": "movistar",
    "operador": "Movistar",
    "ultima_modificacion": "2026-09-30 09:05:00"
  }
}
```

---

## 4. Estrategia de Colas y Reserva por DNI para Telcos

Para materializar esta estrategia sin degradar el rendimiento del motor de base de datos, se implementó una reserva optimizada en 3 componentes:

1. **`MySQLQueueAdapter` ([`adapters/queue/mysql_vps_adapter.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/mysql_vps_adapter.py))**:
   - Al invocar `reservar_lote()` con `canon == "telcos"`, ejecuta primero una consulta indexada con `AND (dni IS NOT NULL AND dni != '')` bajo `FOR UPDATE SKIP LOCKED`.
   - Si la cantidad obtenida es menor al `batch_size`, complementa el remanente con una segunda consulta con `AND (dni IS NULL OR dni = '')`. Esto evita un costoso `filesort` sobre millones de registros y mantiene el tiempo de reserva por debajo de 0.6s.
2. **`VpsSyncAdapter` ([`adapters/queue/vps_sync_adapter.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/vps_sync_adapter.py))**:
   - En la sincronización de staging (`descargar_lote_vps()`), si el scraper es `"telcos"`, reclama primero hasta el límite registros con DNI, y completa con registros sin DNI solo si no llena el cupo.
3. **`SQLiteStagingAdapter` ([`adapters/queue/sqlite_staging_adapter.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/sqlite_staging_adapter.py))**:
   - En SQLite local, `reservar_lote()` para Telcos ordena por `(dni IS NOT NULL AND dni != '') DESC, id_vps ASC`, garantizando que los workers consuman en RAM primero todas las líneas con DNI.

---

## 5. Política de Resiliencia y 3 Reintentos ante Bloqueos WAF

Para evitar falsos positivos de finalización por rate-limiting o desafíos bot de la pasarela de Cobro Express (`pagosce.cobroexpress.com.ar`), los adaptadores [`ClaroAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/claro/claro_adapter.py), [`PersonalAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/personal/personal_adapter.py) y [`MovistarAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/movistar/movistar_adapter.py) aplican un ciclo unificado de **3 reintentos automáticos** (hasta 4 intentos en total):

1. **Disparadores de Reintento**:
   - Bloqueo WAF / Desafío Bot: `HTTP 418` (*I'm a teapot*) o `HTTP 419`.
   - Rate Limit / Forbidden: `HTTP 429` o `HTTP 403`.
   - Errores de Pasarela / Servidor: `HTTP 500`, `502`, `503`, `504`.
   - Errores de Red / Tor: `Timeout`, `ConnectTimeoutError` o `Host unreachable (0x04)`.
2. **Acciones en cada Reintento**:
   - **Rotación Forzada de Circuito Tor**: Emisión de señal reactiva `SIGNAL NEWNYM` para obtener una nueva IP de salida.
   - **Backoff Progresivo**: Pausa incremental (`1.0s` en el 1°, `2.0s` en el 2°, `3.0s` en el 3°).
3. **Comportamiento ante Agotamiento de Reintentos**:
   - Si tras los 3 reintentos persiste el bloqueo de red, el adaptador eleva [`ScraperTransientError`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/exceptions.py).
   - En [`ProcessBatchUseCase`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/process_batch_use_case.py), esta excepción interrumpe el lote y revierte los registros a estado `pendiente` en SQLite local, garantizando que **jamás se marque como completada ni se queme en el CRM una línea afectada por caídas de infraestructura**.

---

## 6. Documentos Relacionados

- [Contrato IScraperEnginePort](contrato_iscraper_engine.md)
- [Guía Creación Claro](guia_creacion_claro.md)
- [Guía Creación Personal](guia_creacion_personal.md)
- [Guía Creación Movistar](guia_creacion_movistar.md)
- [Adaptador de Staging Local SQLite](../03_base_de_datos_y_colas/staging_local_sqlite.md)
- [Adaptador de Cola Automatización](../03_base_de_datos_y_colas/adaptador_cola_automatizacion.md)



