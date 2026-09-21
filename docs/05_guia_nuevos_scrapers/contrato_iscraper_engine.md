# Contrato de Motores de Scraping: IScraperEnginePort

El puerto secundario o conducido (*Driven Port*) [`IScraperEnginePort`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/ports/scraper_port.py) define el contrato abstracto que cualquier motor de extracción externa debe implementar para operar dentro del ecosistema hexagonal del proyecto.

---

## 1. Definición Formal de la Interfaz

```python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from core.domain.entities import Linea, ScrapeResult

class IScraperEnginePort(ABC):
    """Contrato abstracto para cualquier motor de extracción externa."""

    @property
    @abstractmethod
    def nombre(self) -> str:
        """Identificador único del scraper (ej: 'iris', 'claro', 'personal')."""
        pass

    @abstractmethod
    def iniciar(self) -> None:
        """Inicializa recursos subyacentes (cliente HTTP, navegador, pools, etc.)."""
        pass

    @abstractmethod
    def autenticar(self) -> bool:
        """Realiza login o validación de tokens/sesión con el portal remoto."""
        pass

    @abstractmethod
    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Consulta una línea telefónica individual y devuelve el resultado normalizado
        bajo el modelo de dominio ScrapeResult.
        """
        pass

    @abstractmethod
    def verificar_salud(self) -> bool:
        """
        Health-check rápido para el Circuit Breaker (ej: ping al gateway/VPN).
        Retorna True si el portal remoto responde normalmente.
        """
        pass

    @abstractmethod
    def cerrar(self) -> None:
        """Cierra sesiones, clientes y libera el 100% de los recursos en memoria."""
        pass
```

---

## 2. Métodos Obligatorios y Semántica Operativa

### 2.1. `@property def nombre(self) -> str`
- **Retorno**: `str` en minúsculas y sin espacios (ej. `"iris"`, `"claro"`, `"personal"`, `"movistar"`).
- **Semántica**: Es la clave bajo la cual se reclama el lote en la base de datos (`scraper_actual = 'claro'`), la clave del namespace en el JSON enriquecido (`datos_json = {"claro": {...}}`), y el nombre de referencia en [`ReglaPipeline`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L126-L172).

### 2.2. `def iniciar(self) -> None`
- **Semántica**: Debe encargarse de instanciar pools de conexiones HTTP, sesiones con reintentos o lanzar el motor headless de navegación. No debe bloquear indebidamente si el portal no está disponible.

### 2.3. `def autenticar(self) -> bool`
- **Retorno**: `True` si la sesión es válida o el login fue exitoso; `False` en caso contrario.
- **Semántica**: Permite verificar tokens OAuth, cookies JSESSIONID o credenciales activas. Si el portal no requiere autenticación (ej. consulta pública), debe retornar `True`.

### 2.4. `def consultar_linea(self, linea: Linea) -> ScrapeResult`
- **Parámetro**: [`Linea`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L12-L38) (*Value Object* inmutable que garantiza un ANI numérico de 10 dígitos).
- **Retorno**: [`ScrapeResult`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/core/domain/entities.py#L97-L131) tipado con enum `StatusScraping`.
- **Invariante**: **Nunca debe retornar None**. Si no hay coincidencia, debe retornar un `ScrapeResult` con `status=StatusScraping.SIN_COINCIDENCIA`. Si ocurre un fallo irrecuperable de transporte, debe elevar una excepción adecuada para que el caso de uso la registre en el estado `error`.
- **Trazabilidad Temporal Automática**: La entidad `ScrapeResult` estampa de forma automática su atributo `ultima_modificacion` en formato `YYYY-MM-DD HH:MM:SS` al momento de instanciarse. Al llamar a `to_namespace_dict()`, esta marca de tiempo se inyecta directamente en el diccionario del scraper, asegurando auditoría temporal independiente para cada motor.

### 2.5. `def verificar_salud(self) -> bool`
- **Retorno**: `bool` (`True` si el servicio está operativo).
- **Semántica**: Utilizado por el Circuit Breaker y supervisores de liveness para comprobar conectividad rápida con la VPN o el portal sin realizar una consulta completa de scraping.

### 2.6. `def cerrar(self) -> None`
- **Semántica**: Liberación garantizada de memoria, cierre de sockets de red y detención de procesos secundarios. Debe ser seguro de llamar múltiples veces (idempotente).

---

## 3. Clase Base Reutilizable: BaseScraperAdapter

Para evitar duplicación de código en la gestión de pausas aleatorias que protegen la tasa de peticiones del portal remoto, el framework provee [`BaseScraperAdapter`](file:///c:/Users/Usuario/Documents/GitHub/scraper%20iris%20reg%20no%20llame/adapters/scrapers/base_scraper.py):

```python
class BaseScraperAdapter(IScraperEnginePort):
    def sleep_jitter(self, delay_min: float = 1.5, delay_max: float = 2.5) -> None:
        pause = random.uniform(delay_min, delay_max)
        time.sleep(pause)
```

Cualquier nuevo scraper puede heredar de esta clase para disponer automáticamente de `sleep_jitter()`.
