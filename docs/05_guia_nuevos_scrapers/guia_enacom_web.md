# Guía Técnica: Scraper Oficial ENACOM Numeración Web (Portabilidad Numérica en Vivo)

Este documento especifica la arquitectura, mecanismo de evasión de seguridad dual, motor OCR local y operación del adaptador de infraestructura [`EnacomWebAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py), diseñado bajo los principios de la **Arquitectura Hexagonal (Puertos y Adaptadores)** para consultar en tiempo real el portal oficial de **ENACOM Numeración** (`https://numeracion.enacom.gob.ar/`).

---

## 1. Propósito y Valor Estratégico

A diferencia del enriquecedor estático [`EnacomBlockAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/enacom/enacom_adapter.py) (que opera de forma determinista y offline sobre el archivo binario `enacom_lookup.dat` con la asignación regulatoria original del bloque), el motor **ENACOM Web** consulta el portal en línea del Ente Nacional de Comunicaciones y extrae dos atributos fundamentales:
1. `prestador_original`: Licenciatario titular de la asignación del bloque histórico.
2. `prestador_actual`: **Operador activo en vivo** de la línea telefónica.

### Detección de Portabilidad Numérica
Permite determinar de forma fehaciente si una línea migró de operador (ej. de Movistar a Claro o Personal), informando el flag booleano `es_portado = (prestador_original != prestador_actual)`.

```mermaid
flowchart TD
    subgraph Dominio ["Capa de Dominio (core/domain/)"]
        Linea["Linea (ANI: 10 dígitos)"]
        ScrapeResult["ScrapeResult (Inmutable)"]
        Status["StatusScraping: COINCIDENCIA / SIN_COINCIDENCIA"]
    end

    subgraph Puerto ["Puerto Secundario (core/ports/)"]
        Port["IScraperEnginePort"]
    end

    subgraph Adaptador ["Adaptador Secundario (adapters/scrapers/enacom_web/)"]
        Adapter["EnacomWebAdapter"]
        Solver["EnacomCaptchaSolver"]
    end

    subgraph Infraestructura ["Infraestructura Externa"]
        CV["OpenCV (Máscara Cromática)"]
        WinOCR["Windows.Media.Ocr (winsdk)"]
        PW["Playwright Chromium Headless"]
        EnacomSite["Portal Oficial ENACOM (Numeracion.aspx)"]
    end

    Linea --> Port
    Port <|.. Adapter
    Adapter --> Solver
    Solver --> CV
    CV --> WinOCR
    Adapter --> PW
    PW --> EnacomSite
    Adapter --> ScrapeResult
    ScrapeResult --> Status
```

---

## 2. Doble Barrera de Seguridad y Evasión 100% Local

El portal ASP.NET de ENACOM implementa dos mecanismos complementarios de protección contra bots:

### 2.1. Captcha Visual (`Captcha.aspx`)
- **Estructura**: Imagen JPEG/PNG de $50 \times 180\text{ px}$ con 5 caracteres alfanuméricos horizontales en color azul marino sobre fondo blanco, cruzados por 3 a 5 líneas grises de interferencia de 1 px.
- **Filtro Cromático con OpenCV**:
  $$\text{Máscara} = (B > 80) \land (B - G > 25) \land (B - R > 25)$$
  Dado que las líneas parásitas son de tono gris neutro ($R \approx G \approx B$), la máscara las evapora por completo, dejando el texto binarizado en negro puro sobre fondo blanco con escalado $3\times$.
- **Reconocimiento con Windows Native OCR**:
  Se utiliza el motor nativo de reconocimiento óptico de caracteres de Windows 10/11 ([`Windows.Media.Ocr`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/captcha_solver.py)) mediante la librería `winsdk`. Opera 100% en CPU local, con una latencia de **30 a 50 milisegundos** por imagen y costo cero de APIs de terceros.

### 2.2. Google reCAPTCHA v3 (`hfRecaptchaToken`) y Local Fulfill Dinámico
- **Imposibilidad de HTTP Puro**: El servidor ASP.NET valida obligatoriamente el campo oculto `hfRecaptchaToken` contra la API `siteverify` de Google. Un POST HTTP que omita el token devuelve `No se pudo validar la seguridad`, y un token sintético devuelve `Validación de seguridad rechazada`.
- **Evasión Ultraliviana con Chromium Headless**:
  Para mantener una latencia mínima similar a una petición HTTP sin consumir recursos excesivos:
  1. Se bloquean rutas de red pesadas de forma universal: hojas de estilo (`.css`, font-awesome, bootstrap, popper, styles__ltr.css), fuentes (`.woff`, `.woff2`, `.ttf`, `.eot`), media e imágenes no transaccionales (`favicon.ico`, `logo-enacom`, `logo_48.png`). Chromium headless opera sin render visual; el bloqueo universal ahorra el 100% de los bytes estéticos.
  2. El DOM carga en apenas **0.35 segundos**.
  3. Chromium ejecuta automáticamente `grecaptcha.ready(...)` y genera un token v3 genuino de alta reputación.
  4. La misma sesión y pestaña se reutilizan entre consultas consecutivas (context pooling), reduciendo el tiempo de consultas subsecuentes a **~3.5 a 4.5 segundos**.
- **Local Fulfill Dinámico de Assets Pesados (Ahorro > 98% en Wire Size)**:
  - **Problema de Ancho de Banda en Proxies**: Google reCAPTCHA v3 descarga dinámicamente el runtime JavaScript `recaptcha__<lang>.js` (~850 KB) por cada nuevo contexto de navegación. Al operar con proxies residenciales de pago por gigabyte, esto dispararía el costo a más de $2.5 MB por consulta.
  - **Solución Hexagonal Local Fulfill**: [`EnacomWebAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) intercepta las rutas hacia `gstatic.com/recaptcha/releases/` vía `route.fulfill` sirviendo el archivo directamente desde el almacenamiento local ([`data/enacom_static_cache/`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py#L71)).
  - **Fulfill de Scripts Auxiliares**: Adicionalmente, el adaptador sirve desde cache local el loader `recaptcha/api.js` (~1.6 KB), el worker `recaptcha/api2/webworker.js` (102 B) y la librería `jquery-3.4.1.slim.min.js` (~71 KB).
  - **Gestión Dinámica de Versiones (A/B Testing de Google)**: Google alterna canaries y versiones de release (ej. `guXhH0v-XMxlzmbTwqkaT4i5`, `guXhH0v-XMzwgr6KUGswGxeS`). Si se detecta un nuevo release hash, se descarga una única vez por red directa del host local (0 bytes facturados en el túnel de proxy) y se persiste en disco con nomenclatura `<release_hash>_<lang>.js` para abastecer a todos los workers y sesiones posteriores.
  - **Impacto Empírico Medido**: El consumo de red real que atraviesa el socket del proxy desciende de ~2.8 MB a apenas **~15 a 20 KB por consulta completa** (petición de página/POST + imagen `Captcha.aspx` de 2.6 KB + tokens transaccionales `anchor`/`reload`), permitiendo procesar más de 100.000 consultas con solo ~1.8 GB de transferencia total en proxies residenciales de Smartproxy.

---

## 3. Especificación del Payload Normalizado (`datos_json["enacom_web"]`)

Al registrar un resultado exitoso, `EnacomWebAdapter` genera un `ScrapeResult` con el siguiente esquema de atributos:

| Campo | Tipo | Ejemplo | Descripción |
| :--- | :---: | :--- | :--- |
| `numero` | `str` | `"1161234567"` | ANI de 10 dígitos validado y confirmado por ENACOM |
| `prestador_original` | `str` | `"AMX ARGENTINA S.A."` | Licenciatario original del bloque de numeración |
| `prestador_actual` | `str` | `"AMX ARGENTINA S.A."` | Operador actual activo tras portabilidad |
| `operador_comercial_original` | `str` | `"Claro"` | Marca comercial normalizada del prestador de origen |
| `operador_comercial_actual` | `str` | `"Claro"` | Marca comercial normalizada del operador activo |
| `es_portado` | `bool` | `False` | `True` si la línea fue portada hacia otra compañía |
| `intentos_resolucion` | `int` | `1` | Número de ciclos de captcha necesarios (máximo 4) |
| `ultima_modificacion` | `str` | `"2026-10-04 17:10:20"` | Marca temporal exacta de la consulta |

### Ejemplo de JSON Almacenado

```json
{
  "enacom_web": {
    "status": "coincidencia",
    "fuente": "enacom_web",
    "operador": "Claro",
    "operador_receptor": "Claro",
    "titular": {},
    "servicio": {},
    "fechas": {},
    "detalles": {
      "numero": "1161234567",
      "prestador_original": "AMX ARGENTINA S.A.",
      "prestador_actual": "AMX ARGENTINA S.A.",
      "operador_comercial_actual": "Claro",
      "operador_comercial_original": "Claro",
      "es_portado": false,
      "intentos_resolucion": 1
    },
    "raw": {
      "prestador_original": "AMX ARGENTINA S.A.",
      "prestador_actual": "AMX ARGENTINA S.A."
    },
    "ultima_modificacion": "2026-10-04 17:10:20"
  }
}
```

---

## 4. Registro y Uso en el Ecosistema

### 4.1. Registro en Factoría Central
El adaptador está registrado en [`ScraperRegistry`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py) bajo los alias:
- `"enacom_web"`
- `"enacom"`
- `"enacom_numeracion"`

```python
from adapters.scrapers.registry import ScraperRegistry
from core.domain.entities import Linea

adapter = ScraperRegistry.obtener("enacom_web", headless=True)
resultado = adapter.consultar_linea(Linea(ani="1150000000"))
print(resultado.operador_receptor) # Movistar
adapter.cerrar()
```

### 4.2. Ejecución desde CLI
Se dispone del script oficial de consola [`scripts/consultar_enacom_web.py`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/consultar_enacom_web.py):

```powershell
# Consultar una o más líneas directamente
python scripts/consultar_enacom_web.py 1161234567 1150000000 1140000000

# Consultar lote desde archivo de texto plano
python scripts/consultar_enacom_web.py --file lineas_auditar.txt

# Enrutamiento a través de servidor proxy HTTP o SOCKS5
python scripts/consultar_enacom_web.py 1161234567 --proxy "http://192.168.1.100:8080"
```

### 4.3. Enrutamiento de Red: Límite de 100 Consultas Diarias por IP y Proxies
- **Límite Estricto por IP**: ENACOM implementa un tope rígido de **exactamente 100 consultas diarias por dirección IP**. Al superar dicho volumen, el portal devuelve el mensaje: `Ha alcanzado el límite de 100 consultas diarias.` El adaptador detecta este evento de inmediato y retorna `StatusScraping.ERROR` señalando la necesidad de rotación de IP.
- **Proxies Estándar (HTTP / SOCKS5)**: [`EnacomWebAdapter`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) admite el parámetro `proxy` para enrutar el tráfico mediante proxies residenciales, corporativos o de centros de datos, o mediante el gestor dinámico [`ProxyPoolManager`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/proxy_pool.py), permitiendo escalar horizontalmente el procesamiento a razón de 100 consultas por cada IP del pool.
- **Comportamiento con Tor**: La infraestructura gubernamental de ENACOM cuenta con un firewall perimetral corporativo (Check Point con tecnología *UserCheck*) que detecta y bloquea activamente las direcciones IP de salida de la red Tor (*Tor Exit Nodes*), redirigiendo el tráfico hacia `firewall.enacom.gob.ar`. Por tanto, se desaconseja el uso de Tor directo para este portal específico y se exige el uso de proxies estándar o IPs residenciales.

### 4.4. Motor Reactivo de Ráfaga: Estrategia 'Burn-till-Dead' y Protección Anti-Agotamiento
Para maximizar el aprovechamiento de proxies públicos volátiles a costo $0 sin techos artificiales de consultas, se implementó el gestor [`BurstProxyManager`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/burst_proxy_manager.py):
* **Recolección en caliente**: Sondea en paralelo 19 fuentes públicas de Argentina con timeout estricto de 3.5s directamente contra `Numeracion.aspx`.
* **Blacklist Diario Permanente (100 Consultas)**: Las IPs que devuelven `Ha alcanzado el límite de 100 consultas diarias` se incorporan al conjunto inmutable `_daily_blocked_proxies` y se persisten en disco en `data/daily_blocked_proxies.json`. Nunca se re-cosechan, prueban ni encolan por el resto del día, incluso tras reinicios del script.
* **Cola de Cooldown Reactivo (5 Consultas/Min)**: Las IPs que alcanzan el límite de 5 consultas por minuto entran en `_cooldown_proxies` con un temporizador de 65 segundos. Transcurrido ese tiempo, el harvester las reincorpora automáticamente al pool activo.
* **Agotamiento de cuota sin techo fijo**: El worker de Playwright se vincula a un proxy y ejecuta consultas continuas hasta que el proxy muera o alcance las 100 consultas de ENACOM.
* **Rotación y Descarte Atómico**: [`EnacomWebAdapter.rotar_proxy()`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) permuta el contexto de Chromium en **< 250 ms** sin reiniciar el proceso del navegador. Ante cualquier límite o bloqueo, [`EnacomWebAdapter.invalidar_proxy()`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) anula la IP actual forzando la adquisición de un proxy alternativo nuevo, impidiendo quemar reintentos en IPs agotadas.

### 4.5. Control de Cadencia por Proxy (Bypass 0s en Proxies Rotativos vs 8.5s en Públicos)
Para maximizar el rendimiento y la longevidad de cada proxy público sin disparar bloqueos de seguridad:
* **Cadencia Cooperativa IPPacer (8.5s para Proxies Públicos / IP Directa)**: [`IPPacer`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py) regula de forma atómica el intervalo entre peticiones sucesivas originadas desde una **misma IP estática o proxy público**, aplicando una espera estricta de **8.5 segundos**. Si existen múltiples proxies validados en cola, los workers alternan de manera asíncrona sin bloquearse entre sí.
* **Ventana Deslizante (Sliding Window 4/min)**: Limita de forma proactiva a un máximo de **4 consultas cada 60 segundos por IP**, pausando automáticamente si se acumulan 4 peticiones en la ventana para blindar la reputación de la IP y evitar que ENACOM dispare el `límite de 5 consultas por minuto`.
* **Bypass Automático en Proxies Rotativos Residenciales (0.0s)**: Cuando se opera con el proxy residencial contratado (Smartproxy / Decodo en puerto `3120` o `7000`), el gateway rota automáticamente la dirección IP pública de salida en cada conexión TCP. En este escenario, `IPPacer.es_proxy_rotativo()` detecta el endpoint y **suprime al 100% tanto la espera de 8.5s como la ventana de 4/min**, logrando cadencia inmediata y aceleración de hasta **15x**.
* **Preservación de Contexto de Navegador en Reintentos**: Dado que el proxy rotativo ya entrega una IP fresca en cada solicitud, [`procesar_registro_robusto()`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py) no destruye ni recrea el contexto de Chromium entre reintentos de una misma línea cuando el proxy es rotativo, ahorrando 4 a 5 segundos de CPU y tráfico de red por intento.
* **Interrupción Inmediata por Ctrl+C**: La captura de señales `SIGINT` y `SIGTERM` ejecuta una liberación transaccional en SQLite de todos los registros en vuelo (`UPDATE tareas_staging SET estado_local = 'pendiente' WHERE estado_local = 'en_proceso'`) y destruye en milisegundos todo el árbol de procesos huérfanos de Chromium mediante `taskkill /F /T` y `os._exit(0)`, evitando cuelgues por sockets o locks retenidos.

### 4.6. Integración Offline-First: Staging en SQLite (5.000 Reg), Semáforo MySQL y Remanentes
Para la auditoría industrial de líneas marcadas como `no_coincidencia` en `queue_registro_no_llame`, se implementa el script oficial [`scripts/procesar_enacom_no_llame_staging.py`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py) alineado al estándar del sistema:
* **Descarga Masiva de 5.000 Registros (Pull)**: Gestionada por [`SincronizarPullMatutinoUseCase`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/sync_pull_use_case.py). Descarga bloques de 5.000 tareas hacia SQLite local ([`data/staging_local.db`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/sqlite_staging_adapter.py)) con `SELECT ... FOR UPDATE SKIP LOCKED` al descender del umbral de marca de agua (1.000 pendientes).
* **Tolerancia Cero a Errores**: Como prácticamente el 100% de las líneas activas en Argentina tienen asignación en ENACOM, las fallas de red, caídas de proxy o errores OCR se reintentan hasta 4 veces con rotación inmediata de proxy hacia IPs no intentadas. Si una línea no logra certificarse tras agotar los 4 proxies distintos, se revierte a `pendiente` en SQLite local mediante `revertir_a_pendiente()`; **jamás se sube un registro con error o incompleto al VPS**.
* **Subida Protegida por Semáforo Distribuido en Ventana Exclusiva (00:00 a 08:00 hs)**: Gestionada por [`SincronizarPushNocturnoUseCase`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/sync_push_use_case.py). Durante el día (08:00 a 23:59 hs), los resultados se acumulan de forma 100% segura en SQLite local. Únicamente dentro de la ventana de 00:00 a 08:00 hs se habilita el push al VPS, adquiriendo el semáforo `vps_push_nocturno_semaphore` con `GET_LOCK` y subiendo los resultados en chunks de 5.000 registros (o el remanente si hay menos), liberando el semáforo con `RELEASE_LOCK` en el bloque `finally` e interrumpiendo limpiamente si se alcanza el fin de la ventana a las 08:00 AM.

### 4.7. Configuración de Smartproxy Residencial (Argentina - 100 GB) Exclusivo para ENACOM

Para operar de forma masiva y estable evitando los rate-limits de ENACOM (100 consultas/día por IP y 5 consultas/minuto), se integra el servicio de **Smartproxy Residencial (Decodo)** con geolocalización en Argentina, garantizando aislamiento estricto respecto al resto de scrapers:

```mermaid
flowchart LR
    subgraph SmartproxyCloud ["Smartproxy / Decodo Residencial (100 GB)"]
        GW["proxy.smartproxy.net:3120\n(Rotating Residential AR)"]
        PoolAR["Pool Residencial Argentina\n(Telecom, Fibertel, Telecentro)"]
    end

    subgraph ScrapersEcosystem ["Aislamiento de Infraestructura de Red"]
        EnacomScraper["ENACOM Web Scraper\n(EnacomWebAdapter)"]
        TelcosScraper["Scrapers Telcos (Claro/Mov/Pers)\n(ProxyPool Público / Tor)"]
        CobroExpress["Scraper Cobro Express\n(COBRO_EXPRESS_PROXY)"]
    end

    EnacomScraper -->|"ENACOM_PROXY"| GW
    GW --> PoolAR
    TelcosScraper -.->|"No toca Smartproxy"| Tor["Tor / Pool Público"]
    CobroExpress -.->|"No toca Smartproxy"| IPDirecta["Directo / Proxy Dedicado"]
```

#### Parámetros Técnicos de Conexión en `.env`

| Variable de Entorno | Tipo | Ejemplo / Formato | Descripción |
| :--- | :---: | :--- | :--- |
| `ENACOM_PROXY` | `str` | `http://smart-drtj6suyh4dd_area-AR:pass@proxy.smartproxy.net:3120` | Endpoint de Smartproxy con targeting exclusivo a Argentina (`area-AR`) en puerto rotativo `3120` |

#### Modos de Sesión en Smartproxy para ENACOM

1. **Rotating Session Puro (Por Defecto y Recomendado)**:
   - Opera directamente sobre el endpoint rotativo `proxy.smartproxy.net:3120` sin inyectar parámetros de sesión fija.
   - Cada conexión TCP y consulta obtiene una IP residencial argentina distinta, eliminando por completo los bloqueos de 5/min y 100/día de ENACOM y permitiendo concurrencia masiva sin esperas artificiales.
2. **Sticky Session (Opcional vía `_session-<id>`)**:
   - Retiene la misma IP residencial durante 10 a 30 minutos asignando un identificador de sesión. Usado únicamente en casos donde un flujo transaccional requiera persistencia de IP multi-paso.

### 4.8. Optimización de Memoria y Concurrencia Industrial (Ultra-Low Hardware Profile)

En entornos de producción donde coexisten múltiples scrapers (ej. workers de IRIS, Cobro Express o Telcos), el consumo de hardware de cada worker de Chromium headless ha sido minimizado al límite absoluto:

1. **Perfil de Hardware Ultraliviano para Chromium**:
   - **V8 Lite-Mode y Heap Reducido**: `--js-flags=--max-old-space-size=128 --lite-mode`, recortando la asignación de memoria heap de JavaScript en un 50%.
   - **Proceso Render Único y Sin Aislamiento de Sitios**: `--renderer-process-limit=1` y `--disable-site-isolation-trials`, obligando a Chromium a consolidar todas las pestañas e iframes (incluyendo el iframe de reCAPTCHA) en un único subproceso de renderizado, evitando la proliferación de procesos `chrome.exe` adicionales.
   - **Prevención de Estrangulamiento en Segundo Plano y Crash Dumps**: `--disable-breakpad`, `--disable-ipc-flooding-protection`, `--disable-backgrounding-occluded-windows`.
   - **Viewport Mínimo de Framebuffer**: `viewport={"width": 400, "height": 300}` y `--window-size=400,300`. Al evitar resoluciones estándar (1920x1080), se ahorra más del 70% de la memoria de rasterizado y buffer de pantalla.
   - **Supresión Completa de Aceleración y Servicios**: `--disable-gpu`, `--disable-software-rasterizer`, `--disable-dev-shm-usage`, `--no-sandbox`, `--mute-audio`, `--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints,AudioServiceOutOfProcess`.
   - **Consumo Real Medido**: La memoria RAM por worker desciende de ~300 MB a apenas **~65 a 75 MB** en ejecución sostenida.
2. **Reutilización Persistente de Workers Chromium (Pool Inter-Lote)**:
   - En [`scripts/procesar_enacom_no_llame_staging.py`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py), el `ThreadPoolExecutor` se inicializa una sola vez fuera del bucle de micro-lotes. Los hilos de los workers y sus instancias de Chromium asociadas en `_thread_local.adapter` se mantienen calientes permanentemente a lo largo de toda la sesión, eliminando el 100% del costo de reinicio de navegadores entre lotes.
3. **Lectura Atómica Instantánea del DOM (Zero-CDP Overhead)**:
   - Tras el evento POST, la lectura del resultado (`TxtRtaNumero`, `TxtPrestadorActual`, `TxtPrestadorOriginal`, `DivMensaje`) se ejecuta en una única llamada atómica de JavaScript (`page.evaluate`), sustituyendo el sondeo de múltiples selectores CDP y reduciendo el tiempo de confirmación a menos de 40 ms.
4. **Procesamiento de Respuesta por Eventos (`expect_response`)**:
   - Se reemplazó el sondeo en bucle con esperas arbitrarias por la captura reactiva directa de la respuesta HTTP POST de `Numeracion.aspx` (`page.expect_response`), evaluando el resultado de inmediato en menos de 5 ms tras la llegada del paquete.
5. **Arranque Escalonado con Mutex (Anti-Thundering Herd)**:
   - En [`scripts/procesar_enacom_no_llame_staging.py`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py), la función `obtener_adapter_worker()` serializa la creación de navegadores mediante `_browser_init_lock` con una pausa de 1.5s entre workers, impidiendo picos súbitos de asignación de memoria.
6. **Singleton y Serialización de OCR en Windows Media (`winsdk`)**:
   - [`EnacomCaptchaSolver`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/captcha_solver.py) reutiliza una única instancia de `OcrEngine` y serializa el reconocimiento bajo `_ocr_lock`, cerrando de inmediato los streams nativos COM para prevenir fugas de buffers no administrados.
7. **Concurrencia Recomendada**:
   - Gracias al perfil ultraliviano, máquinas estándar de 8 GB de RAM pueden ejecutar confortablemente de **5 a 10 workers concurrentes** de ENACOM consumiendo en conjunto menos de 700 MB de RAM.

---

## 5. Pruebas Unitarias y Certificación

Las pruebas unitarias implementadas en [`tests/test_enacom_web_adapter.py`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/tests/test_enacom_web_adapter.py) garantizan:
1. Cumplimiento riguroso del contrato [`IScraperEnginePort`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/ports/scraper_port.py).
2. Resolución del registro en factoría [`ScraperRegistry`](file:///C:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py).
3. Homologación de razones sociales regulatorias a marcas comerciales (`Claro`, `Movistar`, `Personal`, `Telmex`, `Telecentro`, cooperativas).
4. Cortocircuito ante ANIs inválidos sin levantar el navegador.
5. Filtro de desparasitado cromático en OpenCV.

Para ejecutar las pruebas:
```powershell
python -m unittest tests/test_enacom_web_adapter.py
```
