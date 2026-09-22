# Supervisor Inmortal (Auto-Spawn y Monitoreo de Procesos 24/7)

El orquestador maestro del sistema está encapsulado en la clase [`SupervisorIndustrial`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L31) en [`runtime/supervisor.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py) y se inicia mediante el script CLI de producción [`supervisor_vps.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/supervisor_vps.py).

Su propósito es proporcionar **resiliencia infinita (inmortalidad)**: garantizar que exactamente $N$ workers (predeterminado: 9) se encuentren ejecutando tareas de scraping en paralelo las 24 horas del día, los 7 días de la semana, recuperándose automáticamente de caídas de red, excepciones fatales, terminaciones por fugas de memoria o desautenticaciones.

---

## 1. Topología del Supervisor y Subhilos Centinelas

Cuando se ejecuta `supervisor.ejecutar()`, el proceso maestro no realiza tareas de scraping directamente. Su responsabilidad es puramente de control, telemetría y despacho centralizado de datos. Para ello, levanta **5 hilos centinelas de soporte** (`daemon=True`) y administra una tabla de slots de procesos:

```mermaid
graph TD
    Sup["SUPERVISOR INDUSTRIAL (Proceso Maestro)<br/>supervisor.ejecutar()"]
    
    subgraph Hilos Centinelas de Soporte (Daemon Threads)
        DB["DBDispatcherThread<br/>_db_dispatcher_loop()<br/>(Despacho IPC: 1 conexión MySQL por PC)"]
        CB["CircuitBreakerThread<br/>_circuit_breaker_loop()<br/>(Ping VPN/Web cada 25s)"]
        HC["HorarioComercialThread<br/>_horario_comercial_loop()<br/>(Centinela de horario cada 10s)"]
        WD["WatchdogSweeperThread<br/>_watchdog_sweeper_loop()<br/>(Rescate huérfanos cada 5m)"]
        Dash["DashboardThread<br/>_metrics_dashboard_loop()<br/>(Drena stats_queue cada 1s)"]
    end
    
    subgraph Pool Concurrente de Workers (Multiprocessing)
        W1["Worker Slot 1 (Gen 1..N)<br/>PID 1001"]
        W2["Worker Slot 2 (Gen 1..N)<br/>PID 1002"]
        W3["Worker Slot ...<br/>PID 1003"]
        WN["Worker Slot N (Gen 1..N)<br/>PID 100N"]
    end
    
    Sup --> DB
    Sup --> CB
    Sup --> HC
    Sup --> WD
    Sup --> Dash
    Sup -->|Spawn escalonado| W1
    Sup -->|Spawn escalonado| W2
    Sup -->|Spawn escalonado| W3
    Sup -->|Spawn escalonado| WN
    
    W1 -.->|Peticiones / Persistencia IPC| DB
    W2 -.->|Peticiones / Persistencia IPC| DB
    WN -.->|Peticiones / Persistencia IPC| DB
    W1 -.->|Emite stats IPC| Dash
    W2 -.->|Emite stats IPC| Dash
    WN -.->|Emite stats IPC| Dash
```

---

## 2. Lanzamiento Escalonado (*Staggered Spawning*)

Al arrancar un clúster de múltiples workers concurrentes (por ejemplo, 9 workers de Playwright o Requests), iniciar todos los subprocesos de forma simultánea generaría un pico de contención severo (*thundering herd problem*):
- Saturación repentina del handshake TLS contra el portal de login.
- Contención de bloqueos en la base de datos MySQL por saturación inmediata de sockets TCP.
- Picos de uso de CPU de 100% durante la inicialización de navegadores Chromium.

Para mitigar esto, el supervisor implementa en las líneas 236-243 de [`runtime/supervisor.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L236-L243) un **arranque escalonado** con un retardo de `1.5 segundos` entre slots:

```python
logger.info(f"Lanzando {self.workers_count} workers iniciales de forma escalonada...")
for s_id in range(1, self.workers_count + 1):
    if self.stop_event.is_set():
        break
    self._spawn_worker(s_id, generation=1)
    time.sleep(1.5)
```

### El Método `_spawn_worker`
Definido en las líneas 171-193 de [`runtime/supervisor.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L171-L193):

```python
def _spawn_worker(self, slot_id: int, generation: int):
    p = Process(
        target=worker_lifecycle_process,
        args=(
            slot_id,
            generation,
            self.scraper_name,
            self.batch_size,
            self.max_queries_worker,
            self.stop_event,
            self.pause_event,
            self.stats_queue,
            self.prioridad,
            self.delay_min,
            self.delay_max,
            self.scraper_kwargs
        ),
        name=f"WorkerSlot-{slot_id}-G{generation}"
    )
    p.start()
    self.worker_slots[slot_id] = {"process": p, "gen": generation}
    logger.info(f"🚀 Worker Slot {slot_id} lanzado ({self.scraper_name.upper()}, Gen {generation}, PID {p.pid}).")
```

Cada proceso es nombrado de forma explícita (`WorkerSlot-{slot_id}-G{generation}`) y registrado en el diccionario interno `self.worker_slots` junto con su objeto `Process` y número de generación.

---

## 3. Bucle de Supervisión Activa y Auto-Regeneración

Una vez desplegados los workers iniciales, el hilo principal de [`SupervisorIndustrial`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L31) entra en un bucle de supervisión permanente con un ciclo de chequeo de `2.0 segundos` (líneas 248-268):

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

### Mecánica de Recuperación
1. **Detección de Muerte:** Si `proc.is_alive()` retorna `False` (ya sea por rotación voluntaria a las 350 consultas, excepción de red o muerte violenta del SO), el supervisor detecta la vacante del slot.
2. **Reclamación del Proceso (`join`):** Se invoca `proc.join(timeout=1.0)` para limpiar descriptores y evitar la creación de procesos zombis en Windows/Linux.
3. **Relevo Generacional Inmediato:** Se calcula `nueva_gen = gen + 1` y tras una pequeña pausa de protección de `1.0` segundo, se invoca `_spawn_worker(s_id, nueva_gen)`.
4. **Cero Caída de Concurrencia:** Los otros 8 slots continúan trabajando ininterrumpidamente sin enterarse del relevo del slot intervenido.

---

## 4. Protocolo de Apagado Ordenado (*Graceful Shutdown*)

El sistema garantiza que la interrupción manual del servicio no corrompa lotes ni deje registros en estado inconsistente.

### A. Captura de Señales
En las líneas 218-224 de [`runtime/supervisor.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L218-L224):

```python
def handle_shutdown(signum, frame):
    print("\n🛑 Señal de detención recibida. Notificando a todos los workers para apagado limpio...")
    self.stop_event.set()

signal.signal(signal.SIGINT, handle_shutdown)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, handle_shutdown)
```

### B. Cascada de Drenado y Terminación
Al activarse `self.stop_event.set()`:
1. Todos los workers activos finalizan la consulta actual que estén ejecutando.
2. Dentro de [`ProcesarLoteUseCase.ejecutar_lote`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/process_batch_use_case.py#L57-L60), la verificación `should_stop()` interrumpe el recorrido del lote:
   ```python
   if should_stop and should_stop():
       logger.info("Parada solicitada en mitad del lote. Interrumpiendo ciclo...")
       break
   ```
3. Los registros ya consultados se persisten con sus resultados.
4. Los registros restantes del lote que no alcanzaron a consultarse son devueltos a estado `'pendiente'` mediante [`revertir_a_pendiente(unprocessed_ids)`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/mysql_vps_adapter.py#L246-L286).
5. El bucle principal del supervisor espera hasta `15.0 segundos` para el `join` de cada proceso:
   ```python
   for s_id, slot_info in self.worker_slots.items():
       proc: Process = slot_info["process"]
       if proc.is_alive():
           proc.join(timeout=15.0)
           if proc.is_alive():
               logger.warning(f"Slot {s_id} no respondió en 15s. Forzando terminación...")
               proc.terminate()
   ```
6. Si un subproceso quedase colgado por bloqueo I/O tras 15 segundos, se envía `proc.terminate()` como última medida de protección.

---

## 5. Centinela de Horario Comercial y Gestión de Pausas Multi-Causa

El supervisor incorpora el hilo centinela `HorarioComercialThread` que evalúa continuamente [`PoliticaHorarioComercial`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/schedule.py) cada 10 segundos:

### Mecánica Multi-Causa Thread-Safe
Para evitar que la recuperación de red de `CircuitBreaker` despause prematuramente a los workers durante la noche (o que la llegada de las 08:00 despause a los workers si la VPN está caída), el supervisor gestiona las razones de pausa en un conjunto sincronizado `_pause_reasons`:
- `_set_pause(razon)`: Añade la causa (`'horario'` o `'vpn'`) y activa `self.pause_event.set()`.
- `_clear_pause(razon)`: Remueve la causa; únicamente limpia `self.pause_event.clear()` cuando `_pause_reasons` queda completamente vacío.

### Comportamiento Operativo Nocturno
1. A las **21:00** (Lunes a Viernes) o **13:00** (Sábados), se registra `'horario'` en `_pause_reasons`.
2. Los workers concluyen de procesar su lote en curso y no realizan ninguna nueva reserva en la base de datos central VPS.
3. El proceso supervisor y sus workers permanecen vivos y estables en memoria en modo bajo consumo (*standby*).
4. A las **08:00** del siguiente día hábil, el centinela remueve `'horario'` y los workers reanudan de inmediato el procesamiento sin necesidad de intervención manual ni reinicio de scripts.

---

## Enlaces Relacionados
* [08. Política de Horario Comercial Oficial](../02_dominio_y_casos_de_uso/politica_horario_comercial.md)
* [20. Ciclo de Vida del Worker](ciclo_de_vida_worker.md)
* [22. Circuit Breaker de Red](circuit_breaker_red.md)
* [27. Supervisor de Producción](../07_operacion_y_runbooks/supervisor_produccion.md)
