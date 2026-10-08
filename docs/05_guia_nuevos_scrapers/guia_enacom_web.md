# Guía Técnica: Scraper Oficial ENACOM Numeración Web (Portabilidad Numérica en Vivo)

Este documento especifica la arquitectura, mecanismo de evasión de seguridad dual, motor OCR local y operación del adaptador de infraestructura [`EnacomWebAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py), diseñado bajo los principios de la **Arquitectura Hexagonal (Puertos y Adaptadores)** para consultar en tiempo real el portal oficial de **ENACOM Numeración** (`https://numeracion.enacom.gob.ar/`).

---

## 1. Propósito y Valor Estratégico

A diferencia del enriquecedor estático [`EnacomBlockAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/enacom/enacom_adapter.py) (que opera de forma determinista y offline sobre el archivo binario `enacom_lookup.dat` con la asignación regulatoria original del bloque), el motor **ENACOM Web** consulta el portal en línea del Ente Nacional de Comunicaciones y extrae dos atributos fundamentales:
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
  Se utiliza el motor nativo de reconocimiento óptico de caracteres de Windows 10/11 ([`Windows.Media.Ocr`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/captcha_solver.py)) mediante la librería `winsdk`. Opera 100% en CPU local, con una latencia de **30 a 50 milisegundos** por imagen y costo cero de APIs de terceros.

### 2.2. Google reCAPTCHA v3 (`hfRecaptchaToken`) y Local Fulfill Dinámico
- **Imposibilidad de HTTP Puro**: El servidor ASP.NET valida obligatoriamente el campo oculto `hfRecaptchaToken` contra la API `siteverify` de Google. Un POST HTTP que omita el token devuelve `No se pudo validar la seguridad`, y un token sintético devuelve `Validación de seguridad rechazada`.
- **Evasión Ultraliviana con Chromium Headless**:
  Para mantener una latencia mínima similar a una petición HTTP (~2 a 3 segundos) sin consumir recursos excesivos:
  1. Se bloquean rutas de red pesadas (`.woff`, `.woff2`, `.ttf`, `font-awesome`, `all.min.css`, `bootstrap`, `popper`, `favicon.ico`, `logo-enacom`).
  2. El DOM carga en apenas **0.44 segundos**.
  3. Chromium ejecuta automáticamente `grecaptcha.ready(...)` y genera un token v3 genuino de alta reputación en **1.4 segundos**.
  4. La misma sesión y pestaña se reutilizan entre consultas consecutivas (context pooling), reduciendo el tiempo de consultas subsecuentes a **~2.0 segundos**.
- **Local Fulfill Dinámico de Assets Pesados (Ahorro > 90% en Wire Size)**:
  - **Problema de Ancho de Banda en Proxies**: Google reCAPTCHA v3 descarga dinámicamente el runtime JavaScript `recaptcha__<lang>.js` (~850 KB) por cada nuevo contexto de navegación. Al operar con proxies residenciales de pago por gigabyte, esto dispararía el costo a más de $2.5 MB por consulta.
  - **Solución Hexagonal Local Fulfill**: [`EnacomWebAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) intercepta las rutas hacia `gstatic.com/recaptcha/releases/` vía `route.fulfill` sirviendo el archivo directamente desde el almacenamiento local ([`data/enacom_static_cache/`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py#L65)).
  - **Gestión Dinámica de Versiones (A/B Testing de Google)**: Google alterna canaries y versiones de release (ej. `guXhH0v-XMxlzmbTwqkaT4i5`, `guXhH0v-XMzwgr6KUGswGxeS`). Si se detecta un nuevo release hash, se descarga una única vez por red directa del host local (0 bytes facturados en el túnel de proxy) y se persiste en disco con nomenclatura `<release_hash>_<lang>.js` para abastecer a todos los workers y sesiones posteriores.
  - **Impacto Empírico Medido**: El consumo de red real que atraviesa el socket del proxy desciende de ~2.8 MB a apenas **~25 KB por consulta completa** (21.3 KB de carga de página + 3.4 KB del envío de formulario), permitiendo procesar 80.000 consultas diarias con solo ~1.9 GB de transferencia total en proxies residenciales.

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
El adaptador está registrado en [`ScraperRegistry`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py) bajo los alias:
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
Se dispone del script oficial de consola [`scripts/consultar_enacom_web.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/consultar_enacom_web.py):

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
- **Proxies Estándar (HTTP / SOCKS5)**: [`EnacomWebAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) admite el parámetro `proxy` para enrutar el tráfico mediante proxies residenciales, corporativos o de centros de datos, o mediante el gestor dinámico [`ProxyPoolManager`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/proxy_pool.py), permitiendo escalar horizontalmente el procesamiento a razón de 100 consultas por cada IP del pool.
- **Comportamiento con Tor**: La infraestructura gubernamental de ENACOM cuenta con un firewall perimetral corporativo (Check Point con tecnología *UserCheck*) que detecta y bloquea activamente las direcciones IP de salida de la red Tor (*Tor Exit Nodes*), redirigiendo el tráfico hacia `firewall.enacom.gob.ar`. Por tanto, se desaconseja el uso de Tor directo para este portal específico y se exige el uso de proxies estándar o IPs residenciales.

### 4.4. Motor Reactivo de Ráfaga: Estrategia 'Burn-till-Dead' y Protección Anti-Agotamiento
Para maximizar el aprovechamiento de proxies públicos volátiles a costo $0 sin techos artificiales de consultas, se implementó el gestor [`BurstProxyManager`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/network/burst_proxy_manager.py):
* **Recolección en caliente**: Sondea en paralelo 19 fuentes públicas de Argentina con timeout estricto de 3.5s directamente contra `Numeracion.aspx`.
* **Blacklist Diario Permanente (100 Consultas)**: Las IPs que devuelven `Ha alcanzado el límite de 100 consultas diarias` se incorporan al conjunto inmutable `_daily_blocked_proxies` y se persisten en disco en `data/daily_blocked_proxies.json`. Nunca se re-cosechan, prueban ni encolan por el resto del día, incluso tras reinicios del script.
* **Cola de Cooldown Reactivo (5 Consultas/Min)**: Las IPs que alcanzan el límite de 5 consultas por minuto entran en `_cooldown_proxies` con un temporizador de 65 segundos. Transcurrido ese tiempo, el harvester las reincorpora automáticamente al pool activo.
* **Agotamiento de cuota sin techo fijo**: El worker de Playwright se vincula a un proxy y ejecuta consultas continuas hasta que el proxy muera o alcance las 100 consultas de ENACOM.
* **Rotación y Descarte Atómico**: [`EnacomWebAdapter.rotar_proxy()`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) permuta el contexto de Chromium en **< 250 ms** sin reiniciar el proceso del navegador. Ante cualquier límite o bloqueo, [`EnacomWebAdapter.invalidar_proxy()`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/enacom_web/enacom_web_adapter.py) anula la IP actual forzando la adquisición de un proxy alternativo nuevo, impidiendo quemar reintentos en IPs agotadas.

### 4.5. Control de Cadencia por Proxy (8.5 Segundos + Ventana Deslizante 4/min) y Detención Instantánea
Para maximizar el rendimiento y la longevidad de cada proxy público sin disparar bloqueos de seguridad:
* **Cadencia Cooperativa IPPacer (8.5s)**: [`IPPacer`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py) regula de forma atómica el intervalo entre peticiones sucesivas originadas desde una **misma IP o proxy**, aplicando una espera estricta de **8.5 segundos**. Si existen múltiples proxies validados en cola, los workers alternan de manera asíncrona sin bloquearse entre sí.
* **Ventana Deslizante (Sliding Window 4/min)**: Limita de forma proactiva a un máximo de **4 consultas cada 60 segundos por IP**, pausando automáticamente si se acumulan 4 peticiones en la ventana para blindar la reputación de la IP y evitar que ENACOM dispare el `límite de 5 consultas por minuto`.
* **Aislamiento de Reintentos por ANI**: En [`procesar_registro_robusto()`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py), cada número telefónico registra los proxies ya probados (`proxies_intentados`). Un reintento jamás se ejecuta sobre una IP previamente probada o vetada para esa misma línea, esperando si es necesario por un nuevo proxy argentino válido.
* **Interrupción Inmediata por Ctrl+C**: La captura de señales `SIGINT` y `SIGTERM` ejecuta una liberación transaccional en SQLite de todos los registros en vuelo (`UPDATE tareas_staging SET estado_local = 'pendiente' WHERE estado_local = 'en_proceso'`) y destruye en milisegundos todo el árbol de procesos huérfanos de Chromium mediante `taskkill /F /T` y `os._exit(0)`, evitando cuelgues por sockets o locks retenidos.

### 4.6. Integración Offline-First: Staging en SQLite (5.000 Reg), Semáforo MySQL y Remanentes
Para la auditoría industrial de líneas marcadas como `no_coincidencia` en `queue_registro_no_llame`, se implementa el script oficial [`scripts/procesar_enacom_no_llame_staging.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_enacom_no_llame_staging.py) alineado al estándar del sistema:
* **Descarga Masiva de 5.000 Registros (Pull)**: Gestionada por [`SincronizarPullMatutinoUseCase`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/sync_pull_use_case.py). Descarga bloques de 5.000 tareas hacia SQLite local ([`data/staging_local.db`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/queue/sqlite_staging_adapter.py)) con `SELECT ... FOR UPDATE SKIP LOCKED` al descender del umbral de marca de agua (1.000 pendientes).
* **Tolerancia Cero a Errores**: Como prácticamente el 100% de las líneas activas en Argentina tienen asignación en ENACOM, las fallas de red, caídas de proxy o errores OCR se reintentan hasta 4 veces con rotación inmediata de proxy hacia IPs no intentadas. Si una línea no logra certificarse tras agotar los 4 proxies distintos, se revierte a `pendiente` en SQLite local mediante `revertir_a_pendiente()`; **jamás se sube un registro con error o incompleto al VPS**.
* **Subida Protegida por Semáforo Distribuido en Ventana Exclusiva (00:00 a 08:00 hs)**: Gestionada por [`SincronizarPushNocturnoUseCase`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/use_cases/sync_push_use_case.py). Durante el día (08:00 a 23:59 hs), los resultados se acumulan de forma 100% segura en SQLite local. Únicamente dentro de la ventana de 00:00 a 08:00 hs se habilita el push al VPS, adquiriendo el semáforo `vps_push_nocturno_semaphore` con `GET_LOCK` y subiendo los resultados en chunks de 5.000 registros (o el remanente si hay menos), liberando el semáforo con `RELEASE_LOCK` en el bloque `finally` e interrumpiendo limpiamente si se alcanza el fin de la ventana a las 08:00 AM.

---

## 5. Pruebas Unitarias y Certificación

Las pruebas unitarias implementadas en [`tests/test_enacom_web_adapter.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/tests/test_enacom_web_adapter.py) garantizan:
1. Cumplimiento riguroso del contrato [`IScraperEnginePort`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/ports/scraper_port.py).
2. Resolución del registro en factoría [`ScraperRegistry`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py).
3. Homologación de razones sociales regulatorias a marcas comerciales (`Claro`, `Movistar`, `Personal`, `Telmex`, `Telecentro`, cooperativas).
4. Cortocircuito ante ANIs inválidos sin levantar el navegador.
5. Filtro de desparasitado cromático en OpenCV.

Para ejecutar las pruebas:
```powershell
python -m unittest tests/test_enacom_web_adapter.py
```
