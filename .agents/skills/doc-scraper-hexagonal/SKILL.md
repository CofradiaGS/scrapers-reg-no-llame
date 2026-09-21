---
name: doc-scraper-hexagonal
description: >-
  Mantiene, audita y actualiza la documentación técnica del sistema multi-scraper garantizando
  fidelidad absoluta a la Arquitectura Hexagonal ya implementada. Exige el desacoplamiento de capas
  (Dominio, Puertos, Adaptadores, Runtime), la regla de pipeline en cascada con cortocircuito a finalizado,
  la persistencia acumulativa de datos_json/fuente y la estructura atómica de 7 capas en docs/.
---

# Skill: Guardián de la Arquitectura Hexagonal y Documentación Modular

> **Mandato Principal**: La documentación técnica del sistema **DEBE SEGUIR ESTRICTAMENTE LA ARQUITECTURA YA CONSTRUIDA**. 
> La Arquitectura Hexagonal (Ports & Adapters), la cascada de scrapers con cortocircuito, el enriquecimiento acumulativo en JSON y la concurrencia industrial 24/7 con `SKIP LOCKED` son la ley suprema del proyecto. Ningún documento puede contradecir, omitir ni desviar este diseño.

---

## 1. La Arquitectura Implementada (Fuente de Verdad Inmutable)

Cualquier agente, subagente o desarrollador que documente el sistema debe alinearse a este diagrama y a sus responsabilidades:

```mermaid
flowchart TD
    subgraph DrivingAdapters [1. Adaptadores Primarios / Entrada]
        CLI[CLI: main.py]
        SUP[Supervisor Industrial: supervisor_vps.py / runtime/supervisor.py]
        BAT[Demonio Windows: run_daemon.bat]
    end

    subgraph CoreApplication [2. Capa de Casos de Uso / Orquestación]
        UC_Batch[core/use_cases/process_batch_use_case.py: ProcesarLoteUseCase]
        UC_Orphans[core/use_cases/cleanup_orphans_use_case.py: LiberarHuerfanosUseCase]
    end

    subgraph CoreDomain [3. Núcleo de Dominio Puro (Agnóstico a Tecnología)]
        Entities[core/domain/entities.py: Linea, Titular, Servicio, TramitePortabilidad, ScrapeResult, RegistroCola]
        Rule[core/domain/entities.py: ReglaPipeline]
        Enums[core/domain/enums.py: Prioridad, EstadoRegistro, StatusScraping]
        Exceptions[core/domain/exceptions.py: DomainException]
    end

    subgraph CorePorts [4. Puertos Abstractos / Contratos]
        PortQueue[core/ports/queue_port.py: IColaRepositorioPort]
        PortScraper[core/ports/scraper_port.py: IScraperEnginePort]
    end

    subgraph DrivenAdapters [5. Adaptadores Secundarios / Infraestructura]
        subgraph InfraBD [Persistencia de Datos]
            MySQL[adapters/db/mysql_vps_adapter.py: MySQLQueueAdapter]
            MEM[adapters/db/memory_adapter.py: MemoryQueueAdapter]
        end
        subgraph MotoresScraping [Motores de Extracción]
            HTTP[adapters/scrapers/iris/iris_http_adapter.py: IrisHttpAdapter]
            BROWSER[adapters/scrapers/iris/iris_browser_adapter.py: IrisBrowserAdapter]
            REG[adapters/scrapers/registry.py: ScraperRegistry]
            NUEVOS[Futuros adaptadores: ClaroAdapter, MovistarAdapter, etc.]
        end
    end

    subgraph RuntimeLayer [6. Runtime Concurrente y Resiliencia 24/7]
        Master[runtime/supervisor.py: SupervisorIndustrial]
        Worker[runtime/worker_process.py: worker_lifecycle_process]
        Breaker[runtime/supervisor.py: CircuitBreaker]
        Sweeper[runtime/supervisor.py: WatchdogSweeper]
    end

    DrivingAdapters --> RuntimeLayer
    RuntimeLayer --> Master & Worker
    Worker --> UC_Batch
    Master --> UC_Orphans
    UC_Batch --> Rule & Entities & Enums
    UC_Batch --> PortQueue & PortScraper
    UC_Orphans --> PortQueue
    PortQueue -.->|Implementado por| InfraBD
    PortScraper -.->|Implementado por| MotoresScraping
```

---

## 2. Las 5 Leyes Inviolables de la Arquitectura para Documentar

### Ley I: Desacoplamiento Hexagonal Absoluto
* **Dominio Puro (`core/domain/`)**: Debe documentarse siempre como código sin dependencias externas. Jamás importa librerías de BD (`mysql.connector`), HTTP (`requests`), navegadores (`playwright`) ni frameworks externos. Solo tipos nativos de Python (`dataclasses`, `enum`, `typing`).
* **Puertos (`core/ports/`)**: Son clases base abstractas (`abc.ABC`). Definen las firmas obligatorias. Cualquier nuevo repositorio o motor debe documentarse como una implementación de estos puertos.
* **Casos de Uso (`core/use_cases/`)**: Orquestan el flujo inyectando puertos. No instancian adaptadores concretos directamente; reciben interfaces abstractas.
* **Adaptadores (`adapters/`)**: Residen en la periferia. Aquí se ubican los detalles técnicos de red, HTML parsing, SQL queries y pools de conexiones.
* **Runtime (`runtime/`)**: Administra la concurrencia a nivel de sistema operativo (multiprocessing, señales, memoria RAM, IPC).

### Ley II: Pipeline en Cascada y Cortocircuito (Short-Circuit)
Toda documentación que explique el ciclo de vida de una línea debe reflejar exactamente la lógica de `ReglaPipeline`:
1. **Cadena Oficial de Operadores:** `["iris", "claro", "movistar", "personal", "finalizado"]`.
2. **Paso Inicial:** Una línea ingresa con `scraper_actual = 'iris'`. El scraper de IRIS extrae datos de portabilidad (Port Out) y avanza a `scraper_actual = 'claro'`, `estado = 'pendiente'`.
3. **Cortocircuito Inmediato:** En cualquier operador posterior (`claro`, `movistar`, `personal`), si el scraper obtiene `StatusScraping.COINCIDENCIA`, la línea **CORTOCIRCUITA** de inmediato a:
   * `scraper_actual = 'finalizado'`
   * `estado = 'completado'`
   *(Se documenta explícitamente que no se consulta ningún otro operador para ahorrar ancho de banda y tiempo).*
4. **No Coincidencia:** Si retorna `StatusScraping.SIN_COINCIDENCIA`, la línea avanza a la siguiente posta en estado `pendiente`. Al agotar el último operador sin coincidencia, pasa a `scraper_actual = 'finalizado'`, `estado = 'sin_datos'`.

### Ley III: Enriquecimiento Acumulativo de Datos (`datos_json` y `fuente`)
Toda documentación de persistencia debe detallar que:
* **`datos_json`** almacena un objeto JSON estructurado con namespaces aislados por motor, preservando la historia completa de extracciones:
  ```json
  {
    "iris": {
      "status": "coincidencia",
      "operador_receptor": "Claro",
      "titular": { "nombre": "JUAN PEREZ", "documento": "20123456" }
    },
    "claro": {
      "status": "coincidencia",
      "titular": { "nombre": "JUAN PEREZ", "documento": "20123456" },
      "servicio": { "plan": "Pospago", "modalidad": "Factura" }
    }
  }
  ```
* **`fuente`** registra un array JSON ordenado con los motores que auditaron la línea cronológicamente: `["iris", "claro"]`.

### Ley IV: Concurrencia Transaccional y Anti-Colisión (MySQL 8 VPS)
Toda documentación sobre la base de datos y la cola debe detallar:
* **`FOR UPDATE SKIP LOCKED`**: Reclamo seguro sin contención ni deadlocks entre workers paralelos.
* **Índice Compuesto Principal**: `idx_scraper_estado (scraper_actual, estado)` para búsquedas inmediatas en menos de 0.20s sobre millones de filas.
* **Segmentación por Prioridades B-Tree**: P1 (AMBA 11 y Mendoza 261), P2 (Patagonia Sur), P3 (Resto del País), con penalización de retroceso (`backoff`) de 60s si una cola se agota.
* **Persistencia Atómica**: Uso de `executemany` en una sola transacción (`autocommit=False`).

### Ley V: Resiliencia Concurrente y Rotación Preventiva de RAM (24/7)
Toda documentación de ejecución debe reflejar:
* **Procesos Aislados**: Cada worker corre en su propio subproceso OS (`multiprocessing.Process`) con su propio pool de base de datos (`MySQLConnectionPool`).
* **Rotación Preventiva Anti-Leak**: Relevo automático e imperceptible del worker cada **350 consultas** para evitar la degradación de memoria de Chromium o Requests.
* **Circuit Breaker**: Detección de fallas consecutivas (ej. corte de VPN) para pausar reclamos y no saturar la BD ni quemar líneas.
* **Watchdog Sweeper**: Hilo centinela que libera huérfanos cada 5 minutos revirtiendo registros colgados en `procesando` a `pendiente`.

---

## 3. Matriz de Correspondencia: Código Fuente ➔ Capa Documental

Al documentar cualquier archivo del proyecto, debe asignarse estrictamente a su capa correspondiente:

| Ruta del Código Fuente | Capa Documental | Carpeta Destino | Rol Arquitectónico |
| :--- | :--- | :--- | :--- |
| `core/domain/*.py` | Capa 02: Dominio y Casos de Uso | `docs/02_dominio_y_casos_de_uso/` | Entidades puras, Value Objects, Enums, ReglaPipeline |
| `core/ports/*.py` | Capa 01 / Capa 05 | `docs/01_arquitectura/` y `docs/05_guia_nuevos_scrapers/` | Contratos abstractos (`IColaRepositorioPort`, `IScraperEnginePort`) |
| `core/use_cases/*.py` | Capa 02: Dominio y Casos de Uso | `docs/02_dominio_y_casos_de_uso/` | Orquestación de negocio (`ProcesarLoteUseCase`, `LiberarHuerfanosUseCase`) |
| `adapters/db/*.py` (alias `adapters/queue/`) | Capa 03: Base de Datos y Colas | `docs/03_base_de_datos_y_colas/` | Adaptadores de persistencia MySQL 8 VPS con `SKIP LOCKED` y Memoria |
| `adapters/scrapers/iris/*.py` | Capa 04: Scrapers IRIS | `docs/04_scrapers_iris/` | Adaptadores HTTP Fuego BPM, Playwright, Parser HTML/Regex |
| `adapters/scrapers/registry.py` | Capa 05: Extensibilidad | `docs/05_guia_nuevos_scrapers/` | Factoría dinámica y catálogo de motores de scraping |
| `adapters/scrapers/<nuevo>/*.py` | Capa 05: Extensibilidad | `docs/05_guia_nuevos_scrapers/` | Nuevos adaptadores de operadoras (Claro, Movistar Directo, Personal) |
| `runtime/*.py` | Capa 06: Runtime y Concurrencia | `docs/06_runtime_y_concurrencia/` | Supervisor maestro, worker lifecycle, rotación RAM, IPC, Watchdog |
| `main.py`, `supervisor_vps.py`, `*.bat` | Capa 07: Operación y Runbooks | `docs/07_operacion_y_runbooks/` | Comandos CLI, supervisión de producción, scripts de servicio y incidentes |

---

## 4. Mapa de los 29 Módulos Documentales Existentes en `docs/`

La documentación ya se encuentra organizada en 29 módulos atómicos más el índice maestro `docs/README.md`:

```
docs/
├── README.md                                             # Índice maestro y mapa general del sistema
├── 01_arquitectura/
│   ├── vision_general.md                                 # Módulo 01: Propósito general y pipeline
│   ├── arquitectura_hexagonal.md                         # Módulo 02: Inversión de dependencias y capas
│   └── pipeline_cascada.md                               # Módulo 03: Cadena de responsabilidad y cortocircuito
├── 02_dominio_y_casos_de_uso/
│   ├── entidades_y_value_objects.md                      # Módulo 04: Dataclasses inmutables y contratos
│   ├── regla_pipeline_dominio.md                         # Módulo 05: Algoritmo de transición y namespaces
│   ├── caso_uso_procesar_lote.md                         # Módulo 06: Orquestación ProcesarLoteUseCase
│   └── caso_uso_liberar_huerfanos.md                     # Módulo 07: Recuperación LiberarHuerfanosUseCase
├── 03_base_de_datos_y_colas/
│   ├── esquema_ddl_vps.md                                # Módulo 08: DDL tabla queue_registro_no_llame
│   ├── indices_y_rendimiento.md                          # Módulo 09: Índices compuestos B-Tree y EXPLAIN
│   ├── concurrencia_skip_locked.md                       # Módulo 10: Bloqueo de fila sin esperas
│   ├── cascada_prioridades_btree.md                      # Módulo 11: P1 AMBA/Mza, P2 Sur, P3 Resto
│   └── pool_conexiones_y_reintentos.md                   # Módulo 12: Pools por PID y backoff
├── 04_scrapers_iris/
│   ├── adaptadores_iris_hexagonal.md                     # Módulo 13: IrisHttpAdapter e IrisBrowserAdapter
│   ├── ingenieria_inversa_http.md                        # Módulo 14: WebLogic Plumtree BPM, tokens y AJAX
│   ├── automatizacion_browser.md                         # Módulo 15: Playwright Chromium Headless
│   └── mapeo_campos_parser.md                            # Módulo 16: Parser Regex y BeautifulSoup
├── 05_guia_nuevos_scrapers/
│   ├── contrato_iscraper_engine.md                       # Módulo 17: Interfaz IScraperEnginePort
│   ├── registro_y_factoria.md                            # Módulo 18: Catálogo ScraperRegistry
│   └── guia_creacion_claro.md                            # Módulo 19: Implementación paso a paso de Claro
├── 06_runtime_y_concurrencia/
│   ├── ciclo_de_vida_worker.md                           # Módulo 20: Subprocesos OS aislados
│   ├── rotacion_preventiva_ram.md                        # Módulo 21: Relevo preventivo a las 350 consultas
│   ├── circuit_breaker_red.md                            # Módulo 22: Centinela de conectividad y VPN
│   ├── supervisor_inmortal.md                            # Módulo 23: Supervisor maestro y auto-spawn
│   ├── watchdog_sweeper.md                               # Módulo 24: Limpieza de huérfanos cada 5 min
│   └── tablero_metricas_ipc.md                           # Módulo 25: Dashboard industrial y colas IPC
└── 07_operacion_y_runbooks/
    ├── comandos_cli_main.md                              # Módulo 26: Subcomandos de main.py
    ├── supervisor_produccion.md                          # Módulo 27: supervisor_vps.py y flags CLI
    ├── demonio_windows_bat.md                            # Módulo 28: Demonio 24/7 en run_daemon.bat
    └── resolucion_problemas.md                           # Módulo 29: Runbook de incidentes y VPN F5
```

---

## 5. Reglas de Estilo, No-Regresión y Verificación Automatizada

Al redactar o editar cualquier documento:

1. **Regla de No-Regresión**: Jamás omitir información técnica existente. No resumir tablas detalladas ni reemplazar código funcional con pseudocódigo simplificado.
2. **Sintaxis de Código Estricta**:
   * Delimitadores triple acento grave con especificador de lenguaje: ```` ```python ````, ```` ```mermaid ````, ```` ```sql ````, ```` ```powershell ````.
   * Cadenas de Python con comillas cerradas y sintaxis válida (`.get("clave", "")`, `f"ANI: {ani}"`).
   * En tablas Markdown, nunca usar barras invertidas crudas que coincidan con escapes de formato (`\f`, `\n`, `\r`, `\t`). Los nombres de variables deben protegerse siempre entre backticks: `` `fecha_operacion` ``.
3. **Enlaces Clicables**:
   * Enlaces a código: Usar formato URI absoluto con espacios codificados: `[NombreClase](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L10)`.
   * Enlaces entre documentos: Usar rutas relativas al archivo markdown: `[Arquitectura Hexagonal](../01_arquitectura/arquitectura_hexagonal.md)`.
4. **Verificación Automatizada de Enlaces**:
   Antes de dar por concluida cualquier actualización documental, ejecutar el script de verificación y asegurar **0 enlaces rotos**:
   ```powershell
   python -c "
   import os, re, urllib.parse; from pathlib import Path
   broken = []
   for md in Path('docs').rglob('*.md'):
       t = re.sub(r'```.*?```', '', md.read_text(encoding='utf-8', errors='ignore'), flags=re.DOTALL)
       for txt, l in re.findall(r'\[([^\]]+)\]\(([^)]+)\)', t):
           if l.startswith('file:///'):
               p = Path(urllib.parse.unquote(l.replace('file:///', '')).split('#')[0].lstrip('/' if os.name == 'nt' else ''))
               if not p.exists(): broken.append((md.name, txt, l))
           elif not l.startswith(('http', '#', 'mailto:')):
               p = (md.parent / urllib.parse.unquote(l.split('#')[0])).resolve()
               if not p.exists(): broken.append((md.name, txt, l))
   print(f'Total enlaces analizados. Rotos encontrados: {len(broken)}')
   if broken:
       for f, txt, l in broken: print(f'  [ROTO] {f}: {txt} -> {l}')
       exit(1)
   "
   ```
