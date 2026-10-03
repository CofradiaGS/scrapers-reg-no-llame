# -*- coding: utf-8 -*-
"""
Cliente Oficial: Central de Deudores BCRA (Banco Central de la República Argentina)
Consume la API pública v1.0 sin captcha de Situación Crediticia.
"""
import logging
from typing import Dict, Any, Optional, List
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("BCRAClient")

class BCRAClient:
    BASE_URL = "https://api.bcra.gob.ar/centraldedeudores/v1.0/Deudas"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8"
        })

    def consultar_deuda(self, identificacion: str) -> Optional[Dict[str, Any]]:
        """
        Consulta deudas del sistema financiero por CUIT/CUIL (11 dígitos).
        Retorna estructura normalizada con peor situación, monto total y detalle por entidad bancaria.
        """
        cuit_limpio = "".join(filter(str.isdigit, str(identificacion)))
        if len(cuit_limpio) != 11:
            logger.debug(f"Identificación inválida para BCRA (debe tener 11 dígitos): '{identificacion}'")
            return None

        url = f"{self.BASE_URL}/{cuit_limpio}"
        try:
            resp = self.session.get(url, verify=False, timeout=self.timeout)
            if resp.status_code == 404:
                return {
                    "cuit": cuit_limpio,
                    "sin_deuda": True,
                    "entidades": [],
                    "peor_situacion": 0,
                    "deuda_total_miles": 0.0,
                    "deuda_macro_miles": 0.0,
                    "deuda_macro_situacion": None
                }
            if resp.status_code != 200:
                logger.warning(f"BCRA respondió HTTP {resp.status_code} para {cuit_limpio}: {resp.text[:150]}")
                return None

            data = resp.json()
            results = data.get("results", {})
            if not results:
                return {
                    "cuit": cuit_limpio,
                    "sin_deuda": True,
                    "entidades": [],
                    "peor_situacion": 0,
                    "deuda_total_miles": 0.0,
                    "deuda_macro_miles": 0.0,
                    "deuda_macro_situacion": None
                }

            periodos = results.get("periodos", [])
            if not periodos:
                return {
                    "cuit": cuit_limpio,
                    "denominacion": results.get("denominacion", ""),
                    "sin_deuda": True,
                    "entidades": [],
                    "peor_situacion": 0,
                    "deuda_total_miles": 0.0,
                    "deuda_macro_miles": 0.0,
                    "deuda_macro_situacion": None
                }

            # Tomar el período más reciente (primer elemento)
            ultimo_periodo = periodos[0]
            periodo_str = str(ultimo_periodo.get("periodo", ""))
            entidades_raw = ultimo_periodo.get("entidades", [])

            entidades_procesadas: List[Dict[str, Any]] = []
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
                    "periodo": periodo_str,
                    "dias_atraso": ent.get("diasAtrasoPago", 0),
                    "proceso_judicial": ent.get("procesoJud", False)
                })

            return {
                "cuit": cuit_limpio,
                "denominacion": results.get("denominacion", ""),
                "periodo": periodo_str,
                "entidades": entidades_procesadas,
                "peor_situacion": peor_situacion,
                "deuda_total_miles": round(deuda_total, 2),
                "deuda_macro_miles": round(deuda_macro_miles, 2),
                "deuda_macro_situacion": deuda_macro_situacion
            }

        except Exception as e:
            logger.error(f"Error consultando BCRA para CUIT {cuit_limpio}: {e}")
            return None
