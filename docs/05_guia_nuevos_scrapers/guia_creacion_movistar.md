# Guía Técnica: Adaptador Scraper Movistar (Cobro Express)

Este documento detalla el diseño, la especificación de protocolo, la arquitectura de integración y la guía operativa del motor de extracción para **Movistar Argentina** a través de la pasarela **Cobro Express**, integrado bajo los principios de la **Arquitectura Hexagonal (Puertos y Adaptadores)**.

---

## 1. Ciclo de Vida del Pipeline y Regla de Cortocircuito

El pipeline opera bajo el patrón de **Cadena de Responsabilidad con Cortocircuito** formalizado en [`ReglaPipeline`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L126-L172):

```text
CADENA_DEFAULT: ["iris", "claro", "movistar", "personal"]
```

```mermaid
flowchart TD
    Inicio([Línea en Cola: PENDIENTE]) --> IRIS[1. Motor IRIS Movistar]
    IRIS -->|Coincidencia Port Out| MovistarPos[Titular y DNI Identificados]
    IRIS -->|Sin Port Out / No Posee| NextClaro[Pasa a Claro: PENDIENTE]
    MovistarPos --> NextClaro
    NextClaro --> ClaroEngine[2. Motor Claro Cobro Express]
    ClaroEngine -->|Coincidencia Positiva| Cortocircuito[CORTOCIRCUITO: COMPLETADO / Finalizado]
    ClaroEngine -->|Sin Coincidencia / Sin Deuda| NextMovistar[Pasa a Movistar: PENDIENTE]
    NextMovistar --> MovistarEngine[3. Motor Movistar Cobro Express]
    MovistarEngine -->|Coincidencia Positiva| Cortocircuito
    MovistarEngine -->|Sin Coincidencia / Cliente Inexistente| NextPersonal[Pasa a Personal: PENDIENTE]
    NextPersonal --> PersonalEngine[4. Motor Personal / Telecom]
    PersonalEngine -->|Coincidencia| Cortocircuito
    PersonalEngine -->|Sin Coincidencia| FinNoCoinc[Fin de Cadena: NO_COINCIDENCIA]
```

### Regla de Cortocircuito para Movistar
Cuando [`MovistarAdapter.consultar_linea`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/movistar/movistar_adapter.py) retorna `StatusScraping.COINCIDENCIA`, [`ReglaPipeline.resolver_siguiente_etapa`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L134) detiene de inmediato la evaluación de los siguientes operadores y salta la línea a `scraper_actual = 'finalizado'` y `estado = 'completado'`. Si retorna `StatusScraping.SIN_COINCIDENCIA`, avanza a la siguiente posta: `scraper_actual = 'personal'` y `estado = 'pendiente'`.

Para más detalles sobre la regla en cascada, consultar:
- [Pipeline en Cascada y Cortocircuito](../01_arquitectura/pipeline_cascada.md)
- [Regla de Negocio del Pipeline](../02_dominio_y_casos_de_uso/regla_pipeline_dominio.md)

---

## 2. Especificación de Integración Cobro Express

A diferencia del scraper de Claro (que requiere DNI y teléfono completo), la pasarela de Cobro Express para **Movistar** opera mediante **código de área (característica)** y **número de abonado local**.

### 2.1. Descomposición de Línea con `caracteristicas_argentina.py`

Para consultar a Movistar, el ANI telefónico de 10 dígitos (ej. `1167322906`, `2604275327`, `3874123456`) debe desacoplarse en:
- `C12` (**Característica telefónica**): Longitud variable de 2 a 4 dígitos (ej. `11`, `260`, `387`, `2966`).
- `C13` (**Número local**): Los 6 a 8 dígitos restantes que completan el número nacional.

Esta resolución la realiza [`caracteristicas_argentina.py`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/caracteristicas_argentina.py) mediante `identificar_caracteristica(ani)`:

```python
from caracteristicas_argentina import identificar_caracteristica

caract, num_local = identificar_caracteristica(linea.ani)
# Ejemplo: '1167322906' -> caract='11', num_local='67322906'
# Ejemplo: '2604275327' -> caract='260', num_local='4275327'
```

### 2.2. Parámetros de la Entidad Movistar en Cobro Express

| Campo | Valor | Tipo | Descripción |
| :--- | :--- | :--- | :--- |
| `IdEmpresa` | `20908` | `int` | Identificador de empresa para **Movistar** |
| `IdEmpresaModalidad` | `2843` | `int` | Modalidad de consulta de factura por Característica y Número |
| `C12` | `caract` (2 a 4 dígitos) | `str` | Característica / código de área telefónico |
| `C13` | `num_local` (6 a 8 dígitos) | `str` | Número de abonado local |

### 2.3. Diagrama de Secuencia de Consulta

```mermaid
sequenceDiagram
    autonumber
    actor CasoUso as ProcesarLoteUseCase
    participant Adapter as MovistarAdapter
    participant Caract as caracteristicas_argentina
    participant Session as requests.Session
    participant API as Cobro Express API

    CasoUso->>Adapter: consultar_linea(linea)
    Adapter->>Caract: identificar_caracteristica(linea.ani)
    alt Característica desconocida / ANI inválido
        Adapter-->>CasoUso: ScrapeResult(ERROR, 'Característica no identificada')
    else Característica identificada (C12, C13)
        Adapter->>Session: sleep_jitter(0.2, 0.6)
        Adapter->>API: POST /api/Servicios/DeudaFormulario (C12: caract, C13: num_local)
        alt 200 OK (Comprobantes / Cliente encontrado)
            API-->>Adapter: Array JSON de deudas / comprobantes
            Note over Adapter: Decodifica Base64 (integradorInfo, transaccionInfo)
            Adapter-->>CasoUso: ScrapeResult(COINCIDENCIA, Operador='Movistar')
        else 400 Bad Request (Cliente inexistente)
            API-->>Adapter: Error 400 JSON (Cliente inexistente)
            Adapter-->>CasoUso: ScrapeResult(SIN_COINCIDENCIA, 'Cliente inexistente')
        else 429 Too Many Requests
            API-->>Adapter: Error 429
            Adapter-->>CasoUso: RuntimeError('Rate limit 429')
        end
    end
```

---

## 3. Captura Exhaustiva del 100% de la Información

Por directiva de negocio, **el 100% de los datos retornados por la API de Cobro Express se captura y preserva sin descartar ningún campo**:

1. **`raw`**: Contiene la respuesta original de la API (`raw["items"]`) con los objetos de comprobantes íntegros.
2. **`detalles`**:
   - `id_empresa`, `id_modalidad`, `id_cliente`, `caracteristica`, `numero_local`, `codigo_barra_primario`, `deuda_total`, `cantidad_comprobantes`.
   - `comprobantes`: Lista enriquecida con `idEmpresa`, `nombreEmpresa`, `idEmpresaModalidad`, `codigoBarra`, `idCliente`, `primerImporte`, `segundoImporte`, `importeMin`, `importeMax`, `idTipoMonto`, `hash`, `detalles_item`.
   - **Metadatos Decodificados**: Se procesa el contenido embebido en Base64 de `integradorInfo` (`AmountType`, `ExpirationDate`, `Reference`, `SearchForm`, `Hash`) y `transaccionInfo` (`PrimerImporte`, `SegundoImporte`, `ImporteMin`, `ImporteMax`).
3. **`fechas`**: Contiene `fecha_consulta` (ISO UTC) y `fecha_vencimiento` extraída de los metadatos integradores.
4. **`servicio`**: Detalle del producto (`MOVISTAR`), tecnología (`Móvil / Celular`) y modalidad (`Modalidad 2843 (Cobro Express)`).
5. **`titular`**: `nro_documento` (si existe en metadatos) o `None` con tipo `DNI`.

---

## 4. Arquitectura de Red: Tor Stream Isolation y Proxy Pool

[`MovistarAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/movistar/movistar_adapter.py) implementa los mismos mecanismos de alto rendimiento y costo $0 desarrollados para Claro:

```mermaid
graph TD
    subgraph Orquestacion["Supervisor y CLI"]
        Supervisor["Supervisor VPS / main.py"]
        TorDaemon["Demonio Tor Único (Puerto 9050)<br/>IsolateSOCKSAuth (~45 MB RAM)"]
        ProxyPool["ProxyPoolManager (Cache local live_proxies.txt)"]
    end

    subgraph ModosDeOperacion["Estrategias de Enrutamiento"]
        M1["Modo Tor: socks5h://w{slot}_{hash}:tor@127.0.0.1:9050<br/>Rotación instantánea en 0 ms por circuito"]
        M2["Modo Fast: Pool HTTP/SOCKS5 validados con conexión persistente"]
    end

    subgraph Destino["Cobro Express API"]
        API["POST /api/Servicios/DeudaFormulario (Stateless)"]
    end

    Supervisor -->|Inicia y Monitorea| TorDaemon
    Supervisor -->|Inicia y Monitorea| ProxyPool
    TorDaemon --> M1
    ProxyPool --> M2
    M1 --> API
    M2 --> API
```

### 4.1. Tor Stream Isolation (`IsolateSOCKSAuth`)
- **Aislamiento por Worker**: Cada worker o hilo genera credenciales únicas en `socks5h://w{slot}_{hash}:tor@127.0.0.1:9050` mapeadas a circuitos independientes dentro del demonio central Tor.
- **Desacoplamiento de Ciclo de Vida**: Los workers consumen la instancia Tor sin terminar el proceso maestro al rotar cada 350 consultas (`is_owner=False`).
- **Priorización Cono Sur**: Configurada en [`torrc`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/torrc) (`ExitNodes {ar},{cl},{uy},{br} StrictNodes 0`).

### 4.2. Pool de Proxies Públicos Rotativos (`movistar_fast`)
- **Latencia Ultrabaja**: 1.0s a 2.5s por consulta.
- **Conexiones Reutilizables**: `HTTPAdapter(pool_connections=10, pool_maxsize=10)` para evitar acumulación de sockets `TIME_WAIT` en Windows.

---

## 5. Registro y Factoría Central

El adaptador se registra en [`ScraperRegistry`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/registry.py) bajo tres alias:
- `"movistar"`: Nombre canónico utilizado en la cadena del pipeline (modo por defecto según flags o configuración).
- `"movistar_cobro_express"`: Alias explícito del motor de pasarela.
- `"movistar_fast"`: Alias preconfigurado para forzar el modo de alta velocidad con [`ProxyPoolManager`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/network/proxy_pool.py).

```python
from adapters.scrapers.movistar.movistar_adapter import MovistarAdapter
from adapters.scrapers.registry import ScraperRegistry

ScraperRegistry.registrar("movistar", MovistarAdapter)
ScraperRegistry.registrar("movistar_cobro_express", MovistarAdapter)
ScraperRegistry.registrar("movistar_fast", MovistarAdapter)
```

Para más detalles, consultar:
- [Contrato Abstracto IScraperEnginePort](contrato_iscraper_engine.md)
- [Registro Centralizado y Factoría Dinámica](registro_y_factoria.md)

---

## 6. Guía de Ejecución y Pruebas CLI

### 6.1. Consulta Unitaria (Modo Directo, Tor o Proxy Pool)
Para consultar una línea individual sin necesidad de conocer el DNI:

```powershell
# 1. Conexión directa (sin Tor ni proxies)
python main.py test-line 1167322906 --scraper movistar --no-tor

# 2. Modo Tor Stream Isolation (Máximo anonimato)
python main.py test-line 1167322906 --scraper movistar --tor

# 3. Modo Proxy Pool Rotativo de Alta Velocidad
python main.py test-line 1167322906 --scraper movistar --proxy-pool

# O usando directamente el alias movistar_fast:
python main.py test-line 1167322906 --scraper movistar_fast
```

Salida esperada (Línea Movistar activa):
```text
=======================================================
🎯 RESULTADO NORMALIZADO: 1167322906
=======================================================
  • Estatus:     coincidencia
  • Operador:    Movistar
  • Descripción: Movistar - Cliente: C0046700579 | Deuda: $0.00 | Vto: 2026-09-08 | CB: 08200000000000000000000000000000SF
```

### 6.2. Prueba de Micro-Lote en Memoria (Dry-Run)
Para validar la orquestación del pipeline y verificar el avance o cortocircuito sin impactar la base de datos central:

```powershell
# Con Tor Stream Isolation
python main.py test-batch --scraper movistar --dry-run --batch-size 2 --tor

# Con Proxy Pool ultrarrápido
python main.py test-batch --scraper movistar --dry-run --batch-size 2 --proxy-pool
```

### 6.3. Lanzamiento del Supervisor Concurrente
Para procesar la cola de producción en MySQL 8 VPS dedicada a la etapa de Movistar:

```powershell
# Modo Tor Stream Isolation (Recomendado para evitar bloqueos)
python main.py supervise --scraper movistar --workers 5 --batch-size 15 --prioridad auto --tor

# O mediante supervisor_vps.py:
python supervisor_vps.py --scraper movistar --workers 5 --batch-size 15 --prioridad auto --tor
```
