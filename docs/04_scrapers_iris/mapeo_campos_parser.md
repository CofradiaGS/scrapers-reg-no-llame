# Mapeo de Campos y Parser de Datos de IRIS

Este documento detalla el funcionamiento del módulo de extracción y normalización [`adapters/scrapers/iris/parser.py`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/iris/parser.py).

El parser es compartido de manera agnóstica tanto por el motor HTTP ([`IrisHttpBot`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/iris/iris_http_bot.py)) como por el motor de Playwright ([`IrisBot`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/iris/iris_bot.py)), garantizando consistencia absoluta en las estructuras de datos generadas.

---

## 1. Función de Limpieza y Normalización (`clean_val`)

El motor Fuego BPM incrusta prefijos internos en los campos de sólo lectura o generados por el servidor. La función [`clean_val()`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/iris/parser.py#L5-L19) aplica los siguientes filtros:

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

## 2. Catálogo de los 25+ Campos Extraídos

La función [`parse_iris_detail()`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/iris/parser.py#L21-L113) recibe el código HTML completo de la pantalla de *Detalle de Operación* y procesa cinco secciones de datos:

| # | Campo Diccionario (`datos`) | ID del Componente DOM | Tipo de Dato | Descripción / Ejemplo |
| :---: | :--- | :--- | :--- | :--- |
| **1** | `nro_tramite_abd` | `nroTramiteABD_comp` | Alfanumérico | Número de trámite ante el Administrador de Base de Datos (ABD). |
| **2** | `id_tramite_spn` | `text9_comp` | Alfanumérico | Identificador del trámite en el Sistema de Portabilidad Numérica (SPN). |
| **3** | `sistema_origen` | `origen_comp` | Texto | Sistema que emite la orden de portabilidad (ej. `SPN_BATCH`, `SCL`). |
| **4** | `resultado_spn` | `resultadoSPN_comp` | Texto | Calificación de respuesta SPN (ej. `APROBADA`, `RECHAZADA`). |
| **5** | `sistema_comercial` | `sisComercial_comp` | Texto | Plataforma comercial origen de la línea (ej. `ATIS`, `OPEN`). |
| **6** | `operador_receptor` | `operador_comp` | Texto | Compañía receptora hacia donde se porta la línea (`Claro`, `Personal`). |
| **7** | `fecha_operacion` | `fechaOperacion_comp` | Fecha (DD/MM/YYYY) | Fecha exacta en la que se radicó la operación. |
| **8** | `fecha_alta` | `fechaAlta_comp` | Fecha (DD/MM/YYYY) | Fecha original de alta de la línea en Movistar. |
| **9** | `estado` | `estado_comp` | Texto | Estado del trámite (ej. `Finalizada`, `En Proceso`, `Revertida`). |
| **10** | `fecha_estado` | `fechaEstado_comp` | Fecha/Hora | Última actualización de estado registrada. |
| **11** | `error_spn` | `errorSPN_comp` | Texto | Mensaje o código de error retornado si la portación falló. |
| **12** | `tipo_persona` | `tipoPersona_comp` | Enum / Texto | Tipo de titular: `FÍSICA`, `JURÍDICA`. |
| **13** | `apellido` | `apellidoPF_comp` | Texto | Apellido del titular persona física. |
| **14** | `nombre` | `nombrePF_comp` | Texto | Nombre de pila del titular persona física. |
| **15** | `tipo_documento` | `text2_comp` | Texto | Tipo de documento de identidad (`DNI`, `LC`, `LE`, `PAS`, `CUIT`). |
| **16** | `nro_documento` | `nroIdentificador_comp` | Alfanumérico | Número de documento o CUIT sin puntos. |
| **17** | `razon_social` | `razonSocialNombreEnte_comp` | Texto | Razón social si `tipo_persona` es jurídica. |
| **18** | `telefono_contacto` | `telefonoContacto_comp` | Numérico | Teléfono alternativo de contacto provisto en la solicitud. |
| **19** | `email` | `email_comp` | Texto | Correo electrónico del titular. |
| **20** | `tecnologia` | `tecnologiaServicio_comp` | Texto | Tecnología del servicio (ej. `GSM`, `LTE`, `CDMA`, `FIBRA`). |
| **21** | `producto` | `productoServicio_comp` | Texto | Categoría comercial del producto (ej. `Pospago`, `Prepago`, `Control`). |
| **22** | `modalidad_factura` | `modalidadContratacionFactura_comp` | Texto | Modalidad de contratación y emisión de factura. |
| **23** | `fecha_ventana_cambio_orig` | `fvcOrig_comp` | Fecha (DD/MM/YYYY) | Fecha inicial programada para la Ventana de Cambio técnico. |
| **24** | `fecha_ventana_cambio_aprobada` | `fvcAprob_comp` | Fecha (DD/MM/YYYY) | Fecha final aprobada por el ABD para el corte de servicio. |
| **25** | `observaciones` | `observABD_comp` | Texto | Dictamen u observaciones emitidas por el ABD. |
| **26** | `cantidad_lineas_portadas` | `cantLineasPortadas_comp` | Entero | Cantidad de líneas incluidas en el mismo trámite. |
| **27** | `cantidad_lineas_revertidas` | `cantLineasRev_comp` | Entero | Cantidad de líneas que fueron revertidas al operador donante. |
| **28** | `estado_reversion` | `estadoReversion_comp` | Texto | Condición de la reversión (ej. `NO REVERTIDA`, `REVERSION TOTAL`). |
| **29** | `lineas_asociadas` | Filas `<tr>` / `<td>` | Lista de strings | Lista con todas las líneas celulares (ANI de 10 dígitos) del trámite. |

---

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

Tanto [`IrisHttpAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/iris/iris_http_adapter.py#L31-L101) como [`IrisBrowserAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/iris/iris_browser_adapter.py#L32-L102) transforman este diccionario en la entidad central del dominio:

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
