# -*- coding: utf-8 -*-
"""
Pruebas de Integración y Formateo: ColaAutomatizacionAdapter
Verifica:
1. Conexión y consulta de estadísticas sobre `cola_automatizacion`.
2. Reclamo transaccional con FOR UPDATE SKIP LOCKED y reversión inmediata a 'pendiente'.
3. Formateo de salida enriquecido y compatible con los sistemas legados.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.queue.cola_automatizacion_adapter import ColaAutomatizacionAdapter
from core.domain.entities import Linea, ScrapeResult, Titular, Servicio
from core.domain.enums import StatusScraping


def test_formateo_resultado_coincidencia():
    adapter = ColaAutomatizacionAdapter(auto_id="iris_scraper")

    item_simulado = {
        "id": 12345,
        "ani": "2477500669",
        "scraper_actual": "iris",
        "status": "coincidencia",
        "descripcion": "Port Out - Titular: BUSTOS MARIA INES | Doc: 11020227",
        "datos": {
            "enacom": {
                "operador_origen": "Movistar",
                "operador_oficial": "TELEFONICA MOVILES ARGENTINA S.A.",
                "grupo_economico": "Telefónica Hispanoamérica (Movistar)",
                "tipo_linea": "Móvil / Celular",
                "es_celular": True,
                "soporta_whatsapp": True,
                "codigo_area": "2477",
                "bloque": "5006",
                "prefijo_completo": "247750",
                "localidad_origen": "Pergamino",
                "provincia_origen": "Buenos Aires",
                "formatos": {
                    "e164": "+5492477500669",
                    "whatsapp": "5492477500669",
                    "nacional_celular": "02477 15-500669"
                }
            },
            "iris": {
                "detalles": {
                    "total_operaciones": 3,
                    "registros": [
                        {
                            "operacion": "Altas",
                            "nro_tramite": "17799235732021",
                            "formulario": "PR - Alta de Linea T3",
                            "fecha_alta": "20/11/2021",
                            "estado": "Controlado - Autorizado",
                            "canal": "R50 - Arg intercom",
                            "producto": "Control",
                            "detalle": {
                                "nro_documento": "11020227",
                                "tipo_documento": "Documento Nacional Identidad",
                                "lineas_asociadas": ["2477500669"]
                            }
                        },
                        {
                            "operacion": "Cambios",
                            "nro_tramite": "24739081762024",
                            "formulario": "CH - Cambios T3",
                            "fecha_alta": "13/6/2024",
                            "estado": "Controlado - Autorizado",
                            "canal": "R50 - Arg intercom",
                            "producto": "Full",
                            "detalle": {
                                "nro_documento": "11020227",
                                "tipo_documento": "Documento Nacional Identidad",
                                "lineas_asociadas": ["2477500669"]
                            }
                        },
                        {
                            "operacion": "Port Out",
                            "nro_tramite": "31410855",
                            "formulario": "PS - Solicitud PortOut",
                            "fecha_alta": "7/8/2025",
                            "estado": "Aprobación del ABD",
                            "canal": "",
                            "producto": "",
                            "detalle": {
                                "nombre": "MARIA INES",
                                "apellido": "BUSTOS",
                                "nro_documento": "11020227",
                                "tipo_documento": "Documento Nacional Identidad",
                                "operador_receptor": "Claro",
                                "nro_tramite_abd": "202508071042175557000",
                                "lineas_asociadas": ["2477500669"]
                            }
                        }
                    ]
                }
            }
        }
    }

    res = adapter._formatear_resultado(item_simulado)

    # 1. Comprobar que no contenga _version, iris_v2, status ni timestamp en la raíz
    assert "_version" not in res
    assert "iris_v2" not in res
    assert "status" not in res
    assert "timestamp" not in res

    # 2. Comprobar bloque ENACOM
    assert "enacom" in res
    assert res["enacom"]["operador_origen"] == "Movistar"
    assert res["enacom"]["es_celular"] is True
    assert res["enacom"]["formatos"]["whatsapp"] == "5492477500669"

    # 3. Comprobar bloque IRIS con operaciones agrupadas por tipo
    assert "iris" in res
    iris = res["iris"]
    assert "altas" in iris
    assert "cambios" in iris
    assert "port_out" in iris

    assert "ultima_modificacion" in iris
    assert len(iris["altas"]) == 1
    assert "ultima_modificacion" in iris["altas"]["altas"]
    assert iris["altas"]["altas"]["nro_tramite"] == "17799235732021"
    assert iris["altas"]["altas"]["formulario"] == "PR - Alta de Linea T3"
    assert iris["altas"]["altas"]["nro_documento"] == "11020227"

    assert len(iris["cambios"]) == 1
    assert iris["cambios"]["cambios"]["nro_tramite"] == "24739081762024"
    assert iris["cambios"]["cambios"]["producto"] == "Full"

    assert len(iris["port_out"]) == 1
    assert iris["port_out"]["port_out"]["nro_tramite_abd"] == "202508071042175557000"
    assert iris["port_out"]["port_out"]["titular"] == "MARIA INES BUSTOS"
    assert iris["port_out"]["port_out"]["operador_receptor"] == "Claro"



def test_formateo_resultado_sin_coincidencia():
    adapter = ColaAutomatizacionAdapter(auto_id="iris_scraper")

    item_simulado = {
        "id": 12345,
        "ani": "1122334455",
        "scraper_actual": "iris",
        "status": "sin_coincidencia",
        "descripcion": "Sin registros en IRIS / No posee Port Out",
        "datos": {
            "enacom": {
                "operador_origen": "Movistar",
                "tipo_linea": "Móvil / Celular",
                "es_celular": True
            },
            "iris": {
                "status": "sin_coincidencia",
                "fuente": "iris",
                "operador": "",
                "operador_receptor": "",
                "titular": {},
                "servicio": {},
                "fechas": {},
                "detalles": {
                    "mensaje": "Sin registros en IRIS / No posee Port Out"
                },
                "raw": {},
                "ultima_modificacion": "2026-09-22 15:00:00"
            }
        }
    }

    res = adapter._formatear_resultado(item_simulado)
    assert "_version" not in res
    assert "iris_v2" not in res
    assert "status" not in res
    assert "timestamp" not in res
    assert "enacom" in res
    assert res["enacom"]["operador_origen"] == "Movistar"
    assert "iris" in res
    assert "Sin registros en IRIS" in res["iris"]["message"]


def test_separacion_responsabilidades_error_vs_completado():
    """
    Verifica que en caso de error, el error se aísle en error_msg y resultado sea None (NULL en BD),
    mientras que en caso de éxito, error_msg sea None y resultado contenga el JSON enriquecido.
    """
    from unittest.mock import MagicMock

    adapter = ColaAutomatizacionAdapter(auto_id="iris_scraper")

    item_error = {
        "id": 99999,
        "ani": "1199887766",
        "scraper_actual": "iris",
        "status": "error",
        "descripcion": "[HTTPError:500] Error interno del servidor en IRIS BPM",
        "error_codigo": "500",
        "error_tipo": "HTTPError"
    }

    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    adapter._get_connection = MagicMock(return_value=mock_conn)

    adapter.persistir_resultados([item_error])

    if mock_cursor.executemany.called:
        args = mock_cursor.executemany.call_args_list[0][0]
        query, params_list = args[0], args[1]
        params = params_list[0]
    else:
        assert mock_cursor.execute.called
        args = mock_cursor.execute.call_args_list[0][0]
        query, params = args[0], args[1]

    estado_final, res_json, error_msg = params[0], params[1], params[2]
    assert estado_final == "fallido"
    assert res_json is None, "En caso de fallo, 'resultado' debe ser NULL en BD"
    assert error_msg == "[HTTPError:500] Error interno del servidor en IRIS BPM"


def test_conexion_y_estadisticas_cola_automatizacion():
    adapter = ColaAutomatizacionAdapter(auto_id="iris_scraper")
    stats = adapter.obtener_estadisticas()
    assert isinstance(stats, dict)
    assert "pendiente" in stats
    assert stats["pendiente"] > 0
    print(f"Estadísticas de cola_automatizacion para iris_scraper: {stats}")


def test_reserva_y_reversion_segura_cola_automatizacion():
    adapter = ColaAutomatizacionAdapter(auto_id="iris_scraper", pc_id="PC-00")
    lote = adapter.reservar_lote(batch_size=1)

    if not lote:
        print("No hay tareas pendientes en cola_automatizacion para reservar en prueba. Saltando.")
        return

    assert len(lote) == 1
    reg = lote[0]

    try:
        assert reg.id > 0
        assert len(reg.linea.ani) == 10
        assert reg.linea.ani.isdigit()
        print(f"Tarea reservada ID={reg.id}, ANI={reg.linea.ani}, DNI={reg.linea.dni}")
    finally:
        # SIEMPRE revertir inmediatamente la tarea a 'pendiente' para no afectar producción
        ok = adapter.revertir_a_pendiente([reg.id])
        assert ok is True
        print(f"Tarea ID={reg.id} revertida con éxito a estado 'pendiente'.")


if __name__ == "__main__":
    print("--- [TEST 1] Formateo con Coincidencia ---")
    test_formateo_resultado_coincidencia()
    print("  [OK] Test 1 superado.")

    print("\n--- [TEST 2] Formateo sin Coincidencia ---")
    test_formateo_resultado_sin_coincidencia()
    print("  [OK] Test 2 superado.")

    print("\n--- [TEST 3] Separación Responsabilidades (Error vs Completado) ---")
    test_separacion_responsabilidades_error_vs_completado()
    print("  [OK] Test 3 superado.")

    print("\n--- [TEST 4] Conexión y Estadísticas ---")
    test_conexion_y_estadisticas_cola_automatizacion()
    print("  [OK] Test 4 superado.")

    print("\n--- [TEST 5] Reserva y Reversión Atómica ---")
    test_reserva_y_reversion_segura_cola_automatizacion()
    print("  [OK] Test 5 superado.")

    print("\n" + "=" * 60)
    print(" TODOS LOS TESTS DE COLA AUTOMATIZACION PASARON CON ÉXITO")
    print("=" * 60)

