# Reglas de Proyecto: Arquitectura Hexagonal y Documentación Obligatoria

> **MANDATO PRINCIPAL**: Este proyecto opera bajo una **Arquitectura Hexagonal (Puertos y Adaptadores)** consolidada y un sistema de documentación atómica en 7 capas (`docs/`). Cualquier implementación de código, modificación o refactorización está sujeta a las siguientes reglas innegociables.

---

## 1. Reglas de Implementación de Código

1. **Aislamiento del Dominio (`core/domain/`)**:
   - Jamás importar librerías de infraestructura, red o base de datos en el dominio (`requests`, `playwright`, `mysql`, etc.).
   - Solo usar tipos estándar de Python (`dataclasses`, `typing`, `enum`).

2. **Extensibilidad de Scrapers (`adapters/scrapers/`)**:
   - Todo nuevo scraper (ej. Claro, Movistar, Personal) debe residir en su propia carpeta: `adapters/scrapers/<operador>/`.
   - Debe heredar de [`BaseScraperAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/base_scraper.py) o implementar el puerto abstracto [`IScraperEnginePort`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/ports/scraper_port.py).
   - Debe registrarse en [`ScraperRegistry`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/registry.py).
   - Su método `consultar_linea` debe retornar una instancia inmutable de [`ScrapeResult`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L79).

3. **Regla del Pipeline y Cortocircuito (*Short-Circuit*)**:
   - El orden de posta es: `["iris", "claro", "movistar", "personal", "finalizado"]`.
   - **Cortocircuito**: Toda coincidencia positiva (`StatusScraping.COINCIDENCIA`) en Claro, Movistar o Personal salta directamente a `scraper_actual = 'finalizado'`, `estado = 'completado'`. La lógica reside exclusivamente en [`ReglaPipeline`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L126).
   - **Acumulación de Datos**: `datos_json` se enriquece por namespaces aislados (`{"iris": {...}, "claro": {...}}`) y `fuente` como lista JSON acumulativa (`["iris", "claro"]`).

4. **Concurrencia y Persistencia**:
   - Toda reserva de lote en base de datos debe usar [`IColaRepositorioPort`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/ports/queue_port.py) con cláusula `FOR UPDATE SKIP LOCKED`.
   - Rotación preventiva anti-leak: Los workers deben relevarse cada **350 consultas**.

---

## 2. Obligación Estricta de Documentación

Cualquier cambio o nueva funcionalidad **no se considera completo** hasta que esté documentado en `docs/` según los estándares de la skill `doc-scraper-hexagonal`:

1. **Ubicación en las 7 Capas**:
   - El nuevo documento debe integrarse en la capa correspondiente (`01` a `07`).
   - El índice maestro [`docs/README.md`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/docs/README.md) debe actualizarse incorporando el nuevo módulo.

2. **Formato y Calidad Visual**:
   - Diagramas de arquitectura con ```` ```mermaid ````.
   - Tablas completas con tipos de datos y parámetros. Nombres de variables en tablas siempre entre backticks (`` `campo` ``).
   - Bloques de código con triple acento grave (```` ```python ````, ```` ```powershell ````) y comillas válidas.
   - Enlaces directos a archivos de código (`file:///c:/...`) y enlaces relativos a otros documentos markdown (`../0X_capa/archivo.md`).

3. **Regla de No-Regresión**:
   - Jamás eliminar ni resumir explicaciones técnicas, tablas o firmas existentes. La documentación solo puede crecer o perfeccionarse.

4. **Verificación de Enlaces Obligatoria**:
   - Antes de dar por finalizada la tarea, ejecutar siempre el script de validación para garantizar **0 enlaces rotos**.
