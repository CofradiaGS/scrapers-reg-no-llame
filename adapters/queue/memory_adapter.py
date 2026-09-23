# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Repositorio en Memoria (Testing / Desarrollo Local)
Implementa IColaRepositorioPort completamente en memoria sin dependencias externas.
Permite ejecutar suites de pruebas unitarias en milisegundos.
"""
from typing import List, Dict, Any, Optional
from core.ports.queue_port import IColaRepositorioPort
from core.domain.entities import Linea, RegistroCola, Prioridad, EstadoRegistro, ReglaPipeline

class MemoryQueueAdapter(IColaRepositorioPort):
    def __init__(self, initial_records: Optional[List[Dict[str, Any]]] = None):
        self.records: Dict[int, Dict[str, Any]] = {}
        if initial_records:
            for r in initial_records:
                self.records[r["id"]] = r

    def reservar_lote(
        self, 
        batch_size: int = 15, 
        prioridad: Optional[int] = None, 
        scraper_nombre: str = "iris",
        solo_sin_coincidencia: bool = False
    ) -> List[RegistroCola]:
        candidatos = []
        for r in self.records.values():
            if r.get("estado") != "pendiente":
                continue
            if r.get("scraper_actual") == scraper_nombre:
                if solo_sin_coincidencia:
                    raw_json = r.get("datos_json", {})
                    if isinstance(raw_json, str):
                        import json
                        try:
                            raw_json = json.loads(raw_json)
                        except Exception:
                            raw_json = {}
                    apto, _ = ReglaPipeline.es_elegible_para_scraper(
                        scraper_nombre=scraper_nombre,
                        ani=str(r.get("ani", "")),
                        dni=str(r.get("dni")) if r.get("dni") else None,
                        datos_json=raw_json,
                        dias_validez=7,
                        solo_sin_coincidencia=True
                    )
                    if not apto:
                        continue
                candidatos.append(r)

        if prioridad:
            candidatos = [r for r in candidatos if r.get("prioridad") == prioridad]

        lote_data = candidatos[:batch_size]
        resultado = []

        for r in lote_data:
            r["estado"] = "procesando"
            raw_json = r.get("datos_json", {})
            if isinstance(raw_json, str):
                import json
                try:
                    raw_json = json.loads(raw_json)
                except Exception:
                    raw_json = {}

            dni_val = str(r["dni"]) if r.get("dni") else None
            resultado.append(RegistroCola(
                id=r["id"],
                linea=Linea(ani=str(r["ani"]), dni=dni_val),
                prioridad=Prioridad.from_int(r.get("prioridad", 3)),
                estado=EstadoRegistro.PROCESANDO,
                scraper_actual=scraper_nombre,
                fuente=r.get("fuente"),
                datos_existentes=raw_json,
                dni=dni_val
            ))

        return resultado

    def persistir_resultados(self, resultados: List[Dict[str, Any]]) -> bool:
        for res in resultados:
            r_id = res["id"]
            if r_id in self.records:
                rec = self.records[r_id]
                rec["scraper_actual"] = res.get("scraper_actual", "finalizado")
                rec["estado"] = res.get("estado", "completado")
                rec["fuente"] = res.get("fuente")
                rec["datos_json"] = res.get("datos")
                if res.get("dni"):
                    rec["dni"] = res.get("dni")
        return True

    def revertir_a_pendiente(self, ids: List[int]) -> bool:
        for r_id in ids:
            if r_id in self.records:
                self.records[r_id]["estado"] = "pendiente"
        return True

    def liberar_huerfanos(self, minutos_inactividad: int = 15) -> int:
        count = 0
        for r in self.records.values():
            if r.get("estado") == "procesando":
                r["estado"] = "pendiente"
                count += 1
        return count

    def obtener_estadisticas(self) -> Dict[str, int]:
        stats = {}
        for r in self.records.values():
            e = r.get("estado", "desconocido")
            stats[e] = stats.get(e, 0) + 1
        return stats
