# Supervisor Inmortal (Auto-Spawn y Monitoreo de Procesos 24/7)

El orquestador maestro del sistema está encapsulado en la clase [`SupervisorIndustrial`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L31) en [`runtime/supervisor.py`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py) y se inicia mediante el script CLI de producción [`supervisor_vps.py`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/supervisor_vps.py).

Su propósito es proporcionar **resiliencia infinita (inmortalidad)**: garantizar que exactamente $N$ workers (predeterminado: 9) se encuentren ejecutando tareas de scraping en paralelo las 24 horas del día, los 7 días de la semana, recuperándose automáticamente de caídas de red, excepciones fatales, terminaciones por fugas de memoria o desautenticaciones.

---

## 1. Topología del Supervisor y Subhilos Centinelas

Cuando se ejecuta `supervisor.ejecutar()`, el proceso maestro no realiza tareas de scraping directamente. Su responsabilidad es puramente de control, telemetría y despacho centralizado de datos. Para ello, levanta **hilos centinelas de soporte** (`daemon=True`) y administra una tabla de slots de procesos:

```mermaid
graph TD
    Sup["SUPERVISOR INDUSTRIAL (Proceso Maestro)<br/>supervisor.ejecutar()"]
    
    subgraph Hilos Centinelas de Soporte (Daemon Threads)
        DB["DBDispatcherThread<br/>_db_dispatcher_loop()<br/>(Despacho IPC: 1 conexión MySQL por PC)"]
        CB["CircuitBreakerThread<br/>_circuit_breaker_loop()<br/>(Ping VPN/Web cada 25s)"]
        HC["HorarioComercialThread<br/>_horario_comercial_loop()<br/>(Centinela de horario cada 10s)"]
        WD["WatchdogSweeperThread<br/>_watchdog_sweeper_loop()<br/>(Rescate huérfanos cada 5m)"]
        TorWD["TorPoolWatchdogThread<br/>_tor_pool_watchdog_loop()<br/>(Resiliencia de Pool Tor cada 30s)"]
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
    Sup --> TorWD
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

Para mitigar esto, el supervisor implementa en las líneas 236-243 de [`runtime/supervisor.py`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L236-L243) un **arranque escalonado** con un retardo de `1.5 segundos` entre slots:

```python
logger.info(f"Lanzando {self.workers_count} workers iniciales de forma escalonada...")
for s_id in range(1, self.workers_count + 1):
    if self.stop_event.is_set():
        break
    self._spawn_worker(s_id, generation=1)
    time.sleep(1.5)
```

### El Método `_spawn_worker`
Definido en las líneas 171-193 de [`runtime/supervisor.py`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L171-L193):

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

Una vez desplegados los workers iniciales, el hilo principal de [`SupervisorIndustrial`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L31) entra en un bucle de supervisión permanente con un ciclo de chequeo de `2.0 segundos` (líneas 248-268):

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
En las líneas 218-224 de [`runtime/supervisor.py`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L218-L224):

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
2. Dentro de [`ProcesarLoteUseCase.ejecutar_lote`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/process_batch_use_case.py#L57-L60), la verificación `should_stop()` interrumpe el recorrido del lote:
   ```python
   if should_stop and should_stop():
       logger.info("Parada solicitada en mitad del lote. Interrumpiendo ciclo...")
       break
   ```
3. Los registros ya consultados se persisten con sus resultados.
4. Los registros restantes del lote que no alcanzaron a consultarse son devueltos a estado `'pendiente'` mediante [`revertir_a_pendiente(unprocessed_ids)`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/mysql_vps_adapter.py#L246-L286).
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

El supervisor incorpora el hilo centinela `HorarioComercialThread` que interactúa con [`PoliticaHorarioComercial`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/core/domain/schedule.py) de forma determinista y matemática:

### Mecánica Multi-Causa Thread-Safe
Para evitar que la recuperación de red de `CircuitBreaker` despause prematuramente a los workers durante la noche (o que la llegada de las 08:00 despause a los workers si la VPN está caída), el supervisor gestiona las razones de pausa en un conjunto sincronizado `_pause_reasons`:
- `_set_pause(razon)`: Añade la causa (`'horario'`, `'vpn'` o `'cola_vacia'`) y activa `self.pause_event.set()`.
- `_clear_pause(razon)`: Remueve la causa; únicamente limpia `self.pause_event.clear()` cuando `_pause_reasons` queda completamente vacío.

### Comportamiento Operativo Nocturno
1. A las **21:00** (Lunes a Viernes) o **13:00** (Sábados), se registra `'horario'` en `_pause_reasons`.
2. Los workers concluyen de procesar su lote en curso y no realizan ninguna nueva reserva en la base de datos central VPS.
3. El proceso supervisor y sus workers permanecen vivos y estables en memoria en modo bajo consumo (*standby*).
4. A las **08:00** del siguiente día hábil, el centinela remueve `'horario'` y los workers reanudan de inmediato el procesamiento sin necesidad de intervención manual ni reinicio de scripts.

---

## 6. Centinela de Sincronización Desacoplado (`SyncSchedulerThread`)

<<<<<<< Updated upstream
=======
Cuando la persistencia opera mediante Staging Local (`SQLiteStagingAdapter`), el supervisor instancia el hilo centinela [`SyncSchedulerThread`](file:///C:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/runtime/sync_scheduler.py#L31):
>>>>>>> Stashed changes
- **Watermark Pull Permanente**: Prioridad #1 del centinela. Si las tareas pendientes en SQLite bajan a $\le 2.000$, ejecuta de inmediato la recarga bajo `_pull_lock` para abastecer a los workers sin vaciar la cola.
- **Protección de Backlog Local (Watermark Cap de 10.000)**: Si la suma de tareas activas (`pendientes + listos`) en SQLite alcanza el umbral de 10.000, el pull se pausa automáticamente para priorizar el drenaje de los lotes al VPS y garantizar que la cola `en_proceso` en MySQL nunca exceda las 10.000 tareas.
- **Subida Continua Desacoplada (Bloques de a 5.000)**: Dispara en segundo plano un hilo desacoplado `SyncPushDrainWorker` bajo `_push_lock` cuando hay $\ge 5.000$ registros listos. Drena en ciclos continuos de 5.000 registros al VPS (sin pausas de sondeo ni sleeps de polling) hasta que el remanente sea $< 5.000$.
- **Veda Nocturna (Lunes a Sábado vs. Domingos)**: De lunes a sábado rige la veda nocturna de 21:00 a 00:00 hs (se detiene la subida y los registros se conservan en disco local). Los **domingos NO rige la veda** (subida continua 24 horas del día).
- **Auto-Sanitizador Atómico de Formatos Inválidos**: En el arranque (Boot) y en cada evaluación de pull/sweeper, el scheduler purga en bloque directamente en el VPS cualquier registro pendiente incompatible con el scraper (ej. Datuar con DNI nulo o CUIT corporativo 30/33/34), marcándolo como completado sin coincidencia sin descargarlo a SQLite ni consumir workers.


---

## 7. Control de Calidad Operativa: Umbral Mínimo de Efectividad (`--min-success-rate`)

Para certificar que clústeres de alta concurrencia (como 20 workers simultáneos de Datuar sobre el Pool Tor) no degraden su rendimiento por bloqueos o fallos transitorios desapercibidos, [`SupervisorIndustrial`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/runtime/supervisor.py#L31) incorpora el parámetro de control de calidad:

```python
min_success_rate: float = 95.0
```

### Monitoreo en Tablero Industrial IPC
El centinela de telemetría evalúa continuamente el ratio de consultas finalizadas con éxito (`coincidencias + sin_coincidencias`) frente al total de consultas procesadas (incluyendo errores controlados o transitorios):

$$\text{Tasa de Efectividad} = \frac{\text{Coincidencias Positivas} + \text{Sin Coincidencias}}{\text{Total Consultas}} \times 100$$

En el tablero en tiempo real, el supervisor reporta el indicador con alerta visual dinámica:
- 🟢 **`[🟢 OK]`**: Tasa $\ge \text{min\_success\_rate}$ (ej. $\ge 95.0\%$).
- ⚠️ **`[⚠️ DEGRADADO]`**: Tasa $< \text{min\_success\_rate}$, advirtiendo anomalías en proxies o rotación de IPs antes de causar fallas masivas.

### Asignación Determinista de Puertos Tor por Worker Slot
Cuando se activa el flag `--tor`, el supervisor calcula de forma matemática e inyecta explícitamente el puerto SOCKS y puerto de Control en los argumentos de cada worker (`tor_port` y `tor_control_port`), garantizando un particionamiento balanceado (máximo 3 workers por puerto SOCKS) sin depender de socket probes concurrentes propensos a timeout:

$$\text{group\_idx} = \frac{\text{slot\_id} - 1}{3}$$
$$\text{socks\_port} = 9050 + (\text{group\_idx} \times 2)$$

### Demonio Dedicado de Alta Eficiencia: `demonio_datuar.bat`
Para la ejecución desatendida 24/7 de Datuar con 20 workers y garantía de efectividad $\ge 95\%$, se dispone del launcher:

```powershell
.\demonio_datuar.bat
```
El script configura el pool de 7 instancias de Tor, 20 workers en paralelo, staging local SQLite WAL con push al VPS y flag `--min-success-rate 95.0`.

---

## Enlaces Relacionados
* [08. Política de Horario Comercial Oficial](../02_dominio_y_casos_de_uso/politica_horario_comercial.md)
* [09. Casos de Uso: Sincronización Matutina y Push al VPS](../02_dominio_y_casos_de_uso/casos_uso_sincronizacion.md)
* [20. Ciclo de Vida del Worker](ciclo_de_vida_worker.md)
* [22. Circuit Breaker de Red](circuit_breaker_red.md)
* [27. Supervisor de Producción](../07_operacion_y_runbooks/supervisor_produccion.md)

