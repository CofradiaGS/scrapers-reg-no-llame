# -*- coding: utf-8 -*-
"""
Adaptador de Red: Gestor de Pool de Proxies Públicos Rotativos (Alta Velocidad)
Provee una alternativa a Tor a costo $0 con latencias de 0.8s a 2.5s.
Descarga listas públicas (HTTP/SOCKS5), valida concurrentemente en segundo plano
y mantiene una cola en memoria con persistencia en caché local (live_proxies.txt).
"""
import os
import time
import random
import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Set, Tuple

import requests
import config

logger = logging.getLogger("ProxyPoolManager")


class ProxyPoolManager:
    """
    Administrador thread-safe de proxies públicos rotativos de alta velocidad.
    Implementa descarte reactivo inmediato, validación concurrente y recolección continua.
    """
    _instance: Optional["ProxyPoolManager"] = None
    _instance_lock = threading.Lock()

    # Fuentes públicas de proxies abiertas y de actualización frecuente
    PUBLIC_SOURCES = [
        # HTTP / HTTPS
        ("http", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
        ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
        ("http", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=2500&country=all&ssl=all&anonymity=all"),
        # SOCKS5
        ("socks5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
        ("socks5", "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"),
        ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
        ("socks5", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=2500&country=all&ssl=all&anonymity=all"),
    ]

    def __init__(
        self,
        cache_file: Optional[str] = None,
        timeout: Optional[float] = None,
        max_workers: Optional[int] = None,
        test_target: Optional[str] = None,
        refresh_interval: Optional[int] = None,
        allow_feeder: bool = True
    ):
        self.cache_file = cache_file or config.PROXY_POOL_CACHE_FILE
        self.timeout = timeout or config.PROXY_POOL_TIMEOUT
        self.max_workers = max_workers or config.PROXY_POOL_VALIDATION_WORKERS
        self.test_target = test_target or f"{config.COBRO_EXPRESS_URL.rstrip('/')}/"
        self.refresh_interval = refresh_interval or config.PROXY_POOL_REFRESH_INTERVAL
        self.allow_feeder = allow_feeder

        self._lock = threading.RLock()
        self._pool: deque[str] = deque()
        self._all_seen: Set[str] = set()
        self._is_refreshing = False
        self._feeder_started = False

        # Carga inicial desde caché si existe
        self._load_cache()

    @classmethod
    def get_instance(cls, **kwargs) -> "ProxyPoolManager":
        """Patrón Singleton thread-safe para compartir el pool entre componentes."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    def _load_cache(self) -> None:
        """Carga proxies previamente validados guardados en disco."""
        if not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            with self._lock:
                for p in lines:
                    if p not in self._all_seen:
                        self._pool.append(p)
                        self._all_seen.add(p)
            logger.info(f"Caché de proxies cargada: {len(self._pool)} proxies disponibles en '{self.cache_file}'.")
        except Exception as e:
            logger.warning(f"No se pudo cargar la caché de proxies: {e}")

    def _save_cache(self) -> None:
        """Persiste los proxies activos actualmente en disco con reemplazo atómico."""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with self._lock:
                proxies_to_save = list(self._pool)
            tmp_file = f"{self.cache_file}.tmp_{os.getpid()}_{int(time.time()*1000)}"
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(f"# Proxies validados activos ({time.strftime('%Y-%m-%d %H:%M:%S')})\n")
                for p in proxies_to_save:
                    f.write(f"{p}\n")
            os.replace(tmp_file, self.cache_file)
        except Exception as e:
            logger.debug(f"Error al guardar caché de proxies: {e}")

    def get_proxy(self) -> Optional[str]:
        """
        Retorna el siguiente proxy del pool rotándolo al final.
        Si el pool está vacío o muy bajo (< 3), dispara un refresco en segundo plano si allow_feeder está activo.
        """
        with self._lock:
            if not self._pool:
                # Si el pool está totalmente vacío, intentar recarga síncrona mínima solo si está permitido el feeder
                if not self._is_refreshing and self.allow_feeder:
                    self._is_refreshing = True
                    threading.Thread(target=self._refresh_worker, daemon=True).start()
                return None

            proxy = self._pool.popleft()
            self._pool.append(proxy)

            if len(self._pool) < 5 and not self._is_refreshing and self.allow_feeder:
                self._is_refreshing = True
                threading.Thread(target=self._refresh_worker, daemon=True).start()

            return proxy

    def report_failure(self, proxy_url: str) -> None:
        """Descarta de inmediato un proxy defectuoso, caído o lento."""
        with self._lock:
            if proxy_url in self._pool:
                self._pool.remove(proxy_url)
                logger.info(f"🗑️ Proxy descartado por falla/timeout: {proxy_url} (Restantes: {len(self._pool)})")
                self._save_cache()

    def report_success(self, proxy_url: str) -> None:
        """Confirma que el proxy respondió exitosamente manteniéndolo en la cola."""
        with self._lock:
            if proxy_url not in self._pool:
                self._pool.append(proxy_url)
                self._all_seen.add(proxy_url)

    def size(self) -> int:
        """Devuelve la cantidad de proxies activos disponibles."""
        with self._lock:
            return len(self._pool)

    def _test_candidate(self, protocol: str, raw_host: str) -> Tuple[bool, str, float]:
        """Evalúa un proxy candidato contra el target HTTP midiendo latencia."""
        proxy_url = f"{protocol}://{raw_host}"
        t0 = time.time()
        try:
            proxies = {"http": proxy_url, "https": proxy_url}
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
                "Accept": "*/*",
                "idprovincia": "1",
            }
            # Timeout estricto para garantizar proxies rápidos (< 2.5s)
            resp = requests.get(
                self.test_target,
                headers=headers,
                proxies=proxies,
                timeout=self.timeout
            )
            lat = round(time.time() - t0, 2)
            if resp.status_code in (200, 400, 404):  # Respondió el servidor destino
                return True, proxy_url, lat
        except Exception:
            pass
        return False, proxy_url, 0.0

    def _fetch_source_candidates(self) -> List[Tuple[str, str]]:
        """Descarga candidatos de las fuentes públicas en paralelo."""
        candidates: List[Tuple[str, str]] = []

        def fetch_one(proto: str, url: str) -> List[Tuple[str, str]]:
            items = []
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    for line in r.text.splitlines():
                        line = line.strip()
                        if ":" in line and not line.startswith("#"):
                            items.append((proto, line))
            except Exception:
                pass
            return items

        with ThreadPoolExecutor(max_workers=len(self.PUBLIC_SOURCES)) as ex:
            futures = [ex.submit(fetch_one, proto, url) for proto, url in self.PUBLIC_SOURCES]
            for f in as_completed(futures):
                try:
                    candidates.extend(f.result())
                except Exception:
                    pass

        # Mezclar aleatoriamente para evitar probar siempre los mismos
        random.shuffle(candidates)
        return candidates

    def _refresh_worker(self, target_healthy: int = 15) -> None:
        """Trabajador que busca, testea y alimenta el pool de proxies."""
        logger.info("Iniciando escaneo y validación de proxies públicos rápidos (<2.5s)...")
        t0 = time.time()
        candidates = self._fetch_source_candidates()
        if not candidates:
            logger.warning("No se pudieron descargar candidatos de proxies públicos.")
            self._is_refreshing = False
            return

        logger.info(f"Descargados {len(candidates)} candidatos. Validando concurrentemente...")
        valid_found = 0
        tested = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Tomar lotes de candidatos para no sobrecargar
            batch = candidates[:250]
            future_to_cand = {
                executor.submit(self._test_candidate, proto, host): (proto, host)
                for proto, host in batch
            }

            for future in as_completed(future_to_cand):
                tested += 1
                try:
                    is_ok, proxy_url, lat = future.result()
                    if is_ok:
                        valid_found += 1
                        with self._lock:
                            if proxy_url not in self._all_seen:
                                self._pool.append(proxy_url)
                                self._all_seen.add(proxy_url)
                        logger.info(f"  [+] Proxy rápido verificado: {proxy_url} ({lat}s) [Total vivos: {self.size()}]")
                        if self.size() >= target_healthy:
                            break
                except Exception:
                    pass

        self._save_cache()
        dt = round(time.time() - t0, 1)
        logger.info(f"Escaneo finalizado en {dt}s. Candidatos probados: {tested}, Nuevos añadidos: {valid_found}. Pool total: {self.size()}")
        self._is_refreshing = False

    def bootstrap(self, min_proxies: int = 3, max_wait_sec: int = 15) -> bool:
        """
        Garantiza que existan al menos `min_proxies` en el pool antes de continuar.
        Si ya existen en caché, retorna True de inmediato.
        """
        if self.size() >= min_proxies:
            logger.info(f"ProxyPool operativo de inmediato con {self.size()} proxies en memoria.")
            return True

        if not self.allow_feeder:
            # Los workers secundarios solo esperan a que el feeder maestro pueble la caché
            t_wait = time.time()
            while time.time() - t_wait < max_wait_sec:
                self._load_cache()
                if self.size() >= min_proxies:
                    return True
                time.sleep(1.0)
            return self.size() > 0

        logger.info(f"Pool con pocos proxies ({self.size()}). Ejecutando recarga rápida inicial...")
        t0 = time.time()
        self._refresh_worker(target_healthy=min_proxies + 5)

        t_wait = time.time()
        while time.time() - t_wait < max_wait_sec:
            if self.size() >= min_proxies:
                return True
            time.sleep(1.0)

        return self.size() > 0

    def start_background_feeder(self) -> None:
        """Inicia el hilo permanente de recolección para mantener el pool siempre vivo."""
        if not self.allow_feeder or self._feeder_started:
            return
        self._feeder_started = True

        def feeder_loop():
            while True:
                time.sleep(self.refresh_interval)
                try:
                    if not self._is_refreshing and self.size() < 20:
                        self._refresh_worker()
                except Exception as e:
                    logger.debug(f"Error en feeder de proxies: {e}")

        t = threading.Thread(target=feeder_loop, daemon=True, name="ProxyPoolFeeder")
        t.start()
        logger.info(f"Demonio ProxyPoolFeeder iniciado (Intervalo: {self.refresh_interval}s).")
