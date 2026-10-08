# INFORME GERENCIAL Y TÉCNICO DE SCRAPING
## Desempeño Operativo Septiembre vs. Agosto 2026, Arquitectura de Cascada y Catálogo de Motores

- **Emisión:** 08 de octubre de 2026
- **Audiencia:** Dirección de Operaciones y Tecnología — CofradiaGS
- **Bases Analizadas:** `queue_registro_no_llame` | `cola_automatizacion`
- **Novedades Clave:** ENACOM Web (OCR local), Telecentro, Power CRM FTTH y nueva cuenta corporativa IRIS

---

### Indicadores Clave del Mes (KPIs)

| Dimensión | Valor Septiembre 2026 | Comparativa vs. Agosto 2026 |
| :--- | :---: | :---: |
| **Registro No Llame (Resueltos)** | **48,655** | +100% *(Nuevo inicio productivo, 0 en agosto)* |
| **Cola de Automatización (Completados)** | **1,188,031** | +260.9% *(Multiplicado por 3.6 veces)* |
| **Cola de Automatización (Ingresados)** | **13.40 M** | +486.9% *(Frente a 2.28 M en agosto)* |
| **TOTAL REGISTROS RESUELTOS** | **1,236,686** | **+275.6%** *(Frente a 329,222 en agosto)* |

---

## ¿De qué trata este informe?

Este documento resume lo trabajado por la plataforma de scraping durante septiembre de 2026 y lo compara con agosto. El **scraping** es la consulta automatizada de fuentes externas (operadoras telefónicas, organismos fiscales y financieros) para convertir un número de teléfono en información útil: quién es el titular, en qué compañía está hoy, si tiene deuda, su situación fiscal y crediticia.

Se procesan dos bases con roles distintos:

1. **Cola de Automatización General:** es donde se scrapean los **datos de producción**, es decir, los registros reales de trabajo de la operación. Es el flujo habitual y continuo.
2. **Registro No Llame:** **hasta agosto no se utilizaba**; recién en septiembre se empezó a usar de forma completa. La base llegó "pelada", con el número de línea como único dato. Se la llama así porque de ella extrajimos datos de líneas que no teníamos y que provienen del Registro No Llame.

El informe cubre volumen y comparativas, la lógica de *cascada* y *cortocircuito* que optimiza costos, el flujo de enriquecimiento "Perfil 360°", el catálogo de los 13 motores, tiempos de respuesta y las rutinas de gobernanza de infraestructura.

---

## 01. Resumen ejecutivo y comparativa de volumen

En septiembre la plataforma tuvo una aceleración histórica: **1,236,686 registros resueltos** contra 329,222 de agosto (+275.6%). “Resuelto” significa que el registro completó su ciclo de consulta, con o sin coincidencia. El crecimiento se apoyó en cuatro pilares:

1. **Inicio productivo de Registro No Llame:** La base acumulaba más de 3.1 millones de líneas sin procesar y en agosto hubo 0 consultas. En septiembre se lanzó el operativo continuo: 48,655 líneas resueltas.
2. **Mayor capacidad en IRIS:** Nueva cuenta corporativa de alta capacidad, dedicada solo a Registro No Llame, para no saturar las operaciones habituales. (IRIS es el sistema de gestión de Movistar.)
3. **Nuevos motores de extracción:** ENACOM Web (OCR que resuelve CAPTCHA sin APIs externas pagas, para certificar portabilidad en tiempo real), Telecentro y el validador de fibra óptica Power CRM FTTH.
4. **Cascada y cortocircuito:** Ahorro del 70% en llamadas redundantes a las operadoras; el volumen completado en la cola general se multiplicó por 3.6.

### Tabla Comparativa de Volumen (Agosto vs. Septiembre 2026)

| Dimensión operativa | Agosto 2026 | Septiembre 2026 | Variación neta | Incremento |
| :--- | :---: | :---: | :---: | :---: |
| **Registro No Llame (resueltos / finalizados)** | **0** | **48,655** | +48,655 | **+100% (nuevo inicio)** |
| ↳ Finalizados con coincidencia positiva (titular confirmado) | 0 | 43,222 | +43,222 | — |
| ↳ Finalizados sin coincidencia (barrido total) | 0 | 5,433 | +5,433 | — |
| **Cola de automatización (ingresados)** | 2,283,014 | 13,400,456 | +11,117,442 | **+486.9% (x5.8)** |
| **Cola de automatización (completados exitosos)** | 329,222 | 1,188,031 | +858,809 | **+260.9% (x3.6)** |
| **TOTAL GENERAL PROCESADOS Y RESUELTOS** | **329,222** | **1,236,686** | **+907,464** | **+275.6% (x3.7)** |

> **Lectura:** los ingresos a la cola crecieron más (x5.8) que los completados (x3.6), por lo que parte del volumen ingresado queda como stock pendiente para próximos ciclos. El total resuelto se obtiene sumando Registro No Llame (48,655) y completados de la cola general (1,188,031).

---

## 02. Base Registro No Llame (`queue_registro_no_llame`)

> **Qué es esta base y por qué se llama así:** el Registro No Llame **no se usaba antes**: tenía 3,172,798 registros ingresados a fines de julio y actividad nula durante todo agosto. La base llegó **pelada, solo con el número de línea**, sin titular, DNI ni ningún otro dato. En septiembre se empezó a usar de forma completa, con la nueva cuenta corporativa y la cascada multi-operador, y se resolvieron 48,655 líneas en su ciclo completo. Se llama así porque con este trabajo **extrajimos datos de líneas que no teníamos**, y esas líneas salieron del Registro No Llame.

### Distribución de Estados en Pipeline

| Estado en pipeline | Scraper actual | Cantidad | % resueltos | Comportamiento técnico |
| :--- | :--- | :---: | :---: | :--- |
| **completado** | finalizado | **43,222** | **88.8%** | Línea validada y titular confirmado por cortocircuito (IRIS o telcos). |
| **no_coincidencia** | finalizado | **5,433** | **11.2%** | Línea que agotó todos los scrapers sin coincidencia activa. |
| **TOTAL RESUELTOS EN SEPTIEMBRE** | — | **48,655** | **100%** | **Líneas que completaron el ciclo en el mes.** |
| completado (intermedio) | personal | 9,663 | — | Enriquecido con datos de Personal Flow durante el pipeline. |
| completado (intermedio) | claro | 3,790 | — | Enriquecido con datos de Claro Argentina durante el pipeline. |
| pendiente (en cola) | iris | 2,986,435 | — | Stock disponible en inventario para próximos ciclos. |

> **Cómo leerlo:** las dos primeras filas son estados finales y suman el total resuelto. Las filas “intermedias” son registros que están a mitad de recorrido (ya pasaron por un motor y siguen avanzando), y las “pendientes” son el inventario aún sin tocar, que indica el trabajo que queda por delante.

#### Contexto Operativo Añadido: Efectividad del Barrido y Gestión del Remanente
- **Tasa de éxito del 88.8%:** De las 48,655 líneas que finalizaron su circuito en septiembre, 43,222 obtuvieron titular y compañía confirmada en el primer intento. Esto valida la altísima rentabilidad del proceso, recuperando información valiosa a partir de números de teléfono sin ningún dato inicial.
- **Planificación del stock pendiente (2.98M):** Las líneas remanentes se encuentran indexadas en lotes transaccionales. Su procesamiento se administra en ventanas de bajo tráfico externo para mantener la velocidad promedio sin exceder las políticas de uso de las fuentes.

---

## 03. Cola general de automatización (`cola_automatizacion`)

**Qué es:** la cola de automatización es donde **scrapeamos los datos de producción**, o sea, los registros reales de la operación diaria (a diferencia del Registro No Llame, que es una base nueva que llegó solo con números). La tabla muestra cuántos registros completó cada motor (identificado por su *auto_id*); los porcentajes comparan septiembre contra agosto.

### Desglose por Motor (`auto_id`)

| Motor (`auto_id`) | Ago 2026 | Sep 2026 | Variación | Rol y alcance |
| :--- | :---: | :---: | :---: | :--- |
| `scraper_cobro_express` (telcos) | 188,422 | 624,678 | **+231.5%** | Validación Claro / Personal / Movistar vía Cobro Express. |
| `scraper_power_crm` | 22,183 | 264,322 | **+1,091.5%** | Enriquecimiento y cruce de cartera Power CRM. |
| `scraper_cuit_online` | 68,428 | 120,433 | **+76.0%** | Extracción fiscal de DNI / CUIT / Razón Social. |
| `iris_scraper` *(NUEVO)* | 0 | 101,224 | **+100% (nuevo)** | Nuevo en cola general. Consultas Movistar BPM. |
| `scraper_power_crm_ftth` *(NUEVO)* | 0 | 64,997 | **+100% (nuevo)** | Nuevo en septiembre. Factibilidad de fibra óptica. |
| `bcra_scraper` | 26,535 | 12,377 | **−53.4%** | Situación crediticia e historial de cheques BCRA. |
| `scraper_datuar` | 23,654 | En cascada | — | Padrón telefónico y vínculos de personas físicas (integrado en cascada). |
| **TOTAL COMPLETADOS** | **329,222** | **1,188,031** | **+260.9%** | **Crecimiento neto de +858,809 registros.** |

> **Observaciones:** Cobro Express concentra más de la mitad del volumen (624,678 de 1,188,031). Power CRM tuvo el mayor salto relativo (x12). BCRA fue el único motor con baja (−53.4%); el informe no detalla su causa, por lo que conviene revisarla junto con el volumen de registros que llegan con CUIT válido (BCRA depende de ese dato, ver sección 05).

---

## 04. ¿Cómo funciona la cascada y la regla del cortocircuito?

### 1. ¿Por qué existe una cascada? (eficiencia y costo cero)
En una base de millones de teléfonos, consultar a todas las operadoras a la vez por cada número saturaría la red, aumentaría el riesgo de bloqueos preventivos de los proveedores y multiplicaría los tiempos de espera. Por eso la arquitectura usa una **Cadena de Responsabilidad (cascada)**: se consulta a los operadores uno tras otro, en un orden optimizado por probabilidad de éxito.

*El diagrama de cascada detalla el flujo secuencial: IRIS → Claro → Personal → Movistar → enriquecimiento secundario (Datuar / CUITOnline). Los bloques verdes representan los puntos de cortocircuito.*

### 2. Recorrido paso a paso
- **1° Movistar BPM (IRIS)** *(RESOLUCIÓN INMEDIATA):* Fuente más completa y veloz. Indica si la línea es Movistar o si migró a otra compañía (evento *Port-Out*). Si es Movistar, extrae titular, DNI y operaciones y resuelve en el acto, sin consultar a Claro ni Personal.
- **2° Auditoría Claro (Cobro Express)** *(REQUIERE DNI):* Si la línea migró fuera de Movistar y se dispone del DNI, se consulta a Claro (Línea + DNI). Factura impaga o deuda activa → **CORTOCIRCUITO ⚡** (línea resuelta).
- **3° Auditoría Personal (Telecom Flow)** *(MODALIDAD CBF · LÍNEA SOLA):* Si Claro no registra deuda o no había DNI, pasa a Personal, que admite CBF (consulta directa por línea, sin DNI previo). Deuda o línea activa → **CORTOCIRCUITO ⚡**.
- **4° Movistar Cobros alternativo** *(CANAL SECUNDARIO):* Si ni Claro ni Personal registraron deuda, se consulta el canal alternativo de Movistar por código de área y línea para descartar deudas fuera del portal BPM.
- **5° Cierre o enriquecimiento de identidad** *(PERFIL 360°):* Sin coincidencia en ninguna telco → “Sin Coincidencia Telco”. Si se confirmó titularidad y hay DNI, el registro avanza a los enriquecedores (Datuar, CUITOnline y BCRA).

### 3. La regla del cortocircuito ⚡
- **Definición:** En el instante en que un operador (Claro, Personal o Movistar) confirma una coincidencia positiva (factura impaga, deuda vigente o línea activa), el sistema aborta el resto de las consultas de la cascada.
- **Impacto operativo:** Si la línea se resuelve en Claro (primer salto), se omite el 100% de las consultas a Personal y Movistar. La latencia baja de ~6.5 s a ~1.4 s por línea y se ahorra más del 70% del tráfico hacia servidores externos.

#### Fundamento Técnico Añadido: Por qué Claro corta la cascada inmediatamente
- **Unicidad de la línea:** Por regulaciones de numeración en Argentina, un número móvil sólo puede estar activo en un operador a la vez. Cuando Claro confirma factura activa y titularidad, es técnicamente imposible que la línea tenga servicio simultáneo en Personal o Movistar. Consultar a las demás telcos sería redundante y generaría costo computacional estéril.
- **Persistencia incremental por saltos:** Cada operador consultado almacena su respuesta en el campo estructurado `datos_json`. Si un motor no arroja coincidencia, esa información negativa también se preserva para certificar que la línea no registra deuda en ese operador, evitando re-consultas innecesarias en auditorías futuras.

---

## 05. Flujo de enriquecimiento de datos personales (Perfil 360°)

Una vez determinada la línea y el DNI (por las telcos o el repositorio central), se dispara el enriquecimiento de identidad fiscal y crediticia. Un **despachador paralelo desacoplado** alimenta a la vez los espacios de datos (namespaces) de CUITOnline y BCRA dentro de `datos_json`, el campo donde se consolida toda la información del registro.

- **Paso 1 · Validación de identidad base** *(FILTRO OBLIGATORIO):* Requiere un DNI preexistente (del titular en IRIS, de la base madre o de BigQuery). Sin DNI no es viable la derivación tributaria y el circuito se cierra.
- **Paso 2 · CUIT y vínculos vía Datuar** *(PADRÓN NACIONAL):* Si no fue consultado antes, Datuar extrae el CUIT/CUIL formal, domicilios históricos, vínculos familiares y actividad declarada. Sin CUIT confiable, se detiene el avance crediticio.
- **Paso 3 · Dispatcher paralelo desacoplado ⚡** *(EJECUCIÓN CONCURRENTE):* Con un CUIT válido se lanzan dos consultas independientes.
  - **Rama A (CUITOnline):** constancia oficial AFIP/ARCA, categoría de IVA, razón social y condición de empleador → `datos_json.cuitonline`.
  - **Rama B (BCRA Central de Deudores):** situación de riesgo (1 a 5), deuda consolidada con bancos y cheques rechazados → `datos_json.bcra`.
- **Paso 4 · Consolidación Perfil 360°** *(ESTADO COMPLETADO):* El registro se guarda de forma atómica (todo o nada) con la información unificada por namespaces, listo para CRM o áreas de cobranza.

#### Arquitectura Técnica Añadida: Persistencia por Namespaces y Trazabilidad Acumulativa
- **Aislamiento en `datos_json`:** Para evitar que los datos de un operador sobreescriban a los de otro, la base de datos almacena un objeto JSON estructurado por claves fijas: `datos_json.iris`, `datos_json.claro`, `datos_json.personal`, `datos_json.cuitonline`, `datos_json.bcra` y `datos_json.datuar`. Esto permite que el CRM acceda a los datos fiscales sin alterar los datos telefónicos.
- **Array acumulativo de auditoría (`fuente`):** El registro mantiene una lista JSON acumulativa (ej. `["iris", "claro", "cuitonline", "bcra"]`) que documenta con precisión de auditoría forense la ruta exacta que recorrió el registro y qué fuentes aportaron información fáctica.

---

## 06. Catálogo de scrapers: qué es, hacia dónde va y qué trae cada uno

Ficha de los 13 motores del ecosistema, con su rol operativo y el valor comercial de los datos obtenidos:

1. **ENACOM Web Adapter** *(NUEVO):*
   - **Qué es:** motor en tiempo real que audita el portal oficial de numeración telefónica de Argentina.
   - **Destino:** numeracion.enacom.gob.ar (ENACOM).
   - **Mecanismo:** supera el CAPTCHA visual en <250 ms con tecnología 100% local: filtro de visión por computadora (OpenCV) que borra las líneas de interferencia y lectura con el OCR nativo de Windows, sin costo de APIs externas.
   - **Trae:** prestador original, prestador actual e indicador de si la línea fue portada. Identifica Claro, Movistar, Personal, Telecentro y cooperativas.
   - **Valor:** certeza total de a qué compañía llamar u ofrecer, evitando campañas a operadores erróneos.
2. **Telecentro Adapter** *(NUEVO):*
   - **Qué es:** scraper que identifica y valida clientes y servicios de Telecentro S.A.
   - **Destino:** pasarelas de cobranza homologadas y sistemas de recaudación de Telecentro.
   - **Trae:** cliente activo, número de cuenta/abonado, estado del servicio, deuda pendiente, vencimiento y códigos de barra de factura.
   - **Valor:** completa la cobertura de los 4 grandes operadores (Claro, Movistar, Personal y Telecentro).
3. **Power CRM FTTH** *(NUEVO · 64,997 resueltos):*
   - **Qué es:** auditor de factibilidad técnica y cobertura para vender fibra óptica (FTTH = fibra al hogar).
   - **Destino:** servicio cartográfico de Google Maps y sistema de topología de red de fibra.
   - **Trae:** latitud/longitud exactas, caja de dispersión más cercana, distancia en metros, puertos libres y aptitud de venta.
   - **Valor:** permite vender solo en domicilios con cobertura confirmada, eliminando ventas caídas por falta de infraestructura.
4. **IRIS HTTP Adapter** *(CUENTA CORPORATIVA NUEVA):*
   - **Qué es:** motor de ultra-alta velocidad contra el sistema central de gestión de Movistar Argentina.
   - **Destino:** servidor Movistar BPM (iris.tmoviles.com.ar).
   - **Trae:** nombre y apellido del titular, DNI/CUIT, tipo de plan, ciclo de facturación e historial completo: altas, bajas, cambios de titularidad, Port-In y Port-Out.
   - **Valor:** identifica quién es el dueño real de la línea y si la conservó o migró.
5. **IRIS Browser Adapter** *(CONTINGENCIA VISUAL):*
   - **Qué es:** respaldo que emula a un operador humano con un navegador automatizado (Playwright).
   - **Destino:** el mismo portal Movistar BPM cuando el servidor bloquea peticiones directas.
   - **Valor:** garantiza continuidad: si el método rápido falla por un cambio de interfaz, este robot visual toma el relevo.
6. **Claro Argentina Adapter** *(CORTOCIRCUITO ACTIVO):*
   - **Qué es:** validador de titularidad y facturas de telefonía móvil de Claro.
   - **Destino:** API oficial de la red Cobro Express (modalidad Claro: Línea + DNI).
   - **Trae:** importe de deuda, código de barra de 37 dígitos, DNI del titular, vencimientos y validación de línea activa.
   - **Valor:** confirma si es cliente Claro actual y habilita gestiones de cobranza o retención.
7. **Personal / Telecom Adapter** *(CORTOCIRCUITO ACTIVO):*
   - **Qué es:** validador de líneas y servicios de Telecom Personal / Flow.
   - **Destino:** Cobro Express (modalidad Cobranza sin Factura, CBF, por número de línea).
   - **Trae:** deuda vigente, códigos de recaudación y confirmación de que la línea opera en la red Telecom.
   - **Valor:** detecta clientes de Personal aun sin DNI previo.
8. **Movistar Cobro Express** *(CANAL SECUNDARIO):*
   - **Qué es:** canal alternativo para validar deudas de Movistar sin consumir las credenciales del sistema central.
   - **Destino:** Cobro Express por característica geográfica y número local.
   - **Valor:** camino secundario para validar facturas de líneas fijas y móviles de Movistar.
9. **Telcos en Cascada** *(ORQUESTADOR):*
   - **Qué es:** el “cerebro” que ejecuta en secuencia Claro → Personal → Movistar.
   - **Valor:** automatiza la decisión técnica para investigar cada registro al menor costo y tiempo posibles.
10. **CuitOnline Adapter** *(ENRIQUECEDOR FISCAL):*
    - **Qué es:** convierte documentos de identidad en perfiles tributarios completos.
    - **Destino:** padrón fiscal y constancias públicas de AFIP / ARCA.
    - **Trae:** CUIT/CUIL oficial con dígito verificador corregido, razón social o nombre fiscal, tipo de persona (física/jurídica), condición de IVA (Monotributo / Responsable Inscripto) y constancia de inscripción.
    - **Valor:** facturación legal, precalificación crediticia y verificación de solvencia fiscal.
11. **BCRA Central de Deudores** *(SISTEMA FINANCIERO):*
    - **Qué es:** consultor de riesgo crediticio y comportamiento bancario.
    - **Destino:** Central de Deudores del Banco Central de la República Argentina.
    - **Trae:** calificación (Situación 1: Normal a 5: Irrecuperable), bancos acreedores, deuda consolidada y cheques rechazados sin fondos.
    - **Valor:** clave para evaluar capacidad de pago antes de otorgar productos financieros o planes pospago.
12. **Datuar Adapter** *(VÍNCULOS Y CONTACTOS):*
    - **Qué es:** buscador de relaciones personales y domicilios en bases consolidadas.
    - **Trae:** teléfonos alternativos (fijos y móviles), domicilios postales históricos y vínculos de parentesco.
    - **Valor:** multiplica las vías de contacto cuando un número no responde o fue dado de baja.
13. **ENACOM Bloques** *(INSTANTÁNEO <1 ms):*
    - **Qué es:** base en memoria del Plan Fundamental de Numeración de Argentina (consulta offline).
    - **Trae:** operador de origen asignado, provincia, localidad, modalidad (celular o fijo) y formato internacional E.164.
    - **Valor:** normaliza y geolocaliza números al instante, sin internet ni costo de consulta.

### Matriz Comparativa de Integración del Ecosistema

| Motor / Scraper | Dato de Entrada | Información Aportada | Costo / Consumo | Tipo de Ejecución |
| :--- | :--- | :--- | :--- | :--- |
| **1. ENACOM Web** | Teléfono (10 dígitos) | Operador actual y estado portabilidad | Cero (OCR local) | Paralelo / Clasificación |
| **2. Telecentro** | Teléfono o DNI | Abonado, servicio, deuda y barras | Conexión homologada | Integrado |
| **3. Power CRM FTTH** | Dirección / Coordenadas | Cobertura fibra óptica y puertos libres | Servicio cartográfico | Batch analítico |
| **4. IRIS HTTP** | Teléfono | Titular, DNI, historial Port-Out | Cuenta corporativa | Cascada (Salto 1) |
| **5. IRIS Browser** | Teléfono | Bypass visual completo con Playwright | Contingencia servidor | Respaldo bajo demanda |
| **6. Claro Cobro Express** | Línea + DNI | Deuda vigente, 37 dígitos barra y titular | Red Cobro Express | Cascada (Salto 2 - Cortocircuito) |
| **7. Personal Telecom** | Línea sola (CBF) | Confirmación de red y deuda pendiente | Red Cobro Express | Cascada (Salto 3 - Cortocircuito) |
| **8. Movistar Cobros** | Cód. área + Línea | Deudas complementarias fuera de BPM | Red Cobro Express | Cascada (Salto 4) |
| **9. Telcos Cascada** | Línea (+ DNI opc.) | Orquestador secuencial multi-operador | Optimizado probabilidad | Pipeline inteligente |
| **10. CuitOnline** | DNI / CUIT | Padrón AFIP/ARCA, IVA y Razón Social | Padrón público | Despacho concurrente |
| **11. BCRA Deudores** | CUIT / CUIL | Riesgo 1-5 y cheques sin fondos | Central bancaria oficial | Despacho concurrente |
| **12. Datuar** | DNI / Nombre | Teléfonos alternativos, domicilios, vínculos | Base consolidada | Identidad previa |
| **13. ENACOM Bloques** | Teléfono | Geolocalización y operador de origen | Cero (RAM <1 ms) | Offline síncrono |

---

## 07. Tiempos de respuesta promedio por registro

| Scraper / motor | Tecnología / protocolo | Promedio | Rango típico | Estado / novedad |
| :--- | :--- | :---: | :---: | :--- |
| **1. ENACOM Web Adapter** | Playwright + OpenCV + WinOCR local | ~1.8 s | 1.2 – 3.0 s | 🚀 Nuevo (portabilidad en vivo) |
| **2. Telecentro Adapter** | Gateway de recaudación / API | ~1.5 s | 0.9 – 2.6 s | 🚀 Nuevo (abonados cable/telco) |
| **3. Power CRM FTTH** | Geocoding + polígonos de fibra | ~1.6 s | 0.9 – 3.1 s | 🚀 Nuevo en septiembre |
| **4. IRIS HTTP Adapter** | HTTP directo JSF ViewState | ~2.8 s | 0.72 – 18.2 s | Cuenta corporativa nueva |
| **5. IRIS Browser Adapter** | Playwright Chromium Headless | ~18.5 s | 12.0 – 25.0 s | Bypass visual contingente |
| **6. Claro (Cobro Express)** | REST API Cobro Express | ~1.4 s | 0.8 – 2.5 s | Modo Fast con Proxy Pool / Tor |
| **7. Movistar (Cobro Express)** | REST API Cobro Express | ~1.5 s | 0.8 – 2.5 s | Consulta complementaria |
| **8. Personal (Cobro Express)** | REST API Cobro Express | ~1.7 s | 0.9 – 2.8 s | Modalidad CBF sin factura |
| **9. Telcos en Cascada** | Claro → Personal → Movistar | ~2.6 s | 1.2 – 5.8 s | Cascada con cortocircuito |
| **10. CuitOnline Adapter** | Web scraping rotativo | ~1.2 s | 0.8 – 1.8 s | Consulta fiscal DNI / CUIT |
| **11. BCRA Central Deudores** | API BCRA Deudores | ~2.1 s | 1.5 – 3.2 s | Calificación de riesgo 1-5 |
| **12. Datuar Adapter** | Web scraping estructurado | ~3.2 s | 2.0 – 4.5 s | Padrón telefónico nacional |
| **13. ENACOM Bloques** | Búsqueda binaria en RAM | < 0.001 s | 0.1 – 0.5 ms | Offline determinista |

> **Lectura:** casi todos los motores responden entre 1 y 3 segundos. La excepción es el navegador de contingencia IRIS (~18.5 s), que por eso se usa solo como respaldo. ENACOM Bloques es instantáneo porque no sale a internet.

---

## 08. Rutinas operativas y gobernanza de infraestructura

1. **Rotación preventiva anti-fugas de memoria:** Cada 350 consultas, los procesos se reinician de forma limpia. Evita la degradación de la RAM en servidores y PCs de trabajo.
2. **Lotes seguros de 50 registros:** Cada tanda se reserva con bloqueos transaccionales en base de datos (`FOR UPDATE SKIP LOCKED`), de modo que dos trabajadores o máquinas nunca procesen el mismo número.
3. **Horarios de disponibilidad externa:** Se respetan los horarios de las plataformas para evitar alertas de seguridad:
   - **Movistar BPM:** Lun–Vie 08:00–21:00 · Sáb 08:00–13:00.
   - **Cobro Express:** Lun–Vie 08:00–22:00 · Sáb 08:00–13:00.
4. **Staging local y push nocturno:** En horas pico cada máquina guarda resultados en disco local rápido (offline-first). De 00:00 a 08:00 un coordinador distribuido envía los datos al servidor central, protegiendo la red y la base principal.
5. **Gestión corporativa de cuentas:** Cuenta corporativa de alta disponibilidad para Registro No Llame, junto a un pool distribuido de cuentas secundarias que rotan preventivamente para tolerar fallos.

#### Profundización Técnica Añadida: Gobernanza y Concurrencia Masiva
- **Por qué rotar cada 350 consultas:** Los procesos de automatización basados en Chromium y motores V8 acumulan memoria no liberada en el recolector de basura de JavaScript a medida que se renderizan y destruyen páginas web. Si un worker opera de forma continua por miles de ciclos, el consumo de RAM puede trepar de 120 MB a más de 3 GB, provocando ralentización o caídas por falta de memoria (OOM). Al implementar un reinicio programado cada 350 operaciones, la memoria se restablece a su estado basal (~80 MB) de forma totalmente transparente y sin perder un solo registro.
- **Concurrencia sin bloqueos (`FOR UPDATE SKIP LOCKED`):** En entornos distribuidos de alta demanda, múltiples workers consultan la misma tabla de colas. La cláusula transaccional asegura que cuando un worker solicita un lote de 50 registros, el motor de base de datos bloquea únicamente esas 50 filas e ignora de inmediato aquellas que ya están siendo procesadas por otros procesos. Esto elimina los temidos cuellos de botella (deadlocks) y permite que 15 o más workers trabajen en paralelo a máxima velocidad.
- **Staging Local Offline-First:** Durante los picos de trabajo diurnos, escribir directamente a la base de datos central a través de la red puede saturar las conexiones e impactar a los operadores de gestión de cobranza. Por ello, cada nodo de scraping registra sus transacciones localmente con almacenamiento de alta velocidad. De madrugada (00:00 a 08:00), un coordinador central absorbe estos datos de forma agrupada y optimizada.

---

## Glosario rápido

- **Port-Out / Port-In:** salida / entrada de una línea a otra compañía conservando el número.
- **DNI / CUIT / CUIL:** documento y clave tributaria/laboral de la persona.
- **CBF:** cobranza sin factura; consulta por línea sin DNI previo.
- **Cobro Express:** red de cobranza usada como fuente de consulta de operadoras.
- **AFIP / ARCA:** organismo fiscal argentino (padrón tributario oficial).
- **BCRA:** Banco Central; su Central de Deudores califica el riesgo de 1 a 5.
- **OCR:** lectura óptica automática de texto en imágenes (Optical Character Recognition).
- **CAPTCHA:** prueba visual anti-robots para certificar acceso legítimo.
- **Namespace:** sección ordenada y aislada dentro del objeto `datos_json`.
- **Atómica:** la grabación en base de datos se completa entera o no se hace (sin estados inconsistentes).

---
*Documento confidencial elaborado para Dirección y Gerencia de Operaciones — CofradiaGS · Ecosistema de Scraping*
