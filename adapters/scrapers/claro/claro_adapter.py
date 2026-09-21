# -*- coding: utf-8 -*-
"""
Adaptador Secundario (Driven Adapter): Scraper Claro Argentina (Cobro Express)
Implementa IScraperEnginePort interactuando con la API REST de pagosce.cobroexpress.com.ar.
Extrae el 100% de la información de deuda, comprobantes, códigos de barra y metadatos integradores.
"""
import os
import re
import json
import uuid
import base64
import random
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

import requests

import config
from core.ports.scraper_port import IScraperEnginePort
from core.domain.entities import Linea, ScrapeResult, Titular, Servicio
from core.domain.enums import StatusScraping
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.network.tor_controller import TorController
from adapters.network.proxy_pool import ProxyPoolManager

logger = logging.getLogger("ClaroAdapter")


class ClaroAdapter(BaseScraperAdapter):
    """Adaptador de producción para Claro Argentina a través de Cobro Express."""

    ID_EMPRESA = 20916           # Claro Telefonía
    ID_EMPRESA_MODALIDAD = 2876  # Consulta por Línea y DNI

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]

    def __init__(
        self,
        base_url: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: Optional[int] = None,
        delay_min: Optional[float] = None,
        delay_max: Optional[float] = None,
        use_tor: Optional[bool] = None,
        use_proxy_pool: Optional[bool] = None,
        worker_slot: Optional[int] = None,
        tor_rotate_every: Optional[int] = None,
        **kwargs
    ):
        """
        Inicializa el adaptador con parámetros configurables o variables de entorno.
        Acepta **kwargs para compatibilidad con la factoría ScraperRegistry.
        """
        self.base_url = (base_url or config.COBRO_EXPRESS_URL).rstrip("/")
        self.proxy = proxy or config.COBRO_EXPRESS_PROXY
        self.timeout = timeout or config.COBRO_EXPRESS_TIMEOUT
        self.delay_min = delay_min or config.COBRO_EXPRESS_DELAY_MIN
        self.delay_max = delay_max or config.COBRO_EXPRESS_DELAY_MAX
        
        # Selección de red: ProxyPool (rápido) vs Tor Stream Isolation vs Directo
        self.use_proxy_pool = use_proxy_pool if use_proxy_pool is not None else kwargs.get("proxy_pool", kwargs.get("use_proxy_pool", config.PROXY_POOL_ENABLED))
        if self.use_proxy_pool:
            self.use_tor = False
        else:
            self.use_tor = use_tor if use_tor is not None else kwargs.get("tor", config.TOR_ENABLED)
            
        self.worker_slot = worker_slot or kwargs.get("worker_slot")
        self.tor_rotate_every = tor_rotate_every or kwargs.get("tor_rotate_every", config.TOR_ROTATE_EVERY)
        self.tor_external_daemon = kwargs.get("tor_external_daemon", False) or bool(self.worker_slot)
        self.proxy_pool_shared = kwargs.get("proxy_pool_shared", False) or (self.worker_slot is not None and self.worker_slot > 1)
        self.request_count = random.randint(0, 5)
        self.tor_controller: Optional[TorController] = None
        self.proxy_pool: Optional[ProxyPoolManager] = None
        self._session: Optional[requests.Session] = None
        self._proxy_session: Optional[requests.Session] = None

    @property
    def nombre(self) -> str:
        """Identificador unívoco en la cadena de responsabilidad del pipeline."""
        return "claro"

    def _get_headers(self) -> Dict[str, str]:
        """Cabeceras requeridas por la pasarela de Cobro Express para evitar bloqueos WAF."""
        ua = random.choice(self.USER_AGENTS)
        return {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "Content-Type": "application/json; charset=utf-8",
            "idprovincia": "1",
        }

    @staticmethod
    def _parse_embedded_tokens(raw_str: str) -> Dict[str, Any]:
        """
        Decodifica cadenas base64 embebidas (integradorInfo, transaccionInfo) 
        extrayendo el 100% de los pares clave-valor estructurados o semi-estructurados.
        """
        data: Dict[str, Any] = {}
        if not raw_str or not isinstance(raw_str, str):
            return data
        try:
            decoded_bytes = base64.b64decode(raw_str + "===")
            text = decoded_bytes.decode("utf-8", errors="ignore")
            matches = re.findall(
                r'"([a-zA-Z0-9_-]+)"\s*:\s*("([^"]*)"|([-+]?\d*\.?\d+)|(null)|(true)|(false))',
                text
            )
            for m in matches:
                k = m[0]
                if m[2] is not None and m[2] != "":
                    data[k] = m[2]
                elif m[3]:
                    num_str = m[3]
                    data[k] = float(num_str) if "." in num_str else int(num_str)
                elif m[4]:
                    data[k] = None
                elif m[5]:
                    data[k] = True
                elif m[6]:
                    data[k] = False
        except Exception:
            pass
        return data

    def _generar_proxy_aislado(self) -> str:
        """Genera una URL SOCKS5h con credenciales aleatorias para forzar un circuito Tor independiente."""
        slot_tag = f"w{self.worker_slot or 0}"
        rnd_tag = uuid.uuid4().hex[:8]
        return f"socks5h://{slot_tag}_{rnd_tag}:tor@127.0.0.1:{config.TOR_SOCKS_PORT_BASE}"

    def _rotar_circuito_instantaneo(self) -> None:
        """Rota de IP inmediatamente asignando nuevas credenciales SOCKS sin llamadas stem ni demoras."""
        if self.use_tor:
            self.proxy = self._generar_proxy_aislado()
            if self._session is not None:
                self._session.proxies = {
                    "http": self.proxy,
                    "https": self.proxy
                }
            logger.info(f"🔄 Circuito Tor renovado instantáneamente (Stream Isolation: {self.proxy.split('@')[0]}...)")

    def iniciar(self) -> None:
        """Inicializa la sesión HTTP y configura proxy Tor o ProxyPool según corresponda."""
        if self.use_proxy_pool:
            self.proxy_pool = ProxyPoolManager.get_instance(allow_feeder=not self.proxy_pool_shared)
            if not self.proxy_pool_shared:
                self.proxy_pool.bootstrap(min_proxies=2, max_wait_sec=15)
                self.proxy_pool.start_background_feeder()
            logger.info(f"ClaroAdapter enrutando mediante ProxyPoolManager ({self.proxy_pool.size()} proxies vivos).")
            if self._proxy_session is None:
                self._proxy_session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
                self._proxy_session.mount("http://", adapter)
                self._proxy_session.mount("https://", adapter)
        elif self.use_tor and not self.proxy:
            socks_port = config.TOR_SOCKS_PORT_BASE
            control_port = config.TOR_CONTROL_PORT_BASE
            is_owner = not (self.tor_external_daemon or self.worker_slot is not None)
            self.tor_controller = TorController(
                socks_port=socks_port,
                control_port=control_port,
                is_owner=is_owner
            )
            # Reutiliza el Tor compartido o arranca uno si no está corriendo y es dueño
            if not self.tor_controller.ensure_running(timeout_sec=45):
                logger.warning(f"No se pudo asegurar Tor en puerto {socks_port}. Continuando con conexión directa.")

            if self.tor_controller.is_running():
                self.proxy = self._generar_proxy_aislado()
                logger.info(f"ClaroAdapter enrutando por Tor Stream Isolation (Slot {self.worker_slot or 1}).")

        self._session = requests.Session()
        if self.proxy:
            self._session.proxies = {
                "http": self.proxy,
                "https": self.proxy
            }
            logger.info(f"ClaroAdapter configurado con proxy: {self.proxy[:30]}...")

    def autenticar(self) -> bool:
        """
        Para Cobro Express DeudaFormulario, la pasarela es stateless y no requiere cookies previas.
        Asegura que la sesión HTTP esté operativa sin incurrir en latencia extra.
        """
        if self._session is None:
            self.iniciar()
        return True

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Consulta la línea en la API de Cobro Express bajo la modalidad Claro Telefonía.
        Captura exhaustivamente TODOS los campos de la respuesta.
        """
        if not linea.es_valida:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                operador="Claro",
                descripcion=f"ANI telefónico inválido: '{linea.ani}' (debe tener 10 dígitos)"
            )

        dni_titular = (linea.dni or "").strip()
        if not dni_titular:
            logger.info(f"Línea {linea.ani} sin DNI asociado. Saltando consulta Cobro Express Claro.")
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                operador="Claro",
                descripcion="Sin DNI disponible para consultar Claro en Cobro Express",
                detalles={"motivo": "DNI no provisto ni inferido de etapas previas"}
            )

        if self._session is None:
            self.iniciar()

        # Rotación preventiva instantánea de circuito Tor cada N consultas
        self.request_count += 1
        if self.use_tor and self.tor_rotate_every > 0 and (self.request_count % self.tor_rotate_every == 0):
            self._rotar_circuito_instantaneo()

        # Pausa aleatoria (jitter mínimo optimizado)
        self.sleep_jitter(self.delay_min, self.delay_max)

        payload_data = {
            "IdEmpresa": self.ID_EMPRESA,
            "IdEmpresaModalidad": self.ID_EMPRESA_MODALIDAD,
            "FormData": {
                "C11": linea.ani,
                "C12": dni_titular
            }
        }

        url_api = f"{self.base_url}/api/Servicios/DeudaFormulario"
        headers = self._get_headers()

        try:
            if self.use_proxy_pool and self.proxy_pool:
                max_retries = config.PROXY_POOL_MAX_RETRIES
                resp = None
                last_err = None
                if self._proxy_session is None:
                    self._proxy_session = requests.Session()
                    adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
                    self._proxy_session.mount("http://", adapter)
                    self._proxy_session.mount("https://", adapter)

                for attempt in range(max_retries):
                    current_proxy = self.proxy_pool.get_proxy()
                    if not current_proxy:
                        logger.warning("No hay proxies disponibles en ProxyPool. Esperando recarga...")
                        time.sleep(0.8)
                        continue

                    proxies_dict = {"http": current_proxy, "https": current_proxy}
                    try:
                        resp = self._proxy_session.post(
                            url_api,
                            headers=headers,
                            data=json.dumps(payload_data),
                            proxies=proxies_dict,
                            timeout=config.PROXY_POOL_TIMEOUT
                        )
                        if resp.status_code == 429:
                            logger.warning(f"⚠️ Cobro Express Rate Limit (HTTP 429) en proxy {current_proxy}. Rotando...")
                            if self.proxy_pool.size() <= 8:
                                logger.info(f"Pool reducido ({self.proxy_pool.size()} proxies). Backoff breve de 1.5s antes de rotar.")
                                time.sleep(1.5)
                            else:
                                self.proxy_pool.report_failure(current_proxy)
                            continue

                        # Proxy respondió exitosamente con código HTTP esperado
                        self.proxy_pool.report_success(current_proxy)
                        break
                    except Exception as e:
                        self.proxy_pool.report_failure(current_proxy)
                        last_err = e
                        logger.debug(f"Falla proxy {current_proxy} (intento {attempt+1}/{max_retries}): {e}")

                if resp is None:
                    raise RuntimeError(f"ProxyPool agotó los {max_retries} reintentos para {linea.ani}: {last_err}")
            else:
                try:
                    resp = self._session.post(
                        url_api,
                        headers=headers,
                        data=json.dumps(payload_data),
                        timeout=self.timeout
                    )
                except (requests.exceptions.Timeout, requests.exceptions.RequestException) as net_err:
                    if self.use_tor:
                        logger.warning(f"Timeout/Error de red en circuito Tor ({net_err}). Rotando circuito instantáneamente y reintentando...")
                        self._rotar_circuito_instantaneo()
                        resp = self._session.post(
                            url_api,
                            headers=headers,
                            data=json.dumps(payload_data),
                            timeout=self.timeout
                        )
                    else:
                        raise

            # --- MANEJO DE RESPUESTAS ---

            # Caso 1: Error 400 (Cliente inexistente o datos incorrectos -> Sin coincidencia)
            if resp.status_code == 400:
                try:
                    err_json = resp.json()
                    mensaje_error = err_json.get("message", resp.text)
                except Exception:
                    mensaje_error = resp.text[:150]

                return ScrapeResult(
                    ani=linea.ani,
                    status=StatusScraping.SIN_COINCIDENCIA,
                    fuente_scraper=self.nombre,
                    operador="Claro",
                    titular=Titular(nro_documento=dni_titular),
                    descripcion=f"Sin coincidencia en Claro: {mensaje_error}"[:195],
                    detalles={
                        "http_status": 400,
                        "error_message": mensaje_error,
                        "dni_consultado": dni_titular,
                        "ani_consultado": linea.ani
                    },
                    raw={"status_code": 400, "response": resp.text}
                )

            # Caso 2: Error 429 (Too Many Requests -> Rotación reactiva instantánea)
            if resp.status_code == 429:
                logger.warning(f"⚠️ Cobro Express Rate Limit (HTTP 429) detectado para línea {linea.ani}.")
                if self.use_tor:
                    logger.info("Renovando circuito Tor reactivamente y reintentando consulta...")
                    self._rotar_circuito_instantaneo()
                    resp = self._session.post(
                        url_api,
                        headers=headers,
                        data=json.dumps(payload_data),
                        timeout=self.timeout
                    )
                if resp.status_code == 429:
                    raise RuntimeError(
                        f"Cobro Express Rate Limit persistente (HTTP 429 Too Many Requests): {resp.text[:150]}"
                    )

            # Caso 3: Otros errores HTTP (5xx, etc.)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Cobro Express respondió HTTP {resp.status_code}: {resp.text[:150]}"
                )

            # Caso 4: 200 OK -> Parseo exhaustivo del 100% de los datos
            items = resp.json()
            if not isinstance(items, list) or len(items) == 0:
                return ScrapeResult(
                    ani=linea.ani,
                    status=StatusScraping.SIN_COINCIDENCIA,
                    fuente_scraper=self.nombre,
                    operador="Claro",
                    titular=Titular(nro_documento=dni_titular),
                    descripcion="Sin deuda registrada en Claro (Cobro Express)",
                    detalles={
                        "http_status": 200,
                        "dni_consultado": dni_titular,
                        "ani_consultado": linea.ani,
                        "items_count": 0
                    },
                    raw={"status_code": 200, "items": []}
                )

            # Consolidar TODOS los comprobantes/items retornados
            total_deuda = 0.0
            items_enriquecidos: List[Dict[str, Any]] = []
            fechas_encontradas: Dict[str, Any] = {
                "fecha_consulta": datetime.now(timezone.utc).isoformat()
            }
            primer_item = items[0]

            for idx, item in enumerate(items):
                p_imp = float(item.get("primerImporte", 0.0) or 0.0)
                s_imp = float(item.get("segundoImporte", 0.0) or 0.0)
                total_deuda += p_imp

                # Decodificación exhaustiva de metadatos integrador y transacción
                int_tokens = self._parse_embedded_tokens(item.get("integradorInfo", ""))
                tx_tokens = self._parse_embedded_tokens(item.get("transaccionInfo", ""))

                if "ExpirationDate" in int_tokens and not fechas_encontradas.get("fecha_vencimiento"):
                    fechas_encontradas["fecha_vencimiento"] = int_tokens["ExpirationDate"]

                item_dict = {
                    "indice": idx,
                    "idEmpresa": item.get("idEmpresa"),
                    "nombreEmpresa": (item.get("nombreEmpresa") or "").strip(),
                    "idEmpresaModalidad": item.get("idEmpresaModalidad"),
                    "codigoBarra": item.get("codigoBarra"),
                    "idCliente": item.get("idCliente"),
                    "primerImporte": p_imp,
                    "segundoImporte": s_imp,
                    "importeMin": item.get("importeMin"),
                    "importeMax": item.get("importeMax"),
                    "idTipoMonto": item.get("idTipoMonto"),
                    "detalles_item": item.get("detalles", []),
                    "hash": item.get("hash"),
                    "transaccionInfo_raw": item.get("transaccionInfo"),
                    "transaccionInfo_decoded": tx_tokens,
                    "integradorInfo_raw": item.get("integradorInfo"),
                    "integradorInfo_decoded": int_tokens,
                }
                items_enriquecidos.append(item_dict)

            id_cliente = primer_item.get("idCliente", "")
            codigo_barra = primer_item.get("codigoBarra", "")
            nom_empresa = (primer_item.get("nombreEmpresa") or "CLARO").strip()
            vencimiento = fechas_encontradas.get("fecha_vencimiento", "N/A")

            titular = Titular(
                nro_documento=dni_titular,
                tipo_documento="DNI"
            )

            servicio = Servicio(
                tecnologia="Móvil / Celular",
                producto=nom_empresa,
                modalidad_factura=f"Modalidad {self.ID_EMPRESA_MODALIDAD} (Cobro Express)"
            )

            detalles_completos = {
                "fuente_origen": "Cobro Express API",
                "id_empresa": self.ID_EMPRESA,
                "id_modalidad": self.ID_EMPRESA_MODALIDAD,
                "id_cliente": id_cliente,
                "codigo_barra_primario": codigo_barra,
                "deuda_total": round(total_deuda, 2),
                "cantidad_comprobantes": len(items),
                "comprobantes": items_enriquecidos,
                "dni_consultado": dni_titular,
                "ani_consultado": linea.ani,
            }

            desc = f"Claro - Cliente: {id_cliente} | Deuda: ${total_deuda:.2f} | Vto: {vencimiento} | CB: {codigo_barra}"[:195]

            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.COINCIDENCIA,
                fuente_scraper=self.nombre,
                operador="Claro",
                operador_receptor="Claro",
                titular=titular,
                servicio=servicio,
                fechas=fechas_encontradas,
                detalles=detalles_completos,
                raw={"items": items},
                descripcion=desc
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Error de red al consultar Claro en Cobro Express para {linea.ani}: {e}")
            raise RuntimeError(f"Error de red en ClaroAdapter: {e}")
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.error(f"Excepción no controlada en ClaroAdapter para {linea.ani}: {e}", exc_info=True)
            raise RuntimeError(f"Fallo inesperado en ClaroAdapter: {e}")

    def verificar_salud(self) -> bool:
        """Health-check para el Circuit Breaker. Comprueba conectividad básica."""
        if self._session is None:
            self.iniciar()
        try:
            r = self._session.get(f"{self.base_url}/", headers=self._get_headers(), timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def cerrar(self) -> None:
        """Cierra la sesión HTTP y libera recursos de Tor si fueron iniciados como propietario."""
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
        if self.tor_controller is not None:
            if not (self.tor_external_daemon or self.worker_slot is not None):
                try:
                    self.tor_controller.stop()
                except Exception:
                    pass
            self.tor_controller = None
