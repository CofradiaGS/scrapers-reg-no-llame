# Mapeo de Campos y Parser de Datos de IRIS

Este documento detalla el funcionamiento del módulo de extracción y normalización [`adapters/scrapers/iris/parser.py`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/parser.py).

El parser es compartido de manera agnóstica tanto por el motor HTTP ([`IrisHttpBot`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/iris_http_bot.py)) como por el motor de Playwright ([`IrisBot`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/iris_bot.py)), garantizando consistencia absoluta en las estructuras de datos generadas.

---

## 1. Función de Limpieza y Normalización (`clean_val`)

El motor Fuego BPM incrusta prefijos internos en los campos de sólo lectura o generados por el servidor. La función [`clean_val()`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/parser.py#L5-L19) aplica los siguientes filtros:

```python
def clean_val(text: str) -> str:
    if not text:
        return ""
    text = text.replace("NON_EDITABLE$", "").strip()
    # Corrección de decodificaciones erróneas UTF-8/ISO-8859-1 en WebLogic legacy:
    text = text.replace("Aprobacin", "Aprobación")
    text = text.replace("Operacin", "Operación")
    text = text.replace("Reversin", "Reversión")
    text = text.replace("Lnea", "Línea")
    text = text.replace("Tecnologa", "Tecnología")
    text = text.replace("Contratacin", "Contratación")
    # Colapso de saltos de línea y tabulaciones internas
    text = re.sub(r'\s+', ' ', text)
    return text
```

---

## 2. Catálogo de Campos Extraídos por Tipo de Operación

La función [`parse_iris_detail()`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/parser.py#L42-L219) recibe el código HTML completo de la pantalla de *Detalle de Operación* e identifica automáticamente el tipo de formulario:

### 2.1. Formulario de Solicitud de Servicio (`FormSolicitud` - Altas y Cambios)

| # | Campo Diccionario (`datos`) | ID del Componente DOM | Tipo de Dato | Descripción / Ejemplo |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `canal_agente` | `canal_comp` | Texto | Código y nombre del canal o agente comercial (ej. `R50 - Arg intercom`). |
| **2** | `punto_de_venta` | `canalPuntodeVenta_comp` | Texto | Código y descripción del punto de venta (ej. `06 - San Nicolas 448`). |
| **3** | `vendedor` | `canalVendedor_comp` | Alfanumérico | Legajo del vendedor (ej. `82550`). |
| **4** | `subvendedor` | `canalSubVendedor_comp` | Alfanumérico | Legajo del subvendedor (ej. `006001`). |
| **5** | `usuario` | `usuarioAlta_comp` | Texto | Usuario del sistema que radicó la solicitud (ej. `SDSDIGITAL`). |
| **6** | `tipo_formulario` | `tipoFormulario_comp` | Texto | Tipo de formulario (ej. `PR - Alta de Linea T3`, `CH - Cambios T3`). |
| **7** | `nro_formulario` | `nroFormulario_comp` | Alfanumérico | Número identificador del formulario (ej. `17799235732021`, `24739081762024`). |
| **8** | `fecha_operacion` | `fechaOperacion_comp` | Fecha/Hora | Fecha y hora de radicación de la operación. |
| **9** | `fecha_alta` | `fechaAlta_comp` | Fecha (DD/MM/YYYY) | Fecha formal de alta de la operación. |
| **10** | `estado` | `estado_comp` | Texto | Estado de la solicitud (ej. `Controlado - Autorizado`). |
| **11** | `fecha_estado` | `fechaEstado_comp` | Fecha/Hora | Fecha y hora del último cambio de estado. |
| **12** | `excepcion` | `excepcion_comp` | Texto (`SI` / `NO`) | Si la operación ingresó por excepción comercial. |
| **13** | `tipo_documento` | `tipoIdentificador_comp` | Texto | Tipo de documento (ej. `Documento Nacional Identidad`). |
| **14** | `nro_documento` | `nroIdentificacion_comp` | Numérico | Número de documento del solicitante (ej. `11020227`). |
| **15** | `lineas_multiples` | `text30_comp` | Texto (`SI` / `NO`) | Indica si la solicitud involucra múltiples líneas. |
| **16** | `solicitud_multiple` | `text31_comp` | Texto (`SI` / `NO`) | Indica si se trata de una solicitud múltiple. |
| **17** | `lineas` | `lineas_comp` | Lista de strings | Líneas telefónicas asociadas al trámite (ej. `["2477500669"]`). |
| **18** | `cliente_existente` | `text32_comp` | Texto (`SI` / `NO`) | Si el titular ya era cliente previo de la compañía. |
| **19** | `pricing_diferencial`| `text34_comp` | Texto (`SI` / `NO`) | Si aplica esquema tarifario diferencial. |
| **20** | `modalidad_entrega` | `text35_comp` | Texto | Modalidad de entrega del equipo o SIM (ej. `DIFERIDA`, `En Tienda`). |
| **21** | `tipo_operacion` | `tipoOperacion_comp` | Texto | Operación radicada (ej. `Altas`, `Cambios`). |
| **22** | `subtipo_operacion` | `subTipoOperacion_comp` | Texto | Subclasificación de la operación (ej. `Equipo`, `Venta Nuevo`). |
| **23** | `forma_contratacion`| `tipoFormaContratacion_comp`| Texto | Régimen comercial contratado (ej. `Venta Nuevo`). |
| **24** | `tipo_producto` | `tipoProducto_comp` | Texto | Tipo de producto (ej. `Control`, `Full`). |
| **25** | `segmento` | `tipoSegmento_comp` | Texto | Segmento de cliente (ej. `Individuos`, `Empresas`). |
| **26** | `documentos` | `arrayDocumentos_comp` | Lista de strings | Documentos requeridos y cargados en el legajo digital. |
| **27** | `qr` | `estadoValidacionQR_comp` | Texto | Estado de la validación del código QR. |
| **28** | `fecha_recepcion` | `fechaRecepcion_comp` | Fecha/Hora | Fecha y hora de recepción en administración comercial. |

### 2.2. Formulario de Portabilidad Numérica (`FormSolicitudPortOut` - Port Out)

| # | Campo Diccionario (`datos`) | ID del Componente DOM | Tipo de Dato | Descripción / Ejemplo |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `nro_tramite_abd` | `nroTramiteABD_comp` | Alfanumérico | Número de trámite ante el Administrador de Base de Datos (ABD). |
| **2** | `id_tramite_spn` | `text9_comp` | Alfanumérico | Identificador del trámite en el Sistema de Portabilidad Numérica (SPN). |
| **3** | `sistema_origen` | `origen_comp` | Texto | Sistema que emite la orden de portabilidad (ej. `SPN`, `SPN_BATCH`, `SCL`). |
| **4** | `resultado_spn` | `resultadoSPN_comp` | Texto | Calificación de respuesta SPN (ej. `Aprobado`, `APROBADA`). |
| **5** | `sistema_comercial` | `sisComercial_comp` | Texto | Plataforma comercial origen de la línea (ej. `Amdocs`, `ATIS`, `OPEN`). |
| **6** | `operador_receptor` | `operador_comp` | Texto | Compañía receptora hacia donde se porta la línea (`Claro`, `Personal`). |
| **7** | `fecha_operacion` | `fechaOperacion_comp` | Fecha/Hora | Fecha exacta en la que se radicó la operación. |
| **8** | `fecha_alta` | `fechaAlta_comp` | Fecha/Hora | Fecha original de alta de la línea en Movistar. |
| **9** | `estado` | `estado_comp` | Texto | Estado del trámite (ej. `Aprobación del ABD`, `Finalizada`). |
| **10** | `fecha_estado` | `fechaEstado_comp` | Fecha/Hora | Última actualización de estado registrada. |
| **11** | `error_spn` | `errorSPN_comp` | Texto | Mensaje o código de error retornado si la portación falló. |
| **12** | `tipo_persona` | `tipoPersona_comp` | Enum / Texto | Tipo de titular: `Persona Fisica`, `Persona Juridica`. |
| **13** | `apellido` | `apellidoPF_comp` | Texto | Apellido del titular persona física. |
| **14** | `nombre` | `nombrePF_comp` | Texto | Nombre de pila del titular persona física. |
| **15** | `tipo_documento` | `text2_comp` | Texto | Tipo de documento de identidad (`Documento Nacional Identidad`, `DNI`, `CUIT`). |
| **16** | `nro_documento` | `nroIdentificador_comp` | Alfanumérico | Número de documento o CUIT sin puntos. |
| **17** | `razon_social` | `razonSocialNombreEnte_comp` | Texto | Razón social si `tipo_persona` es jurídica. |
| **18** | `telefono_contacto` | `telefonoContacto_comp` | Numérico | Teléfono alternativo de contacto provisto en la solicitud. |
| **19** | `email` | `email_comp` | Texto | Correo electrónico del titular. |
| **20** | `tecnologia` | `tecnologiaServicio_comp` | Texto | Tecnología del servicio (ej. `Celular`, `GSM`, `LTE`). |
| **21** | `producto` | `productoServicio_comp` | Texto | Categoría comercial del producto (ej. `Contrato CPP`, `Pospago`, `Control`). |
| **22** | `modalidad_factura` | `modalidadContratacionFactura_comp` | Texto | Modalidad de contratación con factura (`SI` / `NO`). |
| **23** | `fecha_ventana_cambio_orig` | `fvcOrig_comp` | Fecha/Hora | Fecha inicial programada para la Ventana de Cambio técnico. |
| **24** | `cantidad_lineas_portar` | `cantidadLineasAPortar_comp` | Entero | Cantidad total de líneas a portar en el trámite. |
| **25** | `lineas` / `lineas_asociadas` | `nroLineaCombo_comp` | Lista de strings | Lista con todas las líneas celulares (ANI de 10 dígitos) del trámite. |
| **26** | `documentos` | `documentoDesc_comp` | Lista de strings | Documentación adjunta presentada en la solicitud de portabilidad. |
| **27** | `fecha_ventana_cambio_aprobada` | `fvcAprob_comp` | Fecha/Hora | Fecha final aprobada por el ABD para el corte de servicio. |
| **28** | `observaciones` | `observABD_comp` | Texto | Dictamen u observaciones emitidas por el ABD. |
| **29** | `cantidad_lineas_portadas` | `cantLineasPortadas_comp` | Entero | Cantidad de líneas efectivamente portadas. |
| **30** | `cantidad_lineas_revertidas` | `cantLineasRev_comp` | Entero | Cantidad de líneas que fueron revertidas al operador donante. |
| **31** | `estado_reversion` | `estadoReversion_comp` | Texto | Condición de la reversión (ej. `Total`, `NO REVERTIDA`). |

---

### 2.3. Formulario de Port In (`PO - Solicitud PortIn`)

El formulario Port In se detecta automáticamente cuando el HTML contiene alguno de los siguientes indicadores: cadena `"Solicitud PortIn"`, `"PortIn"`, `"Operador Donador"`, `"FormSolicitudPortIn"` o el componente DOM `operadorDonador_comp`.

| # | Campo Diccionario (`datos`) | ID del Componente DOM | Tipo de Dato | Descripción / Ejemplo |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `canal_agente` | `canal_comp` | Texto | Canal y agente comercial que radicó el trámite (ej. `282 - Ce - Global solutions`). |
| **2** | `punto_de_venta` | `canalPuntodeVenta_comp` | Texto | Código y descripción del punto de venta (ej. `33 - Las Heras 136`). |
| **3** | `vendedor` | `canalVendedor_comp` | Alfanumérico | Legajo del vendedor (ej. `51633`). |
| **4** | `subvendedor` | `canalSubVendedor_comp` | Alfanumérico | Legajo del subvendedor (ej. `046001`). |
| **5** | `usuario` | `usuarioAlta_comp` | Texto | Usuario del sistema que radicó la solicitud (ej. `WSPORTA`). |
| **6** | `tipo_formulario` | `tipoFormulario_comp` | Texto | Tipo de formulario siempre `PO - Solicitud PortIn`. |
| **7** | `nro_formulario` | `nroFormulario_comp` | Alfanumérico | Número identificador del formulario Port In (ej. `417956261`). |
| **8** | `fecha_operacion` | `fechaOperacion_comp` | Fecha | Fecha de radicación de la solicitud (ej. `18/9/2026`). |
| **9** | `fecha_alta` | `fechaAlta_comp` | Fecha | Fecha de alta formal del trámite (ej. `18/9/2026`). |
| **10** | `estado` | `estado_comp` | Texto | Estado del trámite (ej. `Aprobación del ABD`). |
| **11** | `fecha_estado` | `fechaEstado_comp` | Fecha | Fecha del último cambio de estado (ej. `21/9/2026`). |
| **12** | `modalidad_entrega` | `modalidadEntrega_comp` | Texto | Modalidad de entrega del SIM (ej. `Retira Sim`, `DIFERIDA`). |
| **13** | `tipo_persona` | `tipoPersona_comp` | Texto | Tipo de titular: `Persona Fisica` o `Persona Juridica`. |
| **14** | `apellido` | `apellidoPI_comp` / `apellidoPF_comp` | Texto | Apellido del suscriptor (ej. `FRAGA`). |
| **15** | `nombre` | `nombrePI_comp` / `nombrePF_comp` | Texto | Nombre del suscriptor (ej. `CLAUDIA ELSA`). |
| **16** | `titular` | *(compuesto)* | Texto | Nombre completo: `"CLAUDIA ELSA FRAGA"`. |
| **17** | `cuit` | `cuitPI_comp` | Alfanumérico | CUIT del titular (vacío en persona física sin actividad comercial). |
| **18** | `tipo_documento` | `tipoDocumentoPI_comp` / `tipoIdentificador_comp` | Texto | Tipo de documento (ej. `Documento Nacional Identidad`). |
| **19** | `nro_documento` | `nroDocumentoPI_comp` / `nroIdentificador_comp` | Numérico | Número de documento sin puntos (ej. `16247363`). |
| **20** | `telefono_contacto` | `telefonoContacto_comp` | Numérico | Teléfono alternativo del suscriptor (ej. `1162842458`). |
| **21** | `comentario_telefono_contacto` | `comentarioTelContacto_comp` | Texto | Aclaración o comentario del teléfono (puede ser vacío). |
| **22** | `email` | `email_comp` / `emailPI_comp` | Texto | Correo electrónico (ej. `sindatos@sindatos.com`). |
| **23** | `autorizado` | `apellidoAutorizado_comp`, `nombreAutorizado_comp`, `tipoDocAutorizado_comp`, `nroDocAutorizado_comp` | Objeto `dict` | Datos del tercero autorizado: `{"apellido": "", "nombre": "", "tipo_documento": "", "nro_documento": "0"}`. |
| **24** | `operador_donador` | `operadorDonador_comp` | Texto | Operador donante, es decir, la compañía **desde** donde se porta (ej. `Claro`). |
| **25** | `modalidad_factura` | `modalidadContratacionFactura_comp` | Texto (`SI`/`NO`) | Si la portación incluye facturación asociada. |
| **26** | `tecnologia` | `tecnologiaPI_comp` / `tecnologiaServicio_comp` | Texto | Tecnología del servicio receptor (ej. `Celular`). |
| **27** | `producto` | `productoPI_comp` / `productoServicio_comp` | Texto | Producto comercial a contratar en la portación (ej. `Contrato CPP`). |
| **28** | `fecha_ventana_cambio_orig` | `fvcOrig_comp` | Fecha | Ventana de cambio técnico originalmente planificada (ej. `21/9/2026`). |
| **29** | `fecha_ventana_cambio_abd` | `fvcABD_comp` | Fecha/Hora | Ventana de cambio aprobada por el ABD (ej. `22/9/2026 11:56:00`). |
| **30** | `formularios_alta` | *(tabla HTML)* | Lista de objetos | Formularios de alta asociados al Port In: `[{"tipo_formulario": "PR - Alta de Linea T3", "nro_formulario": "32563349772026", "estado_legajo": "Controlado - Autorizado"}, ...]`. |
| **31** | `cantidad_lineas_portar` | `cantidadLineasAPortar_comp` | Entero | Cantidad total de líneas incluidas en el trámite (ej. `4`). |
| **32** | `lineas` / `lineas_asociadas` | `nroLineaCombo_comp` / tabla | Lista de strings | Números de línea de 10 dígitos a portar (extraídos de tabla o regex sobre el HTML). |
| **33** | `documentos` | `documentoDesc_comp` | Lista de strings | Documentos requeridos (ej. `["PORTABILIDAD - Formulario Solicitud de Portabilidad", "Documento Identificatorio del Titular o representante Legal"]`). |
| **34** | `fecha_recepcion` | `fechaRecepcion_comp` | Fecha/Hora | Fecha y hora de recepción en administración comercial (ej. `18/9/2026 14:31:00`). |

#### Lógica de detección de formularios de alta vinculados

La tabla de "Formularios de alta" está embebida en el HTML como una `<table>` con headers `Tipo de Formulario`, `Nro de Formulario` y `Estado Legajo`. El parser la detecta así:

```python
for table in soup.find_all("table"):
    hdrs = [th.get_text(strip=True) for th in table.find_all("th")]
    if any(h in hdrs for h in ["Tipo de Formulario", "Nro de Formulario", "Estado Legajo"]):
        for row in table.find_all("tr")[1:]:
            tds = row.find_all("td")
            if len(tds) >= 2:
                formularios_alta.append({
                    "tipo_formulario": clean_val(tds[0].get_text()),
                    "nro_formulario":  clean_val(tds[1].get_text()),
                    "estado_legajo":   clean_val(tds[2].get_text()) if len(tds) > 2 else "",
                })
```
## 3. Extracción de Líneas Asociadas

En solicitudes corporativas o planes familiares, un mismo trámite de portabilidad puede abarcar múltiples números telefónicos. El parser recorre la tabla HTML de líneas del suscriptor aplicando una regla de extracción estructurada:


```python
lineas_encontradas: List[str] = []
for tr in soup.find_all('tr'):
    tds = [td.get_text(strip=True) for td in tr.find_all('td', recursive=False)]
    # La columna 0 es el índice (1, 2, ...) y la columna 1 es el número de línea (10 dígitos)
    if len(tds) >= 2 and tds[0].isdigit() and tds[1].isdigit() and len(tds[1]) == 10:
        lineas_encontradas.append(tds[1])

# Deduplicación preservando orden
lineas_a_portar = list(dict.fromkeys(lineas_encontradas))
```

---

## 4. Transformación al Modelo de Dominio ScrapeResult

Tanto [`IrisHttpAdapter`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/iris_http_adapter.py#L31-L101) como [`IrisBrowserAdapter`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/iris/iris_browser_adapter.py#L32-L102) transforman este diccionario en la entidad central del dominio:

```python
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
```

### Estructura del JSON Persistido (`to_namespace_dict`)
Al almacenarse en el campo `datos_json` de la base de datos, el resultado queda aislado bajo el namespace de la fuente (`"iris"`), protegiendo los datos recolectados por otros scrapers en el pipeline:

```json
{
  "iris": {
    "status": "coincidencia",
    "fuente": "iris",
    "operador": "Claro",
    "operador_receptor": "Claro",
    "titular": {
      "nombre": "JUAN CARLOS",
      "apellido": "PEREZ",
      "razon_social": "",
      "tipo_documento": "DNI",
      "nro_documento": "28123456",
      "tipo_persona": "FÍSICA",
      "telefono_contacto": "1140001111",
      "email": "jperez@example.com"
    },
    "servicio": {
      "tecnologia": "GSM",
      "producto": "Pospago",
      "modalidad_factura": "Electrónica"
    },
    "fechas": {
      "fecha_operacion": "15/08/2026",
      "fecha_alta": "10/03/2018",
      "fecha_estado": "16/08/2026 14:32:00",
      "fvc_orig": "18/08/2026",
      "fvc_aprobada": "18/08/2026"
    },
    "detalles": {
      "nro_tramite_abd": "ABD-2026-987654",
      "id_tramite_spn": "SPN-88776655",
      "sistema_origen": "SPN_BATCH",
      "resultado_spn": "APROBADA",
      "sistema_comercial": "ATIS",
      "estado_tramite": "Finalizada",
      "error_spn": "",
      "observaciones": "Sin objeciones técnicas",
      "cantidad_lineas_portadas": 1,
      "cantidad_lineas_revertidas": 0,
      "estado_reversion": "NO REVERTIDA"
    },
    "raw": { },
    "ultima_modificacion": "2026-08-16 14:32:00"
  }
}
```
