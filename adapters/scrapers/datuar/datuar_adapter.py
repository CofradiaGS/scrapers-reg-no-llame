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
from typing import Dict, Any, Optional
import requests
from requests.adapters import HTTPAdapter

import config
from core.domain.entities import Linea, ScrapeResult, StatusScraping, Titular
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.network.tor_controller import TorController

logger = logging.getLogger("ScraperDatuar")


class DatuarAdapter(BaseScraperAdapter):
    """
    Adaptador de extracción y enriquecimiento sobre Datuar Argentina (https://datuar.com).
    Enriquece la línea con nombres, apellidos y CUIL del titular a partir de su DNI.
    """

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/122.0.0.0 Safari/537.36"
    ]

    def __init__(
        self,
        base_url: str = "https://datuar.com",
        timeout: int = 15,
        delay_min: float = 0.2,
        delay_max: float = 0.6,
        use_tor: bool = False,
        tor_controller: Optional[TorController] = None,
        tor_external_daemon: bool = False,
        tor_rotate_every: int = 19,
        worker_slot: int = 1,
        cache_db_path: Optional[str] = None,
        **kwargs
    ):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.use_tor = use_tor
        self.tor_controller = tor_controller
        self.tor_external_daemon = tor_external_daemon
        self.tor_rotate_every = tor_rotate_every
        self.worker_slot = worker_slot
        self.request_count = 0

        self._session: Optional[requests.Session] = None
        self._proxy_url: Optional[str] = None

    @property
    def nombre(self) -> str:
        """Identificador único en el pipeline."""
        return "datuar"

    def _generar_proxy_aislado(self) -> str:
        token = hashlib.md5(f"{time.time()}_{random.random()}".encode()).hexdigest()[:8]
        user_auth = f"w{self.worker_slot}_{token}"
        socks_base = getattr(config, "TOR_SOCKS_PORT_BASE", 9050)
        return f"socks5h://{user_auth}:tor@127.0.0.1:{socks_base}"

    def _rotar_circuito_instantaneo(self) -> None:
        if not self.use_tor:
            return
        self._proxy_url = self._generar_proxy_aislado()
        if self._session is not None:
            self._session.proxies = {
                "http": self._proxy_url,
                "https": self._proxy_url
            }
        logger.debug(f"Datuar rotó instantáneamente circuito Tor: {self._proxy_url[:28]}...")

    def iniciar(self) -> None:
        """Inicializa la sesión HTTP y el enrutamiento de red."""
        if self._session is not None:
            return

        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=1)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        if self.use_tor:
            if not self.tor_controller:
                self.tor_controller = TorController(
                    data_dir=getattr(config, "TOR_DATA_DIR", "tor_data"),
                    socks_port=getattr(config, "TOR_SOCKS_PORT_BASE", 9050),
                    control_port=getattr(config, "TOR_CONTROL_PORT_BASE", 9051),
                    tor_path=getattr(config, "TOR_PATH", "tor.exe"),
                    is_owner=(not self.tor_external_daemon)
                )
            if not self.tor_external_daemon:
                self.tor_controller.ensure_running(timeout_sec=60)
            elif not self.tor_controller.is_alive():
                logger.warning("Tor daemon externo aún no responde. Esperando...")
                self.tor_controller.ensure_running(timeout_sec=15)

            self._proxy_url = self._generar_proxy_aislado()
            self._session.proxies = {
                "http": self._proxy_url,
                "https": self._proxy_url
            }
            logger.info(f"DatuarAdapter enrutando por Tor Stream Isolation (Slot {self.worker_slot}).")

        # Establecer cookies iniciales
        try:
            headers = self._get_headers()
            self._session.get(f"{self.base_url}/", headers=headers, timeout=self.timeout)
        except Exception as e:
            logger.debug(f"Aviso al inicializar cookies en Datuar: {e}")

    def autenticar(self) -> bool:
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

        if self.use_tor and self.tor_controller and not self.tor_external_daemon:
            self.tor_controller.stop()

    def verificar_salud(self) -> bool:
        """Health-check para el Circuit Breaker. Comprueba conectividad básica."""
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

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Consulta Datuar a partir del DNI asociado a la línea.
        Si no se cuenta con DNI, retorna SIN_COINCIDENCIA en fast-path (<1 ms).
        """
        dni = (linea.dni or "").strip()
        dni_limpio = "".join(filter(str.isdigit, dni))

        if not dni_limpio or len(dni_limpio) < 6:
            logger.debug(f"Línea {linea.ani} sin DNI para consultar Datuar. Saltando fast-path.")
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion="Sin DNI disponible para consultar en Datuar",
                detalles={"motivo": "DNI no provisto ni inferido"}
            )

        # Consulta en Red a Datuar
        if self._session is None:
            self.iniciar()

        self.request_count += 1
        if self.use_tor and self.tor_rotate_every > 0 and (self.request_count % self.tor_rotate_every == 0):
            self._rotar_circuito_instantaneo()

        self.sleep_jitter(self.delay_min, self.delay_max)

        url_busqueda = f"{self.base_url}/index2.php?busqueda={dni_limpio}"
        headers = self._get_headers()

        try:
            resp = self._session.get(url_busqueda, headers=headers, timeout=self.timeout)
            
            if resp.status_code == 429:
                logger.warning("⚠️ Datuar Rate Limit (HTTP 429). Rotando circuito...")
                if self.use_tor:
                    self._rotar_circuito_instantaneo()
                time.sleep(1.5)
                # Reintento único
                resp = self._session.get(url_busqueda, headers=headers, timeout=self.timeout)

            if resp.status_code != 200:
                logger.warning(f"Datuar retornó código HTTP {resp.status_code} para DNI {dni_limpio}")
                return ScrapeResult(
                    ani=linea.ani,
                    status=StatusScraping.SIN_COINCIDENCIA,
                    fuente_scraper=self.nombre,
                    descripcion=f"Datuar error HTTP {resp.status_code}",
                    detalles={"http_status": resp.status_code, "dni": dni_limpio}
                )

            html = resp.text
            names = re.findall(r'data-nombre-completo="([^"]+)"', html)
            first_names = re.findall(r'data-nombre="([^"]+)"', html)
            cdus = re.findall(r'data-cdu="([^"]+)"', html)
            edades = re.findall(r'data-edad="([^"]+)"', html)
            generos = re.findall(r'data-genero="([^"]+)"', html)
            provincias = re.findall(r'data-provincia="([^"]+)"', html)
            ciudades = re.findall(r'data-ciudad="([^"]+)"', html)
            municipios = re.findall(r'data-municipio="([^"]+)"', html)

            if names:
                raw_name = names[0].strip()
                cuil = cdus[0].strip() if cdus else None
                edad = edades[0].strip() if edades else None
                genero = generos[0].strip() if generos else None
                provincia = provincias[0].strip().title() if provincias else None
                ciudad = ciudades[0].strip().title() if ciudades else None
                municipio = municipios[0].strip().title() if municipios else None

                parts = [p.strip() for p in raw_name.split(",") if p.strip()]
                if len(parts) >= 2:
                    apellidos = parts[0].upper()
                    nombres = parts[1].title()
                elif first_names:
                    nombres = first_names[0].strip().title()
                    apellidos = raw_name.upper().replace(nombres.upper(), "").strip(" ,")
                else:
                    apellidos = raw_name.upper()
                    nombres = ""

                nombre_completo = f"{apellidos}, {nombres}".strip(", ")

                titular = Titular(
                    nombre=nombres,
                    apellido=apellidos,
                    nro_documento=dni_limpio,
                    tipo_documento="DNI",
                    cuil=cuil or "",
                    edad=edad or "",
                    genero=genero or "",
                    provincia=provincia or "",
                    ciudad=ciudad or "",
                    municipio=municipio or ""
                )

                desc_partes = [f"Datuar - {nombre_completo}"]
                if cuil:
                    desc_partes.append(f"CUIL: {cuil}")
                if edad:
                    desc_partes.append(f"Edad: {edad} años")
                if ciudad or provincia:
                    ub = f"{ciudad or ''}, {provincia or ''}".strip(", ")
                    desc_partes.append(f"Ubicación: {ub}")
                descripcion_line = " | ".join(desc_partes)

                return ScrapeResult(
                    ani=linea.ani,
                    status=StatusScraping.COINCIDENCIA,
                    fuente_scraper=self.nombre,
                    titular=titular,
                    detalles={
                        "nombre_completo": nombre_completo,
                        "nombres": nombres,
                        "apellidos": apellidos,
                        "cuil": cuil,
                        "dni": dni_limpio,
                        "edad": edad,
                        "genero": genero,
                        "provincia": provincia,
                        "ciudad": ciudad,
                        "municipio": municipio,
                        "origen": "datuar_live"
                    },
                    descripcion=descripcion_line
                )

            # No se encontraron nombres en Datuar
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                titular=Titular(nro_documento=dni_limpio, tipo_documento="DNI"),
                detalles={"motivo": "DNI no registrado en Datuar", "dni": dni_limpio},
                descripcion=f"Datuar - DNI {dni_limpio} sin registros"
            )

        except Exception as e:
            logger.error(f"Error al consultar Datuar para DNI {dni_limpio}: {e}")
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion=f"Fallo de conexión a Datuar: {str(e)[:80]}",
                detalles={"error": str(e), "dni": dni_limpio}
            )
