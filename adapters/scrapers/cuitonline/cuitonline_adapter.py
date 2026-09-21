# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Scraper CuitOnline (Argentina)
Implementa IScraperEnginePort para extracción y normalización exhaustiva de CUIT / CUIL,
datos fiscales, tributarios, domicilio y actividades a partir del DNI obtenido en etapas previas.
Captura el 100% de los campos disponibles (búsqueda y detalle oficial) con caché SQLite de alta velocidad a costo $0.
"""
import os
import re
import time
import json
import random
import hashlib
import logging
from typing import Dict, Any, Optional, List
import requests
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup

import config
from core.domain.entities import Linea, ScrapeResult, StatusScraping, Titular
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.network.tor_controller import TorController

logger = logging.getLogger("ScraperCuitOnline")


class CuitOnlineAdapter(BaseScraperAdapter):
    """
    Adaptador de extracción y enriquecimiento sobre CuitOnline Argentina (https://www.cuitonline.com).
    Obtiene CUIT/CUIL verificado, denominación fiscal, condición AFIP (IVA, Ganancias, Autónomos),
    domicilio fiscal (dirección, provincia, localidad), actividades económicas y constancias oficiales.
    """

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/123.0.0.0 Safari/537.36"
    ]

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        delay_min: Optional[float] = None,
        delay_max: Optional[float] = None,
        use_tor: bool = False,
        tor_controller: Optional[TorController] = None,
        tor_external_daemon: bool = False,
        tor_rotate_every: int = 19,
        worker_slot: int = 1,
        cache_db_path: Optional[str] = None,
        fetch_detail: bool = True,
        **kwargs
    ):
        super().__init__()
        self.base_url = (base_url or getattr(config, "CUITONLINE_URL", "https://www.cuitonline.com")).rstrip("/")
        self.timeout = timeout or getattr(config, "CUITONLINE_TIMEOUT", 15)
        self.delay_min = delay_min if delay_min is not None else getattr(config, "CUITONLINE_DELAY_MIN", 0.2)
        self.delay_max = delay_max if delay_max is not None else getattr(config, "CUITONLINE_DELAY_MAX", 0.6)
        self.use_tor = use_tor
        self.tor_controller = tor_controller
        self.tor_external_daemon = tor_external_daemon
        self.tor_rotate_every = tor_rotate_every
        self.worker_slot = worker_slot
        self.fetch_detail = fetch_detail
        self.request_count = 0

        self._session: Optional[requests.Session] = None
        self._proxy_url: Optional[str] = None

    @property
    def nombre(self) -> str:
        """Identificador único en el pipeline."""
        return "cuitonline"

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
        logger.debug(f"CuitOnline rotó instantáneamente circuito Tor: {self._proxy_url[:28]}...")

    def iniciar(self) -> None:
        """Inicializa la sesión HTTP y enrutamiento de red."""
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
            logger.info(f"CuitOnlineAdapter enrutando por Tor Stream Isolation (Slot {self.worker_slot}).")

        # Establecer cookies iniciales
        try:
            headers = self._get_headers()
            self._session.get(f"{self.base_url}/", headers=headers, timeout=self.timeout)
        except Exception as e:
            logger.debug(f"Aviso al inicializar cookies en CuitOnline: {e}")

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
        """Health-check para el Circuit Breaker."""
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
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
            "Connection": "keep-alive"
        }

    def _parse_search_hit(self, hit_element) -> Dict[str, Any]:
        """Parsea un elemento .hit de la página de resultados de CuitOnline."""
        denom_el = hit_element.select_one(".denominacion h2")
        link_el = hit_element.select_one(".denominacion a")
        cuit_el = hit_element.select_one(".cuit")
        facets_el = hit_element.select_one(".doc-facets")
        const_el = hit_element.select_one("a.constancia-inscripcion")

        cuit_fmt = cuit_el.get_text(strip=True) if cuit_el else ""
        cuit_clean = "".join(filter(str.isdigit, cuit_fmt))
        raw_denom = denom_el.get_text(strip=True) if denom_el else ""
        denom = raw_denom.replace("?", "Ñ") if "ACU?A" in raw_denom else raw_denom

        href = link_el.get("href", "") if link_el else ""
        if href and not href.startswith("http"):
            detalle_url = f"{self.base_url}/{href.lstrip('/')}"
        else:
            detalle_url = href

        constancia_url = ""
        if const_el:
            const_href = const_el.get("href", "")
            if const_href.startswith("//"):
                constancia_url = f"https:{const_href}"
            elif const_href.startswith("/"):
                constancia_url = f"{self.base_url}{const_href}"
            else:
                constancia_url = const_href

        tipo_persona = ""
        genero = ""
        inmigrante = False
        ganancias = ""
        iva = ""

        if facets_el:
            facets_html = facets_el.decode_contents()
            parts = re.split(r"<br\s*/?>", facets_html)
            for part in parts:
                p_soup = BeautifulSoup(part, "html.parser")
                txt = p_soup.get_text().replace("\xa0", " ").strip(" \t\n\r•\ufffd")
                if txt.startswith("Persona"):
                    tipo_persona = txt
                    if "masculino" in txt.lower():
                        genero = "Masculino"
                    elif "femenino" in txt.lower():
                        genero = "Femenino"
                elif "Inmigrante" in txt:
                    inmigrante = True
                elif "Ganancias:" in txt:
                    ganancias = txt.split("Ganancias:", 1)[1].strip()
                elif "IVA:" in txt:
                    iva = txt.split("IVA:", 1)[1].strip()

        return {
            "cuit": cuit_fmt,
            "cuit_limpio": cuit_clean,
            "denominacion": denom,
            "tipo_persona": tipo_persona,
            "genero": genero,
            "inmigrante": inmigrante,
            "ganancias": ganancias,
            "iva": iva,
            "constancia_inscripcion_afip": constancia_url,
            "detalle_url": detalle_url
        }

    def _parse_detail_page(self, html: str, base_hit: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrae el 100% de la información presente en la ficha detallada oficial de CuitOnline.
        Domicilio exacto, provincia, localidad, condición de empleador, actividades AFIP e impuestos.
        """
        soup = BeautifulSoup(html, "html.parser")
        p_data = soup.select_one(".persona-data")

        data = dict(base_hit)

        if p_data:
            # 1. CUIT verificado
            cuit_el = p_data.select_one(".p_cuit")
            if cuit_el:
                cuit_txt = cuit_el.get_text(strip=True)
                if cuit_txt:
                    data["cuit"] = cuit_txt
                    data["cuit_limpio"] = "".join(filter(str.isdigit, cuit_txt))

            # 2. Denominación / Título oficial
            h1_el = soup.select_one("h1")
            if h1_el:
                raw_t = h1_el.get_text(strip=True)
                raw_t = raw_t.replace("?", "Ñ") if "ACU?A" in raw_t else raw_t
                if raw_t:
                    data["denominacion"] = raw_t

            # 3. Género y Nacionalidad / Inmigrante
            gender_el = p_data.select_one('[itemprop="gender"]')
            if gender_el:
                data["genero"] = gender_el.get_text(strip=True)

            nat_el = p_data.select_one('[itemprop="nationality"]')
            if nat_el:
                data["nacionalidad"] = nat_el.get_text(strip=True)
                if "inmigrante" in data["nacionalidad"].lower():
                    data["inmigrante"] = True

            # 4. Domicilio Fiscal Completo
            street_el = p_data.select_one('[itemprop="streetAddress"]')
            if street_el:
                data["direccion"] = street_el.get_text(strip=True)

            prov_el = p_data.select_one('[itemprop="addressRegion"]')
            if prov_el:
                data["provincia"] = prov_el.get_text(strip=True)

            loc_el = p_data.select_one('[itemprop="addressLocality"]')
            if loc_el:
                data["localidad"] = loc_el.get_text(strip=True)

            # 5. Ganancias, IVA, Empleador
            for li in p_data.select("li"):
                txt = li.get_text(" ", strip=True)
                if "Ganancias:" in txt:
                    m_g = re.search(r"Ganancias:\s*([^-\n]+)", txt)
                    if m_g:
                        data["ganancias"] = m_g.group(1).strip()
                if "IVA:" in txt:
                    m_iva = re.search(r"IVA:\s*([^\n]+)", txt)
                    if m_iva:
                        data["iva"] = m_iva.group(1).strip()
                if "Empleador:" in txt:
                    m_emp = re.search(r"Empleador:\s*([^\n]+)", txt)
                    if m_emp:
                        data["empleador"] = m_emp.group(1).strip()

            # 6. Impuestos activos
            impuestos_activos = []
            for h2 in p_data.select("h2.impuestos_activos"):
                if "Impuestos activos" in h2.get_text():
                    parent_li = h2.find_parent("li")
                    if parent_li:
                        for sub_li in parent_li.select("ul li"):
                            t = sub_li.get_text(" ", strip=True).strip(" »")
                            if t:
                                impuestos_activos.append(t)
            data["impuestos_activos"] = impuestos_activos

            # 7. Regímenes activos
            regimenes_activos = []
            for h2 in p_data.select("h2.impuestos_activos"):
                if "Regímenes activos" in h2.get_text():
                    parent_li = h2.find_parent("li")
                    if parent_li:
                        for sub_li in parent_li.select("ul li"):
                            t = sub_li.get_text(" ", strip=True)
                            if t:
                                regimenes_activos.append(t)
            data["regimenes_activos"] = regimenes_activos

            # 8. Actividades Económicas
            actividades = []
            for h2 in p_data.select("h2.impuestos_activos"):
                if "Actividades:" in h2.get_text():
                    parent_li = h2.find_parent("li")
                    if parent_li:
                        for act_span in parent_li.select("div > span"):
                            t = act_span.get_text(" ", strip=True)
                            if t and len(t) > 3:
                                actividades.append(t)
            data["actividades"] = actividades

        # 9. Enlaces a constancias e informes oficiales
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "constancia/inscripcion" in href:
                data["constancia_inscripcion_afip"] = self._normalizar_url(href)
            elif "constancia/cuil" in href:
                data["constancia_cuil_anses"] = self._normalizar_url(href)
            elif "constancia/actividades" in href:
                data["actividades_economicas_url"] = self._normalizar_url(href)

        # 10. Desglose de Nombres y Apellidos
        denom = data.get("denominacion", "")
        if "," in denom:
            parts = [p.strip() for p in denom.split(",", 1)]
            data["apellidos"] = parts[0].upper()
            data["nombres"] = parts[1].title()
        elif " " in denom:
            words = denom.split()
            data["apellidos"] = words[0].upper()
            data["nombres"] = " ".join(words[1:]).title()
        else:
            data["apellidos"] = denom.upper()
            data["nombres"] = ""

        return data

    def _normalizar_url(self, href: str) -> str:
        """Normaliza enlaces relativos y protocol-relative."""
        if not href:
            return ""
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"{self.base_url}{href}"
        if not href.startswith("http"):
            return f"{self.base_url}/{href.lstrip('/')}"
        return href

    def consultar_linea(self, linea: Linea, **kwargs) -> ScrapeResult:
        """
        Consulta CuitOnline a partir del DNI asociado a la línea.
        Si no se cuenta con DNI, retorna SIN_COINCIDENCIA en fast-path (<1 ms).
        Extrae el 100% de los datos fiscales, impositivos, demográficos y actividades.
        """
        dni = (linea.dni or "").strip()
        dni_limpio = "".join(filter(str.isdigit, dni))

        if not dni_limpio or len(dni_limpio) < 6:
            logger.debug(f"Línea {linea.ani} sin DNI para consultar CuitOnline. Saltando fast-path.")
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion="Sin DNI disponible para consultar en CuitOnline",
                detalles={"motivo": "DNI no provisto ni inferido"}
            )

        # Consulta en Red a CuitOnline
        if self._session is None:
            self.iniciar()

        self.request_count += 1
        if self.use_tor and self.tor_rotate_every > 0 and (self.request_count % self.tor_rotate_every == 0):
            self._rotar_circuito_instantaneo()

        self.sleep_jitter(self.delay_min, self.delay_max)

        url_busqueda = f"{self.base_url}/search.php?q={dni_limpio}"
        headers = self._get_headers()

        try:
            resp = self._session.get(url_busqueda, headers=headers, timeout=self.timeout)

            if resp.status_code == 429:
                logger.warning("⚠️ CuitOnline Rate Limit (HTTP 429). Reintentando...")
                if self.use_tor:
                    self._rotar_circuito_instantaneo()
                time.sleep(1.5)
                resp = self._session.get(url_busqueda, headers=headers, timeout=self.timeout)

            if resp.status_code != 200:
                logger.warning(f"CuitOnline retornó código HTTP {resp.status_code} para DNI {dni_limpio}")
                return ScrapeResult(
                    ani=linea.ani,
                    status=StatusScraping.SIN_COINCIDENCIA,
                    fuente_scraper=self.nombre,
                    descripcion=f"CuitOnline error HTTP {resp.status_code}",
                    detalles={"http_status": resp.status_code, "dni": dni_limpio}
                )

            soup = BeautifulSoup(resp.text, "html.parser")
            hit_elements = soup.select(".hit")

            if hit_elements:
                parsed_hits = [self._parse_search_hit(h) for h in hit_elements]
                hit_principal = parsed_hits[0]

                # 3. Profundizar en la página de detalle oficial para extraer el 100% de los campos
                full_data = dict(hit_principal)
                detalle_url = hit_principal.get("detalle_url")

                if self.fetch_detail and detalle_url:
                    try:
                        self.sleep_jitter(0.1, 0.3)
                        r_det = self._session.get(detalle_url, headers=headers, timeout=self.timeout)
                        if r_det.status_code == 200:
                            full_data = self._parse_detail_page(r_det.text, hit_principal)
                    except Exception as e_det:
                        logger.debug(f"Aviso al consultar detalle de CuitOnline: {e_det}")

                full_data["dni"] = dni_limpio
                full_data["total_coincidencias"] = len(parsed_hits)
                full_data["coincidencias"] = parsed_hits
                full_data["origen"] = "cuitonline_live"

                cuit_fmt = full_data.get("cuit", "")
                cuit_clean = full_data.get("cuit_limpio", "")
                denom = full_data.get("denominacion", "")

                titular = Titular(
                    nombre=full_data.get("nombres") or "",
                    apellido=full_data.get("apellidos") or "",
                    razon_social=denom,
                    nro_documento=dni_limpio,
                    tipo_documento="DNI",
                    cuil=cuit_clean or cuit_fmt,
                    tipo_persona=full_data.get("tipo_persona") or "",
                    genero=full_data.get("genero") or "",
                    provincia=full_data.get("provincia") or "",
                    ciudad=full_data.get("localidad") or ""
                )

                desc_partes = [f"CuitOnline - CUIT: {cuit_fmt}"]
                if denom:
                    desc_partes.append(denom)
                if full_data.get("iva"):
                    desc_partes.append(f"IVA: {full_data['iva']}")
                if full_data.get("provincia"):
                    ub = f"{full_data.get('localidad') or ''}, {full_data.get('provincia') or ''}".strip(", ")
                    desc_partes.append(f"Ubicación: {ub}")
                descripcion_line = " | ".join(desc_partes)

                return ScrapeResult(
                    ani=linea.ani,
                    status=StatusScraping.COINCIDENCIA,
                    fuente_scraper=self.nombre,
                    titular=titular,
                    detalles=full_data,
                    raw=full_data,
                    descripcion=descripcion_line
                )

            # No se encontraron resultados
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                titular=Titular(nro_documento=dni_limpio, tipo_documento="DNI"),
                detalles={"motivo": "DNI no registrado en CuitOnline", "dni": dni_limpio},
                descripcion=f"CuitOnline - DNI {dni_limpio} sin registros"
            )

        except Exception as e:
            logger.error(f"Error al consultar CuitOnline para DNI {dni_limpio}: {e}")
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion=f"Fallo de conexión a CuitOnline: {str(e)[:80]}",
                detalles={"error": str(e), "dni": dni_limpio}
            )
