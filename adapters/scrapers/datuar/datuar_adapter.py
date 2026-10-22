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
from typing import Dict, Any, Optional, List, Tuple
import requests
from requests.adapters import HTTPAdapter

import config
from core.domain.entities import Linea, ScrapeResult, StatusScraping, Titular
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.network.tor_controller import TorController

logger = logging.getLogger("ScraperDatuar")


def detectar_puerto_tor() -> int:
    """Detecta automáticamente qué puerto local de Tor responde y tiene salida a Datuar."""
    for p in [9058, 9050, 9052, 9054, 9056]:
        try:
            r = requests.get(
                "https://datuar.com/indext.php?busqueda=30111222",
                proxies={"http": f"socks5h://127.0.0.1:{p}", "https": f"socks5h://127.0.0.1:{p}"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=2.5
            )
            if r.status_code == 200 and "contador-resultados" in r.text:
                return p
        except Exception:
            continue
    return 9050


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
        timeout: int = 15,
        delay_min: float = 0.2,
        delay_max: float = 0.6,
        use_tor: bool = False,
        tor_controller: Optional[TorController] = None,
        tor_external_daemon: bool = False,
        tor_rotate_every: int = 25,
        worker_slot: int = 1,
        cache_db_path: Optional[str] = None,
        tor_port: Optional[int] = None,
        prefer_name: bool = False,
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
        self.tor_port = tor_port
        self.prefer_name = prefer_name
        self.request_count = 0
        self.circuit_id = 0

        self._session: Optional[requests.Session] = None
        self._proxy_url: Optional[str] = None

    @property
    def nombre(self) -> str:
        """Identificador único en el pipeline."""
        return "datuar"

    def _generar_proxy_aislado(self, reset: bool = False) -> str:
        """Devuelve la URL del proxy SOCKS5h con credenciales de aislamiento de flujo (IsolateSOCKSAuth)."""
        socks_base = self.tor_port or TorController.resolve_ports_for_worker(self.worker_slot)[0]
        slot = self.worker_slot or 1
        if reset:
            self._stream_id = f"w{slot}_{uuid.uuid4().hex[:6]}"
        elif not getattr(self, "_stream_id", None):
            self._stream_id = f"w{slot}_{uuid.uuid4().hex[:6]}"
        return f"socks5h://{self._stream_id}:tor@127.0.0.1:{socks_base}"

    def _rotar_circuito_instantaneo(self) -> None:
        """Rota de IP instantáneamente mediante Stream Isolation en Tor sin colisiones."""
        if not self.use_tor:
            return
        self._proxy_url = self._generar_proxy_aislado(reset=True)
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = requests.Session()
            adapter = HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=1)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
            self._session.proxies = {
                "http": self._proxy_url,
                "https": self._proxy_url
            }
        logger.debug(f"Datuar rotó limpiamente circuito Tor: {self._proxy_url}...")

    def iniciar(self) -> None:
        """Inicializa la sesión HTTP y el enrutamiento de red."""
        if self._session is not None:
            return

        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=1)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        if self.use_tor:
            if not self.tor_port:
                self.tor_port = detectar_puerto_tor()

            if not self.tor_controller:
                self.tor_controller = TorController(
                    data_dir=getattr(config, "TOR_DATA_DIR", "tor_data"),
                    socks_port=self.tor_port or getattr(config, "TOR_SOCKS_PORT_BASE", 9050),
                    control_port=getattr(config, "TOR_CONTROL_PORT_BASE", 9051),
                    tor_path=getattr(config, "TOR_PATH", "tor.exe"),
                    is_owner=(not self.tor_external_daemon)
                )
            if not self.tor_external_daemon:
                self.tor_controller.ensure_running(timeout_sec=60)
            elif not self.tor_controller.is_running():
                logger.warning("Tor daemon externo aún no responde. Esperando...")
                self.tor_controller.ensure_running(timeout_sec=15)

            self._proxy_url = self._generar_proxy_aislado()
            self._session.proxies = {
                "http": self._proxy_url,
                "https": self._proxy_url
            }
            logger.debug(f"DatuarAdapter enrutando por Tor Stream Isolation puerto {self.tor_port} (Slot {self.worker_slot}).")


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

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Consulta Datuar a partir del DNI o del Nombre asociado a la línea.
        Implementa exactamente 3 intentos con rotación de circuito Tor y manejo estricto de errores.
        Captura todas las coincidencias cuando existen múltiples resultados.
        """
        dni = (linea.dni or "").strip()
        dni_limpio = "".join(filter(str.isdigit, dni))
        nombre = getattr(linea, "nombre", None) or ""
        nombre_limpio = str(nombre).strip()

        # Determinar término y modo de búsqueda (Nombre o DNI)
        if self.prefer_name and nombre_limpio and len(nombre_limpio) >= 3:
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

        if self._session is None:
            self.iniciar()

        url_busqueda = f"{self.base_url}/indext.php?busqueda={urllib.parse.quote(termino_busqueda)}"
        max_intentos = 3
        hubo_error_red = False
        ultimo_error = ""

        for intento in range(1, max_intentos + 1):
            self.request_count += 1
            if self.use_tor and self.tor_rotate_every > 0 and (self.request_count % self.tor_rotate_every == 0):
                self._rotar_circuito_instantaneo()

            self.sleep_jitter(self.delay_min, self.delay_max)
            headers = self._get_headers()

            try:
                resp = self._session.get(url_busqueda, headers=headers, timeout=self.timeout)

                if resp.status_code == 429:
                    logger.warning(f"⚠️ Datuar Rate Limit (HTTP 429) para '{termino_busqueda}'. Rotando circuito Tor (intento {intento}/{max_intentos})...")
                    if self.use_tor:
                        self._rotar_circuito_instantaneo()
                    time.sleep(1.5)
                    continue

                if resp.status_code in (403, 503) or resp.status_code != 200:
                    logger.warning(f"⚠️ Datuar HTTP {resp.status_code} para '{termino_busqueda}'. Rotando circuito Tor (intento {intento}/{max_intentos})...")
                    if self.use_tor:
                        self._rotar_circuito_instantaneo()
                    time.sleep(1.0)
                    continue

                html = resp.text

                # Detección real de Cloudflare (sin falsos positivos por modal turnstile en footer)
                es_desafio = ("just a moment..." in html.lower() or "<title>attention required" in html.lower()) and "contador-resultados" not in html
                if es_desafio:
                    logger.warning(f"⚠️ Datuar desafío Cloudflare detectado. Rotando circuito Tor (intento {intento}/{max_intentos})...")
                    if self.use_tor:
                        self._rotar_circuito_instantaneo()
                    time.sleep(1.2)
                    continue

                personas, total_cnt = self._parse_personas_html(html, termino_busqueda)

                if personas:
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
                            "origen": "datuar_live"
                        },
                        descripcion=descripcion_line
                    )

                # Si no se encontraron personas y el contador es legítimamente 0
                if total_cnt == 0 and "contador-resultados" in html:
                    if self.use_tor and intento < max_intentos:
                        logger.debug(f"Datuar reportó 0 resultados para '{termino_busqueda}' (intento {intento}/{max_intentos}). Verificando circuito secundario...")
                        self._rotar_circuito_instantaneo()
                        time.sleep(0.5)
                        continue

                    # Confirmado sin coincidencia tras reintentos
                    return ScrapeResult(
                        ani=linea.ani,
                        status=StatusScraping.SIN_COINCIDENCIA,
                        fuente_scraper=self.nombre,
                        titular=Titular(nro_documento=dni_limpio, tipo_documento="DNI") if dni_limpio else None,
                        detalles={"motivo": "Sin registros en Datuar", "busqueda": termino_busqueda, "tipo_busqueda": "dni" if es_busqueda_dni else "nombre"},
                        descripcion=f"Datuar - '{termino_busqueda}' sin registros"
                    )

                # Si no hubo personas y tampoco contador=0 (respuesta anómala o truncada)
                if intento < max_intentos:
                    logger.debug(f"Datuar respuesta anómala para '{termino_busqueda}'. Rotando circuito...")
                    if self.use_tor:
                        self._rotar_circuito_instantaneo()
                    time.sleep(0.5)
                    continue

            except Exception as e:
                hubo_error_red = True
                ultimo_error = str(e)
                logger.warning(f"⚠️ Error de conexión en Datuar para '{termino_busqueda}' (intento {intento}/{max_intentos}): {e}")
                if self.use_tor:
                    self._rotar_circuito_instantaneo()
                time.sleep(0.8)

        # Si agotó los 3 intentos por fallas de red o timeouts, RETORNAR ERROR (no sin_coincidencia)
        if hubo_error_red:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.ERROR,
                fuente_scraper=self.nombre,
                descripcion=f"Datuar error de conexión tras {max_intentos} intentos: {ultimo_error}",
                detalles={"error": ultimo_error, "busqueda": termino_busqueda}
            )

        return ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper=self.nombre,
            descripcion=f"Datuar - '{termino_busqueda}' sin registros tras {max_intentos} intentos",
            detalles={"motivo": "Sin registros confirmados tras reintentos", "busqueda": termino_busqueda}
        )

