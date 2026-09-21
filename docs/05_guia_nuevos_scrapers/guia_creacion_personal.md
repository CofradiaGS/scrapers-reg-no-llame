# Guía Técnica: Adaptador Scraper Telecom Personal (Cobro Express)

Este documento detalla el diseño, la especificación de protocolo, la arquitectura de integración y la guía operativa del motor de extracción para **Telecom Personal Argentina** a través de la pasarela **Cobro Express**, integrado bajo los principios de la **Arquitectura Hexagonal (Puertos y Adaptadores)**.

---

## 1. Ciclo de Vida del Pipeline y Regla de Cortocircuito

El pipeline opera bajo el patrón de **Cadena de Responsabilidad con Cortocircuito** formalizado en [`ReglaPipeline`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/core/domain/entities.py#L132):

```text
CADENA_DEFAULT: ["iris", "datuar", "claro", "movistar", "personal"]
```

```mermaid
flowchart TD
    Inicio([Línea en Cola: PENDIENTE]) --> IRIS[1. Motor IRIS Movistar]
    IRIS -->|Coincidencia DNI / Port Out| DatuarEngine[2. Motor Datuar DNI Enriquecimiento]
    IRIS -->|Sin DNI / Bypass Inteligente| MovistarEngine[4. Motor Movistar Cobro Express]
    DatuarEngine --> ClaroEngine[3. Motor Claro Cobro Express]
    ClaroEngine -->|Coincidencia Positiva| Cortocircuito[CORTOCIRCUITO: COMPLETADO / Finalizado]
    ClaroEngine -->|Sin Coincidencia / Sin Deuda| MovistarEngine
    MovistarEngine -->|Coincidencia Positiva| Cortocircuito
    MovistarEngine -->|Sin Coincidencia / Cliente Inexistente| PersonalEngine[5. Motor Telecom Personal Cobro Express]
    PersonalEngine -->|Coincidencia Positiva| Cortocircuito
    PersonalEngine -->|Sin Coincidencia| FinNoCoinc[Fin de Cadena: NO_COINCIDENCIA / Finalizado]
```

### Regla de Cortocircuito para Personal
1. **Derivación**: Cuando [`MovistarAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/movistar/movistar_adapter.py) no encuentra coincidencia (`StatusScraping.SIN_COINCIDENCIA`), [`ReglaPipeline.resolver_siguiente_etapa`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/core/domain/entities.py#L140) deriva la línea hacia `scraper_actual = 'personal'` con `estado = 'pendiente'`.
2. **Cortocircuito**: Cuando [`PersonalAdapter.consultar_linea`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/personal/personal_adapter.py) detecta coincidencia confirmada (`StatusScraping.COINCIDENCIA`), se activa el cortocircuito finalizando la línea en `scraper_actual = 'finalizado'` y `estado = 'completado'`.
3. **Fin de Posta**: Si la línea tampoco posee registro en Personal (`StatusScraping.SIN_COINCIDENCIA`), al ser el último operador celular de la cadena, pasa directamente a `scraper_actual = 'finalizado'` y `estado = 'no_coincidencia'`.

Para más detalles sobre la orquestación en cascada, consultar:
- [Pipeline en Cascada y Cortocircuito](../01_arquitectura/pipeline_cascada.md)
- [Regla de Negocio del Pipeline](../02_dominio_y_casos_de_uso/regla_pipeline_dominio.md)

---

## 2. Especificación de Integración Cobro Express

A diferencia de Claro (que requiere DNI y línea) o Movistar (que fragmenta en característica `C12` y línea local `C13`), la pasarela de Cobro Express para **Telecom Personal** opera mediante **número de línea directo de 10 dígitos** sin requerir DNI.

### 2.1. Parámetros de la Entidad Telecom Personal en Cobro Express

| Parámetro | Valor | Tipo | Descripción |
| :--- | :--- | :--- | :--- |
| `IdEmpresa` | `20910` | `int` | Identificador único de empresa para **TELECOM PERSONAL** en el catálogo Cobro Express |
| `IdEmpresaModalidad` | `2864` | `int` | Modalidad de cobro **COBRANZA SIN FACTURA X NRO DE LINEA** (Modalidad de Cobro 2) |
| `CBF` | `linea.ani` (10 dígitos) | `str` | Número de línea telefónica móvil (código de área sin 0 y sin 15, ej: `1130001234`) |

### 2.2. Diagrama de Secuencia de Consulta

```mermaid
sequenceDiagram
    autonumber
    actor CasoUso as ProcesarLoteUseCase
    participant Adapter as PersonalAdapter
    participant Session as requests.Session (Tor / ProxyPool)
    participant API as Cobro Express API

    CasoUso->>Adapter: consultar_linea(linea)
    Adapter->>Session: sleep_jitter(0.1, 0.3)
    Adapter->>API: POST /api/Servicios/DeudaFormulario {"IdEmpresa": 20910, "IdEmpresaModalidad": 2864, "FormData": {"CBF": linea.ani}}
    alt 200 OK (Comprobantes / Cliente encontrado)
        API-->>Adapter: Array JSON con idCliente, deuda, integradorInfo, transaccionInfo
        Note over Adapter: Decodifica Base64 de integradorInfo y transaccionInfo
        Adapter-->>CasoUso: ScrapeResult(COINCIDENCIA, Operador='Personal')
    else 400 Bad Request (No hay facturas / No es Personal)
        API-->>Adapter: Error 400 JSON ("EP-200 : No hay facturas disponibles...")
        Adapter-->>CasoUso: ScrapeResult(SIN_COINCIDENCIA, Operador='Personal')
    else 429 Too Many Requests
        API-->>Adapter: Error 429 WAF Rate Limit
        Note over Adapter: Rotación reactiva instantánea de circuito Tor o reemplazo en ProxyPool
        Adapter-->>CasoUso: Reintento automático o RuntimeError
    end
```

---

## 3. Captura Exhaustiva del 100% de la Información

Por directiva técnica de negocio, **el 100% de los datos retornados por la pasarela de Cobro Express se captura y almacena sin descartar ningún campo ni añadir datos externos no provistos por el operador**:

1. **`raw`**: Contiene el payload JSON íntegro original devuelto por la API (el array JSON de comprobantes en coincidencia o el diccionario de error 400 en no coincidencia).
2. **`detalles`**:
   - `fuente_origen`: `"Cobro Express API"`.
   - `id_empresa`: `20910`.
   - `id_modalidad`: `2864`.
   - `id_cliente`: Identificador de cuenta Personal (ej: `"1002867077510001"`).
   - `codigo_barra_primario`: Código de barras completo de facturación.
   - `deuda_total`: Sumatoria del primer importe de todas las facturas/cupones devueltos.
   - `cantidad_comprobantes`: Cantidad de facturas/ítems asociados a la línea.
   - `comprobantes`: Array íntegro de comprobantes devuelto por la API.
   - **Metadatos Decodificados**: Extracción de campos clave en Base64:
     - `integradorInfo_decoded`: `AmountType`, `ExpirationDate` (fecha de vencimiento real), `Reference`, `CBF`, `Hash`.
     - `transaccionInfo_decoded`: `PrimerImporte`, `SegundoImporte`, `ImporteMin`, `ImporteMax`.
   - `ani_consultado`: Número telefónico de 10 dígitos consultado.
3. **`fechas`**: Contiene `fecha_consulta` (ISO UTC) y `fecha_vencimiento` obtenida de `integradorInfo`.
4. **`servicio`**: Detalle del producto (`TELECOM PERSONAL`), tecnología (`Móvil / Celular`) y modalidad (`Modalidad 2864 (Cobro Express)`).
5. **`titular`**: Preserva el `nro_documento` heredado de etapas previas (IRIS o Datuar) si existe.

---

## 4. Arquitectura de Red y Anonimato

[`PersonalAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/personal/personal_adapter.py) adopta el esquema de conectividad de alta disponibilidad común a los adaptadores telco:

```mermaid
graph TD
    subgraph Orquestacion["Supervisor y CLI"]
        Supervisor["Supervisor VPS / main.py"]
        TorDaemon["Demonio Tor Central (Puerto 9050)<br/>IsolateSOCKSAuth (~45 MB RAM)"]
        ProxyPool["ProxyPoolManager (Cache local live_proxies.txt)"]
    end

    subgraph ModosDeOperacion["Estrategias de Enrutamiento"]
        M1["Modo Tor: socks5h://w{slot}_{hash}:tor@127.0.0.1:9050<br/>Aislamiento de circuito por worker"]
        M2["Modo Fast: Pool HTTP/SOCKS5 validados con conexión persistente"]
    end

    subgraph Destino["Cobro Express API"]
        API["POST /api/Servicios/DeudaFormulario (Stateless)"]
    end

    Supervisor -->|Gestiona| TorDaemon
    Supervisor -->|Gestiona| ProxyPool
    TorDaemon --> M1
    ProxyPool --> M2
    M1 --> API
    M2 --> API
```

### 4.1. Tor Stream Isolation (`IsolateSOCKSAuth`)
- **Credenciales Únicas por Slot**: Cada worker utiliza un socket autenticado con credenciales efímeras (`socks5h://w{slot}_{hash}:tor@127.0.0.1:9050`), forzando circuitos independientes y salida balanceada.
- **Rotación Proactiva y Reactiva**: Rota instantáneamente de circuito en 0 ms si ocurre un error HTTP 429 o tras `TOR_ROTATE_EVERY` consultas consecutivas.
- **Nodo de Salida**: Enrutamiento optimizado mediante nodos de Sudamérica configurados en [`torrc`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/torrc).

### 4.2. Pool de Proxies Públicos Rotativos (`personal_fast`)
- Para entornos que requieren mayor velocidad (latencia entre 1.0s y 2.5s por consulta), el alias `personal_fast` delega las peticiones en [`ProxyPoolManager`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/network/proxy_pool.py) con descarte inmediato de proxies bloqueados.

---

## 5. Registro y Factoría Central

El adaptador se encuentra expuesto en [`ScraperRegistry`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/adapters/scrapers/registry.py) bajo tres alias unificados:
- `"personal"`: Nombre canónico utilizado por [`ReglaPipeline`](file:///c:/Users/Usuario/Documents/GitHub/scrapers%20reg%20no%20llame/core/domain/entities.py#L132).
- `"personal_cobro_express"`: Identificador explícito de pasarela.
- `"personal_fast"`: Instanciador optimizado con `use_proxy_pool=True` y `use_tor=False`.

```python
from adapters.scrapers.personal.personal_adapter import PersonalAdapter
from adapters.scrapers.registry import ScraperRegistry

ScraperRegistry.registrar("personal", PersonalAdapter)
ScraperRegistry.registrar("personal_cobro_express", PersonalAdapter)
ScraperRegistry.registrar("personal_fast", PersonalAdapter)
```

Para más detalles, consultar:
- [Contrato Abstracto IScraperEnginePort](contrato_iscraper_engine.md)
- [Registro Centralizado y Factoría Dinámica](registro_y_factoria.md)

---

## 6. Guía de Ejecución y Pruebas CLI

### 6.1. Consulta Unitaria de Prueba

```powershell
# 1. Conexión directa
python main.py test-line 1130001234 --scraper personal --no-tor

# 2. Con Tor Stream Isolation (Recomendado para anonimato industrial)
python main.py test-line 1130001234 --scraper personal --tor

# 3. Con Proxy Pool de alta velocidad
python main.py test-line 1130001234 --scraper personal_fast
```

Salida esperada (Línea Personal activa con deuda):
```text
=======================================================
🎯 RESULTADO NORMALIZADO: 1130001234
=======================================================
  • Estatus:     coincidencia
  • Operador:    Personal
  • Descripción: Personal - Cliente: 1002867077510001 | Deuda: $80099.97 | Vto: 2026-10-05 | Región: Capital Federal, Capital Federal
```

### 6.2. Prueba de Micro-Lote en Memoria (Dry-Run)
Valida la reserva, procesamiento acumulativo de fuentes y persistencia en memoria sin impactar la base de datos central:

```powershell
python main.py test-batch --scraper personal --dry-run --batch-size 2 --no-tor
```

Salida esperada:
```text
============================================================
🧪 TEST DE LOTE - SCRAPER: PERSONAL (Dry-run: True)
============================================================
  ➔ ID: 99901 | ANI: 2604275327 | sin_coincidencia ➔ Siguiente: [finalizado:no_coincidencia]
  ➔ ID: 99902 | ANI: 2614556677 | sin_coincidencia ➔ Siguiente: [finalizado:no_coincidencia]
------------------------------------------------------------
Procesados con éxito: 2 | Sin procesar: 0
============================================================
```

### 6.3. Lanzamiento del Supervisor Concurrente 24/7
Para procesar masivamente la cola de registros asignados a la etapa Personal en MySQL 8 VPS:

```powershell
# Modo Tor Stream Isolation (Recomendado)
python main.py supervise --scraper personal --workers 5 --batch-size 15 --prioridad auto --tor

# O mediante el script supervisor de VPS directo:
python supervisor_vps.py --scraper personal --workers 5 --batch-size 15 --prioridad auto --tor
```
