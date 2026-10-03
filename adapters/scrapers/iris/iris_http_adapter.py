# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Scraper IRIS Movistar (Motor HTTP Ultrarrápido)
Implementa IScraperEnginePort interactuando directamente con el motor Fuego/WebLogic de Oracle BPM.
"""
from typing import Optional
import config
from core.ports.scraper_port import IScraperEnginePort
from core.domain.entities import Linea, ScrapeResult, Titular, Servicio
from core.domain.enums import StatusScraping
from core.domain.schedule import PoliticaHorarioComercial
from core.domain.exceptions import FueraDeHorarioComercialException
from adapters.scrapers.iris.iris_http_bot import IrisHttpBot

class IrisHttpAdapter(IScraperEnginePort):
    """Adaptador de producción para Movistar IRIS mediante cliente HTTP asíncrono."""

    def __init__(self, forzar_horario: bool = False, **kwargs):
        self._bot: Optional[IrisHttpBot] = None
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
        self._bot = IrisHttpBot()
        self._bot.start()

    def autenticar(self) -> bool:
        self._validar_horario()
        if not self._bot:
            self.iniciar()
        return self._bot.login()

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        if not linea.es_valida:
            if linea.dni and len("".join(filter(str.isdigit, str(linea.dni)))) >= 6:
                return self.consultar_por_dni(linea.dni)
            return ScrapeResult(
                ani=linea.ani,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion=f"ANI telefónico inválido: '{linea.ani}' (debe tener 10 dígitos)"
            )

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
                descripcion="Sin registros en IRIS",
                detalles={"mensaje": "Sin registros en IRIS"}
            )

        tit_val = datos.get("titular")
        if isinstance(tit_val, dict):
            nom = (tit_val.get("nombre") or datos.get("nombre") or "").strip()
            ape = (tit_val.get("apellido") or datos.get("apellido") or "").strip()
            tipo_doc = (tit_val.get("tipo_documento") or datos.get("tipo_documento") or "DNI").strip()
            nro_doc = (tit_val.get("nro_documento") or datos.get("nro_documento") or "").strip()
            tipo_per = (tit_val.get("tipo_persona") or datos.get("tipo_persona") or "").strip()
            tel_con = (tit_val.get("telefono_contacto") or datos.get("telefono_contacto") or "").strip()
            mail_con = (tit_val.get("email") or datos.get("email") or "").strip()
        elif isinstance(tit_val, str):
            nom = (datos.get("nombre") or tit_val).strip()
            ape = (datos.get("apellido") or "").strip()
            tipo_doc = (datos.get("tipo_documento") or "DNI").strip()
            nro_doc = (datos.get("nro_documento") or "").strip()
            tipo_per = (datos.get("tipo_persona") or "").strip()
            tel_con = (datos.get("telefono_contacto") or "").strip()
            mail_con = (datos.get("email") or "").strip()
        else:
            nom = (datos.get("nombre") or "").strip()
            ape = (datos.get("apellido") or "").strip()
            tipo_doc = (datos.get("tipo_documento") or "DNI").strip()
            nro_doc = (datos.get("nro_documento") or "").strip()
            tipo_per = (datos.get("tipo_persona") or "").strip()
            tel_con = (datos.get("telefono_contacto") or "").strip()
            mail_con = (datos.get("email") or "").strip()

        titular = Titular(
            nombre=nom,
            apellido=ape,
            razon_social=(datos.get("razon_social") or "").strip(),
            tipo_documento=tipo_doc,
            nro_documento=nro_doc,
            tipo_persona=tipo_per,
            telefono_contacto=tel_con,
            email=mail_con
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

        registros = datos.get("registros", [])
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
            "estado_reversion": datos.get("estado_reversion", ""),
            "total_operaciones": len(registros),
            "registros": registros
        }

        nom_tit = f"{titular.nombre} {titular.apellido}".strip()
        ops_nombres = ", ".join(list(dict.fromkeys(r.get("operacion", "") for r in registros if r.get("operacion"))))
        if nom_tit or titular.nro_documento:
            desc = f"IRIS ({len(registros)} ops: {ops_nombres}) - Titular: {nom_tit} | Doc: {titular.nro_documento}"[:195]
        else:
            desc = f"IRIS ({len(registros)} ops: {ops_nombres}) - Sin datos de titular"[:195]

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

    def consultar_por_dni(self, dni: str) -> ScrapeResult:
        """
        Consulta IRIS a partir del número de documento mediante att$nroIdentificacion.
        Extrae exhaustivamente todas las operaciones históricas (lupas) y acumula
        todas las líneas telefónicas descubiertas para el DNI.
        """
        self._validar_horario()
        if not self._bot:
            self.iniciar()
            self.autenticar()

        dni_limpio = "".join(filter(str.isdigit, str(dni)))
        datos = self._bot.consultar_dni(dni_limpio)

        if not datos or not datos.get("total_operaciones"):
            return ScrapeResult(
                ani="",
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper=self.nombre,
                descripcion=f"Sin registros en IRIS para DNI {dni_limpio}",
                detalles={"mensaje": "Sin registros en IRIS", "dni": dni_limpio}
            )

        titular_raw = datos.get("titular")
        if isinstance(titular_raw, dict):
            nom = (titular_raw.get("nombre") or datos.get("nombre") or "").strip()
            ape = (titular_raw.get("apellido") or datos.get("apellido") or "").strip()
            tipo_doc = (titular_raw.get("tipo_documento") or datos.get("tipo_documento") or "DNI").strip()
            tel_con = (titular_raw.get("telefono_contacto") or datos.get("telefono_contacto") or "").strip()
            mail_con = (titular_raw.get("email") or datos.get("email") or "").strip()
        elif isinstance(titular_raw, str):
            nom = (datos.get("nombre") or titular_raw).strip()
            ape = (datos.get("apellido") or "").strip()
            tipo_doc = (datos.get("tipo_documento") or "DNI").strip()
            tel_con = (datos.get("telefono_contacto") or "").strip()
            mail_con = (datos.get("email") or "").strip()
        else:
            nom = (datos.get("nombre") or "").strip()
            ape = (datos.get("apellido") or "").strip()
            tipo_doc = (datos.get("tipo_documento") or "DNI").strip()
            tel_con = (datos.get("telefono_contacto") or "").strip()
            mail_con = (datos.get("email") or "").strip()

        titular = Titular(
            nombre=nom,
            apellido=ape,
            razon_social=(datos.get("razon_social") or "").strip(),
            tipo_documento=tipo_doc,
            nro_documento=dni_limpio,
            tipo_persona=(datos.get("tipo_persona") or "").strip(),
            telefono_contacto=tel_con,
            email=mail_con
        )

        servicio = Servicio(
            tecnologia=(datos.get("tecnologia") or "").strip(),
            producto=(datos.get("producto") or "").strip(),
            modalidad_factura=(datos.get("modalidad_factura") or "").strip()
        )

        fechas = {
            "fecha_operacion": datos.get("fecha_operacion", ""),
            "fecha_alta": datos.get("fecha_alta", ""),
            "fecha_estado": datos.get("fecha_estado", ""),
            "fvc_orig": datos.get("fecha_ventana_cambio_orig", ""),
            "fvc_aprobada": datos.get("fecha_ventana_cambio_aprobada", "")
        }

        lineas = datos.get("lineas", [])
        ani_principal = lineas[0] if lineas else ""

        detalles = {
            "dni": dni_limpio,
            "lineas": lineas,
            "lineas_asociadas": lineas,
            "lineas_descubiertas": lineas,
            "total_lineas": len(lineas),
            "nro_tramite_abd": datos.get("nro_tramite_abd", ""),
            "id_tramite_spn": datos.get("id_tramite_spn", ""),
            "sistema_origen": datos.get("sistema_origen", ""),
            "resultado_spn": datos.get("resultado_spn", ""),
            "sistema_comercial": datos.get("sistema_comercial", ""),
            "estado_tramite": datos.get("estado", ""),
            "operador_receptor": datos.get("operador_receptor", ""),
            "error_spn": datos.get("error_spn", ""),
            "total_registros_historicos": datos.get("total_operaciones", 0),
            "registros_historicos": datos.get("registros", []),
            "operaciones": datos.get("registros", [])
        }

        nom_tit = f"{titular.nombre} {titular.apellido}".strip()
        desc = f"IRIS DNI {dni_limpio} - {len(lineas)} líneas descubiertas en {datos.get('total_operaciones', 0)} ops | Titular: {nom_tit}"[:195]

        return ScrapeResult(
            ani=ani_principal,
            status=StatusScraping.COINCIDENCIA,
            fuente_scraper=self.nombre,
            operador="Movistar",
            operador_receptor=datos.get("operador_receptor", "").strip(),
            titular=titular,
            servicio=servicio,
            fechas=fechas,
            detalles=detalles,
            raw=datos,
            descripcion=desc
        )

    def verificar_salud(self) -> bool:
        if self._bot:
            return self._bot.check_health()
        return False

    def cerrar(self) -> None:
        if self._bot:
            self._bot.close()
            self._bot = None
