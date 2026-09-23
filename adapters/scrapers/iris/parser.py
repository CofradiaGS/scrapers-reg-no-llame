import re
from typing import Dict, Any, List
from bs4 import BeautifulSoup

def clean_val(text: str) -> str:
    """Limpia cadenas, normaliza espacios y remueve prefijos de Fuego BPM como NON_EDITABLE$"""
    if not text:
        return ""
    text = text.replace("NON_EDITABLE$", "").strip()
    
    # Reparar posibles artefactos de codificación comunes de Latin1 / Windows-1252
    reemplazos = {
        "Aprobacin": "Aprobación",
        "Operacin": "Operación",
        "Reversin": "Reversión",
        "Lnea": "Línea",
        "Lneas": "Líneas",
        "Tecnologa": "Tecnología",
        "Contratacin": "Contratación",
        "Recepcin": "Recepción",
        "Descripcin": "Descripción",
        "Cdigo": "Código",
        "SubCdigo": "SubCódigo",
        "Razn": "Razón",
        "Trmite": "Trámite",
        "Solucin": "Solución",
        "Administracin": "Administración",
        "Excepcin": "Excepción",
        "Telfono": "Teléfono",
        "Mltiples": "Múltiples",
        "Mltiple": "Múltiple",
        "Tamao": "Tamaño",
        "debern": "deberán",
        "comn": "común",
        "slo": "sólo",
    }
    for k, v in reemplazos.items():
        text = text.replace(k, v)
    text = text.replace("\ufffd", "")
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def parse_iris_detail(html_content: str) -> Dict[str, Any]:
    """
    Parsea la pantalla de 'Consulta de operación Detalle' de IRIS (Oracle/Fuego BPM).
    Extrae exhaustivamente todos los campos para todos los tipos de formularios
    (Altas, Cambios, Port Out, Port In, etc.) sin omitir ningún campo.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    def get_comp_text(comp_id: str) -> str:
        elem = soup.find(id=comp_id)
        if not elem:
            return ""
        txt = clean_val(elem.get_text())
        if txt:
            return txt
        inp = elem.find(['input', 'select', 'textarea'])
        if inp:
            if inp.name == 'select':
                opt = inp.find('option', selected=True)
                if opt and opt.get_text(strip=True):
                    return clean_val(opt.get_text(strip=True))
            if inp.get('value'):
                return clean_val(inp.get('value'))
        return ""

    def get_label_value(label_regex: str) -> str:
        pattern = re.compile(label_regex, re.IGNORECASE)
        for td in soup.find_all(['td', 'th', 'div', 'label']):
            txt = td.get_text(strip=True)
            if pattern.search(txt) and len(txt) <= 50:
                sib = td.find_next_sibling(['td', 'th', 'div'])
                if sib:
                    inp = sib.find(['input', 'select', 'textarea'])
                    if inp and inp.get('value'):
                        return clean_val(inp.get('value'))
                    val = clean_val(sib.get_text())
                    if val and not pattern.search(val):
                        return val
        return ""

    # Determinar tipo de formulario
    # NOTA: Port In y Port Out comparten 'lineasAPortarTable_comp'.
    # Verificamos primero si es Port In por sus señales distintivas.
    is_port_in = bool(
        "Solicitud PortIn" in html_content or
        "FormSolicitudPortIn" in html_content or
        "PortIn" in html_content or
        "Operador Donador" in html_content or
        soup.find(id="operadorDonador_comp") is not None
    )
    is_port_out = bool(
        not is_port_in and (
            soup.find(id="nroTramiteABD_comp") or
            "FormSolicitudPortOut" in html_content or
            "lineasAPortarTable_comp" in html_content
        )
    )

    detalle: Dict[str, Any] = {}

    if is_port_out:
        # =========================================================================
        # FORMULARIO DE PORTABILIDAD / PORT OUT
        # =========================================================================
        # 1. Trámite y Operación
        detalle["nro_tramite_abd"] = get_comp_text("nroTramiteABD_comp") or get_label_value(r"Nro\s*Tr[aá]mite\s*ABD")
        detalle["id_tramite_spn"] = get_comp_text("text9_comp") or get_label_value(r"Id\s*Tramite\s*SPN")
        detalle["sistema_origen"] = get_comp_text("origen_comp") or get_label_value(r"Sistema\s*Origen")
        detalle["resultado_spn"] = get_comp_text("resultadoSPN_comp") or get_label_value(r"Resultado\s*SPN")
        detalle["sistema_comercial"] = get_comp_text("sisComercial_comp") or get_label_value(r"Sistema\s*Comercial")
        detalle["operador_receptor"] = get_comp_text("operador_comp") or get_label_value(r"Operador\s*Receptor")
        detalle["fecha_operacion"] = get_comp_text("fechaOperacion_comp") or get_label_value(r"Fecha\s*Operaci[oó]n")
        detalle["fecha_alta"] = get_comp_text("fechaAlta_comp") or get_label_value(r"Fecha\s*Alta")
        detalle["estado"] = get_comp_text("estado_comp") or get_label_value(r"^Estado$")
        detalle["fecha_estado"] = get_comp_text("fechaEstado_comp") or get_label_value(r"Fecha\s*Estado")
        detalle["error_spn"] = get_comp_text("errorSPN_comp") or get_label_value(r"Error\s*SPN")

        # 2. Suscriptor
        detalle["tipo_persona"] = get_comp_text("tipoPersona_comp") or get_label_value(r"Tipo\s*de\s*Persona")
        detalle["apellido"] = get_comp_text("apellidoPF_comp") or get_label_value(r"Apellido")
        detalle["nombre"] = get_comp_text("nombrePF_comp") or get_label_value(r"Nombre")
        detalle["tipo_documento"] = get_comp_text("text2_comp") or get_label_value(r"Tipo\s*Doc(?:umento)?(?:\s*Ident)?") or "Documento Nacional Identidad"
        detalle["nro_documento"] = get_comp_text("nroIdentificador_comp") or get_label_value(r"Nro\.?\s*Doc(?:\.?\s*Ident)?")
        detalle["razon_social"] = get_comp_text("razonSocialNombreEnte_comp") or get_label_value(r"Raz[oó]n\s*Social")
        detalle["telefono_contacto"] = get_comp_text("telefonoContacto_comp") or get_label_value(r"Tel[eé]fono\s*de\s*Contacto")
        detalle["email"] = get_comp_text("email_comp") or get_label_value(r"E-?mail")

        # 3. Servicio Actual
        detalle["tecnologia"] = get_comp_text("tecnologiaServicio_comp") or get_label_value(r"Tecnolog[ií]a")
        detalle["producto"] = get_comp_text("productoServicio_comp") or get_label_value(r"^Producto$")
        detalle["modalidad_factura"] = get_comp_text("modalidadContratacionFactura_comp") or get_label_value(r"Modalidad\s*de\s*Contrataci[oó]n\s*con\s*Factura")
        detalle["fecha_ventana_cambio_orig"] = get_comp_text("fvcOrig_comp") or get_label_value(r"Fecha\s*Ventana\s*de\s*Cambio\s*Orig")

        # 4. Líneas a Portar
        detalle["linea_declara_pin"] = get_label_value(r"Nro\s*de\s*la\s*L[ií]nea\s*que\s*declara\s*el\s*PIN")
        cant_portar = get_comp_text("cantidadLineasAPortar_comp") or get_label_value(r"Cantidad\s*de\s*L[ií]neas\s*a\s*Portar")
        detalle["cantidad_lineas_portar"] = cant_portar

        lineas_portar: List[str] = []
        for combo in soup.find_all(id="nroLineaCombo_comp"):
            txt = clean_val(combo.get_text())
            if txt and txt.isdigit() and len(txt) == 10:
                lineas_portar.append(txt)
        if not lineas_portar:
            for tr in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all("td", recursive=False)]
                if len(tds) >= 2 and tds[0].isdigit() and tds[1].isdigit() and len(tds[1]) == 10:
                    lineas_portar.append(tds[1])
        lineas_port_unicas = list(dict.fromkeys(lineas_portar))
        detalle["lineas"] = lineas_port_unicas
        detalle["lineas_asociadas"] = lineas_port_unicas

        # 5. Documentos
        docs_port: List[str] = []
        for doc_desc in soup.find_all(id="documentoDesc_comp"):
            d_txt = clean_val(doc_desc.get_text())
            if d_txt and d_txt not in docs_port:
                docs_port.append(d_txt)
        detalle["documentos"] = docs_port

        # 6. Respuesta ABD y Reversión
        detalle["fecha_ventana_cambio_aprobada"] = get_comp_text("fvcAprob_comp") or get_label_value(r"Fecha\s*Ventana\s*de\s*Cambio\s*Aprobada")
        detalle["observaciones"] = get_comp_text("observABD_comp") or get_label_value(r"Observaciones")
        detalle["cantidad_lineas_portadas"] = get_comp_text("cantLineasPortadas_comp") or get_label_value(r"Cantidad\s*de\s*Lineas\s*Portadas") or cant_portar or str(len(lineas_port_unicas))
        detalle["cantidad_lineas_revertidas"] = get_comp_text("cantLineasRev_comp") or get_label_value(r"Cantidad\s*de\s*Lineas\s*Revertidas")
        detalle["estado_reversion"] = get_comp_text("estadoReversion_comp") or get_label_value(r"Estado\s*de\s*Reversi[oó]n")

    elif is_port_in:
        # =========================================================================
        # FORMULARIO PORT IN (PO - Solicitud PortIn)
        # =========================================================================
        # 1. Canal / Vendedor / Usuario
        detalle["canal_agente"] = get_comp_text("canal_comp") or get_label_value(r"Canal\s*/\s*Agente")
        detalle["punto_de_venta"] = get_comp_text("canalPuntodeVenta_comp") or get_label_value(r"Punto\s*de\s*Venta")
        detalle["vendedor"] = get_comp_text("canalVendedor_comp") or get_label_value(r"^Vendedor$")
        detalle["subvendedor"] = get_comp_text("canalSubVendedor_comp") or get_label_value(r"SubVendedor")
        detalle["usuario"] = get_comp_text("usuarioAlta_comp") or get_label_value(r"^Usuario$")

        # 2. Datos de la Operación
        detalle["tipo_formulario"] = get_comp_text("tipoFormulario_comp") or get_label_value(r"Tipo\s*de\s*Formulario")
        detalle["nro_formulario"] = get_comp_text("nroFormulario_comp") or get_label_value(r"Nro\s*de\s*Formulario")
        detalle["fecha_operacion"] = get_comp_text("fechaOperacion_comp") or get_label_value(r"Fecha\s*Operaci[oó]n")
        detalle["fecha_alta"] = get_comp_text("fechaAlta_comp") or get_label_value(r"Fecha\s*Alta")
        detalle["estado"] = get_comp_text("estado_comp") or get_label_value(r"^Estado$")
        detalle["fecha_estado"] = get_comp_text("fechaEstado_comp") or get_label_value(r"Fecha\s*Estado")
        detalle["modalidad_entrega"] = get_comp_text("modalidadEntrega_comp") or get_label_value(r"Modalidad(?:de)?Entrega|ModalidadEntrega")

        # 3. Datos del Suscriptor
        detalle["tipo_persona"] = get_comp_text("tipoPersona_comp") or get_label_value(r"Tipo\s*de\s*Persona")
        apellido_pi = get_comp_text("apellidoPI_comp") or get_comp_text("apellidoPF_comp") or get_label_value(r"^Apellido$")
        nombre_pi = get_comp_text("nombrePI_comp") or get_comp_text("nombrePF_comp") or get_label_value(r"^Nombre$")
        detalle["apellido"] = apellido_pi
        detalle["nombre"] = nombre_pi
        titular_pi = f"{nombre_pi} {apellido_pi}".strip()
        if titular_pi:
            detalle["titular"] = titular_pi
        detalle["cuit"] = get_comp_text("cuitPI_comp") or get_label_value(r"^Cuit$")
        detalle["tipo_documento"] = (
            get_comp_text("tipoDocumentoPI_comp")
            or get_comp_text("tipoIdentificador_comp")
            or get_label_value(r"Tipo\s*Doc(?:umento)?(?:\s*Ident)?")
            or "Documento Nacional Identidad"
        )
        detalle["nro_documento"] = (
            get_comp_text("nroDocumentoPI_comp")
            or get_comp_text("nroIdentificador_comp")
            or get_label_value(r"Nro\.?\s*Doc(?:\.?\s*Ident)?")
        )
        detalle["telefono_contacto"] = get_comp_text("telefonoContacto_comp") or get_label_value(r"Tel[eé]fono\s*de\s*Contacto")
        detalle["comentario_telefono_contacto"] = get_comp_text("comentarioTelContacto_comp") or get_label_value(r"Comentario\s*Tel\.?\s*de\s*Contacto")
        detalle["email"] = get_comp_text("email_comp") or get_comp_text("emailPI_comp") or get_label_value(r"E-?mail")

        # 4. Datos del Autorizado
        autorizado_apellido = get_comp_text("apellidoAutorizado_comp") or get_comp_text("apellidoApo_comp") or get_label_value(r"Apellido\s*Autorizado")
        autorizado_nombre = get_comp_text("nombreAutorizado_comp") or get_comp_text("nombreApo_comp") or get_label_value(r"Nombre\s*Autorizado")
        autorizado_tipo_doc = get_comp_text("tipoDocAutorizado_comp") or get_comp_text("tipoIdentificadorApo_comp") or ""
        autorizado_nro_doc = get_comp_text("nroDocAutorizado_comp") or get_comp_text("nroDocIdentApo_comp") or "0"
        detalle["autorizado"] = {
            "apellido": autorizado_apellido,
            "nombre": autorizado_nombre,
            "tipo_documento": autorizado_tipo_doc,
            "nro_documento": autorizado_nro_doc,
        }

        # 5. Datos del Servicio Actual (operador donador)
        detalle["operador_donador"] = get_comp_text("operadorDonador_comp") or get_label_value(r"Operador\s*Donador")
        detalle["modalidad_factura"] = (
            get_comp_text("modalidadContratacionFactura_comp")
            or get_label_value(r"Modalidad\s*de\s*Contrataci[oó]n\s*con\s*Factura")
        )

        # 6. Datos del Servicio a Contratar
        detalle["tecnologia"] = get_comp_text("tecnologiaPI_comp") or get_comp_text("tecnologiaServicio_comp") or get_label_value(r"Tecnolog[ií]a")
        detalle["producto"] = get_comp_text("productoPI_comp") or get_comp_text("productoServicio_comp") or get_label_value(r"^Producto$")
        detalle["fecha_ventana_cambio_orig"] = (
            get_comp_text("fechaVCOrig_comp")
            or get_comp_text("fvcOrig_comp")
            or get_label_value(r"Fecha\s*Ventana\s*de\s*Cambio\s*Orig(?:\.|inal)?")
        )
        detalle["fecha_ventana_cambio_abd"] = (
            get_comp_text("fechaVCABD_comp")
            or get_comp_text("fvcABD_comp")
            or get_label_value(r"Fecha\s*Ventana\s*de\s*Cambio\s*ABD")
        )

        # 7. Formularios de Alta vinculados (tabla de asociados al Port In)
        formularios_alta: List[Dict[str, str]] = []
        container = soup.find(id="arrayLineasAPortar_comp") or soup.find(id="formularios_alta_table")
        search_scope = container.find_all("table") if container else soup.find_all("table")

        for table in search_scope:
            rows = table.find_all("tr")
            for r_idx, row in enumerate(rows):
                cell_texts = [clean_val(c.get_text()) for c in row.find_all(["th", "td"])]
                if "Estado Legajo" in cell_texts and ("Tipo de Formulario" in cell_texts or "Nro de Formulario" in cell_texts):
                    for data_row in rows[r_idx + 1:]:
                        tds = data_row.find_all("td", recursive=False)
                        texts = [clean_val(td.get_text()) for td in tds if clean_val(td.get_text())]
                        if texts and texts[0].isdigit() and len(texts[0]) <= 3:
                            texts = texts[1:]
                        if len(texts) >= 2:
                            tf = texts[0]
                            nf = texts[1]
                            el = texts[2] if len(texts) > 2 else ""
                            if tf and nf and tf != "Tipo de Formulario" and "Nro de Formulario" not in nf and "/" not in tf and "Paging" not in tf:
                                formularios_alta.append({
                                    "tipo_formulario": tf,
                                    "nro_formulario": nf,
                                    "estado_legajo": el,
                                })
                    break
            if formularios_alta:
                break
        detalle["formularios_alta"] = formularios_alta

        # 8. Líneas a Portar
        cant_portar_pi = (
            get_comp_text("cantidadLineasAPortar_comp")
            or get_label_value(r"Cantidad\s*de\s*L[ií]neas\s*a\s*Portar")
        )
        detalle["cantidad_lineas_portar"] = cant_portar_pi

        lineas_pi: List[str] = []
        for combo in soup.find_all(id="nroLineaCombo_comp"):
            txt = clean_val(combo.get_text())
            if txt and txt.isdigit() and len(txt) == 10:
                lineas_pi.append(txt)
        if not lineas_pi:
            for tr in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all("td", recursive=False)]
                if len(tds) >= 2 and tds[0].isdigit() and tds[1].isdigit() and len(tds[1]) == 10:
                    lineas_pi.append(tds[1])
        if not lineas_pi:
            for container_id in ["arrayLineasRechazoABD_comp", "arrayReversion_comp", "lineasAPortarTable_comp"]:
                c = soup.find(id=container_id)
                if c:
                    for td in c.find_all(["td", "div"]):
                        t = clean_val(td.get_text())
                        if t.isdigit() and len(t) == 10 and not t.startswith("0"):
                            lineas_pi.append(t)
        lineas_pi_unicas = list(dict.fromkeys(lineas_pi))
        detalle["lineas"] = lineas_pi_unicas
        detalle["lineas_asociadas"] = lineas_pi_unicas

        # 9. Documentos
        docs_pi: List[str] = []
        for doc_desc in soup.find_all(id="documentoDesc_comp"):
            d_txt = clean_val(doc_desc.get_text())
            if d_txt and d_txt not in docs_pi:
                docs_pi.append(d_txt)
        if not docs_pi:
            # fallback: buscar texto de documentos en contexto de tabla de documentos
            for tag in soup.find_all(string=re.compile(r"PORTABILIDAD|Documento Identificatorio", re.IGNORECASE)):
                d_txt = clean_val(str(tag))
                if d_txt and d_txt not in docs_pi:
                    docs_pi.append(d_txt)
        detalle["documentos"] = docs_pi

        # 10. Recepción en administración comercial
        detalle["fecha_recepcion"] = (
            get_comp_text("fechaRecepcion_comp")
            or get_label_value(r"Fecha\s*de\s*Recepci[oó]n")
        )

        # 11. Datos de Reversión (si existen)
        cant_rev = get_comp_text("cantLineasRev_comp") or get_label_value(r"Cantidad\s*de\s*Lineas\s*Revertidas")
        if cant_rev or soup.find(id="estadoReversion_comp"):
            detalle["cantidad_lineas_portadas"] = get_comp_text("cantLineasPortadas_comp") or get_label_value(r"Cantidad\s*de\s*Lineas\s*Portadas") or cant_portar_pi
            detalle["cantidad_lineas_revertidas"] = cant_rev or "0"
            detalle["estado_reversion"] = get_comp_text("estadoReversion_comp") or get_label_value(r"Estado\s*de\s*Reversi[oó]n")

    else:
        # =========================================================================
        # FORMULARIO DE SOLICITUD DE SERVICIO (ALTAS / CAMBIOS)
        # =========================================================================
        # 1. Canal / Agente / Usuario
        detalle["canal_agente"] = get_comp_text("canal_comp") or get_label_value(r"Canal\s*/\s*Agente")
        detalle["punto_de_venta"] = get_comp_text("canalPuntodeVenta_comp") or get_label_value(r"Punto\s*de\s*Venta")
        detalle["vendedor"] = get_comp_text("canalVendedor_comp") or get_label_value(r"^Vendedor$")
        detalle["subvendedor"] = get_comp_text("canalSubVendedor_comp") or get_label_value(r"SubVendedor")
        detalle["usuario"] = get_comp_text("usuarioAlta_comp") or get_label_value(r"^Usuario$")

        # 2. Datos de la solicitud de servicio
        detalle["tipo_formulario"] = get_comp_text("tipoFormulario_comp") or get_label_value(r"Tipo\s*de\s*Formulario")
        detalle["nro_formulario"] = get_comp_text("nroFormulario_comp") or get_label_value(r"Nro\s*de\s*Formulario")
        detalle["fecha_operacion"] = get_comp_text("fechaOperacion_comp") or get_label_value(r"Fecha\s*Operaci[oó]n")
        detalle["fecha_alta"] = get_comp_text("fechaAlta_comp") or get_label_value(r"Fecha\s*Alta")
        detalle["estado"] = get_comp_text("estado_comp") or get_label_value(r"^Estado$")
        detalle["fecha_estado"] = get_comp_text("fechaEstado_comp") or get_label_value(r"Fecha\s*Estado")
        detalle["excepcion"] = get_comp_text("excepcion_comp") or get_label_value(r"Excepci[oó]n")
        detalle["tipo_documento"] = get_comp_text("tipoIdentificador_comp") or get_label_value(r"Tipo\s*de\s*Documento") or "Documento Nacional Identidad"
        detalle["nro_documento"] = get_comp_text("nroIdentificacion_comp") or get_label_value(r"Nro\s*de\s*Documento")
        detalle["lineas_multiples"] = get_comp_text("text30_comp") or get_label_value(r"L[ií]neas\s*M[uú]ltiples")
        detalle["solicitud_multiple"] = get_comp_text("text31_comp") or get_label_value(r"Solicitud\s*M[uú]ltiple")

        # Líneas
        lineas_sol: List[str] = []
        for c_id in ["lineas[0][0]_comp", "lineas_comp"]:
            el = soup.find(id=c_id)
            if el:
                for num in re.findall(r'\b\d{10}\b', el.get_text()):
                    lineas_sol.append(num)
        if not lineas_sol:
            for num in re.findall(r'\b\d{10}\b', soup.get_text()):
                lineas_sol.append(num)
        lineas_sol_unicas = list(dict.fromkeys(lineas_sol))
        detalle["lineas"] = lineas_sol_unicas
        detalle["lineas_asociadas"] = lineas_sol_unicas

        # 3. Datos de la operación
        detalle["cliente_existente"] = get_comp_text("text32_comp") or get_label_value(r"Cliente\s*Existente")
        detalle["pricing_diferencial"] = get_comp_text("text34_comp") or get_label_value(r"Pricing\s*Diferencial")
        detalle["modalidad_entrega"] = get_comp_text("text35_comp") or get_label_value(r"Modalidad\s*Entrega")

        # Tabla de Operación (Tipo de Operación, SubTipo, Forma de Contratación, Tipo de Producto, Segmento)
        tipo_op = get_comp_text("tipoOperacion_comp")
        subtipo_op = get_comp_text("subTipoOperacion_comp")
        forma_contratacion = get_comp_text("tipoFormaContratacion_comp")
        tipo_prod = get_comp_text("tipoProducto_comp")
        segmento = get_comp_text("tipoSegmento_comp")

        op_elem = soup.find(id="tipoOperacion_comp")
        if op_elem:
            parent_table = op_elem.find_parent("table")
            if parent_table:
                rows = parent_table.find_all("tr")
                if len(rows) >= 2:
                    headers = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
                    vals = [td.get_text(strip=True) for td in rows[1].find_all(["td", "th"])]
                    col_map = dict(zip(headers, vals))
                    if not tipo_op: tipo_op = col_map.get("Tipo de Operación", "")
                    if not subtipo_op: subtipo_op = col_map.get("SubTipo de Operación", "")
                    if not forma_contratacion: forma_contratacion = col_map.get("Forma de Contratación", "")
                    if not tipo_prod: tipo_prod = col_map.get("Tipo de Producto", "")
                    if not segmento: segmento = col_map.get("Segmento", "")

        detalle["tipo_operacion"] = tipo_op
        detalle["subtipo_operacion"] = subtipo_op
        detalle["forma_contratacion"] = forma_contratacion
        detalle["tipo_producto"] = tipo_prod
        detalle["segmento"] = segmento

        # 4. Documentos
        docs_sol: List[str] = []
        for doc_desc in soup.find_all(id="documentoDesc_comp"):
            d_txt = clean_val(doc_desc.get_text())
            if d_txt and d_txt not in docs_sol:
                docs_sol.append(d_txt)
        detalle["documentos"] = docs_sol

        # 5. Validación QR
        detalle["qr"] = get_comp_text("estadoValidacionQR_comp") or get_label_value(r"QR") or ""

        # 6. Recepción en administración comercial
        detalle["fecha_recepcion"] = get_comp_text("fechaRecepcion_comp") or get_label_value(r"Fecha\s*de\s*Recepci[oó]n")

    return detalle
