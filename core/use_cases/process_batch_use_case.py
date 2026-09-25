# -*- coding: utf-8 -*-
"""
Caso de Uso de Aplicación: Procesar Lote de Scraping
Orquesta el ciclo de vida de un lote:
1. Reclama atómicamente registros desde el puerto de cola.
2. Ejecuta la consulta a través del puerto de scraper.
3. Aplica la Regla de Dominio del Pipeline (Cortocircuito o paso a la siguiente etapa).
4. Persiste los datos enriquecidos en la cola.
"""
import time
import json
import logging
from typing import Optional, List, Dict, Any, Callable
from core.ports.queue_port import IColaRepositorioPort
from core.ports.scraper_port import IScraperEnginePort
from core.ports.operator_lookup_port import IOperatorLookupPort
from core.domain.entities import ReglaPipeline, Linea, ScrapeResult
from core.domain.enums import StatusScraping
from core.domain.exceptions import FueraDeHorarioComercialException

logger = logging.getLogger("ProcesarLoteUseCase")

class ProcesarLoteUseCase:
    """Caso de uso orquestador de lotes de scraping desacoplado."""

    def __init__(
        self,
        cola_repo: IColaRepositorioPort,
        scraper_engine: IScraperEnginePort,
        cadena_pipeline: Optional[List[str]] = None,
        operator_lookup: Optional[IOperatorLookupPort] = None
    ):
        self.cola = cola_repo
        self.scraper = scraper_engine
        self.cadena = cadena_pipeline
        self.operator_lookup = operator_lookup

    @staticmethod
    def parsear_fuentes_previas(fuente_raw: Optional[str]) -> List[str]:
        """Parsea de forma segura cualquier formato de fuente (JSON array o string plano legado)."""
        if not fuente_raw:
            return []
        s = str(fuente_raw).strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                return [x.strip(" '\"") for x in s[1:-1].split(",") if x.strip(" '\"")]
        elif s not in ("None", "null", ""):
            return [s]
        return []

    @staticmethod
    def normalizar_fuente(fuente_raw: Optional[str], nuevo_scraper: str) -> str:
        """Enriquece acumulativamente el historial de fuentes asegurando JSON array válido."""
        lista = ProcesarLoteUseCase.parsear_fuentes_previas(fuente_raw)
        if nuevo_scraper not in lista:
            lista.append(nuevo_scraper)
        return json.dumps(lista, ensure_ascii=False)

    def ejecutar_lote(
        self,
        batch_size: int = 20,
        prioridad: Optional[int] = None,
        on_item_procesado: Optional[Callable[[Dict[str, Any]], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        solo_sin_coincidencia: bool = False
    ) -> tuple[int, List[int]]:
        """
        Ejecuta un ciclo de procesamiento de lote.
        Retorna: (cantidad_procesados, lista_ids_no_procesados_si_hubo_parada)
        """
        # 1. Reclamar lote a través del puerto de cola
        lote = self.cola.reservar_lote(
            batch_size=batch_size,
            prioridad=prioridad,
            scraper_nombre=self.scraper.nombre,
            solo_sin_coincidencia=solo_sin_coincidencia
        )

        if not lote:
            return 0, []

        resultados = []
        unprocessed_ids = [r.id for r in lote]
        is_ipc_streaming = getattr(self.cola, "is_ipc", False)

        def registrar_resultado(item: Dict[str, Any]):
            resultados.append(item)
            if item["id"] in unprocessed_ids:
                unprocessed_ids.remove(item["id"])
            if on_item_procesado:
                on_item_procesado(item)
            if is_ipc_streaming:
                self.cola.persistir_resultados([item])

        for reg in lote:
            if should_stop and should_stop():
                logger.info("Parada solicitada en mitad del lote. Interrumpiendo ciclo...")
                break

            if should_pause and should_pause():
                logger.info("Pausa operativa activa en mitad del lote (ej: horario comercial/VPN). Interrumpiendo ciclo y liberando registros...")
                break

            t0 = time.time()
            try:
                # 2. Consultar a través del puerto de scraper
                linea_consulta = Linea(ani=reg.linea.ani, dni=reg.linea.dni or getattr(reg, 'dni', None))

                # Parsear datos_json previos
                if isinstance(reg.datos_existentes, dict):
                    datos_previos = dict(reg.datos_existentes)
                else:
                    try:
                        datos_previos = json.loads(reg.datos_existentes)
                    except Exception:
                        datos_previos = {}

                # Enriquecimiento determinista de bloque oficial ENACOM si no existe previamente
                if self.operator_lookup and "enacom" not in datos_previos:
                    enacom_data = self.operator_lookup.consultar_bloque(reg.linea.ani)
                    if enacom_data:
                        datos_previos["enacom"] = enacom_data.to_dict()

                if not linea_consulta.dni and datos_previos:
                    # Propagar DNI desde etapas previas (ej: IRIS titular, Datuar o CuitOnline)
                    titular_iris = datos_previos.get("iris", {})
                    if isinstance(titular_iris, dict):
                        titular_info = titular_iris.get("titular", {})
                    else:
                        titular_info = {}
                    datos_datuar = datos_previos.get("datuar", {})
                    if not isinstance(datos_datuar, dict):
                        datos_datuar = {}
                    datos_cuitonline = datos_previos.get("cuitonline", {})
                    if not isinstance(datos_cuitonline, dict):
                        datos_cuitonline = {}

                    dni_previo = (
                        datos_datuar.get("detalles", {}).get("dni")
                        or datos_datuar.get("dni")
                        or datos_cuitonline.get("detalles", {}).get("dni")
                        or datos_cuitonline.get("dni")
                        or (titular_info.get("nro_documento") if isinstance(titular_info, dict) else None)
                        or (titular_info.get("dni") if isinstance(titular_info, dict) else None)
                        or datos_previos.get("dni")
                    )
                    if dni_previo:
                        linea_consulta = Linea(ani=reg.linea.ani, dni=str(dni_previo))

                # 2. Pre-flight de Dominio: Validar elegibilidad antes de consultar la red
                es_apto, motivo = ReglaPipeline.es_elegible_para_scraper(
                    scraper_nombre=self.scraper.nombre,
                    ani=linea_consulta.ani,
                    dni=linea_consulta.dni,
                    datos_json=datos_previos,
                    solo_sin_coincidencia=solo_sin_coincidencia
                )

                if not es_apto:
                    lat = round(time.time() - t0, 3)
                    dni_activo = bool(linea_consulta.dni)
                    fuentes_previas_list = self.parsear_fuentes_previas(reg.fuente)

                    coincidencia_telco_previa = any(
                        isinstance(datos_previos.get(t), dict) and datos_previos.get(t, {}).get("status") == "coincidencia"
                        for t in ReglaPipeline.TELCOS
                    )

                    resultado_salteado = ScrapeResult(
                        ani=linea_consulta.ani,
                        status=StatusScraping.SIN_COINCIDENCIA,
                        fuente_scraper=self.scraper.nombre,
                        descripcion=f"Salteado por Dominio: {motivo}",
                        detalles={"motivo_salteado": motivo}
                    )

                    sig_scraper, sig_estado = ReglaPipeline.resolver_siguiente_etapa(
                        scraper_actual=self.scraper.nombre,
                        resultado=resultado_salteado,
                        cadena=self.cadena,
                        dni_disponible=dni_activo,
                        fuentes_previas=fuentes_previas_list,
                        coincidencia_telco_previa=coincidencia_telco_previa,
                        datos_previos=datos_previos
                    )

                    item_res = {
                        "id": reg.id,
                        "ani": reg.linea.ani,
                        "dni": linea_consulta.dni,
                        "scraper_actual": sig_scraper,
                        "estado": sig_estado,
                        "descripcion": f"Salteado: {motivo}"[:195],
                        "fuente": reg.fuente or "[]",
                        "datos": datos_previos,
                        "latencia": lat,
                        "status": "salteado"
                    }
                    registrar_resultado(item_res)
                    continue

                resultado = self.scraper.consultar_linea(linea_consulta)
                lat = round(time.time() - t0, 2)

                # Extraer DNI resultante para persistencia en columna relacional
                dni_res = (
                    linea_consulta.dni
                    or (resultado.titular.nro_documento if resultado.titular and resultado.titular.nro_documento else None)
                    or (resultado.detalles.get("dni") if isinstance(resultado.detalles, dict) else None)
                    or (resultado.detalles.get("titular", {}).get("nro_documento") if isinstance(resultado.detalles, dict) else None)
                )

                # 3. Aplicar Regla de Dominio: Cortocircuito y Siguiente Posta
                dni_activo = bool(dni_res)
                fuentes_previas_list = self.parsear_fuentes_previas(reg.fuente)

                coincidencia_telco_previa = any(
                    isinstance(datos_previos.get(t), dict) and datos_previos.get(t, {}).get("status") == "coincidencia"
                    for t in ReglaPipeline.TELCOS
                )

                sig_scraper, sig_estado = ReglaPipeline.resolver_siguiente_etapa(
                    scraper_actual=self.scraper.nombre,
                    resultado=resultado,
                    cadena=self.cadena,
                    dni_disponible=dni_activo,
                    fuentes_previas=fuentes_previas_list,
                    coincidencia_telco_previa=coincidencia_telco_previa,
                    datos_previos=datos_previos
                )

                # Construir historial de fuentes acumulativo garantizando JSON array válido
                fuente_norm = self.normalizar_fuente(reg.fuente, self.scraper.nombre)

                # Fusión acumulativa de datos_json preservando scrapers previos
                datos_acumulados = dict(datos_previos)
                datos_acumulados.update(resultado.to_namespace_dict())

                item_res = {
                    "id": reg.id,
                    "ani": reg.linea.ani,
                    "dni": dni_res,
                    "scraper_actual": sig_scraper,
                    "estado": sig_estado,
                    "descripcion": resultado.descripcion or f"Scrapeado por {self.scraper.nombre}",
                    "fuente": fuente_norm,
                    "datos": datos_acumulados,
                    "latencia": lat,
                    "status": resultado.status.value
                }
                registrar_resultado(item_res)

            except FueraDeHorarioComercialException as e_hc:
                logger.warning(
                    f"⏸️ Horario comercial cerrado durante el procesamiento (Línea {reg.linea.ani}). "
                    f"Interrumpiendo lote de inmediato. {len(unprocessed_ids)} registros restantes "
                    f"serán liberados intactos a estado 'pendiente'."
                )
                # No se remueve reg.id de unprocessed_ids para que sea devuelto intacto a pendiente
                break

            except Exception as e:
                lat = round(time.time() - t0, 2)
                err_type = type(e).__name__
                err_msg = str(e).replace("\n", " ").strip()
                err_code = getattr(e, "code", None) or getattr(e, "status_code", None) or getattr(e, "errno", None)
                if not err_code and hasattr(e, "response") and getattr(e.response, "status_code", None):
                    err_code = e.response.status_code

                codigo_fmt = f"[{err_type}:{err_code}]" if err_code else f"[{err_type}]"
                descripcion_error = f"{codigo_fmt} {err_msg}".strip()
                logger.error(f"Error procesando línea {reg.linea.ani}: {descripcion_error}")

                item_err = {
                    "id": reg.id,
                    "ani": reg.linea.ani,
                    "scraper_actual": self.scraper.nombre,
                    "estado": "error",
                    "descripcion": descripcion_error[:250],
                    "error_codigo": str(err_code) if err_code else err_type,
                    "error_tipo": err_type,
                    "error_detalle": err_msg,
                    "fuente": reg.fuente or f'["{self.scraper.nombre}"]',
                    "datos": datos_previos if datos_previos else reg.datos_existentes,
                    "latencia": lat,
                    "status": "error"
                }
                registrar_resultado(item_err)

        # 4. Persistir lote completado a través del puerto de cola (solo si no se transmitió por streaming IPC)
        if resultados and not is_ipc_streaming:
            self.cola.persistir_resultados(resultados)

        # 5. Si quedaron registros sin procesar por interrupción, devolverlos a pendiente
        if unprocessed_ids:
            self.cola.revertir_a_pendiente(unprocessed_ids)

        return len(resultados), unprocessed_ids
