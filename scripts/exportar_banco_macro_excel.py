# -*- coding: utf-8 -*-
"""
Exportador Industrial Ejecutivo: Base Banco Macro Enriquecida a Excel (.xlsx)
Genera un libro multi-hoja corporativo apto para entrega a cliente sin exposición de fuentes internas:
- Hoja 0: 'Índice y Diccionario' (Glosario exhaustivo de cada columna enriquecida y su utilidad)
- Hoja 1: 'Resumen Ejecutivo' (Dashboard de KPIs consolidados)
- Hoja 2: 'Consolidado Clientes' (Master con perfil crediticio, fiscal, demográfico y telefónico)
- Hoja 3: 'Líneas Telefónicas' (Detalle de líneas con operador de origen, operador actual y saldo)
- Hoja 4: 'Central de Deudores' (Auditoría crediticia por banco y financiera)
- Hoja 5: 'Gestiones de Portabilidad' (Historial de trámites y portabilidad numérica)
- Hoja 6: 'Operaciones Cartera' (Registro individual de operaciones de la cartera)
"""
import sys
import json
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Reconfigurar stdout en Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.enacom.enacom_adapter import EnacomBlockAdapter

DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"
DEFAULT_OUTPUT = PROJECT_ROOT / "base_macro_enriquecida.xlsx"
ENACOM_DAT_PATH = PROJECT_ROOT / "data" / "enacom" / "enacom_lookup.dat"


def asegurar_operadores_origen(conn):
    """Garantiza que todas las líneas descubiertas tengan su operador de origen ENACOM asignado."""
    if not ENACOM_DAT_PATH.exists():
        return
    cursor = conn.cursor()
    cursor.execute("SELECT id, ani FROM lineas_descubiertas WHERE operador_origen IS NULL")
    pendientes = cursor.fetchall()
    if not pendientes:
        return
    print(f"📡 Asignando operador de origen a {len(pendientes)} líneas pendientes...")
    adapter = EnacomBlockAdapter(str(ENACOM_DAT_PATH))
    if not adapter.esta_listo():
        return
    for row_id, ani in pendientes:
        res = adapter.consultar_bloque_dict(ani)
        if res and res.get("operador_origen"):
            cursor.execute("UPDATE lineas_descubiertas SET operador_origen = ? WHERE id = ?", (res["operador_origen"], row_id))
    conn.commit()


def limpiar_actividades(val):
    if not val:
        return ""
    s_val = str(val).strip()
    if s_val.startswith("[") and s_val.endswith("]"):
        try:
            acts = json.loads(s_val)
            if isinstance(acts, list):
                return " | ".join(str(x) for x in acts)
        except Exception:
            pass
    return s_val


def extraer_impuestos_cuit(datos_json_str):
    if not datos_json_str:
        return ""
    try:
        d = json.loads(datos_json_str)
        co = d.get("cuitonline", {})
        imps = co.get("impuestos_activos") or []
        if isinstance(imps, list):
            return " | ".join(str(x) for x in imps)
        return str(imps)
    except Exception:
        return ""


def construir_indice_diccionario() -> pd.DataFrame:
    """Construye el catálogo exhaustivo de metadatos y definiciones de cada columna enriquecida."""
    catalogo = [
        # HOJA: Consolidado Clientes
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "DNI", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Documento Nacional de Identidad del titular. Identificador unívoco maestro en la cartera.", "Valores Posibles / Ejemplos": "Ej: 33406554"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Nombre Completo", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Nombre y apellido oficial validado ante organismos y padrones nacionales.", "Valores Posibles / Ejemplos": "Ej: YANINA MARCELA STECKLER"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Nombre Registrado", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Nombre del cliente tal como constaba en la asignación original del banco.", "Valores Posibles / Ejemplos": "Ej: STECKLER YANINA"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "CUIT / CUIL", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Clave Única de Identificación Tributaria o Laboral con dígito verificador auditado.", "Valores Posibles / Ejemplos": "Ej: 27334065544"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Condición Fiscal", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Situación impositiva activa. Permite evaluar formalidad laboral y capacidad contributiva.", "Valores Posibles / Ejemplos": "Monotributista | Responsable Inscripto | No Inscripto (Relación de Dependencia / Jubilado) | IVA Exento"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Impuestos Activos", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Listado de tributos en los cuales el titular se encuentra inscripto de forma vigente.", "Valores Posibles / Ejemplos": "Ej: IVA | GANANCIAS | REGIMEN GENERAL"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Género", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Género formal registrado (M: Masculino, F: Femenino, X: No binario).", "Valores Posibles / Ejemplos": "M | F | X"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Edad", "Tipo de Dato": "Entero", "Definición y Utilidad de Negocio": "Edad cronológica del titular. Facilita scoring y segmentación por rango etario.", "Valores Posibles / Ejemplos": "Ej: 38"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Peor Situación Crediticia", "Tipo de Dato": "Entero (0 a 5)", "Definición y Utilidad de Negocio": "Máximo nivel de atraso o morosidad que registra el cliente en todo el sistema financiero.", "Valores Posibles / Ejemplos": "0 (Sin deuda), 1 (Normal), 2 (Seguimiento Especial), 3 (Con Problemas), 4 (Alto Riesgo), 5 (Irrecuperable)"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Descripción Situación Crediticia", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Descripción literal estandarizada de la peor situación crediticia sistémica.", "Valores Posibles / Ejemplos": "Ej: 5 - Irrecuperable"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Deuda Banco Macro ($)", "Tipo de Dato": "Moneda ($)", "Definición y Utilidad de Negocio": "Saldo deudor total exigible por el Banco Macro expresado en moneda de curso legal ($ ARS).", "Valores Posibles / Ejemplos": "Ej: $1,250,000.00"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Sit. Banco Macro", "Tipo de Dato": "Entero (0 a 5)", "Definición y Utilidad de Negocio": "Clasificación crediticia informada específicamente en el Banco Macro.", "Valores Posibles / Ejemplos": "0 a 5"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Desc. Sit. Banco Macro", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Detalle cualitativo de la situación de deuda directa con Banco Macro.", "Valores Posibles / Ejemplos": "Ej: 5 - Irrecuperable"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Deuda Total Sistema Financiero ($)", "Tipo de Dato": "Moneda ($)", "Definición y Utilidad de Negocio": "Sumatoria consolidada de compromisos crediticios en todos los bancos y financieras del país en pesos reales.", "Valores Posibles / Ejemplos": "Ej: $3,450,000.00"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Cant. Entidades Financieras", "Tipo de Dato": "Entero", "Definición y Utilidad de Negocio": "Cantidad de instituciones bancarias o emisoras de crédito que reportan deuda activa del titular.", "Valores Posibles / Ejemplos": "Ej: 3"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Entidades con Deuda", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Nombres de los bancos y financieras donde el cliente mantiene deudas vigentes.", "Valores Posibles / Ejemplos": "Ej: BANCO SANTANDER, BANCO BBVA, TARJETA NARANJA"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Cantidad de Líneas", "Tipo de Dato": "Entero", "Definición y Utilidad de Negocio": "Total de números de teléfono celular descubiertos y vinculados al titular.", "Valores Posibles / Ejemplos": "Ej: 2"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Teléfonos Identificados", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Números móviles normalizados a 10 dígitos listos para discador o WhatsApp.", "Valores Posibles / Ejemplos": "Ej: 1151418931, 3515729876"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Operador de Origen", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Compañía original asignada a la numeración según el Plan Fundamental de Numeración.", "Valores Posibles / Ejemplos": "Movistar | Claro | Personal | Telecentro"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Teléfonos con Detalle y Deuda", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Detalle técnico por línea indicando operador de origen, operador actual y saldo adeudado.", "Valores Posibles / Ejemplos": "Ej: 1151418931 [Movistar > Personal $44115.61]"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Deuda Total Telefonía ($)", "Tipo de Dato": "Moneda ($)", "Definición y Utilidad de Negocio": "Monto acumulado exigible por prestadores de servicios de telefonía móvil.", "Valores Posibles / Ejemplos": "Ej: $44,115.61"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Domicilio Fiscal", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Dirección formal declarada para envío de notificaciones e intimaciones fehacientes.", "Valores Posibles / Ejemplos": "Ej: AV CORRIENTES 1234 PISO 4"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Localidad", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Localidad o barrio correspondiente al asiento del titular.", "Valores Posibles / Ejemplos": "Ej: SAN ISIDRO"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Municipio", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Municipio, partido o departamento administrativo.", "Valores Posibles / Ejemplos": "Ej: SAN ISIDRO"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Ciudad", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Ciudad identificada para geolocalización de cobranzas.", "Valores Posibles / Ejemplos": "Ej: BUENOS AIRES"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Provincia", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Provincia donde se asienta el domicilio del cliente.", "Valores Posibles / Ejemplos": "Ej: BUENOS AIRES | CORDOBA | SANTA FE"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Tipo Persona", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Naturaleza jurídica (Física o Jurídica).", "Valores Posibles / Ejemplos": "FISICA | JURIDICA"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Empleador", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Razón social del empleador registrado en caso de relación de dependencia activa.", "Valores Posibles / Ejemplos": "Ej: TELECOM ARGENTINA S.A."},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Actividades Económicas", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Rubros comerciales o profesionales formalmente declarados.", "Valores Posibles / Ejemplos": "Ej: SERVICIOS INMOBILIARIOS | VENTA AL POR MENOR"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Cant. Operaciones en Cartera", "Tipo de Dato": "Entero", "Definición y Utilidad de Negocio": "Cantidad de registros o deudas individuales que integraban la cartera original para el DNI.", "Valores Posibles / Ejemplos": "Ej: 1, 2, 5"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Detalle Operaciones en Cartera (IDs)", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Lista de números de registro individual que vinculan 1 a 1 con la hoja 'Operaciones Cartera'.", "Valores Posibles / Ejemplos": "Ej: 2366, 4274, 4462"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Estado Auditoría", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Estado operativo del proceso de verificación.", "Valores Posibles / Ejemplos": "completado"},
        {"Hoja de Referencia": "Consolidado Clientes", "Columna": "Fecha Auditoría", "Tipo de Dato": "Fecha", "Definición y Utilidad de Negocio": "Marca temporal en la que se recopiló y validó la información.", "Valores Posibles / Ejemplos": "YYYY-MM-DD HH:MM:SS"},

        # HOJA: Líneas Telefónicas
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "DNI", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Documento Nacional de Identidad del titular asociado a la línea.", "Valores Posibles / Ejemplos": "Ej: 33406554"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Nombre Cliente", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Nombre y apellido del cliente.", "Valores Posibles / Ejemplos": "Ej: MATIAS WALTER ATRICE"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Número de Línea", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Número telefónico depurado a 10 dígitos (ANI sin prefijos 0 ni 15).", "Valores Posibles / Ejemplos": "Ej: 1151418931"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Operador de Origen", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Compañía original titular del bloque de numeración asignado por el Plan Fundamental de Numeración.", "Valores Posibles / Ejemplos": "Movistar | Claro | Personal | Telecentro"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Operador Detectado", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Prestador de telecomunicaciones actual donde se encuentra activa la línea.", "Valores Posibles / Ejemplos": "Personal | Claro | Movistar | No Auditado"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Tiene Deuda", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Indica si la línea registra saldo deudor o facturas impagas en la compañía telefónica.", "Valores Posibles / Ejemplos": "SÍ | NO"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Deuda Telefonía ($)", "Tipo de Dato": "Moneda ($)", "Definición y Utilidad de Negocio": "Importe total adeudado verificado en el prestador del servicio telefónico.", "Valores Posibles / Ejemplos": "Ej: $44,115.61"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Nro Trámite Portabilidad", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Identificador único de trámite ante el Administrador de Base de Datos (ABD).", "Valores Posibles / Ejemplos": "Ej: 412780364"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Fecha Identificación", "Tipo de Dato": "Fecha", "Definición y Utilidad de Negocio": "Fecha en la que la línea fue vinculada al titular.", "Valores Posibles / Ejemplos": "YYYY-MM-DD"},
        {"Hoja de Referencia": "Líneas Telefónicas", "Columna": "Fecha Auditoría", "Tipo de Dato": "Fecha", "Definición y Utilidad de Negocio": "Fecha en la que se consultó el estado del servicio y saldo deudor.", "Valores Posibles / Ejemplos": "YYYY-MM-DD HH:MM:SS"},

        # HOJA: Central de Deudores
        {"Hoja de Referencia": "Central de Deudores", "Columna": "DNI", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Documento Nacional de Identidad del titular evaluado.", "Valores Posibles / Ejemplos": "Ej: 14352814"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "CUIT / CUIL", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Clave fiscal utilizada para la consulta en la Central de Deudores del sistema financiero.", "Valores Posibles / Ejemplos": "Ej: 20143528148"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "Nombre Cliente", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Nombre y apellido del cliente consultado.", "Valores Posibles / Ejemplos": "Ej: RAMON ANTONIO SORIA"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "Entidad Financiera", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Entidad bancaria, financiera emisora de tarjeta o fideicomiso que reporta la acreencia.", "Valores Posibles / Ejemplos": "Ej: BANCO MACRO S.A. | BANCO SANTANDER"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "Situación", "Tipo de Dato": "Entero (0 a 5)", "Definición y Utilidad de Negocio": "Categoría de mora regulatoria según días de atraso en el pago.", "Valores Posibles / Ejemplos": "0 (Sin deuda), 1 (0-31d), 2 (32-90d), 3 (91-180d), 4 (181-365d), 5 (>365d)"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "Descripción Situación", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Definición oficial de la situación crediticia informada.", "Valores Posibles / Ejemplos": "1 - Normal | 5 - Irrecuperable"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "Deuda ($)", "Tipo de Dato": "Moneda ($)", "Definición y Utilidad de Negocio": "Monto de la deuda con esa entidad específica expresado en pesos reales ($ ARS).", "Valores Posibles / Ejemplos": "Ej: $8,450,000.00"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "Período Informado", "Tipo de Dato": "Texto (AAAAMM)", "Definición y Utilidad de Negocio": "Mes y año correspondiente a la posición contable declarada por la entidad.", "Valores Posibles / Ejemplos": "Ej: 202608"},
        {"Hoja de Referencia": "Central de Deudores", "Columna": "Fecha Auditoría", "Tipo de Dato": "Fecha", "Definición y Utilidad de Negocio": "Fecha en la que se extrajo el reporte de la Central de Deudores.", "Valores Posibles / Ejemplos": "YYYY-MM-DD HH:MM:SS"},

        # HOJA: Gestiones de Portabilidad
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "DNI", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Documento Nacional de Identidad del titular que gestionó el trámite.", "Valores Posibles / Ejemplos": "Ej: 11872659"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Nombre Cliente", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Nombre y apellido del cliente.", "Valores Posibles / Ejemplos": "Ej: JUAN CARLOS GOMEZ"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Nro Trámite Portabilidad", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Número oficial de trámite ante el Administrador de Base de Datos de Portabilidad (ABD).", "Valores Posibles / Ejemplos": "Ej: 412780364"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "ID Solicitud Portabilidad", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Identificador de solicitud SPN registrado en el sistema.", "Valores Posibles / Ejemplos": "Ej: 19561568662022"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Tipo Operación", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Modalidad de la gestión de telecomunicaciones.", "Valores Posibles / Ejemplos": "Port In | Port Out | Altas | Cambio de Titularidad"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Operador Destino", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Compañía receptora de la numeración portada.", "Valores Posibles / Ejemplos": "Claro | Movistar | Personal"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Estado Gestión", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Resolución administrativa del trámite.", "Valores Posibles / Ejemplos": "Aprobación del ABD | Controlado - Autorizado"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Producto", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Tipo de plan contratado en la operación.", "Valores Posibles / Ejemplos": "Control | Ahorro | Abono"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Tecnología", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Medio físico o tecnológico del servicio.", "Valores Posibles / Ejemplos": "Celular | Fija"},
        {"Hoja de Referencia": "Gestiones de Portabilidad", "Columna": "Fecha Operación", "Tipo de Dato": "Fecha / Texto", "Definición y Utilidad de Negocio": "Momento en que se asentó la gestión de portabilidad.", "Valores Posibles / Ejemplos": "DD/MM/AAAA"},

        # HOJA: Operaciones Cartera
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "ID Operación", "Tipo de Dato": "Entero", "Definición y Utilidad de Negocio": "Identificador único de cada fila de la cartera original del banco.", "Valores Posibles / Ejemplos": "Ej: 1, 2, 3559"},
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "DNI", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Documento Nacional de Identidad asignado a la operación.", "Valores Posibles / Ejemplos": "Ej: 33406554"},
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "Nombre Registrado", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Nombre del deudor informado originalmente por la entidad bancaria.", "Valores Posibles / Ejemplos": "Ej: YANINA MARCELA STECKLER"},
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "CUIT / CUIL", "Tipo de Dato": "Numérico (Texto)", "Definición y Utilidad de Negocio": "Identificador tributario verificado del titular de la operación.", "Valores Posibles / Ejemplos": "Ej: 27334065544"},
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "Condición Fiscal", "Tipo de Dato": "Texto", "Definición y Utilidad de Negocio": "Régimen impositivo del titular.", "Valores Posibles / Ejemplos": "Monotributista | Responsable Inscripto | No Inscripto"},
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "Deuda Banco Macro ($)", "Tipo de Dato": "Moneda ($)", "Definición y Utilidad de Negocio": "Monto de la deuda con el banco para ese titular en pesos reales ($ ARS).", "Valores Posibles / Ejemplos": "Ej: $1,250,000.00"},
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "Peor Situación Crediticia", "Tipo de Dato": "Entero (0 a 5)", "Definición y Utilidad de Negocio": "Nivel de morosidad sistémica registrado.", "Valores Posibles / Ejemplos": "1 a 5"},
        {"Hoja de Referencia": "Operaciones Cartera", "Columna": "Líneas Identificadas", "Tipo de Dato": "Entero", "Definición y Utilidad de Negocio": "Cantidad de números telefónicos recuperados para contactar por esta operación.", "Valores Posibles / Ejemplos": "Ej: 2"}
    ]
    return pd.DataFrame(catalogo)


def exportar_base_macro_excel(output_path: Path = DEFAULT_OUTPUT) -> Path:
    """Exporta toda la base SQLite de Banco Macro a un archivo Excel multi-hoja profesional para clientes."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"No se encontró la base de datos en: {DB_PATH}")

    print(f"🔌 Conectando a SQLite: {DB_PATH}...")
    conn = sqlite3.connect(str(DB_PATH))

    # Asegurar operadores de origen oficiales
    asegurar_operadores_origen(conn)

    print("📊 Extrayendo datos y calculando agregaciones para entrega a cliente...")

    # -------------------------------------------------------------------------
    # HOJA 0: Índice y Diccionario de Datos
    # -------------------------------------------------------------------------
    df_indice = construir_indice_diccionario()

    # -------------------------------------------------------------------------
    # HOJA 2: Consolidado Clientes (Master)
    # -------------------------------------------------------------------------
    query_personas = """
        WITH lineas_agg AS (
            SELECT 
                l.dni,
                COUNT(DISTINCT l.ani) as cant_lineas,
                GROUP_CONCAT(DISTINCT l.ani) as lineas_puras,
                GROUP_CONCAT(DISTINCT COALESCE(l.operador_origen, 'No Identificado')) as operadores_origen_lista,
                GROUP_CONCAT(
                    DISTINCT l.ani || CASE 
                        WHEN t.operador_detectado IS NOT NULL 
                        THEN ' [' || CASE WHEN l.operador_origen IS NOT NULL AND l.operador_origen != t.operador_detectado THEN l.operador_origen || ' > ' ELSE '' END || t.operador_detectado || CASE WHEN t.tiene_deuda = 1 THEN ' $' || ROUND(t.deuda_total, 2) ELSE '' END || ']' 
                        WHEN l.operador_origen IS NOT NULL 
                        THEN ' [' || l.operador_origen || ']'
                        ELSE '' 
                    END
                ) as lineas_con_detalle,
                SUM(CASE WHEN t.tiene_deuda = 1 THEN t.deuda_total ELSE 0 END) as deuda_total_telcos
            FROM lineas_descubiertas l
            LEFT JOIN telcos_scraping t ON l.dni = t.dni AND l.ani = t.ani
            WHERE l.ani NOT IN ('0', '0000000000', '1111111111')
            GROUP BY l.dni
        ),
        bcra_agg AS (
            SELECT 
                b.dni,
                COUNT(DISTINCT b.entidad) as cant_entidades_bcra,
                GROUP_CONCAT(DISTINCT b.entidad) as entidades_bcra_lista
            FROM bcra_entidades b
            GROUP BY b.dni
        ),
        operaciones_agg AS (
            SELECT 
                dni,
                COUNT(id) as cant_ops,
                GROUP_CONCAT(id, ', ') as ops_detalle
            FROM operaciones_excel
            GROUP BY dni
        )
        SELECT 
            p.dni AS "DNI",
            COALESCE(p.nombre_oficial, p.nombre_excel) AS "Nombre Completo",
            p.nombre_excel AS "Nombre Registrado",
            p.cuit AS "CUIT / CUIL",
            p.condicion_afip AS "Condición Fiscal",
            p.genero AS "Género",
            p.edad AS "Edad",
            p.peor_situacion_bcra AS "Peor Situación Crediticia",
            CASE p.peor_situacion_bcra
                WHEN 0 THEN '0 - Sin deuda'
                WHEN 1 THEN '1 - Normal'
                WHEN 2 THEN '2 - Con seguimiento especial'
                WHEN 3 THEN '3 - Con problemas'
                WHEN 4 THEN '4 - Alto riesgo de insolvencia'
                WHEN 5 THEN '5 - Irrecuperable'
                ELSE ''
            END AS "Descripción Situación Crediticia",
            ROUND(p.deuda_macro_miles * 1000.0, 2) AS "Deuda Banco Macro ($)",
            p.deuda_macro_situacion AS "Sit. Banco Macro",
            CASE p.deuda_macro_situacion
                WHEN 0 THEN '0 - Sin deuda'
                WHEN 1 THEN '1 - Normal'
                WHEN 2 THEN '2 - Con seguimiento especial'
                WHEN 3 THEN '3 - Con problemas'
                WHEN 4 THEN '4 - Alto riesgo de insolvencia'
                WHEN 5 THEN '5 - Irrecuperable'
                ELSE ''
            END AS "Desc. Sit. Banco Macro",
            ROUND(p.deuda_total_bcra_miles * 1000.0, 2) AS "Deuda Total Sistema Financiero ($)",
            COALESCE(ba.cant_entidades_bcra, 0) AS "Cant. Entidades Financieras",
            ba.entidades_bcra_lista AS "Entidades con Deuda",
            COALESCE(la.cant_lineas, 0) AS "Cantidad de Líneas",
            la.lineas_puras AS "Teléfonos Identificados",
            COALESCE(la.operadores_origen_lista, '-') AS "Operador de Origen",
            la.lineas_con_detalle AS "Teléfonos con Detalle y Deuda",
            COALESCE(la.deuda_total_telcos, 0) AS "Deuda Total Telefonía ($)",
            p.domicilio_fiscal AS "Domicilio Fiscal",
            COALESCE(p.localidad, p.ciudad) AS "Localidad",
            COALESCE(p.municipio, p.ciudad) AS "Municipio",
            p.ciudad AS "Ciudad",
            p.provincia AS "Provincia",
            p.tipo_persona AS "Tipo Persona",
            p.empleador AS "Empleador",
            p.actividades_afip AS "Actividades Económicas",
            COALESCE(oa.cant_ops, p.total_operaciones_excel, 1) AS "Cant. Operaciones en Cartera",
            COALESCE(oa.ops_detalle, '-') AS "Detalle Operaciones en Cartera (IDs)",
            p.estado_proceso AS "Estado Auditoría",
            p.fecha_actualizacion AS "Fecha Auditoría",
            p.datos_json
        FROM personas p
        LEFT JOIN lineas_agg la ON p.dni = la.dni
        LEFT JOIN bcra_agg ba ON p.dni = ba.dni
        LEFT JOIN operaciones_agg oa ON p.dni = oa.dni
        ORDER BY p.deuda_macro_miles DESC, p.total_operaciones_excel DESC;
    """
    df_personas = pd.read_sql_query(query_personas, conn)

    # Extraer y formatear impuestos y actividades limpias
    df_personas["Impuestos Activos"] = df_personas["datos_json"].apply(extraer_impuestos_cuit)
    df_personas["Actividades Económicas"] = df_personas["Actividades Económicas"].apply(limpiar_actividades)

    # Reordenar columna de Impuestos Activos junto a Condición Fiscal
    cols = list(df_personas.columns)
    cols.remove("datos_json")
    idx_afip = cols.index("Condición Fiscal")
    cols.remove("Impuestos Activos")
    cols.insert(idx_afip + 1, "Impuestos Activos")
    df_personas = df_personas[cols]

    # -------------------------------------------------------------------------
    # HOJA 3: Líneas Telefónicas
    # -------------------------------------------------------------------------
    query_lineas = """
        SELECT 
            l.dni AS "DNI",
            COALESCE(p.nombre_oficial, p.nombre_excel) AS "Nombre Cliente",
            l.ani AS "Número de Línea",
            COALESCE(l.operador_origen, 'No Identificado') AS "Operador de Origen",
            COALESCE(t.operador_detectado, 'No Auditado') AS "Operador Detectado",
            CASE WHEN t.tiene_deuda = 1 THEN 'SÍ' ELSE 'NO' END AS "Tiene Deuda",
            COALESCE(t.deuda_total, 0.0) AS "Deuda Telefonía ($)",
            l.nro_tramite_abd AS "Nro Trámite Portabilidad",
            l.fecha_descubrimiento AS "Fecha Identificación",
            t.fecha_consulta AS "Fecha Auditoría"
        FROM lineas_descubiertas l
        LEFT JOIN personas p ON l.dni = p.dni
        LEFT JOIN telcos_scraping t ON l.dni = t.dni AND l.ani = t.ani
        WHERE l.ani NOT IN ('0', '0000000000', '1111111111')
        ORDER BY t.tiene_deuda DESC, t.deuda_total DESC, l.dni ASC;
    """
    df_lineas = pd.read_sql_query(query_lineas, conn)

    # -------------------------------------------------------------------------
    # HOJA 4: Central de Deudores
    # -------------------------------------------------------------------------
    query_bcra = """
        SELECT 
            b.dni AS "DNI",
            b.cuit AS "CUIT / CUIL",
            COALESCE(p.nombre_oficial, p.nombre_excel) AS "Nombre Cliente",
            b.entidad AS "Entidad Financiera",
            b.situacion AS "Situación",
            CASE b.situacion
                WHEN 0 THEN '0 - Sin deuda'
                WHEN 1 THEN '1 - Normal'
                WHEN 2 THEN '2 - Con seguimiento especial'
                WHEN 3 THEN '3 - Con problemas'
                WHEN 4 THEN '4 - Alto riesgo de insolvencia'
                WHEN 5 THEN '5 - Irrecuperable'
                ELSE CAST(b.situacion AS TEXT)
            END AS "Descripción Situación",
            ROUND(b.monto_miles * 1000.0, 2) AS "Deuda ($)",
            b.periodo AS "Período Informado",
            b.fecha_consulta AS "Fecha Auditoría"
        FROM bcra_entidades b
        LEFT JOIN personas p ON b.dni = p.dni
        ORDER BY b.monto_miles DESC, b.situacion DESC;
    """
    df_bcra = pd.read_sql_query(query_bcra, conn)

    # -------------------------------------------------------------------------
    # HOJA 5: Gestiones de Portabilidad
    # -------------------------------------------------------------------------
    query_iris = """
        SELECT 
            o.dni AS "DNI",
            COALESCE(p.nombre_oficial, p.nombre_excel) AS "Nombre Cliente",
            o.nro_tramite_abd AS "Nro Trámite Portabilidad",
            o.id_tramite_spn AS "ID Solicitud Portabilidad",
            o.tipo_operacion AS "Tipo Operación",
            o.operador_receptor AS "Operador Destino",
            o.estado AS "Estado Gestión",
            o.producto AS "Producto",
            o.tecnologia AS "Tecnología",
            o.fecha_operacion AS "Fecha Operación"
        FROM operaciones_iris o
        LEFT JOIN personas p ON o.dni = p.dni
        ORDER BY o.fecha_operacion DESC, o.dni ASC;
    """
    df_iris = pd.read_sql_query(query_iris, conn)

    # -------------------------------------------------------------------------
    # HOJA 6: Operaciones Cartera (Detalle Individual)
    # -------------------------------------------------------------------------
    query_operaciones = """
        SELECT 
            oe.id AS "ID Operación",
            oe.dni AS "DNI",
            oe.nombre_apellido AS "Nombre Registrado",
            p.cuit AS "CUIT / CUIL",
            p.condicion_afip AS "Condición Fiscal",
            ROUND(p.deuda_macro_miles * 1000.0, 2) AS "Deuda Banco Macro ($)",
            p.peor_situacion_bcra AS "Peor Situación Crediticia",
            p.total_lineas_iris AS "Líneas Identificadas"
        FROM operaciones_excel oe
        LEFT JOIN personas p ON oe.dni = p.dni
        ORDER BY oe.id ASC;
    """
    df_operaciones = pd.read_sql_query(query_operaciones, conn)

    # -------------------------------------------------------------------------
    # HOJA 1: Resumen Ejecutivo / Dashboard KPIs
    # -------------------------------------------------------------------------
    total_clientes = len(df_personas)
    total_deuda_macro = float(df_personas["Deuda Banco Macro ($)"].sum())
    total_deuda_sistema = float(df_personas["Deuda Total Sistema Financiero ($)"].sum())
    con_deuda_macro = int((df_personas["Deuda Banco Macro ($)"] > 0).sum())
    clientes_sit_5 = int((df_personas["Peor Situación Crediticia"] == 5).sum())

    clientes_con_tel = int((df_personas["Cantidad de Líneas"] > 0).sum())
    total_lineas = int(len(df_lineas))
    lineas_con_deuda = int((df_lineas["Tiene Deuda"] == "SÍ").sum()) if not df_lineas.empty else 0
    deuda_telcos_total = float(df_lineas["Deuda Telefonía ($)"].sum()) if not df_lineas.empty else 0.0

    clientes_con_cuit = int(df_personas["CUIT / CUIL"].notna().sum())
    clientes_con_edad = int(df_personas["Edad"].notna().sum())

    resumen_data = [
        {"Categoría": "Cartera General", "Métrica": "Total Clientes en Cartera", "Valor": total_clientes, "Detalle": "100% de clientes de Banco Macro evaluados"},
        {"Categoría": "Cartera General", "Métrica": "Deuda Total Banco Macro", "Valor": f"${total_deuda_macro:,.2f}", "Detalle": "Sumatoria de deuda consolidada exigible exclusiva de Banco Macro en pesos reales"},
        {"Categoría": "Cartera General", "Métrica": "Deuda Total Sistema Financiero", "Valor": f"${total_deuda_sistema:,.2f}", "Detalle": "Deuda acumulada en entidades bancarias y financieras en pesos reales"},
        {"Categoría": "Cartera General", "Métrica": "Clientes en Situación 5 (Irrecuperable)", "Valor": clientes_sit_5, "Detalle": f"{(clientes_sit_5*100/total_clientes):.1f}% de la cartera con mora máxima"},
        {"Categoría": "Cartera General", "Métrica": "Clientes con CUIT / CUIL Identificado", "Valor": clientes_con_cuit, "Detalle": f"{(clientes_con_cuit*100/total_clientes):.1f}% de cobertura de identidad tributaria"},

        {"Categoría": "Contactabilidad Telefónica", "Métrica": "Clientes con Teléfonos Identificados", "Valor": clientes_con_tel, "Detalle": f"{(clientes_con_tel*100/total_clientes):.1f}% de contactabilidad telefónica directa"},
        {"Categoría": "Contactabilidad Telefónica", "Métrica": "Total Líneas Celulares Identificadas", "Valor": total_lineas, "Detalle": "Líneas móviles vinculadas y verificadas"},
        {"Categoría": "Contactabilidad Telefónica", "Métrica": "Líneas con Deuda en Telefonía", "Valor": lineas_con_deuda, "Detalle": "Líneas con deuda activa en prestadores de telefonía móvil"},
        {"Categoría": "Contactabilidad Telefónica", "Métrica": "Deuda Total en Telefonía Móvil", "Valor": f"${deuda_telcos_total:,.2f}", "Detalle": "Monto total adeudado en servicios de telefonía"},

        {"Categoría": "Datos Demográficos", "Métrica": "Clientes con Edad y Demografía", "Valor": clientes_con_edad, "Detalle": f"{(clientes_con_edad*100/total_clientes):.1f}% con edad, ciudad y municipio verificados"},

        {"Categoría": "Perfil Fiscal", "Métrica": "Monotributistas Detectados", "Valor": int((df_personas['Condición Fiscal'] == 'Monotributista').sum()), "Detalle": "Contribuyentes en Régimen Simplificado"},
        {"Categoría": "Perfil Fiscal", "Métrica": "Responsables Inscriptos Detectados", "Valor": int((df_personas['Condición Fiscal'] == 'Responsable Inscripto').sum()), "Detalle": "Régimen General con liquidación tributaria"},
        {"Categoría": "Perfil Fiscal", "Métrica": "No Inscriptos (Relación de Dependencia / Jubilados)", "Valor": int((df_personas['Condición Fiscal'] == 'No Inscripto (Solo CUIL / Empleado o Jubilado)').sum()), "Detalle": "Trabajadores en relación de dependencia o jubilados"},
        {"Categoría": "Auditoría Crediticia", "Métrica": "Entidades Financieras Auditadas", "Valor": int(len(df_bcra)), "Detalle": "Desglose oficial de bancos y financieras en Central de Deudores"}
    ]
    df_resumen = pd.DataFrame(resumen_data)

    conn.close()

    print(f"📝 Escribiendo Excel multi-hoja en {output_path}...")
    with pd.ExcelWriter(str(output_path), engine="openpyxl") as writer:
        df_indice.to_excel(writer, sheet_name="Índice y Diccionario", index=False)
        df_resumen.to_excel(writer, sheet_name="Resumen Ejecutivo", index=False)
        df_personas.to_excel(writer, sheet_name="Consolidado Clientes", index=False)
        df_lineas.to_excel(writer, sheet_name="Líneas Telefónicas", index=False)
        df_bcra.to_excel(writer, sheet_name="Central de Deudores", index=False)
        df_iris.to_excel(writer, sheet_name="Gestiones de Portabilidad", index=False)
        df_operaciones.to_excel(writer, sheet_name="Operaciones Cartera", index=False)

    print("🎨 Aplicando diseño corporativo premium, auto-filtros y ajuste de columnas...")
    wb = openpyxl.load_workbook(str(output_path))

    # Paleta de diseño corporativo de alta gama
    fill_header = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid") # Navy Blue institucional
    fill_kpi_header = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
    fill_zebra = PatternFill(start_color="F9FAFB", end_color="F9FAFB", fill_type="solid")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_kpi_metric = Font(name="Calibri", size=11, bold=True, color="1F4E78")
    border_thin = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9")
    )
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_left_wrap = Alignment(horizontal="left", vertical="center", wrap_text=True)
    align_right = Alignment(horizontal="right", vertical="center")

    for sheetname in wb.sheetnames:
        ws = wb[sheetname]
        ws.views.sheetView[0].showGridLines = True

        if sheetname == "Índice y Diccionario":
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.fill = fill_header
                cell.font = font_header
                cell.alignment = align_center

            for row_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
                z_fill = fill_zebra if row_idx % 2 == 0 else PatternFill(fill_type=None)
                for cell in row:
                    cell.border = border_thin
                    if z_fill.fill_type:
                        cell.fill = z_fill

                row[0].alignment = align_center # Hoja de Referencia
                row[0].font = Font(name="Calibri", size=10, bold=True, color="1F4E78")
                row[1].alignment = align_left # Columna
                row[1].font = Font(name="Calibri", size=10, bold=True)
                row[2].alignment = align_center # Tipo de Dato
                row[3].alignment = align_left_wrap # Definición
                row[4].alignment = align_left # Ejemplos

            ws.column_dimensions["A"].width = 25
            ws.column_dimensions["B"].width = 36
            ws.column_dimensions["C"].width = 20
            ws.column_dimensions["D"].width = 75
            ws.column_dimensions["E"].width = 45
            continue

        if sheetname == "Resumen Ejecutivo":
            ws.freeze_panes = "A2"
            for col_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.fill = fill_kpi_header
                cell.font = font_header
                cell.alignment = align_center

            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.border = border_thin
                row[0].alignment = align_left
                row[1].alignment = align_left
                row[1].font = font_kpi_metric
                row[2].alignment = align_center
                row[2].font = Font(name="Calibri", size=11, bold=True)
                row[3].alignment = align_left

            ws.column_dimensions["A"].width = 28
            ws.column_dimensions["B"].width = 44
            ws.column_dimensions["C"].width = 24
            ws.column_dimensions["D"].width = 65
            continue

        # Inmovilizar fila de encabezados y primeras 2 columnas (DNI y Nombre)
        ws.freeze_panes = "C2"

        # Auto-filtro activo en toda la tabla
        ws.auto_filter.ref = ws.dimensions

        # Formato de encabezados (Fila 1)
        for col_idx in range(1, ws.max_column + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = fill_header
            cell.font = font_header
            cell.alignment = align_center

        # Formatos por celda y auto-fit de ancho
        for col in ws.columns:
            header_val = str(col[0].value or "")
            col_letter = get_column_letter(col[0].column)
            max_len = len(header_val)

            is_dni_or_cuit = any(k in header_val for k in ["DNI", "CUIT", "Línea", "ANI", "Trámite", "(IDs)"]) or header_val == "ID Operación"
            is_currency = any(k in header_val for k in ["($ Miles)", "($)", "Deuda", "Monto"])
            is_date = "Fecha" in header_val
            is_sit = "Sit." in header_val or "Situación" in header_val or any(k in header_val for k in ["Edad", "Cant.", "Cantidad"])

            for row_idx, cell in enumerate(col[1:], start=2):
                val = cell.value
                cell.border = border_thin

                if val is not None:
                    str_val = str(val)
                    if len(str_val) > max_len:
                        max_len = len(str_val)

                    if is_dni_or_cuit:
                        cell.number_format = "@"
                        if "(IDs)" in header_val:
                            cell.alignment = align_left
                        else:
                            cell.alignment = align_center
                    elif is_currency:
                        try:
                            cell.value = float(val)
                            cell.number_format = "#,##0.00"
                            cell.alignment = align_right
                        except Exception:
                            cell.alignment = align_right
                    elif is_date:
                        cell.alignment = align_center
                    elif is_sit:
                        cell.alignment = align_center
                    else:
                        cell.alignment = align_left

            # Ancho óptimo de columna
            ws.column_dimensions[col_letter].width = max(min(max_len + 3, 55), 12)

    wb.save(str(output_path))
    print(f"✨ Archivo Excel generado y estilizado exitosamente en: {output_path}")

    # Mostrar resumen
    print("\n" + "=" * 65)
    print("📈 RESUMEN DEL ARCHIVO EXCEL GENERADO (CLIENT-READY):")
    print("=" * 65)
    print(f"  • Hoja 'Índice y Diccionario':       {len(df_indice)} columnas documentadas")
    print(f"  • Hoja 'Resumen Ejecutivo':          Dashboard Corporativo")
    print(f"  • Hoja 'Consolidado Clientes':      {len(df_personas):,} personas maestras")
    print(f"  • Hoja 'Líneas Telefónicas':        {len(df_lineas):,} líneas verificadas")
    print(f"  • Hoja 'Central de Deudores':        {len(df_bcra):,} deudas financieras")
    print(f"  • Hoja 'Gestiones de Portabilidad':  {len(df_iris):,} trámites registrados")
    print(f"  • Hoja 'Operaciones Cartera':        {len(df_operaciones):,} operaciones detalladas")
    print(f"  • Archivo guardado en:              {output_path.resolve()}")
    print("=" * 65 + "\n")

    return output_path


if __name__ == "__main__":
    out_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    exportar_base_macro_excel(out_arg)
