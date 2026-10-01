# Guía de Adaptador Compuesto: Cascada Telcos con Cortocircuito

> **Capa 05: Guía de Nuevos Scrapers** | Adaptador Compuesto `TelcoCascadeAdapter`

El adaptador [`TelcoCascadeAdapter`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/telcos/telco_cascade_adapter.py) orquesta la resolución secuencial de líneas exclusivamente en las tres principales compañías telefónicas de Argentina (Claro, Personal y Movistar), aplicando la regla innegociable de cortocircuito (*short-circuit*).

Está registrado en [`ScraperRegistry`](file:///c:/Users/automatizacion.crm/Documents/GitHub/scrapers-reg-no-llame/adapters/scrapers/registry.py) bajo los alias:
- `'telcos'` (alias principal)
- `'telco_cascade'`
- `'claro_personal_movistar'`

---

## 1. Diagrama de Flujo de la Cascada

```mermaid
flowchart TD
    Inicio([Línea a consultar]) --> CheckDNI{¿Posee DNI\nen la tarea?}

    CheckDNI -- Sí --> Claro[Paso 1: Claro Cobro Express\nConsulta por DNI]
    CheckDNI -- No --> Personal[Paso 2: Telecom Personal\nConsulta por ANI]

    Claro --> ResClaro{¿Coincidencia\nen Claro?}
    ResClaro -- Sí --> ShortClaro[🎯 Cortocircuito Claro\nstatus = COINCIDENCIA\noperador = Claro]
    ResClaro -- No --> Personal

    Personal --> ResPersonal{¿Coincidencia\nen Personal?}
    ResPersonal -- Sí --> ShortPersonal[🎯 Cortocircuito Personal\nstatus = COINCIDENCIA\noperador = Telecom Personal]
    ResPersonal -- No --> Movistar[Paso 3: Movistar\nConsulta por Código Área + Línea]

    Movistar --> ResMovistar{¿Coincidencia\nen Movistar?}
    ResMovistar -- Sí --> ShortMovistar[🎯 Cortocircuito Movistar\nstatus = COINCIDENCIA\noperador = Movistar]
    ResMovistar -- No --> SinCoincidencias[Paso 4: Sin Coincidencias\nstatus = SIN_COINCIDENCIA\nConserva namespaces de las 3]

    ShortClaro --> Fin([Fin del Ciclo Local])
    ShortPersonal --> Fin
    ShortMovistar --> Fin
    SinCoincidencias --> Fin
```

---

## 2. Invariantes de Negocio y Reglas de Cascada

1. **Requisito de DNI para Claro**:
   - Claro Cobro Express exige DNI activo. Si la línea no tiene DNI (`dni IS NULL` o `dni = ''`), la etapa Claro se omite limpiamente anotando en su namespace:
     ```json
     "claro": {
       "status": "sin_coincidencia",
       "detalles": {"motivo": "Omitido: Claro Cobro Express exige DNI disponible y la línea no posee DNI"}
     }
     ```
   - El pipeline salta de inmediato a Telecom Personal.
2. **Cortocircuito Inmediato**:
   - En cuanto cualquier compañía retorna `StatusScraping.COINCIDENCIA`, se interrumpe la evaluación de las compañías restantes y se retorna inmediatamente el `ScrapeResult`.
3. **Acumulación No Destructiva**:
   - Los datos de cada motor se almacenan bajo su propio namespace (`datos_acumulados["claro"]`, `datos_acumulados["personal"]`, `datos_acumulados["movistar"]`).
   - Se preserva el 100% de comprobantes, montos adeudados, fechas y detalles técnicos.

---

## 3. Ejemplo de Salida JSON Acumulada

En caso de no coincidencia en ninguna telco:

```json
{
  "claro": {
    "status": "sin_coincidencia",
    "fuente": "claro",
    "operador": "Claro",
    "ultima_modificacion": "2026-09-30 09:04:30"
  },
  "personal": {
    "status": "sin_coincidencia",
    "fuente": "personal",
    "operador": "Personal",
    "ultima_modificacion": "2026-09-30 09:04:45"
  },
  "movistar": {
    "status": "sin_coincidencia",
    "fuente": "movistar",
    "operador": "Movistar",
    "ultima_modificacion": "2026-09-30 09:05:00"
  }
}
```

---

## 4. Documentos Relacionados

- [Contrato IScraperEnginePort](contrato_iscraper_engine.md)
- [Guía Creación Claro](guia_creacion_claro.md)
- [Guía Creación Personal](guia_creacion_personal.md)
- [Guía Creación Movistar](guia_creacion_movistar.md)
- [Adaptador de Staging Local SQLite](../03_base_de_datos_y_colas/staging_local_sqlite.md)

