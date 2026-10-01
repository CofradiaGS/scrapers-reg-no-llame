# -*- coding: utf-8 -*-
"""
Adaptador Secundario (Driven Adapter): Scraper Telcos en Cascada con Cortocircuito
Arquitectura Hexagonal - Implementa IScraperEnginePort

Ejecuta el flujo de extracción en cascada con cortocircuito:
1. Claro: si la línea posee DNI. Si da COINCIDENCIA ➔ corta y finaliza.
2. Personal: si Claro dio sin coincidencia (o no había DNI). Si da COINCIDENCIA ➔ corta y finaliza.
3. Movistar: si Personal dio sin coincidencia. Si da COINCIDENCIA ➔ corta y finaliza.
4. Sin coincidencias: si ninguna operadora halló deuda, retorna SIN_COINCIDENCIA
   consolidando los 3 resultados y el bloque oficial ENACOM.
"""
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

import config
from core.ports.scraper_port import IScraperEnginePort
from core.domain.entities import Linea, ScrapeResult, Titular, Servicio
from core.domain.enums import StatusScraping
from core.domain.exceptions import ScraperTransientError
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.scrapers.claro.claro_adapter import ClaroAdapter
from adapters.scrapers.personal.personal_adapter import PersonalAdapter
from adapters.scrapers.movistar.movistar_adapter import MovistarAdapter

logger = logging.getLogger("TelcoCascadeAdapter")


class TelcoCascadeAdapter(BaseScraperAdapter):
    """Adaptador compuesto de Telcos: Claro ➔ Personal ➔ Movistar con cortocircuito."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        use_tor: Optional[bool] = None,
        use_proxy_pool: Optional[bool] = None,
        worker_slot: Optional[int] = None,
        delay_min: Optional[float] = None,
        delay_max: Optional[float] = None,
        **kwargs
    ):
        self.worker_slot = worker_slot or kwargs.get("worker_slot")
        self.delay_min = delay_min or 0.1
        self.delay_max = delay_max or 0.3

        adapter_kwargs = {
            "base_url": base_url,
            "use_tor": use_tor,
            "use_proxy_pool": use_proxy_pool,
            "worker_slot": self.worker_slot,
            "delay_min": self.delay_min,
            "delay_max": self.delay_max,
            **kwargs
        }

        self.claro = ClaroAdapter(**adapter_kwargs)
        self.personal = PersonalAdapter(**adapter_kwargs)
        self.movistar = MovistarAdapter(**adapter_kwargs)

    @property
    def nombre(self) -> str:
        return "telcos"

    def iniciar(self) -> None:
        """Inicia las sesiones de red de los 3 adaptadores."""
        self.claro.iniciar()
        self.personal.iniciar()
        self.movistar.iniciar()

    def autenticar(self) -> bool:
        """La pasarela Cobro Express es stateless; verifica sesiones activas."""
        return (
            self.claro.autenticar()
            and self.personal.autenticar()
            and self.movistar.autenticar()
        )

    def verificar_salud(self) -> bool:
        """Verifica salud de la pasarela Cobro Express a través de los adaptadores."""
        return self.personal.verificar_salud() or self.claro.verificar_salud()

    def cerrar(self) -> None:
        """Libera sesiones y descriptores de red."""
        self.claro.cerrar()
        self.personal.cerrar()
        self.movistar.cerrar()

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Ejecuta la cascada con cortocircuito:
        Claro (si DNI) ➔ Personal ➔ Movistar ➔ Sin coincidencias.
        Enriquece determinísticamente el bloque ENACOM y acumula la historia completa.
        """
        if not linea.es_valida:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                operador="Desconocido",
                descripcion=f"ANI inválido: '{linea.ani}' (debe tener 10 dígitos)"
            )

        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        datos_acumulados: Dict[str, Any] = {}
        scrapers_intentados: List[str] = []

        # ----------------------------------------------------------------------
        # PASO 1: CLARO (Solo si tiene DNI)
        # ----------------------------------------------------------------------
        dni_activo = (linea.dni or "").strip()
        if dni_activo:
            scrapers_intentados.append("claro")
            try:
                res_claro = self.claro.consultar_linea(linea)
                datos_acumulados["claro"] = res_claro.to_namespace_dict()["claro"]

                if res_claro.status == StatusScraping.COINCIDENCIA:
                    logger.info(f"🎯 [CORTOCIRCUITO CLARO] Coincidencia hallada para línea {linea.ani} (DNI: {dni_activo}).")
                    res_claro.detalles["scrapers_intentados"] = list(scrapers_intentados)
                    return res_claro
            except ScraperTransientError:
                raise
            except Exception as e_claro:
                logger.warning(f"Error en consulta Claro para {linea.ani}: {e_claro}")
                datos_acumulados["claro"] = {
                    "status": "error",
                    "fuente": "claro",
                    "operador": "Claro",
                    "detalles": {"error": str(e_claro)},
                    "ultima_modificacion": ahora_iso
                }
        else:
            # Línea sin DNI: se documenta omisión de Claro y avanza directo a Personal
            datos_acumulados["claro"] = {
                "status": "sin_coincidencia",
                "fuente": "claro",
                "operador": "Claro",
                "operador_receptor": "",
                "titular": {},
                "servicio": {},
                "fechas": {},
                "detalles": {
                    "motivo": "Omitido: Claro Cobro Express exige DNI disponible y la línea no posee DNI",
                    "ani_consultado": linea.ani
                },
                "raw": {},
                "ultima_modificacion": ahora_iso
            }

        # ----------------------------------------------------------------------
        # PASO 2: PERSONAL (Solo con ANI)
        # ----------------------------------------------------------------------
        scrapers_intentados.append("personal")
        try:
            res_personal = self.personal.consultar_linea(linea)
            datos_acumulados["personal"] = res_personal.to_namespace_dict()["personal"]

            if res_personal.status == StatusScraping.COINCIDENCIA:
                logger.info(f"🎯 [CORTOCIRCUITO PERSONAL] Coincidencia hallada para línea {linea.ani}.")
                res_personal.detalles["scrapers_intentados"] = list(scrapers_intentados)
                res_personal.detalles["datos_acumulados"] = {"claro": dict(datos_acumulados.get("claro", {}))}
                return res_personal
        except ScraperTransientError:
            raise
        except Exception as e_pers:
            logger.warning(f"Error en consulta Personal para {linea.ani}: {e_pers}")
            datos_acumulados["personal"] = {
                "status": "error",
                "fuente": "personal",
                "operador": "Personal",
                "detalles": {"error": str(e_pers)},
                "ultima_modificacion": ahora_iso
            }

        # ----------------------------------------------------------------------
        # PASO 3: MOVISTAR (Área + Línea)
        # ----------------------------------------------------------------------
        scrapers_intentados.append("movistar")
        try:
            res_movistar = self.movistar.consultar_linea(linea)
            datos_acumulados["movistar"] = res_movistar.to_namespace_dict()["movistar"]

            if res_movistar.status == StatusScraping.COINCIDENCIA:
                logger.info(f"🎯 [COINCIDENCIA MOVISTAR] Coincidencia hallada para línea {linea.ani}.")
                res_movistar.detalles["scrapers_intentados"] = list(scrapers_intentados)
                res_movistar.detalles["datos_acumulados"] = {
                    "claro": dict(datos_acumulados.get("claro", {})),
                    "personal": dict(datos_acumulados.get("personal", {}))
                }
                return res_movistar
        except ScraperTransientError:
            raise
        except Exception as e_mov:
            logger.warning(f"Error en consulta Movistar para {linea.ani}: {e_mov}")
            datos_acumulados["movistar"] = {
                "status": "error",
                "fuente": "movistar",
                "operador": "Movistar",
                "detalles": {"error": str(e_mov)},
                "ultima_modificacion": ahora_iso
            }

        # ----------------------------------------------------------------------
        # PASO 4: SIN COINCIDENCIAS EN NINGUNA
        # ----------------------------------------------------------------------
        desc_final = "Sin coincidencias en Claro, Personal ni Movistar"
        detalles_finales = {
            "status": "sin_coincidencias",
            "operador": None,
            "descripcion": desc_final,
            "scrapers_intentados": scrapers_intentados,
            "datos_acumulados": datos_acumulados,
            "ultima_modificacion": ahora_iso
        }

        return ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.SIN_COINCIDENCIA,
            fuente_scraper=self.nombre,
            operador="Desconocido",
            operador_receptor="",
            titular=Titular(nro_documento=dni_activo) if dni_activo else None,
            servicio=None,
            fechas={"fecha_consulta": ahora_iso},
            detalles=detalles_finales,
            raw={
                "claro": datos_acumulados.get("claro", {}).get("raw", {}),
                "personal": datos_acumulados.get("personal", {}).get("raw", {}),
                "movistar": datos_acumulados.get("movistar", {}).get("raw", {})
            },
            descripcion=desc_final,
            ultima_modificacion=ahora_iso
        )
