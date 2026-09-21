# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Scraper IRIS Movistar (Motor Playwright Chromium)
Implementa IScraperEnginePort usando automatización de navegador headless.
"""
from typing import Optional
import config
from core.ports.scraper_port import IScraperEnginePort
from core.domain.entities import Linea, ScrapeResult, Titular, Servicio
from core.domain.enums import StatusScraping
from core.domain.schedule import PoliticaHorarioComercial
from core.domain.exceptions import FueraDeHorarioComercialException
from adapters.scrapers.iris.iris_bot import IrisBot

class IrisBrowserAdapter(IScraperEnginePort):
    """Adaptador para Movistar IRIS mediante Chromium Headless (Playwright)."""

    def __init__(self, headless: bool = True, forzar_horario: bool = False, **kwargs):
        self._headless = headless
        self._bot: Optional[IrisBot] = None
        self._forzar_horario = forzar_horario
        self.worker_slot = kwargs.get("worker_slot")
        self._politica_horario = PoliticaHorarioComercial(
            activo=config.HORARIO_COMERCIAL_ACTIVO,
            hora_inicio_lv=config.HORARIO_COMERCIAL_INICIO_LV,
            hora_fin_lv=config.HORARIO_COMERCIAL_FIN_LV,
            hora_inicio_sab=config.HORARIO_COMERCIAL_INICIO_SAB,
            hora_fin_sab=config.HORARIO_COMERCIAL_FIN_SAB,
            timezone_name=config.HORARIO_COMERCIAL_TIMEZONE
        )

    def _validar_horario(self) -> None:
        if not self._forzar_horario and not self._politica_horario.esta_en_horario():
            estado = self._politica_horario.obtener_estado()
            raise FueraDeHorarioComercialException(
                f"Consulta rechazada: IRIS opera únicamente en horario comercial ({estado['descripcion']}). "
                "Para forzar la ejecución en pruebas use el flag 'forzar_horario=True' o '--forzar-horario'."
            )

    @property
    def nombre(self) -> str:
        return "iris"

    def iniciar(self) -> None:
        self._bot = IrisBot(headless=self._headless)
        self._bot.start()

    def autenticar(self) -> bool:
        self._validar_horario()
        if not self._bot:
            self.iniciar()
        return self._bot.login()

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        self._validar_horario()
        if not self._bot:
            self.iniciar()
            self.autenticar()

        datos = self._bot.consultar_linea(linea.ani)

        if not datos:
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion="Sin registros en IRIS / No posee Port Out",
                detalles={"mensaje": "Sin registros en IRIS / No posee Port Out"}
            )

        titular = Titular(
            nombre=datos.get("nombre", "").strip(),
            apellido=datos.get("apellido", "").strip(),
            razon_social=datos.get("razon_social", "").strip(),
            tipo_documento=datos.get("tipo_documento", "").strip(),
            nro_documento=datos.get("nro_documento", "").strip(),
            tipo_persona=datos.get("tipo_persona", "").strip(),
            telefono_contacto=datos.get("telefono_contacto", "").strip(),
            email=datos.get("email", "").strip()
        )

        servicio = Servicio(
            tecnologia=datos.get("tecnologia", "").strip(),
            producto=datos.get("producto", "").strip(),
            modalidad_factura=datos.get("modalidad_factura", "").strip()
        )

        fechas = {
            "fecha_operacion": datos.get("fecha_operacion", ""),
            "fecha_alta": datos.get("fecha_alta", ""),
            "fecha_estado": datos.get("fecha_estado", ""),
            "fvc_orig": datos.get("fecha_ventana_cambio_orig", ""),
            "fvc_aprobada": datos.get("fecha_ventana_cambio_aprobada", "")
        }

        detalles = {
            "nro_tramite_abd": datos.get("nro_tramite_abd", ""),
            "id_tramite_spn": datos.get("id_tramite_spn", ""),
            "sistema_origen": datos.get("sistema_origen", ""),
            "resultado_spn": datos.get("resultado_spn", ""),
            "sistema_comercial": datos.get("sistema_comercial", ""),
            "estado_tramite": datos.get("estado", ""),
            "error_spn": datos.get("error_spn", ""),
            "observaciones": datos.get("observaciones", ""),
            "cantidad_lineas_portadas": datos.get("cantidad_lineas_portadas", ""),
            "cantidad_lineas_revertidas": datos.get("cantidad_lineas_revertidas", ""),
            "estado_reversion": datos.get("estado_reversion", "")
        }

        nom_tit = f"{titular.nombre} {titular.apellido}".strip()
        desc = f"Port Out - Titular: {nom_tit} | Doc: {titular.nro_documento}"[:195]

        return ScrapeResult(
            ani=linea.ani,
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper=self.nombre,
            operador=datos.get("operador_receptor", "Movistar"),
            operador_receptor=datos.get("operador_receptor", ""),
            titular=titular,
            servicio=servicio,
            fechas=fechas,
            detalles=detalles,
            raw=datos,
            descripcion=desc
        )

    def verificar_salud(self) -> bool:
        return self._bot is not None

    def cerrar(self) -> None:
        if self._bot:
            self._bot.close()
            self._bot = None
