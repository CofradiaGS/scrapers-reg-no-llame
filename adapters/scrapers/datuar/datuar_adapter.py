# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Scraper Datuar (Argentina)
Implementa IScraperEnginePort para enriquecimiento de identidad (Nombre, Apellido, CUIL)
a partir del DNI obtenido en etapas previas (ej. IRIS Movistar).
Opera como paso no-terminal del pipeline con caché SQLite de alta velocidad a costo $0.
"""
import os
import re
import time
import json
import random
import hashlib
import logging
import uuid
import urllib.parse
from typing import Dict, Any, Optional, List, Tuple, Union
import requests
from requests.adapters import HTTPAdapter
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import config
from core.domain.entities import Linea, ScrapeResult, StatusScraping, Titular
from core.domain.exceptions import ScraperTransientError
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.network.tor_controller import TorController
from adapters.network.proxy_pool import ProxyPoolManager

logger = logging.getLogger("ScraperDatuar")


class DatuarAdapter(BaseScraperAdapter):
    """
    Adaptador de extracción y enriquecimiento sobre Datuar Argentina (https://datuar.com).
    Enriquece la línea con nombres, apellidos y CUIL del titular a partir de su DNI.
    """

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36"
    ]

    def __init__(
        self,
        base_url: str = "https://datuar.com",
        timeout: Optional[Union[int, float, Tuple[float, float]]] = None,
        delay_min: float = 0.0,
        delay_max: float = 0.0,
        use_tor: Optional[bool] = None,
        use_proxy_pool: Optional[bool] = None,
        tor_controller: Optional[TorController] = None,
        tor_external_daemon: bool = False,
        tor_rotate_every: Optional[int] = None,
        worker_slot: int = 1,
        cache_db_path: Optional[str] = None,
        tor_port: Optional[int] = None,
        prefer_name: bool = False,
        proxy: Optional[str] = None,
        **kwargs
    ):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.delay_min = delay_min
        self.delay_max = delay_max

        # Configuración de red: Tor (predeterminado) vs ProxyPool
        self.use_proxy_pool = use_proxy_pool if use_proxy_pool is not None else kwargs.get("proxy_pool", kwargs.get("use_proxy_pool", getattr(config, "PROXY_POOL_ENABLED", False)))
        if self.use_proxy_pool:
            self.use_tor = False
        else:
            self.use_tor = use_tor if use_tor is not None else kwargs.get("tor", getattr(config, "TOR_ENABLED", True))

        if self.use_tor:
            # En Tor, timeout predeterminado de 12s para TLS handshake salvo override explícito (ej. (4.0, 8.0) en supervisor)
            if timeout is not None:
                if isinstance(timeout, tuple):
                    self.timeout = timeout
                elif isinstance(timeout, (int, float)):
                    self.timeout = (min(float(timeout), 5.0), float(timeout))
                else:
                    self.timeout = timeout
            else:
                self.timeout = getattr(config, "DATUAR_TOR_TIMEOUT", (12.0, 15.0))
        elif isinstance(timeout, (int, float)):
            self.timeout: Tuple[float, float] = (min(float(timeout), 5.0), float(timeout))
        elif isinstance(timeout, tuple):
            self.timeout = timeout
        else:
            self.timeout = timeout or (5.0, 10.0)

        self.delay_min = delay_min
        self.delay_max = delay_max
        self.tor_controller = tor_controller
        self.tor_external_daemon = tor_external_daemon or kwargs.get("tor_external_daemon", False)
        # Reutilización óptima de circuitos sanos (hasta 30 consultas antes de rotar proactivamente)
        default_rotate = getattr(config, "DATUAR_TOR_ROTATE_EVERY", 30)
        self.tor_rotate_every = int(tor_rotate_every if (tor_rotate_every is not None and tor_rotate_every > 0) else default_rotate)
        self.worker_slot = worker_slot
        self.tor_port = tor_port
        self.prefer_name = prefer_name
        self.request_count = 0
        self.consecutive_success = 0
        self.circuit_id = 0
        self.socks_port = tor_port or TorController.resolve_ports_for_worker(self.worker_slot)[0]
        self.control_port = config.TOR_CONTROL_PORT_BASE
        self.proxy_pool_shared = kwargs.get("proxy_pool_shared", False) or (self.worker_slot is not None and self.worker_slot > 1)
        self.proxy_pool: Optional[ProxyPoolManager] = None

        self._session: Optional[requests.Session] = None
        self._proxy_session: Optional[requests.Session] = None
        self._proxy_url: Optional[str] = None

    @property
    def nombre(self) -> str:
        """Identificador único en el pipeline."""
        return "datuar"

    def _generar_proxy_aislado(self, reset: bool = False, attempt: int = 1) -> str:
        """
        Devuelve la URL del proxy SOCKS5h con credenciales de aislamiento de flujo determinista (IsolateSOCKSAuth).
        Mantiene el worker vinculado a su puerto Tor particionado (resolve_ports_for_worker),
        evitando colisiones y eliminando el hopping caótico entre instancias.
        Reutiliza el circuito caliente mientras sea válido; sólo incrementa circuit_id ante rotación explícita.
        """
        socks_base = getattr(self, "socks_port", None) or self.tor_port or TorController.resolve_ports_for_worker(self.worker_slot)[0]
        self.current_socks_port = socks_base
        slot = self.worker_slot or 1
        if reset or not hasattr(self, "_stream_id") or not self._stream_id:
            self.circuit_id = getattr(self, "circuit_id", 0) + 1
            self._stream_id = f"w{slot}_r{self.circuit_id}"
        return f"socks5h://{self._stream_id}:tor@127.0.0.1:{socks_base}"

    def _rotar_circuito_instantaneo(self, attempt: int = 1, es_bloqueo: bool = False) -> None:
        """
        Rota de circuito en Tor de manera limpia y ultra-rápida.
        Bajo IsolateSOCKSAuth, generar una nueva credencial (ident) fuerza la asignación
        inmediata de un circuito independiente y limpio sin destruir sockets ni esperar NEWNYM.
        """
        if not self.use_tor:
            return

        self.consecutive_success = 0
        self._current_circuit_ip = None
        self._proxy_url = self._generar_proxy_aislado(reset=True, attempt=attempt)

        if es_bloqueo:
            try:
                from adapters.network.tor_controller import TorController
                TorController.rotate_for_worker(worker_slot=self.worker_slot, force_new_circuit=True)
            except Exception:
                pass

        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=0)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        self._session.proxies = {
            "http": self._proxy_url,
            "https": self._proxy_url
        }
        logger.debug(f"Datuar Slot {self.worker_slot} rotó a circuito {self._stream_id} en proxy {self._proxy_url} (bloqueo={es_bloqueo})...")

    def iniciar(self) -> None:
        """Inicializa la sesión HTTP y el enrutamiento de red por ProxyPool o Tor."""
        if self.use_proxy_pool:
            self.proxy_pool = ProxyPoolManager.get_instance(
                test_target=f"{self.base_url}/",
                allow_feeder=not self.proxy_pool_shared
            )
            if not self.proxy_pool_shared:
                self.proxy_pool.bootstrap(min_proxies=2, max_wait_sec=15)
                self.proxy_pool.start_background_feeder()
            logger.info(f"DatuarAdapter enrutando mediante ProxyPoolManager ({self.proxy_pool.size()} proxies vivos).")
            if self._proxy_session is None:
                self._proxy_session = requests.Session()
                adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
                self._proxy_session.mount("http://", adapter)
                self._proxy_session.mount("https://", adapter)
            return

        if self._session is not None:
            return

        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        if self.use_tor:
            socks_p, ctrl_p = TorController.resolve_ports_for_worker(self.worker_slot)
            self.socks_port = self.tor_port or socks_p
            self.control_port = ctrl_p

            if not self.tor_controller:
                self.tor_controller = TorController(
                    data_dir=os.path.join(os.getcwd(), "tor_data", f"port_{self.socks_port}"),
                    socks_port=self.socks_port,
                    control_port=self.control_port,
                    tor_path=getattr(config, "TOR_PATH", "tor.exe"),
                    is_owner=(not self.tor_external_daemon)
                )
            if not self.tor_external_daemon:
                self.tor_controller.ensure_running(timeout_sec=60)
            elif not self.tor_controller.is_running():
                logger.warning(f"Tor daemon externo en puerto {self.socks_port} aún no responde. Esperando...")
                self.tor_controller.ensure_running(timeout_sec=15)

            self._proxy_url = self._generar_proxy_aislado()
            self._session.proxies = {
                "http": self._proxy_url,
                "https": self._proxy_url
            }
            logger.debug(f"DatuarAdapter enrutando por Tor Stream Isolation puerto {self.socks_port} (Slot {self.worker_slot}).")

    def autenticar(self) -> bool:
        if self.use_proxy_pool:
            if self._proxy_session is None:
                self.iniciar()
            return True
        if self._session is None:
            self.iniciar()
        return self._session is not None

    def cerrar(self) -> None:
        """Cierra la sesión HTTP."""
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

        if self._proxy_session is not None:
            try:
                self._proxy_session.close()
            except Exception:
                pass
            self._proxy_session = None

        if self.use_tor and self.tor_controller and not self.tor_external_daemon:
            self.tor_controller.stop()

    def verificar_salud(self) -> bool:
        """Health-check para el Circuit Breaker. Comprueba conectividad básica."""
        if self.use_proxy_pool and self.proxy_pool:
            return self.proxy_pool.size() > 0
        if self._session is None:
            self.iniciar()
        try:
            r = self._session.get(f"{self.base_url}/", headers=self._get_headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def _get_headers(self) -> Dict[str, str]:
        ua = random.choice(self.USER_AGENTS)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Referer": f"{self.base_url}/",
            "Connection": "keep-alive"
        }

    def _obtener_ip_salida_actual(self) -> Optional[str]:
        """Obtiene la IP pública de salida del circuito actual para control de lista de descanso."""
        if getattr(self, "_current_circuit_ip", None):
            return self._current_circuit_ip
        if not self._session:
            return None
        endpoints = [
            "https://api.ipify.org",
            "https://icanhazip.com",
            "https://checkip.amazonaws.com"
        ]
        for ep in endpoints:
            try:
                r = self._session.get(ep, timeout=1.5)
                if r.status_code == 200:
                    text_ip = r.text.strip()
                    parts = text_ip.split(".")
                    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                        self._current_circuit_ip = text_ip
                        return text_ip
            except Exception:
                continue
        return None

    def _parse_personas_html(self, html: str, query: str) -> Tuple[List[Dict[str, Any]], int]:
        """Extrae la lista completa de personas y el total reportado por Datuar."""
        names = re.findall(r'data-nombre-completo="([^"]+)"', html)
        first_names = re.findall(r'data-nombre="([^"]+)"', html)
        cdus = re.findall(r'data-cdu="([^"]+)"', html)
        edades = re.findall(r'data-edad="([^"]+)"', html)
        generos = re.findall(r'data-genero="([^"]+)"', html)
        provincias = re.findall(r'data-provincia="([^"]+)"', html)
        ciudades = re.findall(r'data-ciudad="([^"]+)"', html)
        municipios = re.findall(r'data-municipio="([^"]+)"', html)

        # Contador de resultados (admite separadores de miles con punto o coma)
        m_cnt = re.search(r'id="contador-resultados">\s*([0-9.,]+)\s*<', html)
        total_cnt = 0
        if m_cnt:
            raw_cnt = m_cnt.group(1).replace(".", "").replace(",", "").strip()
            total_cnt = int(raw_cnt) if raw_cnt.isdigit() else 0

        personas = []
        if names:
            for i in range(len(names)):
                raw_name = names[i].strip()
                cuil = cdus[i].strip() if i < len(cdus) else None
                edad_raw = edades[i].strip() if i < len(edades) else None
                gen_raw = generos[i].strip().lower() if i < len(generos) else ""
                provincia = provincias[i].strip().title() if i < len(provincias) else None
                ciudad = ciudades[i].strip().title() if i < len(ciudades) else None
                municipio = municipios[i].strip().title() if i < len(municipios) else None

                genero = ""
                if gen_raw in ("m", "masculino"):
                    genero = "Masculino"
                elif gen_raw in ("f", "femenino"):
                    genero = "Femenino"

                edad = int(edad_raw) if edad_raw and edad_raw.isdigit() else None

                # Extraer DNI de 7 u 8 dígitos desde el CUIL (caracteres 2 a 10 de 11 dígitos)
                dni_person = ""
                if cuil and len(cuil) == 11 and cuil.isdigit():
                    dni_person = cuil[2:-1]
                elif query.isdigit():
                    dni_person = query

                parts = [p.strip() for p in raw_name.split(",") if p.strip()]
                if len(parts) >= 2:
                    apellidos = parts[0].upper()
                    nombres = parts[1].title()
                elif i < len(first_names):
                    nombres = first_names[i].strip().title()
                    apellidos = raw_name.upper().replace(nombres.upper(), "").strip(" ,")
                else:
                    apellidos = raw_name.upper()
                    nombres = ""

                nombre_completo = f"{apellidos}, {nombres}".strip(", ")
                personas.append({
                    "nombre_completo": nombre_completo,
                    "nombres": nombres,
                    "apellidos": apellidos,
                    "cuil": cuil,
                    "dni": dni_person,
                    "edad": edad,
                    "genero": genero,
                    "provincia": provincia,
                    "ciudad": ciudad,
                    "municipio": municipio
                })

        return personas, total_cnt

    def _construir_resultado_coincidencia(
        self,
        linea: Linea,
        personas: List[Dict[str, Any]],
        total_cnt: int,
        termino_busqueda: str,
        es_busqueda_dni: bool,
        dni_limpio: str,
        origen: str = "datuar_live"
    ) -> ScrapeResult:
        p_primero = personas[0]
        titular = Titular(
            nombre=p_primero["nombres"],
            apellido=p_primero["apellidos"],
            nro_documento=p_primero["dni"] or (dni_limpio if es_busqueda_dni else ""),
            tipo_documento="DNI",
            cuil=p_primero["cuil"] or "",
            edad=str(p_primero["edad"]) if p_primero["edad"] is not None else "",
            genero=p_primero["genero"] or "",
            provincia=p_primero["provincia"] or "",
            ciudad=p_primero["ciudad"] or "",
            municipio=p_primero["municipio"] or ""
        )

        desc_partes = [f"Datuar - {p_primero['nombre_completo']}"]
        if p_primero["cuil"]:
            desc_partes.append(f"CUIL: {p_primero['cuil']}")
        if p_primero["edad"]:
            desc_partes.append(f"Edad: {p_primero['edad']} años")
        if p_primero["ciudad"] or p_primero["provincia"]:
            ub = f"{p_primero['ciudad'] or ''}, {p_primero['provincia'] or ''}".strip(", ")
            desc_partes.append(f"Ubicación: {ub}")
        if len(personas) > 1:
            desc_partes.append(f"Total coincidencias: {len(personas)}")
        descripcion_line = " | ".join(desc_partes)

        return ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper=self.nombre,
            titular=titular,
            detalles={
                "busqueda": termino_busqueda,
                "tipo_busqueda": "dni" if es_busqueda_dni else "nombre",
                "nombre_completo": p_primero["nombre_completo"],
                "nombres": p_primero["nombres"],
                "apellidos": p_primero["apellidos"],
                "cuil": p_primero["cuil"],
                "dni": p_primero["dni"] or (dni_limpio if es_busqueda_dni else ""),
                "edad": p_primero["edad"],
                "genero": p_primero["genero"],
                "provincia": p_primero["provincia"],
                "ciudad": p_primero["ciudad"],
                "municipio": p_primero["municipio"],
                "total_coincidencias": len(personas),
                "total_reportado": total_cnt or len(personas),
                "coincidencias": personas,
                "origen": origen
            },
            descripcion=descripcion_line
        )

    def _es_respuesta_bloqueo(self, status_code: int, html: str) -> bool:
        """Determina si la respuesta de Datuar corresponde a un bloqueo por límite o WAF."""
        if status_code in (429, 403, 503) or status_code != 200:
            return True
        html_l = html.lower()
        return (
            "superado el" in html_l
            or "búsquedas gratuitas" in html_l
            or "busquedas gratuitas" in html_l
            or "obligatorio completar" in html_l
            or "fa-shield-halved" in html_l
            or ("just a moment..." in html_l and "contador-resultados" not in html)
            or ("<title>attention required" in html_l and "contador-resultados" not in html)
        )

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Consulta Datuar a partir del DNI o del Nombre asociado a la línea.
        Soporta ProxyPoolManager (rotación continua) o Tor Multi-Instance (aislamiento de circuitos).
        Captura todas las coincidencias cuando existen múltiples resultados.
        """
        dni = (linea.dni or "").strip()
        dni_limpio = "".join(filter(str.isdigit, dni))
        if len(dni_limpio) == 11 and dni_limpio.startswith(("20", "27", "23", "24")):
            dni_limpio = dni_limpio[2:10].lstrip("0")
        nombre = getattr(linea, "nombre", None) or ""
        nombre_limpio = str(nombre).strip()

        es_cuit_empresa = len(dni_limpio) == 11 and dni_limpio.startswith(("30", "33", "34"))

        # Determinar término y modo de búsqueda (Nombre o DNI)
        if self.prefer_name and nombre_limpio and len(nombre_limpio) >= 3:
            termino_busqueda = nombre_limpio
            es_busqueda_dni = False
        elif es_cuit_empresa and nombre_limpio and len(nombre_limpio) >= 3:
            # Si el DNI es un CUIT de Persona Jurídica, priorizar búsqueda por Nombre de la persona física
            termino_busqueda = nombre_limpio
            es_busqueda_dni = False
        elif dni_limpio and len(dni_limpio) >= 6:
            termino_busqueda = dni_limpio
            es_busqueda_dni = True
        elif nombre_limpio and len(nombre_limpio) >= 3:
            termino_busqueda = nombre_limpio
            es_busqueda_dni = False
        else:
            logger.debug(f"Línea {linea.ani} sin DNI ni Nombre para consultar Datuar. Saltando fast-path.")
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion="Sin DNI ni Nombre disponible para consultar en Datuar",
                detalles={"motivo": "DNI/Nombre no provisto"}
            )

        url_busqueda = f"{self.base_url}/indext.php?busqueda={urllib.parse.quote(termino_busqueda)}"

        # -------------------------------------------------------------
        # MODALIDAD A: Enrutamiento mediante ProxyPoolManager (Fast / Anti-bloqueo)
        # Cada consulta sale por una IP pública diferente; jamás acumula las ~15 continuas de Datuar.
        # -------------------------------------------------------------
        if self.use_proxy_pool and self.proxy_pool:
            if self._proxy_session is None:
                self._proxy_session = requests.Session()
                adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
                self._proxy_session.mount("http://", adapter)
                self._proxy_session.mount("https://", adapter)

            max_confirmaciones = int(getattr(config, "DATUAR_SIN_COINCIDENCIA_CONFIRMACIONES", 3))
            max_retries = max(int(getattr(config, "PROXY_POOL_MAX_RETRIES", 6)), max_confirmaciones * 2)
            sin_coincidencia_cnt = 0
            last_err = None

            for attempt in range(max_retries):
                current_proxy = self.proxy_pool.get_proxy(timeout=3.0)
                if not current_proxy:
                    logger.warning("No hay proxies disponibles en ProxyPool para Datuar. Esperando recarga...")
                    time.sleep(0.8)
                    continue

                proxies_dict = {"http": current_proxy, "https": current_proxy}
                headers = self._get_headers()
                sess = self._proxy_session
                if sess is None:
                    break

                try:
                    resp = sess.get(
                        url_busqueda,
                        headers=headers,
                        proxies=proxies_dict,
                        timeout=getattr(config, "PROXY_POOL_TIMEOUT", 8.0),
                        verify=False
                    )
                    html = resp.text

                    if self._es_respuesta_bloqueo(resp.status_code, html):
                        logger.debug(f"⚠️ Proxy {current_proxy} con límite/bloqueo en Datuar. Rotando...")
                        if hasattr(self.proxy_pool, "report_block"):
                            self.proxy_pool.report_block(current_proxy)
                        else:
                            self.proxy_pool.report_failure(current_proxy)
                        continue

                    # Éxito: Proxy respondió limpio
                    self.proxy_pool.report_success(current_proxy)
                    personas, total_cnt = self._parse_personas_html(html, termino_busqueda)

                    if personas:
                        return self._construir_resultado_coincidencia(
                            linea, personas, total_cnt, termino_busqueda, es_busqueda_dni, dni_limpio, origen="datuar_proxypool"
                        )

                    # Si el contador es legítimamente 0 (sin bloqueo)
                    # Exigir 3 confirmaciones independientes antes de marcar SIN_COINCIDENCIA
                    if total_cnt == 0 and "contador-resultados" in html:
                        sin_coincidencia_cnt += 1
                        if sin_coincidencia_cnt < max_confirmaciones and attempt < max_retries - 1:
                            logger.debug(
                                f"ℹ️ ProxyPool sin coincidencias para '{termino_busqueda}' "
                                f"(confirmación {sin_coincidencia_cnt}/{max_confirmaciones}). Probando con siguiente proxy..."
                            )
                            continue

                        return ScrapeResult(
                            ani=linea.ani,
                            status=StatusScraping.SIN_COINCIDENCIA,
                            fuente_scraper=self.nombre,
                            titular=Titular(nro_documento=dni_limpio, tipo_documento="DNI") if dni_limpio else None,
                            detalles={
                                "motivo": "Sin registros en Datuar",
                                "busqueda": termino_busqueda,
                                "tipo_busqueda": "dni" if es_busqueda_dni else "nombre",
                                "confirmaciones_sin_coincidencia": sin_coincidencia_cnt
                            },
                            descripcion=f"Datuar - '{termino_busqueda}' sin registros (confirmado tras {sin_coincidencia_cnt} verificaciones)"
                        )

                    # Respuesta anómala o truncada
                    self.proxy_pool.report_failure(current_proxy)
                    continue

                except Exception as e:
                    self.proxy_pool.report_failure(current_proxy)
                    last_err = e
                    logger.debug(f"Falla proxy {current_proxy} en Datuar (intento {attempt+1}/{max_retries}): {e}")

            raise ScraperTransientError(f"ProxyPool agotó los {max_retries} reintentos en Datuar para {termino_busqueda}: {last_err}")

        # -------------------------------------------------------------
        # MODALIDAD B: Enrutamiento mediante Tor Multi-Instance o Conexión Directa
        # -------------------------------------------------------------
        if self._session is None:
            self.iniciar()

        max_confirmaciones = int(getattr(config, "DATUAR_SIN_COINCIDENCIA_CONFIRMACIONES", 3))
        max_intentos = max(8, max_confirmaciones * 2 + 2)
        sin_coincidencia_cnt = 0
        hubo_error_red = False
        hubo_bloqueo = False
        ultimo_error = ""

        for intento in range(1, max_intentos + 1):
            self.request_count += 1
            if self.use_tor and self.tor_rotate_every > 0 and self.consecutive_success >= self.tor_rotate_every:
                self._rotar_circuito_instantaneo(attempt=intento, es_bloqueo=False)

            if self.use_tor and getattr(self, "_current_circuit_ip", None):
                try:
                    from adapters.network.cooldown_manager import IPCooldownManager
                    if IPCooldownManager.get_instance().esta_en_descanso(self._current_circuit_ip):
                        self._rotar_circuito_instantaneo(attempt=intento, es_bloqueo=True)
                        time.sleep(random.uniform(0.2, 0.4))
                except Exception:
                    pass

            self.sleep_jitter(self.delay_min, self.delay_max)
            headers = self._get_headers()

            try:
                resp = self._session.get(url_busqueda, headers=headers, timeout=self.timeout)
                html = resp.text

                # 1. Si Datuar devolvió personas (coincidencia positiva), retornar DE INMEDIATO
                personas, total_cnt = self._parse_personas_html(html, termino_busqueda)
                if personas:
                    self.consecutive_success += 1
                    if self.use_tor and getattr(self, "_current_circuit_ip", None):
                        try:
                            from adapters.network.cooldown_manager import IPCooldownManager
                            IPCooldownManager.get_instance().reportar_exito(self._current_circuit_ip)
                        except Exception:
                            pass
                    return self._construir_resultado_coincidencia(
                        linea, personas, total_cnt, termino_busqueda, es_busqueda_dni, dni_limpio, origen="datuar_live"
                    )

                # 2. Si no hay personas, verificar si la respuesta es un bloqueo de tasa/WAF
                if self._es_respuesta_bloqueo(resp.status_code, html):
                    hubo_bloqueo = True
                    self.consecutive_success = 0
                    if self._session is not None:
                        self._session.cookies.clear()

                    # Identificar IP de salida y gestionar lista de descanso atómica en SQLite WAL
                    current_ip = self._obtener_ip_salida_actual()
                    if current_ip:
                        from adapters.network.cooldown_manager import IPCooldownManager
                        cd_mgr = IPCooldownManager.get_instance()
                        strikes, es_nuevo = cd_mgr.registrar_bloqueo(current_ip)
                        if es_nuevo:
                            logger.debug(f"💤 [LISTA DE DESCANSO] IP {current_ip} en descanso (15m tras 2 captchas).")
                        else:
                            logger.debug(f"⚠️ IP {current_ip} con captcha/límite (Strike {strikes}).")

                    if intento < max_intentos:
                        logger.debug(
                            f"⚠️ Datuar circuito con límite o HTTP {resp.status_code} para '{termino_busqueda}' "
                            f"(intento {intento}/{max_intentos}). Rotando a nuevo circuito..."
                        )
                        if self.use_tor:
                            self._rotar_circuito_instantaneo(attempt=intento + 1, es_bloqueo=True)
                            time.sleep(random.uniform(0.35, 0.6))
                    continue

                # 3. Si no es bloqueo y el contador es 0 confirmado:
                # Regla del usuario: Reintentar 3 veces con circuitos independientes antes de confirmar 'SIN_COINCIDENCIA'
                if total_cnt == 0 and "contador-resultados" in html:
                    sin_coincidencia_cnt += 1
                    if sin_coincidencia_cnt < max_confirmaciones and intento < max_intentos:
                        logger.debug(
                            f"ℹ️ Datuar sin coincidencias para '{termino_busqueda}' "
                            f"(confirmación {sin_coincidencia_cnt}/{max_confirmaciones}). "
                            f"Rotando circuito para verificación #{sin_coincidencia_cnt + 1}..."
                        )
                        if self.use_tor:
                            self._rotar_circuito_instantaneo(attempt=intento + 1, es_bloqueo=False)
                            time.sleep(random.uniform(0.1, 0.2))
                        continue

                    # Solo tras acumular las 3 confirmaciones de 0 resultados se marca SIN_COINCIDENCIA
                    self.consecutive_success += 1
                    if self.use_tor and getattr(self, "_current_circuit_ip", None):
                        try:
                            from adapters.network.cooldown_manager import IPCooldownManager
                            IPCooldownManager.get_instance().reportar_exito(self._current_circuit_ip)
                        except Exception:
                            pass
                    return ScrapeResult(
                        ani=linea.ani,
                        status=StatusScraping.SIN_COINCIDENCIA,
                        fuente_scraper=self.nombre,
                        titular=Titular(nro_documento=dni_limpio, tipo_documento="DNI") if dni_limpio else None,
                        detalles={
                            "motivo": "Sin registros en Datuar",
                            "busqueda": termino_busqueda,
                            "tipo_busqueda": "dni" if es_busqueda_dni else "nombre",
                            "confirmaciones_sin_coincidencia": sin_coincidencia_cnt
                        },
                        descripcion=f"Datuar - '{termino_busqueda}' sin registros (confirmado tras {sin_coincidencia_cnt} verificaciones)"
                    )

                # Si no hubo personas y tampoco contador=0 (respuesta anómala o truncada)
                self.consecutive_success = 0
                if intento < max_intentos:
                    logger.debug(f"Datuar respuesta anómala para '{termino_busqueda}'. Rotando circuito...")
                    if self.use_tor:
                        self._rotar_circuito_instantaneo(attempt=intento + 1, es_bloqueo=False)
                    time.sleep(0.15)
                    continue

            except Exception as e:
                hubo_error_red = True
                ultimo_error = str(e)
                logger.debug(f"Aviso de red Datuar para '{termino_busqueda}' (intento {intento}/{max_intentos}): {e}")
                if self._session is not None:
                    self._session.cookies.clear()
                if intento < max_intentos and self.use_tor:
                    self._rotar_circuito_instantaneo(attempt=intento + 1, es_bloqueo=False)
                    time.sleep(random.uniform(0.15, 0.3))

        # -------------------------------------------------------------
        # FALLBACK RESILIENTE: Solo si Tor agotó sus circuitos Y ProxyPool está habilitado
        # -------------------------------------------------------------
        if (hubo_bloqueo or hubo_error_red) and self.use_tor and (self.use_proxy_pool or getattr(config, "PROXY_POOL_ENABLED", False)):
            try:
                ppm = ProxyPoolManager.get_instance(test_target=f"{self.base_url}/")
                if ppm.size() == 0 and not getattr(self, "_ppm_bootstrapped", False) and not self.proxy_pool_shared:
                    ppm.bootstrap(min_proxies=5, max_wait_sec=8)
                    self._ppm_bootstrapped = True
                if ppm.size() > 0:
                    logger.info(f"🛡️ [DATUAR FAILOVER] Tor bloqueado/caído para '{termino_busqueda}'. Resolviendo mediante ProxyPool auxiliar ({ppm.size()} proxies)...")
                    for p_att in range(5):
                        pxy = ppm.get_proxy(timeout=3.0)
                        if not pxy:
                            break
                        try:
                            proxies_dict = {"http": pxy, "https": pxy}
                            sess_p = self._proxy_session or requests.Session()
                            resp_p = sess_p.get(
                                url_busqueda,
                                headers=self._get_headers(),
                                proxies=proxies_dict,
                                timeout=getattr(config, "PROXY_POOL_TIMEOUT", 8.0),
                                verify=False
                            )
                            html_p = resp_p.text
                            if not self._es_respuesta_bloqueo(resp_p.status_code, html_p):
                                ppm.report_success(pxy)
                                personas_p, total_cnt_p = self._parse_personas_html(html_p, termino_busqueda)
                                if personas_p:
                                    return self._construir_resultado_coincidencia(
                                        linea, personas_p, total_cnt_p, termino_busqueda, es_busqueda_dni, dni_limpio, origen="datuar_proxypool_failover"
                                    )
                                if total_cnt_p == 0 and "contador-resultados" in html_p:
                                    sin_coincidencia_cnt += 1
                                    if sin_coincidencia_cnt < max_confirmaciones and p_att < 4:
                                        continue
                                    return ScrapeResult(
                                        ani=linea.ani,
                                        status=StatusScraping.SIN_COINCIDENCIA,
                                        fuente_scraper=self.nombre,
                                        titular=Titular(nro_documento=dni_limpio, tipo_documento="DNI") if dni_limpio else None,
                                        detalles={
                                            "motivo": "Sin registros en Datuar",
                                            "busqueda": termino_busqueda,
                                            "tipo_busqueda": "dni" if es_busqueda_dni else "nombre",
                                            "confirmaciones_sin_coincidencia": sin_coincidencia_cnt
                                        },
                                        descripcion=f"Datuar - '{termino_busqueda}' sin registros (confirmado tras {sin_coincidencia_cnt} verificaciones)"
                                    )
                            else:
                                ppm.report_failure(pxy)
                        except Exception:
                            ppm.report_failure(pxy)
            except Exception as e_failover:
                logger.debug(f"Aviso en failover ProxyPool para Datuar: {e_failover}")

        # Si aún tras agotar Tor y el pool auxiliar persiste el bloqueo, elevar excepción transitoria
        if hubo_bloqueo:
            raise ScraperTransientError(
                f"Datuar bloqueado por límite de búsquedas o verificación tras {max_intentos} intentos para '{termino_busqueda}'"
            )

        if hubo_error_red:
            raise ScraperTransientError(
                f"Datuar error de conexión tras {max_intentos} intentos para '{termino_busqueda}': {ultimo_error}"
            )

        return ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper=self.nombre,
            descripcion=f"Datuar - '{termino_busqueda}' sin registros tras {max_intentos} intentos",
            detalles={"motivo": "Sin registros confirmados tras reintentos", "busqueda": termino_busqueda}
        )

