# -*- coding: utf-8 -*-
"""
Adaptador de Red: Gestor Reactivo de Ráfaga de Proxies (Burn-till-Dead)
Especializado para ENACOM y servicios con límites estrictos de IP y alta volatilidad.

Mecánica:
1. Harvester en segundo plano: recolecta continuamente candidatos (prioridad Argentina / Cono Sur / Elite HTTPS),
   los valida en caliente con sondeos de 3.5s y puebla una cola en memoria (ready_queue).
2. Consumidor por agotamiento (Burn-till-Dead): el worker se 'ata' a un proxy y lo exprime sin techo
   hasta que:
   - Falle (timeout o desconexión) -> Se descarta al instante y se toma el siguiente en < 200ms.
   - ENACOM devuelva el límite diario (100 consultas) -> Se retira del pool del día.
"""

import os
import json
import time
import logging
import threading
import queue
from typing import Optional, Set, Tuple, List
import requests
import urllib3

urllib3.disable_warnings()

logger = logging.getLogger("BurstProxyManager")


class BurstProxyManager:
    """
    Administrador de proxies de ráfaga continua con estrategia de agotamiento hasta muerte.
    """
    _instance: Optional["BurstProxyManager"] = None
    _instance_lock = threading.Lock()

    SOURCES = [
        # Fuentes específicas de Argentina
        ("http", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=AR&ssl=all&anonymity=all"),
        ("socks4", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=AR"),
        ("socks5", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=AR"),
        ("geonode", "https://proxylist.geonode.com/api/proxy-list?country=AR&limit=500&page=1&sort_by=lastChecked&sort_type=desc"),
        ("geonode", "https://proxylist.geonode.com/api/proxy-list?country=AR&limit=500&page=2&sort_by=lastChecked&sort_type=desc"),
        ("http", "https://www.proxy-list.download/api/v1/get?type=http&country=AR"),
        ("https", "https://www.proxy-list.download/api/v1/get?type=https&country=AR"),
        ("socks4", "https://www.proxy-list.download/api/v1/get?type=socks4&country=AR"),
        ("socks5", "https://www.proxy-list.download/api/v1/get?type=socks5&country=AR"),
        ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/http.txt"),
        ("socks4", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/socks4.txt"),
        ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR/socks5.txt"),
        ("all", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_geolocation/AR.txt"),
        ("http", "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/country/AR.txt"),
        ("http", "https://raw.githubusercontent.com/casals-ar/proxy-list/main/ar.txt"),
        ("http", "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/ar.txt"),
        ("http", "https://raw.githubusercontent.com/prx-chk/proxy-list/main/countries/ar.txt"),
        ("http", "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/countries/AR.txt"),
        ("http", "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/ar.txt"),
    ]

    TARGET_URL = "https://numeracion.enacom.gob.ar/Numeracion.aspx"

    def __init__(self, check_interval: int = 180, probe_timeout: float = 6.0):
        self.check_interval = check_interval
        self.probe_timeout = probe_timeout
        
        self.ready_queue: queue.Queue[str] = queue.Queue()
        self._dead_proxies: Set[str] = set()
        self._daily_blocked_proxies: Set[str] = set()
        self._cooldown_proxies: dict[str, float] = {}
        self._in_queue: Set[str] = set()
        self._tested_recently: Set[str] = set()
        self._static_proxy: Optional[str] = None
        self._lock = threading.Lock()
        
        self._is_running = False
        self._harvester_thread: Optional[threading.Thread] = None
        
        # Estadísticas de rendimiento
        self.stats = {
            "total_harvested": 0,
            "total_validated": 0,
            "queries_executed": 0,
            "proxies_exhausted": 0,
            "daily_limits": 0,
            "minute_limits": 0,
        }
        
        self._blacklist_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data",
            "daily_blocked_proxies.json"
        )
        self._load_daily_blacklist()

    def _load_daily_blacklist(self) -> None:
        """Carga la lista de proxies vetados hoy para evitar reintentar IPs bloqueadas tras reinicios."""
        today_str = time.strftime("%Y-%m-%d")
        try:
            if os.path.exists(self._blacklist_file):
                with open(self._blacklist_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today_str:
                        blocked = data.get("blocked_proxies", [])
                        self._daily_blocked_proxies.update(blocked)
                        self._dead_proxies.update(blocked)
                        logger.info(f"Cargados {len(blocked)} proxies vetados para hoy desde {self._blacklist_file}")
        except Exception as e:
            logger.debug(f"No se pudo cargar blacklist diario previo: {e}")

    def _save_daily_blacklist(self) -> None:
        """Persiste en disco los proxies bloqueados por límite de 100 del día."""
        today_str = time.strftime("%Y-%m-%d")
        try:
            os.makedirs(os.path.dirname(self._blacklist_file), exist_ok=True)
            with open(self._blacklist_file, "w", encoding="utf-8") as f:
                json.dump({
                    "date": today_str,
                    "blocked_proxies": sorted(list(self._daily_blocked_proxies))
                }, f, indent=2)
        except Exception:
            pass

    @classmethod
    def get_instance(cls, **kwargs) -> "BurstProxyManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    def set_static_proxy(self, proxy: str) -> None:
        """Fija un proxy único y permanente para ser retornado a todos los workers."""
        p_clean = proxy.strip()
        if p_clean and not (p_clean.startswith("http://") or p_clean.startswith("https://") or p_clean.startswith("socks")):
            p_clean = f"http://{p_clean}"
        with self._lock:
            self._static_proxy = p_clean
        logger.info(f"Proxy estático configurado para todos los workers: {self._static_proxy}")

    def start(self) -> None:
        """Inicia el harvester continuo en segundo plano."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._harvester_thread = threading.Thread(
                target=self._harvester_loop,
                daemon=True,
                name="BurstProxyHarvester"
            )
            self._harvester_thread.start()
            logger.info("Harvester de proxies de ráfaga iniciado en segundo plano.")

    def stop(self) -> None:
        """Detiene el harvester."""
        self._is_running = False

    # Alias en español
    iniciar = start
    detener = stop

    def _enqueue_proxy(self, proxy_url: str) -> bool:
        """Encola un proxy asegurando unicidad, que no esté en cooldown y que no tenga límite diario ni esté muerto."""
        with self._lock:
            if proxy_url in self._daily_blocked_proxies:
                return False
            if proxy_url in self._dead_proxies and proxy_url not in self._cooldown_proxies:
                return False
            if proxy_url in self._cooldown_proxies and time.time() < self._cooldown_proxies[proxy_url]:
                return False
            if proxy_url in self._in_queue:
                return False
            self._in_queue.add(proxy_url)
            self.ready_queue.put(proxy_url)
            return True

    def get_blocked_proxies(self) -> Set[str]:
        """Retorna conjunto de proxies vetados por el resto del día."""
        with self._lock:
            return set(self._daily_blocked_proxies)

    def get_next_proxy(self, timeout: Optional[float] = 10.0, exclude: Optional[Set[str]] = None) -> Optional[str]:
        """
        Obtiene el siguiente proxy vivo validado de la cola.
        Soporta exclusión para evitar entregar a un worker un proxy que acaba de fallar para su consulta actual.
        Filtra proxies en límite diario, muertos o en cooldown.
        """
        if self._static_proxy:
            return self._static_proxy
            
        end_time = time.time() + (timeout if timeout is not None else 10.0)
        skipped: List[str] = []
        
        while time.time() < end_time:
            remaining = max(0.1, end_time - time.time())
            try:
                cand = self.ready_queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
                
            with self._lock:
                self._in_queue.discard(cand)
                
                # 1. Filtro estricto: Si está en límite diario o muerto, descartar
                if cand in self._daily_blocked_proxies:
                    continue
                if cand in self._dead_proxies and cand not in self._cooldown_proxies:
                    continue
                    
                # 2. Filtro cooldown: Si está en cooldown (5 consultas/min), no entregar aún
                if cand in self._cooldown_proxies:
                    if time.time() < self._cooldown_proxies[cand]:
                        skipped.append(cand)
                        continue
                    else:
                        del self._cooldown_proxies[cand]
                        
                # 3. Filtro exclusión por consumidor (ej: ya probado para este ANI)
                if exclude and cand in exclude:
                    skipped.append(cand)
                    continue
                    
                # Re-encolar los saltados temporalmente para otros workers
                for s in skipped:
                    self._enqueue_proxy(s)
                return cand
                
        # Re-encolar si no se encontró candidato apto en este ciclo
        for s in skipped:
            self._enqueue_proxy(s)
        return None

    obtener_proxy_vivo = get_next_proxy

    def load_custom_proxies(self, proxies: List[str]) -> int:
        """Carga una lista manual de proxies y los prioriza en la cola listos para usar."""
        c = 0
        for p in proxies:
            p_clean = p.strip()
            if p_clean and not p_clean.startswith("#"):
                if not (p_clean.startswith("http://") or p_clean.startswith("https://") or p_clean.startswith("socks")):
                    p_clean = f"http://{p_clean}"
                if self._enqueue_proxy(p_clean):
                    c += 1
        logger.info(f"Cargados {c} proxies personalizados prioritarios en la cola.")
        return c

    cargar_proxies = load_custom_proxies

    def report_daily_limit(self, proxy_url: str) -> None:
        """
        Registra un proxy que alcanzó el límite estricto de 100 consultas diarias en ENACOM.
        Queda vetado por el resto de la jornada y NUNCA se volverá a cosechar o encolar.
        """
        with self._lock:
            self._daily_blocked_proxies.add(proxy_url)
            self._dead_proxies.add(proxy_url)
            self._in_queue.discard(proxy_url)
            self.stats["daily_limits"] += 1
            self.stats["proxies_exhausted"] += 1
            self._save_daily_blacklist()
        logger.warning(
            f"🚫 [BLACK-LIST DIARIO 100 CONSULTAS] Proxy {proxy_url} bloqueado por el resto del día "
            f"(Total bloqueados hoy: {len(self._daily_blocked_proxies)})"
        )

    def report_minute_limit(self, proxy_url: str, cooldown_sec: float = 65.0) -> None:
        """
        Registra un proxy que alcanzó el límite de 5 consultas por minuto en ENACOM.
        Entra en cooldown temporal para volver al pool una vez transcurrido el tiempo.
        """
        with self._lock:
            self._cooldown_proxies[proxy_url] = time.time() + cooldown_sec
            self._in_queue.discard(proxy_url)
            self.stats["minute_limits"] += 1
        logger.warning(
            f"⏳ [COOLDOWN RATE LIMIT 5/MIN] Proxy {proxy_url} en pausa por {cooldown_sec:.0f}s "
            f"(Total en reposo: {len(self._cooldown_proxies)})"
        )

    def report_proxy_result(self, proxy_url: str, success: bool, reason: str = "") -> None:
        """
        Reporta el resultado de una consulta con el proxy activo.
        Distingue inteligentemente entre límite diario, límite por minuto y muerte de conexión.
        """
        if success:
            with self._lock:
                self.stats["queries_executed"] += 1
        else:
            r_lower = reason.lower()
            if "100" in r_lower or "diaria" in r_lower:
                self.report_daily_limit(proxy_url)
            elif "5" in r_lower or "minuto" in r_lower:
                self.report_minute_limit(proxy_url, cooldown_sec=65.0)
            else:
                with self._lock:
                    self._dead_proxies.add(proxy_url)
                    self._in_queue.discard(proxy_url)
                    self.stats["proxies_exhausted"] += 1
                logger.info(f"Proxy retirado [{reason}]: {proxy_url} (Total agotados: {self.stats['proxies_exhausted']})")

    def _fetch_candidates(self) -> List[Tuple[str, str]]:
        """Descarga candidatos frescos de las fuentes configuradas en paralelo."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        raw_candidates: List[Tuple[str, str]] = []

        def fetch_one(default_proto, url):
            items = []
            try:
                r = requests.get(url, timeout=5)
                if r.status_code == 200:
                    if default_proto == "geonode":
                        for item in r.json().get("data", []):
                            ip = item.get("ip")
                            port = item.get("port")
                            protos = item.get("protocols", ["http"])
                            for p in protos:
                                items.append((p.lower(), f"{ip}:{port}"))
                    else:
                        for line in r.text.splitlines():
                            clean = line.strip()
                            if ":" in clean and not clean.startswith("#"):
                                proto = default_proto
                                hp = clean
                                if "://" in clean:
                                    parts = clean.split("://")
                                    proto = parts[0]
                                    hp = parts[1]
                                items.append((proto, hp))
            except Exception:
                pass
            return items

        with ThreadPoolExecutor(max_workers=len(self.SOURCES)) as executor:
            futures = [executor.submit(fetch_one, proto, u) for proto, u in self.SOURCES]
            for f in as_completed(futures):
                raw_candidates.extend(f.result())
                
        # Filtrar los ya descartados, vetados hoy, en cooldown o ya en cola
        unique = []
        seen = set()
        with self._lock:
            for proto, hp in raw_candidates:
                p_url = f"{proto}://{hp}"
                if (
                    p_url not in self._dead_proxies and
                    p_url not in self._daily_blocked_proxies and
                    p_url not in self._cooldown_proxies and
                    p_url not in self._in_queue and
                    p_url not in seen
                ):
                    seen.add(p_url)
                    unique.append((proto, hp))
                    
        return unique

    def _probe_proxy(self, proto: str, hp: str) -> Optional[str]:
        """Sondeo ultrarrápido contra ENACOM para verificar si el proxy está listo."""
        p_url_client = f"{proto}://{hp}"
        with self._lock:
            if p_url_client in self._daily_blocked_proxies or p_url_client in self._dead_proxies:
                return None

        probe_proto = "socks5h" if proto == "socks5" else proto
        p_url_probe = f"{probe_proto}://{hp}"
        proxies = {"http": p_url_probe, "https": p_url_probe}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        
        try:
            r = requests.get(
                self.TARGET_URL,
                headers=headers,
                proxies=proxies,
                timeout=self.probe_timeout,
                verify=False
            )
            has_enacom = ("ENACOM" in r.text or "Numeración" in r.text or "ctl00" in r.text or "TxtNumero" in r.text)
            is_firewall = (r.status_code == 303 or "firewall.enacom.gob.ar" in r.text)
            if r.status_code == 200 and has_enacom and not is_firewall:
                return p_url_client
        except Exception:
            pass
            
        with self._lock:
            self._dead_proxies.add(p_url_client)
        return None

    def _harvester_loop(self) -> None:
        """Bucle permanente de cosecha y alimentación de la cola."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        while self._is_running:
            if self._static_proxy:
                time.sleep(5)
                continue

            # 1. Recuperar proxies que cumplieron su período de cooldown (5/min)
            ahora = time.time()
            listos_cooldown = []
            with self._lock:
                for p, exp_t in list(self._cooldown_proxies.items()):
                    if ahora >= exp_t:
                        del self._cooldown_proxies[p]
                        if p not in self._daily_blocked_proxies:
                            listos_cooldown.append(p)
            for p in listos_cooldown:
                if self._enqueue_proxy(p):
                    logger.info(f"♻️  [PROXY POST-COOLDOWN] Proxy reactivado a la cola: {p}")

            # 2. Si ya tenemos al menos 5 proxies en espera, pausar brevemente
            if self.ready_queue.qsize() >= 5:
                time.sleep(10)
                continue

            candidates = self._fetch_candidates()
            with self._lock:
                self.stats["total_harvested"] = len(candidates)
                
            if candidates:
                # Probar todos los candidatos concurrentemente
                with ThreadPoolExecutor(max_workers=30) as executor:
                    futures = [executor.submit(self._probe_proxy, proto, hp) for proto, hp in candidates]
                    for f in as_completed(futures):
                        p_ready = f.result()
                        if p_ready:
                            if self._enqueue_proxy(p_ready):
                                with self._lock:
                                    self.stats["total_validated"] += 1
                                logger.info(f">> [PROXY AR VÁLIDO EN COLA] {p_ready} (Disponibles en cola: {self.ready_queue.qsize()})")

            # 3. Limpiar memoria de descarte periódicamente para permitir reintentos de fallos de red transitorios,
            # PERO PRESERVANDO SIEMPRE los vetados del día (límite de 100)
            if len(self._dead_proxies) > 80:
                with self._lock:
                    self._dead_proxies.clear()
                    self._dead_proxies.update(self._daily_blocked_proxies)

            # Pausa breve entre ciclos
            wait_time = 10 if self.ready_queue.empty() else self.check_interval
            time.sleep(wait_time)

