# Caso de Uso: Procesar Lote (`ProcesarLoteUseCase`)

El caso de uso [`ProcesarLoteUseCase`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/process_batch_use_case.py#L19-L133) es el corazón orquestador de la capa de aplicación. Su responsabilidad es coordinar el ciclo de vida completo de un micro-lote de registros desde su reserva atómica en la cola hasta su persistencia enriquecida en la base de datos central.

---

## 1. Responsabilidades del Caso de Uso

1. **Reclamo Atómico:** Solicita un micro-lote de registros disponibles al puerto de cola [`IColaRepositorioPort`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/ports/queue_port.py#L11-L57) para el scraper especificado.
2. **Ejecución de Scraping:** Invoca secuencialmente la consulta externa a través del puerto [`IScraperEnginePort`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/ports/scraper_port.py#L10-L49).
3. **Aplicación de Reglas de Negocio:** Evalúa el resultado mediante [`ReglaPipeline`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/domain/entities.py#L126-L172) para resolver el próximo scraper y estado.
4. **Fusión Acumulativa de Namespaces:** Agrega la información nueva al diccionario `datos_json` existente sin sobreescribir datos previos.
5. **Auditoría de Fuentes:** Actualiza la lista JSON histórica en la columna `fuente`.
6. **Persistencia en Lote:** Persiste masivamente las actualizaciones de estado y datos con una sola llamada atómica.
7. **Compensación de Fallos (Rollback):** Si se interrumpe el ciclo (por parada ordenada o error), revierte a estado `pendiente` todos aquellos registros que no hayan llegado a ser procesados.

---

## 2. Código Fuente Completo y Comentado

```python
class ProcesarLoteUseCase:
    """Caso de uso orquestador de lotes de scraping desacoplado."""

    def __init__(
        self,
        cola_repo: IColaRepositorioPort,
        scraper_engine: IScraperEnginePort,
        cadena_pipeline: Optional[List[str]] = None
    ):
        self.cola = cola_repo
        self.scraper = scraper_engine
        self.cadena = cadena_pipeline

    def ejecutar_lote(
        self,
        batch_size: int = 12,
        prioridad: Optional[int] = None,
        on_item_procesado: Optional[Callable[[Dict[str, Any]], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None
    ) -> tuple[int, List[int]]:
        """
        Ejecuta un ciclo de procesamiento de lote.
        Retorna: (cantidad_procesados, lista_ids_no_procesados_si_hubo_parada)
        """
        # 1. Reclamar lote a través del puerto de cola
        lote = self.cola.reservar_lote(
            batch_size=batch_size,
            prioridad=prioridad,
            scraper_nombre=self.scraper.nombre
        )

        if not lote:
            return 0, []

        resultados = []
        unprocessed_ids = [r.id for r in lote]

        for reg in lote:
            if should_stop and should_stop():
                logger.info("Parada solicitada en mitad del lote. Interrumpiendo ciclo...")
                break

            t0 = time.time()
            try:
                # 2. Consultar a través del puerto de scraper
                resultado = self.scraper.consultar_linea(reg.linea)
                lat = round(time.time() - t0, 2)

                # 3. Aplicar Regla de Dominio: Cortocircuito y Siguiente Posta
                sig_scraper, sig_estado = ReglaPipeline.resolver_siguiente_etapa(
                    scraper_actual=self.scraper.nombre,
                    resultado=resultado,
                    cadena=self.cadena
                )

                # Construir historial de fuentes
                fuentes_actuales = reg.fuente or "[]"
                if self.scraper.nombre not in fuentes_actuales:
                    fuente_norm = f'["{self.scraper.nombre}"]' if fuentes_actuales in ("[]", "None", "") else fuentes_actuales.rstrip("]") + f', "{self.scraper.nombre}"]'
                else:
                    fuente_norm = fuentes_actuales

                # Fusión acumulativa de datos_json preservando scrapers previos
                datos_acumulados = dict(reg.datos_existentes or {})
                datos_acumulados.update(resultado.to_namespace_dict())

                item_res = {
                    "id": reg.id,
                    "ani": reg.linea.ani,
                    "scraper_actual": sig_scraper,
                    "estado": sig_estado,
                    "descripcion": resultado.descripcion or f"Scrapeado por {self.scraper.nombre}",
                    "fuente": fuente_norm,
                    "datos": datos_acumulados,
                    "latencia": lat,
                    "status": resultado.status.value
                }
                resultados.append(item_res)
                unprocessed_ids.remove(reg.id)

                if on_item_procesado:
                    on_item_procesado(item_res)

            except Exception as e:
                lat = round(time.time() - t0, 2)
                err_str = str(e).replace("\n", " ")[:190]
                logger.error(f"Error procesando línea {reg.linea.ani}: {err_str}")

                item_err = {
                    "id": reg.id,
                    "ani": reg.linea.ani,
                    "scraper_actual": self.scraper.nombre,
                    "estado": "error",
                    "descripcion": f"Error: {err_str}"[:195],
                    "fuente": reg.fuente or f'["{self.scraper.nombre}"]',
                    "datos": None,
                    "latencia": lat,
                    "status": "error"
                }
                resultados.append(item_err)
                unprocessed_ids.remove(reg.id)

                if on_item_procesado:
                    on_item_procesado(item_err)

        # 4. Persistir lote completado a través del puerto de cola
        if resultados:
            self.cola.persistir_resultados(resultados)

        # 5. Si quedaron registros sin procesar por interrupción, devolverlos a pendiente
        if unprocessed_ids:
            self.cola.revertir_a_pendiente(unprocessed_ids)

        return len(resultados), unprocessed_ids
```

---

## 3. Diagrama de Flujo del Proceso

```mermaid
flowchart TD
    Inicio([Llamada a ejecutar_lote]) --> Claim["1. cola.reservar_lote(batch_size, prioridad, scraper)"]
    Claim --> CheckLote{"¿Lote vacío?"}
    CheckLote -->|Sí| ReturnEmpty["Retornar (0, [])"]
    
    CheckLote -->|No| InitList["Inicializar lista de resultados<br/>unprocessed_ids = [ids...]"]
    
    InitList --> LoopItems{"¿Hay más registros<br/>en el lote?"}
    
    LoopItems -->|Sí| CheckStop{"¿should_stop() == True?"}
    CheckStop -->|Sí| BreakLoop["Interrumpir bucle por parada ordenada"]
    
    CheckStop -->|No| ScrapeItem["2. scraper.consultar_linea(reg.linea)"]
    
    ScrapeItem -->|Éxito| ResolveDomain["3. ReglaPipeline.resolver_siguiente_etapa(...)"]
    ResolveDomain --> BuildAudit["Normalizar array fuente<br/>['iris', 'claro']"]
    BuildAudit --> MergeJSON["datos_acumulados = {...existente, ...to_namespace_dict()}"]
    MergeJSON --> AppendResult["Agregar a resultados[]<br/>Remover ID de unprocessed_ids"]
    AppendResult --> CallbackOk["Invocar on_item_procesado(item_res)"]
    CallbackOk --> LoopItems

    ScrapeItem -->|Excepción| CatchError["Capturar error y medir latencia"]
    CatchError --> AppendErr["Agregar a resultados[] con estado='error'<br/>Remover ID de unprocessed_ids"]
    AppendErr --> CallbackErr["Invocar on_item_procesado(item_err)"]
    CallbackErr --> LoopItems

    LoopItems -->|No| Persist["4. cola.persistir_resultados(resultados)"]
    BreakLoop --> Persist
    
    Persist --> CheckUnproc{"¿unprocessed_ids no está vacío?"}
    CheckUnproc -->|Sí| Rollback["5. cola.revertir_a_pendiente(unprocessed_ids)"]
    CheckUnproc -->|No| Finish([Retornar len(resultados), unprocessed_ids])
    Rollback --> Finish
```

---

## 4. Análisis Detallado de Mecanismos Clave

### 4.1. Fusión Acumulativa de JSON
```python
datos_acumulados = dict(reg.datos_existentes or {})
datos_acumulados.update(resultado.to_namespace_dict())
```
Cuando un registro es reclamado de la base de datos, el adaptador de cola deserializa su columna `datos_json` en el diccionario `reg.datos_existentes`. Al finalizar la consulta del scraper actual, `resultado.to_namespace_dict()` produce un payload aislado (ej: `{"claro": {...}}`). 

El método `update` de Python realiza una combinación a nivel raíz:
- Si ya existía información de `"iris"`, esta se preserva intacta.
- Se inserta o actualiza la clave `"claro"`.
- Cada clave de scraper contiene su propio campo `ultima_modificacion` (`YYYY-MM-DD HH:MM:SS`), capturando la hora exacta del update de ese scraper particular sin alterar las marcas temporales de los scrapers anteriores.
- Cuando el lote es persistido, el adaptador de base de datos serializa de nuevo `datos_acumulados` a JSON completo.

### 4.2. Normalización de la Cadena de Fuentes
```python
fuentes_actuales = reg.fuente or "[]"
if self.scraper.nombre not in fuentes_actuales:
    fuente_norm = f'["{self.scraper.nombre}"]' if fuentes_actuales in ("[]", "None", "") else fuentes_actuales.rstrip("]") + f', "{self.scraper.nombre}"]'
else:
    fuente_norm = fuentes_actuales
```
Evita duplicaciones si un scraper se reintenta, y añade el nombre entrecomillado respetando el formato JSON string de la base de datos (ej: `["iris", "claro"]`).

### 4.3. Tratamiento de Parada Ordenada (Graceful Shutdown)
El parámetro opcional `should_stop: Optional[Callable[[], bool]] = None` recibe un callback (en producción, `stop_event.is_set`).
- Si el usuario o el supervisor envían una señal `SIGINT` (Ctrl+C) mientras un lote de 12 registros se está procesando y ya se ejecutaron 4:
  1. `should_stop()` devuelve `True`.
  2. El bucle se interrumpe de inmediato.
  3. Los 4 registros ya procesados se persisten con sus datos completos (`cola.persistir_resultados`).
  4. Los 8 registros restantes (`unprocessed_ids`) se liberan a la cola en una sola llamada SQL (`cola.revertir_a_pendiente`), cambiando su estado de `procesando` a `pendiente`.
  5. Ningún registro queda bloqueado ni perdido en el limbo.

### 4.4. Actualización Obligatoria de `updated_at` en MySQL
Toda operación que altere el estado o los datos de un registro en `queue_registro_no_llame` debe actualizar sin excepción la columna relacional `updated_at`. En el adaptador [`MySQLQueueAdapter`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/mysql_vps_adapter.py#L197-L203):
- Al persistir resultados (`persistir_resultados`): `SET estado = %s, scraper_actual = %s, fuente = %s, datos_json = %s, updated_at = CURRENT_TIMESTAMP`.
- Al revertir a pendiente (`revertir_a_pendiente`): `SET estado = 'pendiente', updated_at = CURRENT_TIMESTAMP`.
- Al reclamar lote o liberar huérfanos: `updated_at = CURRENT_TIMESTAMP`.

---

## 5. Referencias Cruzadas
- [Regla de Pipeline en Dominio](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/docs/02_dominio_y_casos_de_uso/regla_pipeline_dominio.md)
- [Caso de Uso: Liberar Huérfanos](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/docs/02_dominio_y_casos_de_uso/caso_uso_liberar_huerfanos.md)
- [Entidades y Value Objects de Dominio](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/docs/02_dominio_y_casos_de_uso/entidades_y_value_objects.md)
- [Adaptador de Cola MySQL VPS](../03_base_de_datos_y_colas/esquema_ddl_vps.md)
- [Worker Runtime y Ciclo de Vida](../06_runtime_y_concurrencia/ciclo_de_vida_worker.md)
