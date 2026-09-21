import re
from typing import Dict, Any, List
from bs4 import BeautifulSoup

def clean_val(text: str) -> str:
    """Limpia cadenas, normaliza espacios y remueve prefijos de Fuego BPM como NON_EDITABLE$"""
    if not text:
        return ""
    text = text.replace("NON_EDITABLE$", "").strip()
    # Limpiar posibles caracteres mal decodificados comunes
    text = text.replace("Aprobacin", "Aprobación")
    text = text.replace("Operacin", "Operación")
    text = text.replace("Reversin", "Reversión")
    text = text.replace("Lnea", "Línea")
    text = text.replace("Tecnologa", "Tecnología")
    text = text.replace("Contratacin", "Contratación")
    # Limpiar saltos de línea internos excesivos
    text = re.sub(r'\s+', ' ', text)
    return text

def parse_iris_detail(html_content: str) -> Dict[str, Any]:
    """
    Parsea la pantalla de 'Consulta de operación Detalle' de IRIS (Oracle/Fuego BPM).
    Retorna un diccionario estructurado con todos los campos extraídos.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    def get_comp_text(comp_id: str) -> str:
        elem = soup.find(id=comp_id)
        if not elem:
            return ""
        inp = elem.find(['input', 'select', 'textarea'])
        if inp and inp.get('value'):
            return clean_val(inp.get('value'))
        return clean_val(elem.get_text())

    # 1. Trámite y Operación
    nro_tramite_abd = get_comp_text("nroTramiteABD_comp")
    id_tramite_spn = get_comp_text("text9_comp")
    sistema_origen = get_comp_text("origen_comp")
    resultado_spn = get_comp_text("resultadoSPN_comp")
    sistema_comercial = get_comp_text("sisComercial_comp")
    operador_receptor = get_comp_text("operador_comp")
    fecha_operacion = get_comp_text("fechaOperacion_comp")
    fecha_alta = get_comp_text("fechaAlta_comp")
    estado = get_comp_text("estado_comp")
    fecha_estado = get_comp_text("fechaEstado_comp")
    error_spn = get_comp_text("errorSPN_comp")

    # 2. Suscriptor
    tipo_persona = get_comp_text("tipoPersona_comp")
    apellido_pf = get_comp_text("apellidoPF_comp")
    nombre_pf = get_comp_text("nombrePF_comp")
    tipo_doc = get_comp_text("text2_comp")
    nro_doc = get_comp_text("nroIdentificador_comp")
    razon_social = get_comp_text("razonSocialNombreEnte_comp")
    telefono_contacto = get_comp_text("telefonoContacto_comp")
    email = get_comp_text("email_comp")

    # 3. Servicio Actual
    tecnologia = get_comp_text("tecnologiaServicio_comp")
    producto = get_comp_text("productoServicio_comp")
    modalidad_factura = get_comp_text("modalidadContratacionFactura_comp")
    fecha_ventana_orig = get_comp_text("fvcOrig_comp")

    # 4. Respuesta ABD
    fvc_aprobada = get_comp_text("fvcAprob_comp")
    observaciones_abd = get_comp_text("observABD_comp")
    cant_lineas_portadas = get_comp_text("cantLineasPortadas_comp")
    cant_lineas_rev = get_comp_text("cantLineasRev_comp")
    estado_reversion = get_comp_text("estadoReversion_comp")

    # 5. Extracción de Líneas Asociadas / Portadas
    lineas_encontradas: List[str] = []
    for tr in soup.find_all('tr'):
        tds = [td.get_text(strip=True) for td in tr.find_all('td', recursive=False)]
        if len(tds) >= 2 and tds[0].isdigit() and tds[1].isdigit() and len(tds[1]) == 10:
            lineas_encontradas.append(tds[1])

    lineas_a_portar = list(dict.fromkeys(lineas_encontradas))

    return {
        "nro_tramite_abd": nro_tramite_abd,
        "id_tramite_spn": id_tramite_spn,
        "sistema_origen": sistema_origen,
        "resultado_spn": resultado_spn,
        "sistema_comercial": sistema_comercial,
        "operador_receptor": operador_receptor,
        "fecha_operacion": fecha_operacion,
        "fecha_alta": fecha_alta,
        "estado": estado,
        "fecha_estado": fecha_estado,
        "error_spn": error_spn,
        "tipo_persona": tipo_persona,
        "apellido": apellido_pf,
        "nombre": nombre_pf,
        "tipo_documento": tipo_doc,
        "nro_documento": nro_doc,
        "razon_social": razon_social,
        "telefono_contacto": telefono_contacto,
        "email": email,
        "tecnologia": tecnologia,
        "producto": producto,
        "modalidad_factura": modalidad_factura,
        "fecha_ventana_cambio_orig": fecha_ventana_orig,
        "fecha_ventana_cambio_aprobada": fvc_aprobada,
        "observaciones": observaciones_abd,
        "cantidad_lineas_portadas": cant_lineas_portadas or str(len(lineas_a_portar)),
        "cantidad_lineas_revertidas": cant_lineas_rev,
        "estado_reversion": estado_reversion,
        "lineas_asociadas": lineas_a_portar
    }
