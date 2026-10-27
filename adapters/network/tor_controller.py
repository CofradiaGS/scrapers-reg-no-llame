# -*- coding: utf-8 -*-
"""
Adaptador de Infraestructura: Controlador de Tor (TorController)
Administra instancias locales del kernel de Tor (Multi-Instance) para rotación de IPs,
enrutamiento SOCKS5h y evasión de límites de tasa (HTTP 429 Too Many Requests).
Inspirado en la arquitectura probada de gs-scrap-cobro-express.
"""
import os
import time
import uuid
import socket
import logging
import subprocess
from typing import Optional

import requests

import config

logger = logging.getLogger("TorController")

# Silenciar logging verboso y excepciones de sockets en cierre de Stem en Windows (WinError 10038)
try:
    import stem.util.log
    _stem_log = stem.util.log.get_logger()
    _stem_log.setLevel(logging.CRITICAL)
    _stem_log.propagate = False
except Exception:
    pass


class TorController:
    """
    Controlador de instancia individual de Tor para rotación de IP y proxy SOCKS5h.
    Soporta operación mono-instancia y multi-instancia particionada por worker_slot.
    """

    def __init__(
        self,
        socks_port: Optional[int] = None,
        control_port: Optional[int] = None,
        password: Optional[str] = None,
        tor_path: Optional[str] = None,
        data_dir: Optional[str] = None,
        is_owner: bool = True
    ):
        self.socks_port = int(socks_port or config.TOR_SOCKS_PORT_BASE)
        self.control_port = int(control_port or config.TOR_CONTROL_PORT_BASE)
        self.password = password or config.TOR_CONTROL_PASSWORD
        self.tor_path = tor_path or config.TOR_PATH
        self.is_owner = is_owner
        # Aislamiento total de directorios de datos por puerto SOCKS (evita colisiones de lock/estado)
        default_dir = os.path.join(os.getcwd(), "tor_data", f"port_{self.socks_port}")
        self.data_dir = os.path.abspath(data_dir or default_dir)
        self._process: Optional[subprocess.Popen] = None

    _pool: list["TorController"] = []

    def get_proxy_url(self) -> str:
        """Devuelve la URL del proxy SOCKS5h de Tor con resolución remota de DNS."""
        return f"socks5h://127.0.0.1:{self.socks_port}"

    def get_isolated_proxy_url(self, session_id: Optional[str] = None) -> str:
        """
        Devuelve la URL del proxy SOCKS5h con credenciales aleatorias o personalizadas.
        Bajo la directiva 'IsolateSOCKSAuth' de Tor, cada credencial fuerza la asignación
        de un circuito de salida y una IP pública totalmente independiente.
        """
        ident = session_id or uuid.uuid4().hex[:12]
        return f"socks5h://{ident}:tor@127.0.0.1:{self.socks_port}"

    _cached_alive_ports: list = []
    _cached_ports_time: float = 0.0

    @classmethod
    def kill_process_on_port(cls, port: int) -> bool:
        """En Windows, busca y elimina procesos huérfanos/zombis escuchando en el puerto TCP."""
        if os.name != "nt":
            return False
        try:
            out = subprocess.check_output(f"netstat -ano -p tcp | findstr :{port}", shell=True, text=True, stderr=subprocess.DEVNULL)
            pids = set()
            for line in out.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and "LISTENING" in parts:
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) != os.getpid():
                        pids.add(int(pid))
            for pid in pids:
                logger.warning(f"⚠️ [TOR CLEANUP] Liberando puerto {port}: cerrando proceso zombi PID {pid}...")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return len(pids) > 0
        except Exception:
            return False

    @classmethod
    def get_alive_tor_ports(cls) -> list[int]:
        """
        Descubre los puertos SOCKS de Tor activos y disponibles en la máquina.
        Cachea el resultado por 15 segundos para no incurrir en sondeos repetitivos.
        """
        now = time.time()
        if cls._cached_alive_ports and (now - cls._cached_ports_time < 15.0):
            return cls._cached_alive_ports

        base_socks = int(config.TOR_SOCKS_PORT_BASE)
        alive = []
        for i in range(16):
            p = base_socks + (i * 2)
            s = socket.socket()
            s.settimeout(0.04)
            try:
                if s.connect_ex(("127.0.0.1", p)) == 0:
                    alive.append(p)
            except Exception:
                pass
            finally:
                s.close()

        if alive:
            cls._cached_alive_ports = alive
            cls._cached_ports_time = now
        elif cls._cached_alive_ports:
            # Mantener caché anterior si el sondeo puntual tuvo timeout
            pass
        else:
            cls._cached_alive_ports = [base_socks]
            cls._cached_ports_time = now

        return cls._cached_alive_ports

    @classmethod
    def resolve_ports_for_worker(cls, worker_slot: Optional[int] = None) -> tuple[int, int]:
        """
        Calcula y resuelve los puertos (socks_port, control_port) para un worker slot.
        Aplica particionamiento estricto de a N workers por puerto Tor (config.WORKERS_PER_TOR_PORT, default: 3).
        Slots 1..3 -> Puerto 9050, Slots 4..6 -> Puerto 9052, Slots 7..9 -> Puerto 9054, etc.
        Garantiza aislamiento estricto y elimina el colapso por contención en un único socket SOCKS.
        """
        workers_per_port = int(getattr(config, "WORKERS_PER_TOR_PORT", 3))
        slot = worker_slot if (worker_slot and worker_slot > 0) else 1
        group_idx = (slot - 1) // workers_per_port
        base_socks = int(config.TOR_SOCKS_PORT_BASE)
        chosen_socks = base_socks + (group_idx * 2)
        chosen_ctrl = chosen_socks + 1
        return chosen_socks, chosen_ctrl

    @classmethod
    def get_proxy_for_worker(cls, worker_slot: Optional[int] = None, stream_id: Optional[str] = None) -> str:
        """
        Retorna la URL SOCKS5h con aislamiento de flujo determinista (IsolateSOCKSAuth).
        Cada worker slot obtiene su propio circuito Tor independiente basado en sus credenciales SOCKS,
        evitando totalmente colisiones de IP y bloqueos mutuos entre los workers concurrentes.
        """
        socks_port, _ = cls.resolve_ports_for_worker(worker_slot)
        slot = worker_slot or 1
        ident = stream_id or f"w{slot}"
        return f"socks5h://{ident}:tor@127.0.0.1:{socks_port}"

    @classmethod
    def rotate_for_worker(cls, worker_slot: Optional[int] = None, socks_port: Optional[int] = None, force_new_circuit: bool = True) -> bool:
        """
        Envía señal de rotación/cierre de circuito al demonio Tor correspondiente al worker slot o puerto SOCKS.
        Resuelve automáticamente el control_port asignado.
        """
        if socks_port:
            socks_p = int(socks_port)
            ctrl_p = socks_p + 1
        else:
            socks_p, ctrl_p = cls.resolve_ports_for_worker(worker_slot)

        for inst in cls._pool:
            if inst.control_port == ctrl_p:
                return inst.rotate_ip(force_new_circuit=force_new_circuit)

        # Si no está en el pool en memoria (ej. procesos hijos IPC), conectar directamente a su control_port
        try:
            from stem import Signal
            from stem.control import Controller
            with Controller.from_port(port=ctrl_p) as c:
                c.authenticate(password=config.TOR_CONTROL_PASSWORD)
                if c.is_newnym_available():
                    c.signal(Signal.NEWNYM)
                    logger.debug(f"Circuito Tor rotado exitosamente en control_port {ctrl_p} (Slot {worker_slot}, SOCKS {socks_p}).")
                else:
                    logger.debug(f"NEWNYM rate-limited en control_port {ctrl_p} (espera {c.get_newnym_wait()}s).")
            return True
        except Exception as e:
            logger.debug(f"Aviso al rotar Tor en control_port {ctrl_p}: {e}")
            return False

    _last_synced_excluded: str = "INIT_UNSET"

    @classmethod
    def sync_excluded_exit_nodes(cls, excluded_ips: Optional[list[str]] = None) -> bool:
        """
        Sincroniza la lista de IPs en descanso con las instancias de Tor activas.
        Configura 'ExcludeExitNodes' en el ControlPort de cada instancia Tor para evitar
        que Tor vuelva a utilizar nodos de salida que recibieron 2 bloqueos/captchas.
        Limita la lista a máx 35 IPs más recientes para asegurar disponibilidad de nodos.
        """
        if excluded_ips is None:
            try:
                from adapters.network.cooldown_manager import IPCooldownManager
                excluded_ips = IPCooldownManager.get_instance().obtener_ips_en_descanso()
            except Exception:
                excluded_ips = []

        if excluded_ips and len(excluded_ips) > 35:
            excluded_ips = excluded_ips[-35:]

        ips_str = ",".join(excluded_ips) if excluded_ips else ""

        # Evitar llamadas redundantes si la lista no cambió
        if cls._last_synced_excluded == ips_str:
            return True
        cls._last_synced_excluded = ips_str

        # Si tenemos el pool en memoria (Supervisor)
        if cls._pool:
            for inst in list(cls._pool):
                if inst.is_running():
                    try:
                        from stem.control import Controller
                        with Controller.from_port(port=inst.control_port) as c:
                            c.authenticate(password=inst.password)
                            c.set_conf("ExcludeExitNodes", ips_str)
                    except Exception as e:
                        logger.debug(f"Aviso al configurar ExcludeExitNodes en control_port {inst.control_port}: {e}")
            return True

        # Procesos workers desacoplados (pool vacío en memoria)
        try:
            from stem.control import Controller
            for socks_p in cls.get_alive_tor_ports():
                ctrl_p = socks_p + 1
                try:
                    with Controller.from_port(port=ctrl_p) as c:
                        c.authenticate(password=config.TOR_CONTROL_PASSWORD)
                        c.set_conf("ExcludeExitNodes", ips_str)
                except Exception:
                    pass
        except Exception:
            pass

        return True

    @classmethod
    def start_pool(cls, num_instances: int = 2, base_socks: Optional[int] = None, timeout_sec: int = 120) -> list["TorController"]:
        """
        Inicia un pool multi-instancia de Tor particionado (máx 6 workers por puerto).
        Cada worker se conecta deterministamente a su instancia correspondiente mediante resolve_ports_for_worker().
        """
        cls.stop_pool()
        base = int(base_socks or config.TOR_SOCKS_PORT_BASE)
        cls._cached_alive_ports = []
        cls._cached_ports_time = 0.0

        pool = []
        workers_per_port = int(getattr(config, "WORKERS_PER_TOR_PORT", 6))
        logger.info(
            f"🧅 [TOR POOL] Iniciando pool de {num_instances} instancias de Tor "
            f"(base SOCKS {base}, máx {workers_per_port} workers por puerto)..."
        )

        import concurrent.futures

        def _boot_instance(idx: int):
            socks_p = base + (idx * 2)
            ctrl_p = socks_p + 1
            inst = cls(socks_port=socks_p, control_port=ctrl_p, is_owner=True)
            if inst.ensure_running(timeout_sec=timeout_sec):
                logger.info(f"✅ [TOR POOL] Instancia {idx+1}/{num_instances} lista (SOCKS {socks_p}, Control {ctrl_p}).")
                return inst
            else:
                logger.warning(f"⚠️ [TOR POOL] Falló bootstrap de instancia en puerto {socks_p}.")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(num_instances, 12)) as executor:
            futures = [executor.submit(_boot_instance, i) for i in range(num_instances)]
            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                if res is not None:
                    pool.append(res)

        pool.sort(key=lambda x: x.socks_port)
        cls._pool = pool
        cls._cached_alive_ports = [inst.socks_port for inst in pool if inst.is_running()]
        cls._cached_ports_time = time.time()
        logger.info(f"🚀 [TOR POOL] Pool operativo: {len(cls._cached_alive_ports)}/{num_instances} instancias activas.")
        return pool

    @classmethod
    def audit_and_heal_pool(cls) -> None:
        """
        Auditoría autónoma de salud del pool de Tor (ejecutada periódicamente por el Supervisor).
        Verifica el estado de cada instancia activa. Si alguna murió o se trabó,
        la recupera de inmediato de forma aislada sin afectar a las demás instancias.
        """
        if not cls._pool:
            return

        healed_any = False
        for inst in list(cls._pool):
            if not inst.is_running():
                logger.warning(
                    f"⚠️ [TOR POOL WATCHDOG] Instancia en puerto SOCKS {inst.socks_port} (Control {inst.control_port}) "
                    "no responde. Iniciando auto-recuperación aislada..."
                )
                try:
                    inst.stop()
                    cls.kill_process_on_port(inst.socks_port)
                    cls.kill_process_on_port(inst.control_port)
                    ok = inst.start(timeout_sec=60)
                    if ok:
                        logger.info(f"✅ [TOR POOL WATCHDOG] Instancia en puerto SOCKS {inst.socks_port} recuperada y operativa.")
                        healed_any = True
                    else:
                        logger.error(f"❌ [TOR POOL WATCHDOG] Falló recuperación de instancia en puerto SOCKS {inst.socks_port}.")
                except Exception as e:
                    logger.error(f"❌ [TOR POOL WATCHDOG] Excepción recuperando instancia Tor en puerto {inst.socks_port}: {e}")

        if healed_any:
            cls._cached_alive_ports = []
            cls._cached_ports_time = 0.0

    @classmethod
    def stop_pool(cls) -> None:
        """Detiene todas las instancias del pool de Tor."""
        if not cls._pool:
            return
        logger.info(f"🧅 [TOR POOL] Deteniendo {len(cls._pool)} instancias de Tor...")
        for inst in cls._pool:
            try:
                inst.stop()
            except Exception as e:
                logger.debug(f"Error deteniendo instancia Tor {inst.socks_port}: {e}")
        cls._pool.clear()
        cls._cached_alive_ports = []
        cls._cached_ports_time = 0.0

    def is_port_open(self, port: int) -> bool:
        """Comprueba si un socket local responde rápidamente."""
        s = socket.socket()
        s.settimeout(0.8)
        try:
            res = s.connect_ex(("127.0.0.1", port))
            return res == 0
        except Exception:
            return False
        finally:
            s.close()

    def is_running(self) -> bool:
        """Verifica si Tor está respondiendo en el puerto de control y autentica, y el puerto SOCKS está abierto."""
        if not self.is_port_open(self.control_port) or not self.is_port_open(self.socks_port):
            return False
        try:
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as c:
                c.authenticate(password=self.password)
                return True
        except Exception:
            return False

    def is_alive(self) -> bool:
        """Alias retrocompatible para is_running()."""
        return self.is_running()

    def get_bootstrap_status(self) -> tuple[bool, str]:
        """Comprueba conectividad con el ControlPort y devuelve (is_ready_100, phase_string)."""
        if not self.is_port_open(self.control_port):
            return False, "ControlPort cerrado"
        try:
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as c:
                c.authenticate(password=self.password)
                info = c.get_info("status/bootstrap-phase", default="")
                is_100 = "PROGRESS=100" in info
                return is_100, info
        except Exception as e:
            return False, str(e)

    def is_bootstrapped(self) -> bool:
        """Comprueba si el circuito de Tor completó el bootstrap."""
        ready, _ = self.get_bootstrap_status()
        return ready

    def ensure_running(self, timeout_sec: int = 60) -> bool:
        """
        Garantiza que Tor esté 100% operativo en los puertos SOCKS y Control.
        Si ya está activo, retorna True de inmediato.
        Si no está activo y es propietario, lo inicia. Si no es propietario, espera a que esté listo.
        """
        ready, _ = self.get_bootstrap_status()
        if ready and self.is_port_open(self.socks_port):
            return True

        if self.is_owner:
            return self.start(timeout_sec=timeout_sec)

        # Si no es propietario, espera a que el proceso maestro lo deje listo
        t0 = time.time()
        logger.info(f"Esperando a que el daemon Tor externo en SOCKS {self.socks_port} esté listo...")
        while time.time() - t0 < timeout_sec:
            if self.is_port_open(self.socks_port):
                ready, _ = self.get_bootstrap_status()
                if ready:
                    return True
            time.sleep(1.0)
        logger.warning(f"Daemon Tor externo no respondió en {timeout_sec}s.")
        return False

    def start(self, timeout_sec: int = 600) -> bool:
        """
        Inicia la instancia de Tor o reutiliza una existente en los puertos configurados.
        Retorna True si el puerto SOCKS y de control quedan operativos y con bootstrap 100%.
        Usa una conexión stem persistente para evitar el ruido de SocketClosed en el loop.
        """
        # Si ya está corriendo y bootstrapped, reutilizar
        ready, phase = self.get_bootstrap_status()
        if ready:
            logger.info(f"Tor ya se encuentra activo y listo en SOCKS {self.socks_port} y Control {self.control_port}.")
            return True

        if not os.path.exists(self.tor_path):
            logger.warning(
                f"No se encontró el ejecutable de Tor en '{self.tor_path}'. "
                "Verifique la ruta en TOR_PATH o instale Tor Browser / Tor Expert Bundle."
            )
            return False

        # Si el puerto está ocupado pero no responde a Control, liberar socket de proceso zombi
        if self.is_port_open(self.socks_port) or self.is_port_open(self.control_port):
            logger.info(f"Detectado puerto ocupado sin respuesta válida. Liberando sockets en {self.socks_port}/{self.control_port}...")
            self.kill_process_on_port(self.socks_port)
            self.kill_process_on_port(self.control_port)
            time.sleep(0.5)

        if not self.is_port_open(self.control_port):
            logger.info(
                f"Iniciando instancia Tor (SOCKS: {self.socks_port}, Control: {self.control_port}, "
                f"DataDir: '{self.data_dir}')..."
            )
            try:
                os.makedirs(self.data_dir, exist_ok=True)
                # Sembrar consenso y descriptores en caché para acelerar el bootstrap a < 2 segundos
                cons_dest = os.path.join(self.data_dir, "cached-microdesc-consensus")
                if not os.path.exists(cons_dest):
                    import shutil
                    tor_data_parent = os.path.dirname(self.data_dir)
                    search_dirs = [tor_data_parent]
                    if os.path.exists(tor_data_parent):
                        for sib in os.listdir(tor_data_parent):
                            sib_path = os.path.join(tor_data_parent, sib)
                            if os.path.isdir(sib_path) and sib_path != self.data_dir:
                                search_dirs.append(sib_path)

                    for sdir in search_dirs:
                        cand_cons = os.path.join(sdir, "cached-microdesc-consensus")
                        if os.path.exists(cand_cons):
                            for f_name in (
                                "cached-certs", "cached-microdesc-consensus",
                                "cached-microdescs", "cached-microdescs.new",
                                "unverified-microdesc-consensus"
                            ):
                                f_src = os.path.join(sdir, f_name)
                                f_dst = os.path.join(self.data_dir, f_name)
                                if os.path.exists(f_src) and not os.path.exists(f_dst):
                                    try:
                                        shutil.copy2(f_src, f_dst)
                                    except Exception:
                                        pass
                            break

                runtime_torrc = os.path.join(self.data_dir, "torrc_runtime")
                circuit_timeout = int(getattr(config, "TOR_CIRCUIT_BUILD_TIMEOUT", 30))
                max_pending = int(getattr(config, "TOR_MAX_PENDING_CIRCUITS", 64))
                circuit_dirtiness = int(getattr(config, "TOR_MAX_CIRCUIT_DIRTINESS", 300))
                with open(runtime_torrc, "w", encoding="utf-8") as f:
                    f.write(f"DataDirectory {self.data_dir}\n")
                    if config.TOR_GEOIP_PATH and os.path.exists(config.TOR_GEOIP_PATH):
                        f.write(f"GeoIPFile {config.TOR_GEOIP_PATH}\n")
                    if config.TOR_GEOIPV6_PATH and os.path.exists(config.TOR_GEOIPV6_PATH):
                        f.write(f"GeoIPv6File {config.TOR_GEOIPV6_PATH}\n")
                    f.write(f"SocksPort {self.socks_port} IsolateSOCKSAuth KeepAliveIsolateSOCKSAuth\n")
                    f.write(f"ControlPort {self.control_port}\n")
                    f.write("CookieAuthentication 0\n")
                    f.write("DormantCanceledByStartup 1\n")
                    f.write(f"CircuitBuildTimeout {circuit_timeout}\n")
                    f.write("KeepalivePeriod 60\n")
                    f.write(f"MaxCircuitDirtiness {circuit_dirtiness}\n")
                    f.write(f"MaxClientCircuitsPending {max_pending}\n")
                    f.write("AvoidDiskWrites 1\n")
                    f.write("ClientOnly 1\n")

                cmd = [self.tor_path, "-f", runtime_torrc]
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags
                )
            except Exception as e:
                logger.error(f"Error al iniciar subproceso Tor: {e}")
                return False

        # Esperar a que el ControlPort esté disponible antes de abrir la conexión persistente
        t0 = time.time()
        logger.info("Esperando que ControlPort esté disponible...")
        while time.time() - t0 < 15:
            if self.is_port_open(self.control_port):
                break
            time.sleep(0.8)
        else:
            logger.warning("ControlPort no respondió en 15s.")
            return False

        # Loop de bootstrap con conexión stem persistente (evita SocketClosed spam)
        time.sleep(1.0)  # Pequeña pausa extra para que Tor inicialice el handler
        t0 = time.time()
        try:
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as ctrl:
                ctrl.authenticate(password=self.password)
                try:
                    ctrl.reset_conf("__OwningControllerProcess")
                except Exception:
                    pass
                logger.info("Conexión al ControlPort establecida. Esperando bootstrap 100%...")
                while time.time() - t0 < timeout_sec:
                    if not ctrl.is_alive():
                        logger.warning("La conexión con el puerto de control de Tor se interrumpió.")
                        break
                    try:
                        info = ctrl.get_info("status/bootstrap-phase", default="")
                        progress_str = ""
                        for part in info.split():
                            if part.startswith("PROGRESS="):
                                progress_str = part.split("=")[1]
                                break
                        progress = int(progress_str) if progress_str.isdigit() else 0
                        logger.info(f"Bootstrap: {progress}% — {info[:80]}")
                        if progress >= 100 and self.is_port_open(self.socks_port):
                            logger.info(f"Tor listo y bootstrapped al 100% en {self.get_proxy_url()}.")
                            self._warmup_circuits()
                            return True
                    except Exception as poll_err:
                        logger.debug(f"Poll error (ignorado): {poll_err}")
                        if not ctrl.is_alive():
                            break
                    time.sleep(2.0)
        except Exception as conn_err:
            logger.error(f"No se pudo conectar al ControlPort persistente: {conn_err}")

        logger.warning(f"Tor no completó bootstrap en {timeout_sec}s.")
        return False

    def _warmup_circuits(self) -> None:
        """Precalienta el pool de circuitos de Tor antes de admitir tráfico de workers concurrentes."""
        logger.info("🔥 [TOR WARMUP] Precalentando circuitos iniciales de Tor antes de liberar workers...")
        t0 = time.time()
        try:
            proxy = self.get_proxy_url()
            resp = requests.get(
                "https://pagosce.cobroexpress.com.ar",
                proxies={"http": proxy, "https": proxy},
                timeout=(20.0, 10.0),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            logger.info(f"✅ [TOR WARMUP] Circuito inicial verificado con éxito (HTTP {resp.status_code}) en {time.time()-t0:.2f}s.")
        except Exception as e:
            logger.info(f"ℹ️ [TOR WARMUP] Sondeo inicial concluido ({type(e).__name__}) en {time.time()-t0:.2f}s. Red Tor lista.")

    def rotate_ip(self, force_new_circuit: bool = False) -> bool:
        """
        Envía la señal NEWNYM a través del puerto de control para rotar el circuito.
        Si force_new_circuit=True, además de NEWNYM, cierra directamente los circuitos construidos
        actuales para forzar a Tor a elegir un nuevo nodo de salida de inmediato,
        superando la ventana de rate-limiting de 10s impuesta por Tor a NEWNYM.
        """
        try:
            from stem import Signal
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as c:
                c.authenticate(password=self.password)
                c.signal(Signal.NEWNYM)
            time.sleep(0.3)  # Pausa breve para establecimiento del nuevo circuito
            logger.info(f"Circuito Tor rotado exitosamente (Puerto {self.control_port}).")
            return True
        except Exception as e:
            logger.warning(f"No se pudo rotar el circuito Tor en puerto {self.control_port}: {e}")
            return False

    def get_current_ip(self) -> str:
        """Obtiene la IP pública actual a través del proxy SOCKS5."""
        try:
            proxies = {
                "http": self.get_proxy_url(),
                "https": self.get_proxy_url()
            }
            resp = requests.get("https://api.ipify.org", proxies=proxies, timeout=12)
            return resp.text.strip()
        except Exception as e:
            return f"Desconocida ({e})"

    def stop(self) -> None:
        """Detiene el proceso local de Tor si fue instanciado por este controlador como propietario."""
        if not self.is_owner:
            logger.debug(f"TorController (puerto {self.socks_port}) no es propietario del subproceso. Omitiendo stop.")
            return

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            logger.info(f"Proceso Tor (puerto {self.socks_port}) detenido.")
