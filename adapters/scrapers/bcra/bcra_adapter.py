# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Scraper Central de Deudores BCRA (Banco Central de la República Argentina)
Implementa IScraperEnginePort para extracción y normalización de la totalidad de las deudas financieras,
entidades bancarias acreedoras, situación crediticia oficial (0 a 5) y montos totales en pesos tal cual
los expone el BCRA en su Central de Deudores del Sistema Financiero.

Reglas de Negocio Industriales:
- Motor HTTP con SSL/TLS SECLEVEL=1 (evita RemoteDisconnected y cortes abruptos de OpenSSL 3.0).
- Rate Limiter Global coordinado (intervalo mínimo 0.85s y enfriamiento de 3.5s ante HTTP 429).
- Resolución inteligente DNI -> CUIT/CUIL vía Módulo 11 oficial con candidatos primario/alternativo.
- Normalización monetaria dual: montos en miles y montos exactos en pesos ($ ARS).
"""
import ssl
import time
import logging
import threading
from typing import Dict, Any, Optional, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
import urllib3

from core.domain.entities import Linea, ScrapeResult, StatusScraping, Titular
from core.domain.cuit_validator import validar_cuit_modulo11, obtener_cuils_candidatos
from adapters.scrapers.base_scraper import BaseScraperAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("ScraperBCRA")


class BCRAGlobalRateLimiter:
    """
    Controlador de tasa global coordinado entre todos los hilos/workers.
    La API de BCRA (api.bcra.gob.ar) posee un WAF perimetral estricto: ráfagas simultáneas
    activan el reseteo TCP. Mantener >= 0.85s entre peticiones garantiza 100% de efectividad.
    """
    def __init__(self, min_interval: float = 0.85):
        self.min_interval = min_interval
        self.next_allowed_time = time.time()
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.time()
            target_time = max(now, self.next_allowed_time)
            self.next_allowed_time = target_time + self.min_interval

        sleep_dur = target_time - now
        if sleep_dur > 0:
            time.sleep(sleep_dur)

    def enfriar(self, segundos: float = 3.5) -> None:
        with self.lock:
            now = time.time()
            self.next_allowed_time = max(self.next_allowed_time, now + segundos)


# Instancia singleton de rate limiter para coordinar todas las llamadas al BCRA en el proceso
global_bcra_rate_limiter = BCRAGlobalRateLimiter(min_interval=0.85)


class BCRA_SSLAdapter(HTTPAdapter):
    """Adaptador SSL con SECLEVEL=1 para evitar cortes de conexión en la API de BCRA."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


class BcraAdapter(BaseScraperAdapter):
    """
    Adaptador de extracción y enriquecimiento sobre la API Central de Deudores del BCRA.
    URL: https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas/{cuit}
    """
    BASE_URL = "https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas"

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: int = 12,
        rate_limiter: Optional[BCRAGlobalRateLimiter] = None,
        **kwargs
    ):
        super().__init__()
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.timeout = timeout
        self.rate_limiter = rate_limiter or global_bcra_rate_limiter
        self._session: Optional[requests.Session] = None

    @property
    def nombre(self) -> str:
        return "bcra"

    def _crear_session(self) -> requests.Session:
        s = requests.Session()
        s.mount("https://", BCRA_SSLAdapter())
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
            "Connection": "keep-alive"
        })
        return s

    def iniciar(self) -> None:
        if self._session is None:
            self._session = self._crear_session()

    def autenticar(self) -> bool:
        if self._session is None:
            self.iniciar()
        return True

    def verificar_salud(self) -> bool:
        if self._session is None:
            self.iniciar()
        try:
            self.rate_limiter.wait()
            # Consulta CUIT de prueba de Banco Central
            r = self._session.get(f"{self.base_url}/30500010912", timeout=6.0)
            return r.status_code in (200, 404, 429)
        except Exception:
            return False

    def cerrar(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    def _consultar_cuit_directo(self, cuit_limpio: str, max_intentos: int = 4) -> Optional[Dict[str, Any]]:
        """Ejecuta la llamada HTTP con rate limiting coordinado, manejo de 429 y parseo financiero."""
        if len(cuit_limpio) != 11:
            return None

        url = f"{self.base_url}/{cuit_limpio}"

        for intento in range(1, max_intentos + 1):
            try:
                self.rate_limiter.wait()
                resp = self._session.get(url, timeout=self.timeout)

                # Rate limiting de BCRA (HTTP 429)
                if resp.status_code == 429:
                    self.rate_limiter.enfriar(3.5 * intento)
                    continue

                # 404: Sin registros en Central de Deudores (Persona al día / sin deuda)
                if resp.status_code == 404:
                    return {
                        "cuit": cuit_limpio,
                        "sin_deuda": True,
                        "entidades": [],
                        "cantidad_entidades": 0,
                        "operaciones_en_cartera": 0,
                        "peor_situacion": 0,
                        "deuda_total_miles": 0.0,
                        "deuda_total_pesos": 0.0,
                        "deuda_macro_miles": 0.0,
                        "deuda_macro_pesos": 0.0,
                        "deuda_macro_situacion": None,
                        "periodo": ""
                    }

                if resp.status_code != 200:
                    time.sleep(0.5)
                    continue

                data = resp.json()
                results = data.get("results", {})
                if not results:
                    return {
                        "cuit": cuit_limpio,
                        "sin_deuda": True,
                        "entidades": [],
                        "cantidad_entidades": 0,
                        "operaciones_en_cartera": 0,
                        "peor_situacion": 0,
                        "deuda_total_miles": 0.0,
                        "deuda_total_pesos": 0.0,
                        "deuda_macro_miles": 0.0,
                        "deuda_macro_pesos": 0.0,
                        "deuda_macro_situacion": None,
                        "periodo": ""
                    }

                denominacion = results.get("denominacion", "").strip()
                periodos = results.get("periodos", [])

                if not periodos:
                    return {
                        "cuit": cuit_limpio,
                        "denominacion": denominacion,
                        "sin_deuda": True,
                        "entidades": [],
                        "cantidad_entidades": 0,
                        "operaciones_en_cartera": 0,
                        "peor_situacion": 0,
                        "deuda_total_miles": 0.0,
                        "deuda_total_pesos": 0.0,
                        "deuda_macro_miles": 0.0,
                        "deuda_macro_pesos": 0.0,
                        "deuda_macro_situacion": None,
                        "periodo": ""
                    }

                # Tomar el período más reciente (primer elemento)
                ultimo_periodo = periodos[0]
                periodo_str = str(ultimo_periodo.get("periodo", ""))
                entidades_raw = ultimo_periodo.get("entidades", [])

                entidades_procesadas = []
                peor_situacion = 0
                deuda_total = 0.0
                deuda_macro_miles = 0.0
                deuda_macro_situacion = None

                for ent in entidades_raw:
                    nombre_ent = ent.get("entidad", "").strip()
                    sit = int(ent.get("situacion", 1))
                    monto = float(ent.get("monto", 0.0))

                    peor_situacion = max(peor_situacion, sit)
                    deuda_total += monto

                    if "MACRO" in nombre_ent.upper():
                        deuda_macro_miles += monto
                        deuda_macro_situacion = sit

                    entidades_procesadas.append({
                        "entidad": nombre_ent,
                        "situacion": sit,
                        "monto_miles": monto,
                        "monto_pesos": round(monto * 1000.0, 2),
                        "periodo": periodo_str,
                        "dias_atraso": ent.get("diasAtrasoPago", 0),
                        "proceso_judicial": ent.get("procesoJud", False)
                    })

                return {
                    "cuit": cuit_limpio,
                    "denominacion": denominacion,
                    "periodo": periodo_str,
                    "entidades": entidades_procesadas,
                    "cantidad_entidades": len({e["entidad"] for e in entidades_procesadas}),
                    "operaciones_en_cartera": len(entidades_procesadas),
                    "peor_situacion": peor_situacion,
                    "deuda_total_miles": round(deuda_total, 2),
                    "deuda_total_pesos": round(deuda_total * 1000.0, 2),
                    "deuda_macro_miles": round(deuda_macro_miles, 2),
                    "deuda_macro_pesos": round(deuda_macro_miles * 1000.0, 2),
                    "deuda_macro_situacion": deuda_macro_situacion,
                    "sin_deuda": (len(entidades_procesadas) == 0)
                }

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = self._crear_session()
                self.rate_limiter.enfriar(3.0 * intento)
            except Exception as e:
                logger.debug(f"Aviso en consulta BCRA {cuit_limpio}: {e}")
                time.sleep(0.5)

        return None

    def consultar_linea(self, linea: Linea, **kwargs) -> ScrapeResult:
        """
        Consulta BCRA Central de Deudores para el CUIT o DNI asociado a la línea.
        Si la línea solo cuenta con DNI, genera y consulta candidatos mediante algoritmo Módulo 11.
        Retorna ScrapeResult inmutable.
        """
        identificador = (linea.dni or "").strip()
        limpio = "".join(filter(str.isdigit, identificador))

        if not limpio or len(limpio) < 6:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion="Sin DNI ni CUIT disponible para consultar en BCRA",
                detalles={"motivo": "Identificación ausente o inválida"}
            )

        if self._session is None:
            self.iniciar()

        datos_bcra = None
        cuit_consultado = None

        if len(limpio) == 11 and validar_cuit_modulo11(limpio):
            cuit_consultado = limpio
            datos_bcra = self._consultar_cuit_directo(cuit_consultado)
        else:
            # Es DNI: generar candidatos (primario y alternativo)
            c1, c2 = obtener_cuils_candidatos(limpio)
            cuit_consultado = c1
            datos_bcra = self._consultar_cuit_directo(c1)
            # Si el primario no arrojó deuda ni denominación, intentar con el alternativo
            if datos_bcra and datos_bcra.get("sin_deuda") and not datos_bcra.get("denominacion"):
                datos_alt = self._consultar_cuit_directo(c2)
                if datos_alt and (not datos_alt.get("sin_deuda") or datos_alt.get("denominacion")):
                    datos_bcra = datos_alt
                    cuit_consultado = c2

        if not datos_bcra:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion=f"BCRA no respondió para identificador {limpio}",
                detalles={"error": "Sin respuesta de API BCRA", "identificador": limpio}
            )

        # Enriquecer detalles finales
        datos_bcra["dni"] = limpio if len(limpio) != 11 else str(int(limpio[2:10]))
        datos_bcra["origen"] = "bcra_api"

        denominacion = datos_bcra.get("denominacion") or ""
        peor_sit = datos_bcra.get("peor_situacion", 0)
        sin_deuda = datos_bcra.get("sin_deuda", True)
        deuda_pesos = datos_bcra.get("deuda_total_pesos", 0.0)
        deuda_macro = datos_bcra.get("deuda_macro_pesos", 0.0)

        titular = Titular(
            razon_social=denominacion,
            cuil=cuit_consultado or "",
            nro_documento=datos_bcra["dni"],
            tipo_documento="DNI"
        )

        desc_partes = [f"BCRA - CUIT {cuit_consultado}"]
        if denominacion:
            desc_partes.append(denominacion)
        if sin_deuda:
            desc_partes.append("Sin Deuda Registrada (Sit 0)")
        else:
            cant_ent = datos_bcra.get("cantidad_entidades", len(datos_bcra.get("entidades", [])))
            ops_cart = datos_bcra.get("operaciones_en_cartera", len(datos_bcra.get("entidades", [])))
            desc_partes.append(f"Peor Sit: {peor_sit} | {cant_ent} entidad(es) | {ops_cart} op(s) | Total: ${deuda_pesos:,.2f}")

        descripcion_line = " | ".join(desc_partes)

        return ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper=self.nombre,
            titular=titular,
            detalles=datos_bcra,
            raw=datos_bcra,
            descripcion=descripcion_line
        )
