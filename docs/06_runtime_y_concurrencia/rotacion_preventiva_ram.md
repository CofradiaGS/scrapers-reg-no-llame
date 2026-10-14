# Rotación Preventiva de Memoria RAM (Relevo Anti-Leak)

En procesos de extracción web continua y automatización de interfaces (especialmente con motores que involucran Chromium / Playwright, parsing de árboles DOM complejos o clientes HTTP que retienen cachés de conexiones TLS persistentes), la fragmentación y fuga gradual de memoria RAM representan la principal causa de degradación en operaciones 24/7.

Para erradicar este problema sin depender de recolectores de basura lentos o reinicios manuales, el sistema implementa una **política estricta de rotación preventiva anti-leak** a nivel de subproceso, configurada por defecto a **350 consultas por ciclo de worker**.

---

## 1. La Problemática del Leak de Memoria en Scraping 24/7

Cuando un worker interactúa sostenidamente con portales corporativos complejos (como IRIS Telecom o autogestiones de operadoras):
1. **Fugas en el Motor de Navegación (Chromium):** El árbol de renderizado, los buffers de DevTools Protocol (CDP), la caché de renderizado WebKit/Blink y los contextos de JavaScript van acumulando memoria residente (RSS) en el subproceso del navegador. Aunque se cierren pestañas, el proceso padre de Chromium retiene páginas de memoria no liberadas al sistema operativo.
2. **Fragmentación en el Heap de CPython:** La asignación y destrucción masiva de diccionarios, strings JSON pesados y estructuras XML/HTML (`lxml`, `BeautifulSoup` o expresiones regulares compiladas) provoca fragmentación en el *pymalloc arena*. La memoria total retenida por el intérprete raramente se devuelve al SO mientras el PID continúe vivo.
3. **Cachés de Sockets y SSL:** Los pools de conexiones `urllib3` y sesiones `requests.Session` acumulan estados de negociación TLS y buffers de lectura en ráfagas de miles de requests.

### Solución Arquitectónica
En lugar de forzar llamadas periódicas a `gc.collect()` (que no desfragmentan la memoria del SO ni purgan procesos Chromium huérfanos), el sistema adopta el principio de **inmutabilidad y reciclabilidad de procesos efímeros**:
> **"Un worker nace, procesa un límite seguro de consultas (350), se retira ordenadamente cerrando sus recursos y transfiere su puesto a un nuevo proceso idéntico (Generación N+1)."**

Al terminar el proceso a nivel del sistema operativo, el kernel limpia de raíz y de manera instantánea el 100% de la tabla de páginas y recursos asignados al PID.

---

## 2. Lógica de Control de Cuota y Cálculo de Lote Dinámico

La lógica de control reside en el bucle principal de [`worker_lifecycle_process`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/worker_process.py#L118-L161).

```mermaid
flowchart TD
    Inicio([Inicio de Ciclo de Lote]) --> CheckCuota{consultas_realizadas >= max_queries?}
    CheckCuota -- Sí (ej. 350/350) --> SalidaLimpia[Log: Cuota alcanzada -> Break del bucle]
    CheckCuota -- No --> CheckCB{Circuit Breaker activo?}
    
    CheckCB -- Sí --> EsperaCB[Dormir 5s y reintentar] --> Inicio
    CheckCB -- No --> CalcLote[Calcular lote exacto sin rebasar cuota]
    
    CalcLote --> Formula["batch_to_claim = min(batch_size, max_queries - consultas_realizadas)"]
    Formula --> ClaimDB[use_case.ejecutar_lote(batch_to_claim)]
    ClaimDB --> EvalProc{num_proc == 0?}
    
    EvalProc -- Sí (Cola vacía) --> EsperaVacia[Dormir 6s y reintentar] --> Inicio
    EvalProc -- No --> Acumular[consultas_realizadas += num_proc]
    Acumular --> Jitter[Dormir jitter cortesía delay_min a delay_max]
    Jitter --> Inicio
    
    SalidaLimpia --> Finally[Bloque finally: cerrar scraper y emitir worker_exit con recycled=True]
    Finally --> Fin([Terminación limpia con exitcode 0])
```

### El Algoritmo de Truncado Atómico de Lote
Un error común en arquitecturas distribuidas es reservar un lote estándar (ej. 12 registros) cuando al worker solo le restan 3 consultas para alcanzar su cuota de 350, lo que provocaría que ejecute 359 consultas o corte el lote a mitad de camino.

Para evitar esto, en la línea 136 de [`runtime/worker_process.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/worker_process.py#L136):
```python
# Calcular tamaño de lote sin exceder la cuota de rotación preventiva
batch_to_claim = min(batch_size, max_queries - consultas_realizadas)
```
Si el worker ha completado 345 consultas y el `batch_size` configurado es 12:
$$\text{batch\_to\_claim} = \min(12, 350 - 345) = 5$$
El worker reserva exactamente 5 registros en el VPS con `FOR UPDATE SKIP LOCKED`. Al procesarlos, su contador alcanza exactamente 350. En la siguiente iteración:
```python
if consultas_realizadas >= max_queries:
    w_log.info(
        f"[{worker_tag}] 🔄 Cuota de rotación preventiva alcanzada "
        f"({consultas_realizadas}/{max_queries}). Relevo limpio anti-leak."
    )
    break
```
El bucle finaliza limpiamente sin dejar registros a medio procesar ni transacciones pendientes.

---

## 3. Emisión de Mensaje de Retiro y Parámetro `recycled`

Cuando el bucle `while` rompe, la ejecución entra indefectiblemente en el bloque `finally` de [`runtime/worker_process.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/worker_process.py#L164-L181):

```python
finally:
    w_log.info(f"[{worker_tag}] Cerrando recursos del scraper...")
    if scraper_engine:
        try:
            scraper_engine.cerrar()
        except Exception:
            pass

    stats_queue.put({
        "tipo": "worker_exit",
        "slot": worker_slot,
        "gen": generation,
        "scraper": scraper_name,
        "consultas": consultas_realizadas,
        "recycled": consultas_realizadas >= max_queries
    })
    w_log.info(f"[{worker_tag}] Proceso finalizado. Total consultas ejecutadas: {consultas_realizadas}.")
```

### Significado del Flag `recycled`
- **`recycled == True`:** La salida fue un relevo programado por diseño anti-leak (`consultas_realizadas >= max_queries`). El tablero de control del supervisor contabiliza esto como un éxito operativo (`stats["reciclados"] += 1`).
- **`recycled == False`:** La salida fue prematura, producto de una excepción fatal no capturada o interrupción. No se contabiliza como rotación sino que activa alarmas de supervisión.

---

## 4. Recepción y Auto-Spawn en el Supervisor

El proceso [`SupervisorIndustrial`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L31) en [`runtime/supervisor.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L248-L268) realiza un monitoreo no bloqueante del pool de workers cada 2.0 segundos:

```python
while not self.stop_event.is_set():
    time.sleep(2.0)

    for s_id in range(1, self.workers_count + 1):
        slot_info = self.worker_slots.get(s_id)
        if not slot_info:
            continue

        proc: Process = slot_info["process"]
        gen: int = slot_info["gen"]

        if not proc.is_alive():
            proc.join(timeout=1.0)
            logger.info(f"Slot {s_id} (Gen {gen}) concluyó.")

            if not self.stop_event.is_set():
                nueva_gen = gen + 1
                logger.info(f"🔄 Auto-Spawn: Reemplazando Slot {s_id} con Generación {nueva_gen}...")
                time.sleep(1.0)
                self._spawn_worker(s_id, nueva_gen)
```

### Proceso de Relevo Paso a Paso
1. **Detección Inmediata:** Mediante `proc.is_alive()`, el supervisor comprueba si el proceso del slot ha concluido.
2. **Join y Recolección:** Se invoca `proc.join(timeout=1.0)` para recolectar el código de salida del subproceso y evitar procesos zombis en la tabla del sistema operativo.
3. **Incremento Generacional:** La variable `gen` del slot avanza (`nueva_gen = gen + 1`). Esto otorga trazabilidad absoluta en logs (ej. `WorkerSlot-3-G1` $\rightarrow$ `WorkerSlot-3-G2`).
4. **Pausa de Estabilización:** Un breve intervalo de `1.0` segundo previene picos de consumo de I/O al lanzar el nuevo proceso.
5. **Re-engendrado (`_spawn_worker`):** Se instancia un nuevo `Process` con memoria 100% limpia y nuevo PID.

---

## 5. Parámetros CLI y Ajuste Fino

En [`supervisor_vps.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/supervisor_vps.py#L68), el operador puede parametrizar la cadencia de rotación según la memoria RAM disponible en el nodo de cómputo:

```bash
python supervisor_vps.py --scraper iris_http --workers 9 --max-queries-worker 350
```

| Escenario de Operación | Motor | `--max-queries-worker` recomendado | Consumo RAM Observado por Worker |
|---|---|---|---|
| Servidor VPS 8GB RAM | `iris_browser` (Playwright Chromium) | **200 - 300** | Crece de 180MB a ~450MB antes del relevo. |
| Servidor VPS 16GB+ RAM | `iris_browser` (Playwright Chromium) | **350 - 500** | Crece hasta ~650MB antes de liberación total. |
| VPS o Instancia Cloud | `iris_http` (HTTP Puro Requests) | **500 - 1000** | Muy ligero (~60MB a 110MB). Permite cuotas mayores. |
