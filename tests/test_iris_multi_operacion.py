#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests unitarios para la funcionalidad multi-operación y extracción sin filtro de IRIS HTTP.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from bs4 import BeautifulSoup
from adapters.scrapers.iris.parser import parse_iris_detail
from adapters.scrapers.iris.iris_http_adapter import IrisHttpAdapter
from adapters.queue.cola_automatizacion_adapter import ColaAutomatizacionAdapter
from core.domain.entities import Linea, ScrapeResult, Titular, Servicio
from core.domain.enums import StatusScraping

class TestIrisMultiOperacion(unittest.TestCase):

    def test_parser_fallback_form_solicitud(self):
        """Verifica que parse_iris_detail extraiga DNI de formularios tipo FormSolicitud (Altas/Cambios)."""
        html_dummy = """
        <html>
            <body>
                <table id="table10_comp">
                    <tr>
                        <td>Tipo de Documento</td>
                        <td>Documento Nacional Identidad</td>
                        <td>Nro de Documento</td>
                        <td>33445566</td>
                    </tr>
                </table>
            </body>
        </html>
        """
        parsed = parse_iris_detail(html_dummy)
        self.assertEqual(parsed.get("nro_documento"), "33445566")
        self.assertEqual(parsed.get("tipo_documento"), "Documento Nacional Identidad")

    def test_iris_adapter_coincidencia_con_registros(self):
        """Verifica que IrisHttpAdapter mapee registros múltiples y status COINCIDENCIA."""
        adapter = IrisHttpAdapter(forzar_horario=True)
        # Mock de bot que retorna datos con múltiples operaciones
        class MockBot:
            def consultar_linea(self, ani):
                return {
                    "nombre": "JUAN",
                    "apellido": "PEREZ",
                    "nro_documento": "20123456",
                    "tipo_documento": "Documento Nacional Identidad",
                    "operador_receptor": "Claro",
                    "total_operaciones": 2,
                    "registros": [
                        {"operacion": "Altas", "fecha_alta": "01/01/2021", "estado": "Autorizado"},
                        {"operacion": "Port Out", "fecha_alta": "10/05/2024", "estado": "Aprobado"}
                    ]
                }
        adapter._bot = MockBot()

        res = adapter.consultar_linea(Linea(ani="1122334455"))
        self.assertEqual(res.status, StatusScraping.COINCIDENCIA)
        self.assertEqual(res.detalles.get("total_operaciones"), 2)
        self.assertEqual(len(res.detalles.get("registros", [])), 2)
        self.assertIn("JUAN PEREZ", res.descripcion)
        self.assertIn("20123456", res.descripcion)

    def test_cola_adapter_formateo_registros(self):
        """Verifica que ColaAutomatizacionAdapter estructure iris con sus grupos (altas, port_out) directamente."""
        adapter = ColaAutomatizacionAdapter(auto_id="iris_scraper")
        item = {
            "ani": "1122334455",
            "status": "coincidencia",
            "datos": {
                "enacom": {"operador": "Movistar", "modalidad": "Movil"},
                "iris": {
                    "detalles": {
                        "total_operaciones": 2,
                        "registros": [
                            {"operacion": "Altas", "fecha_alta": "01/01/2021", "nro_tramite": "1001"},
                            {"operacion": "Port Out", "fecha_alta": "10/05/2024", "nro_tramite": "2002"}
                        ]
                    },
                    "raw": {
                        "registros": [
                            {"operacion": "Altas", "fecha_alta": "01/01/2021", "nro_tramite": "1001"},
                            {"operacion": "Port Out", "fecha_alta": "10/05/2024", "nro_tramite": "2002"}
                        ]
                    }
                }
            }
        }
        res_json = adapter._formatear_resultado(item)
        self.assertNotIn("_version", res_json)
        self.assertNotIn("iris_v2", res_json)
        self.assertIn("enacom", res_json)
        self.assertIn("iris", res_json)
        self.assertIn("altas", res_json["iris"])
        self.assertIn("port_out", res_json["iris"])
        self.assertEqual(len(res_json["iris"]["altas"]), 1)
        self.assertEqual(len(res_json["iris"]["port_out"]), 1)
        self.assertEqual(res_json["iris"]["altas"]["altas"]["nro_tramite"], "1001")
        self.assertEqual(res_json["iris"]["port_out"]["port_out"]["nro_tramite"], "2002")

    def test_extraccion_exhaustiva_todos_los_campos(self):
        """Verifica que el parser y el adaptador capturen todos los campos sin omitir ninguno."""
        fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "iris"
        html_altas = (fixtures_dir / "detalle_altas.html").read_text(encoding="utf-8")
        html_cambios = (fixtures_dir / "detalle_cambios.html").read_text(encoding="utf-8")
        html_port_out = (fixtures_dir / "detalle_port_out.html").read_text(encoding="utf-8")

        # 1. Altas
        det_altas = parse_iris_detail(html_altas)
        self.assertEqual(det_altas.get("canal_agente"), "R50 - Arg intercom")
        self.assertEqual(det_altas.get("punto_de_venta"), "06 - San Nicolas 448")
        self.assertEqual(det_altas.get("vendedor"), "82550")
        self.assertEqual(det_altas.get("subvendedor"), "006001")
        self.assertEqual(det_altas.get("usuario"), "SDSDIGITAL")
        self.assertEqual(det_altas.get("tipo_formulario"), "PR - Alta de Linea T3")
        self.assertEqual(det_altas.get("nro_formulario"), "17799235732021")
        self.assertEqual(det_altas.get("fecha_operacion"), "20/11/2021 12:26:00")
        self.assertEqual(det_altas.get("fecha_alta"), "20/11/2021")
        self.assertEqual(det_altas.get("estado"), "Controlado - Autorizado")
        self.assertEqual(det_altas.get("fecha_estado"), "23/11/2021 23:55:00")
        self.assertEqual(det_altas.get("excepcion"), "NO")
        self.assertEqual(det_altas.get("tipo_documento"), "Documento Nacional Identidad")
        self.assertEqual(det_altas.get("nro_documento"), "11020227")
        self.assertEqual(det_altas.get("lineas_multiples"), "NO")
        self.assertEqual(det_altas.get("solicitud_multiple"), "NO")
        self.assertIn("2477500669", det_altas.get("lineas", []))
        self.assertEqual(det_altas.get("cliente_existente"), "NO")
        self.assertEqual(det_altas.get("pricing_diferencial"), "NO")
        self.assertEqual(det_altas.get("modalidad_entrega"), "DIFERIDA")
        self.assertEqual(det_altas.get("tipo_operacion"), "Altas")
        self.assertEqual(det_altas.get("forma_contratacion"), "Venta Nuevo")
        self.assertEqual(det_altas.get("tipo_producto"), "Control")
        self.assertEqual(det_altas.get("segmento"), "Individuos")
        self.assertIn("Solicitud de Servicio de Altas Iniciales", det_altas.get("documentos", []))
        self.assertEqual(det_altas.get("qr"), "QR no encontrado")
        self.assertEqual(det_altas.get("fecha_recepcion"), "23/11/2021 23:55:00")

        # 2. Cambios
        det_cambios = parse_iris_detail(html_cambios)
        self.assertEqual(det_cambios.get("tipo_formulario"), "CH - Cambios T3")
        self.assertEqual(det_cambios.get("nro_formulario"), "24739081762024")
        self.assertEqual(det_cambios.get("cliente_existente"), "SI")
        self.assertEqual(det_cambios.get("subtipo_operacion"), "Equipo")
        self.assertEqual(det_cambios.get("modalidad_entrega"), "En Tienda")
        self.assertEqual(det_cambios.get("tipo_producto"), "Full")
        self.assertEqual(det_cambios.get("fecha_recepcion"), "18/6/2024 23:55:00")

        # 3. Port Out
        det_po = parse_iris_detail(html_port_out)
        self.assertEqual(det_po.get("nro_tramite_abd"), "202508071042175557000")
        self.assertEqual(det_po.get("id_tramite_spn"), "31410855")
        self.assertEqual(det_po.get("sistema_origen"), "SPN")
        self.assertEqual(det_po.get("resultado_spn"), "Aprobado")
        self.assertEqual(det_po.get("sistema_comercial"), "Amdocs")
        self.assertEqual(det_po.get("operador_receptor"), "Claro")
        self.assertEqual(det_po.get("nombre"), "BUSTOS MARIA INES")
        self.assertEqual(det_po.get("tipo_documento"), "Documento Nacional Identidad")
        self.assertEqual(det_po.get("nro_documento"), "11020227")
        self.assertEqual(det_po.get("tecnologia"), "Celular")
        self.assertEqual(det_po.get("producto"), "Contrato CPP")
        self.assertEqual(det_po.get("modalidad_factura"), "SI")
        self.assertEqual(det_po.get("cantidad_lineas_portar"), "2")
        self.assertEqual(det_po.get("lineas"), ["2477500669", "2477650555"])
        self.assertEqual(det_po.get("fecha_ventana_cambio_aprobada"), "11/8/2025 9:43:00")
        self.assertEqual(det_po.get("cantidad_lineas_revertidas"), "0")
        self.assertEqual(det_po.get("estado_reversion"), "Total")


    def test_parser_port_in_todos_los_campos(self):
        """Verifica que parse_iris_detail extraiga TODOS los campos del formulario Port In."""
        fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "iris"
        html_pi = (fixtures_dir / "detalle_port_in.html").read_text(encoding="utf-8")

        det_pi = parse_iris_detail(html_pi)

        # 1. Canal / Vendedor
        self.assertEqual(det_pi.get("canal_agente"), "282 - Ce - Global solutions")
        self.assertEqual(det_pi.get("punto_de_venta"), "33 - Las Heras 136")
        self.assertEqual(det_pi.get("vendedor"), "51633")
        self.assertEqual(det_pi.get("subvendedor"), "046001")
        self.assertEqual(det_pi.get("usuario"), "WSPORTA")

        # 2. Datos de la Operación
        self.assertEqual(det_pi.get("tipo_formulario"), "PO - Solicitud PortIn")
        self.assertEqual(det_pi.get("nro_formulario"), "417956261")
        self.assertEqual(det_pi.get("fecha_operacion"), "18/9/2026")
        self.assertEqual(det_pi.get("fecha_alta"), "18/9/2026")
        self.assertEqual(det_pi.get("estado"), "Aprobación del ABD")
        self.assertEqual(det_pi.get("fecha_estado"), "21/9/2026")
        self.assertEqual(det_pi.get("modalidad_entrega"), "Retira Sim")

        # 3. Datos del Suscriptor
        self.assertEqual(det_pi.get("tipo_persona"), "Persona Fisica")
        self.assertEqual(det_pi.get("apellido"), "FRAGA")
        self.assertEqual(det_pi.get("nombre"), "CLAUDIA ELSA")
        self.assertIn("FRAGA", det_pi.get("titular", ""))
        self.assertEqual(det_pi.get("tipo_documento"), "Documento Nacional Identidad")
        self.assertEqual(det_pi.get("nro_documento"), "16247363")
        self.assertEqual(det_pi.get("telefono_contacto"), "1162842458")
        self.assertEqual(det_pi.get("email"), "sindatos@sindatos.com")

        # 4. Autorizado
        aut = det_pi.get("autorizado", {})
        self.assertIsInstance(aut, dict)
        self.assertIn("nro_documento", aut)

        # 5. Servicio Actual / Donador
        self.assertEqual(det_pi.get("operador_donador"), "Claro")
        self.assertEqual(det_pi.get("modalidad_factura"), "SI")

        # 6. Servicio a Contratar
        self.assertEqual(det_pi.get("tecnologia"), "Celular")
        self.assertEqual(det_pi.get("producto"), "Contrato CPP")
        self.assertEqual(det_pi.get("fecha_ventana_cambio_orig"), "21/9/2026")
        self.assertEqual(det_pi.get("fecha_ventana_cambio_abd"), "22/9/2026 11:56:00")

        # 7. Formularios de Alta vinculados
        forms = det_pi.get("formularios_alta", [])
        self.assertIsInstance(forms, list)
        self.assertEqual(len(forms), 4)
        self.assertEqual(forms[0]["tipo_formulario"], "PR - Alta de Linea T3")
        self.assertEqual(forms[0]["nro_formulario"], "32563349772026")
        self.assertEqual(forms[0]["estado_legajo"], "Controlado - Autorizado")
        self.assertEqual(forms[3]["nro_formulario"], "32563349822026")

        # 8. Líneas y cantidad a portar
        self.assertEqual(det_pi.get("cantidad_lineas_portar"), "4")
        self.assertIsInstance(det_pi.get("lineas"), list)
        self.assertIsInstance(det_pi.get("lineas_asociadas"), list)

        # 9. Documentos
        docs = det_pi.get("documentos", [])
        self.assertIsInstance(docs, list)
        self.assertTrue(any("PORTABILIDAD" in d for d in docs))
        self.assertTrue(any("Documento Identificatorio" in d for d in docs))

        # 10. Fecha de recepción
        self.assertEqual(det_pi.get("fecha_recepcion"), "18/9/2026 14:31:00")

    def test_cola_adapter_normaliza_port_in(self):
        """Verifica que _agrupar_operaciones_iris clasifique correctamente operaciones port_in."""
        adapter = ColaAutomatizacionAdapter(auto_id="iris_scraper")
        item = {
            "ani": "1162842458",
            "status": "coincidencia",
            "datos": {
                "enacom": {"operador": "Claro"},
                "iris": {
                    "raw": {
                        "registros": [
                            {"operacion": "Port In", "nro_tramite": "417956261", "fecha_alta": "18/9/2026"},
                            {"operacion": "port_in", "nro_tramite": "417956262", "fecha_alta": "19/9/2026"},
                        ]
                    }
                }
            }
        }
        res_json = adapter._formatear_resultado(item)
        self.assertIn("port_in", res_json.get("iris", {}))
        self.assertEqual(len(res_json["iris"]["port_in"]), 2)
        self.assertEqual(res_json["iris"]["port_in"]["port_in_1"]["nro_tramite"], "417956261")
        self.assertEqual(res_json["iris"]["port_in"]["port_in_2"]["nro_tramite"], "417956262")


if __name__ == "__main__":
    unittest.main()

