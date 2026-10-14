# Runbook: Enriquecimiento Total Banco Macro a SQLite

> **Capa 07: Operación y Runbooks** | Manual Operativo  
> **Scripts**: [`scripts/import_banco_macro_sqlite.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/import_banco_macro_sqlite.py) | [`scripts/procesar_banco_macro_sqlite.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_banco_macro_sqlite.py) | [`scripts/reauditar_iris_telcos.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/reauditar_iris_telcos.py) | [`scripts/reparar_bcra_completados.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/reparar_bcra_completados.py)  
> **Dominio**: [`core/domain/cuit_validator.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/cuit_validator.py)  
> **Base de Datos**: `data/banco_macro.sqlite` (SQLite WAL)

---

## 1. Visión General del Proceso

El flujo de enriquecimiento para carteras de deudores (como `base_macro.xlsx`) desacopla la carga inicial del procesamiento distribuido:
1. **Ingesta y Deduplicación**: Extrae las 11.675 filas originales, deduplica los 4.553 DNIs únicos en la tabla `personas` y conserva el detalle transaccional en `operaciones_excel`.
2. **Cascada Exhaustiva de Enriquecimiento (0% Omisiones)**:
   * **CuitOnline**: Resuelve CUIT verificado, constancia AFIP, actividades y domicilio fiscal.
   * **Datuar**: Nutre campos demográficos (edad, género, localidad, municipio, ciudad).
   * **Resolución Algorítmica de CUIL**: Si CuitOnline y Datuar no disponen de CUIT, el módulo de dominio [`core/domain/cuit_validator.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/core/domain/cuit_validator.py) calcula automáticamente el CUIL oficial (Módulo 11 ANSES con manejo de colisiones e inferencia de género).
   * **BCRA Central de Deudores**: Consulta obligatoria para el 100% de los registros con reintento automático anti-rate limit (HTTP 429), capturando deudas de Banco Macro, peor situación crediticia (1 a 5) y entidades bancarias.
   * **IRIS por DNI**: Consulta `att$nroIdentificacion` e itera sobre todas las lupas históricas, descubriendo las líneas telefónicas asociadas.
   * **Telcos Cobro Express**: Para cada línea hallada, audita en paralelo Claro (Línea + DNI), Personal (Línea) y Movistar (Característica + Línea), determinando prestador actual y deuda activa.
3. **Resumable y Control C Limpio**: Si el proceso se detiene con `Ctrl+C`, las transacciones no se corrompen y se puede relanzar inmediatamente para continuar con los registros en estado `pendiente`.

---

## 2. Ingesta Inicial a SQLite

Para inicializar la base de datos y cargar el archivo Excel:

```powershell
python scripts/import_banco_macro_sqlite.py
```

Salida esperada:
```text
📖 Leyendo base_macro.xlsx...
📊 Total filas leídas: 11675
🛠️ Inicializando esquema DDL en SQLite...
👥 Total personas / DNIs únicos a insertar: 4552
📥 Insertando personas maestras...
📥 Insertando operaciones detalle...
✅ Ingesta inicial completada con éxito.
📍 Base de datos: data/banco_macro.sqlite
```

---

## 3. Comandos de Ejecución del Orquestador

### 3.1 Procesamiento por Lotes Acotados (Pruebas)
Para procesar un bloque de `N` registros:
```powershell
python scripts/procesar_banco_macro_sqlite.py --limit 10
```

### 3.2 Procesamiento Completo de la Cartera
Para procesar todos los DNIs pendientes en la base SQLite:
```powershell
python scripts/procesar_banco_macro_sqlite.py
```

### 3.3 Consulta Puntual de un DNI
Para auditar un DNI específico end-to-end:
```powershell
python scripts/procesar_banco_macro_sqlite.py --dni 22120248
```

### 3.4 Opciones y Banderas Avanzadas

| Flag | Tipo | Descripción |
| :--- | :--- | :--- |
| `--limit N` | Entero | Cantidad máxima de registros pendientes a procesar en esta ejecución. |
| `--dni <DNI>` | String | Ejecuta el flujo completo para un único DNI específico. |
| `--skip-iris` | Booleano | Omite la consulta a IRIS (útil para pruebas o fuera de horario comercial). |
| `--skip-telcos` | Booleano | Omite la auditoría de telecomunicaciones en Cobro Express. |
| `--forzar-horario` | Booleano | Fuerza la consulta en IRIS ignorando validación de horario comercial. |
| `--export-excel` | Booleano | Genera el archivo Excel `base_macro_enriquecida.xlsx` y finaliza. |

### 3.5 Reparación Rápida de BCRA en Registros Completados

Si existen registros previamente completados sin CUIT ni datos de BCRA, se puede ejecutar el script de enriquecimiento rápido sin alterar las líneas telefónicas ya descubiertas en IRIS o Telcos:

```powershell
python scripts/reparar_bcra_completados.py
```

Calcula el CUIL algorítmico, consulta la Central de Deudores del BCRA y actualiza deudas, entidades bancarias y peor situación con idempotencia total.

### 3.6 Re-auditoría Focalizada de IRIS y Telcos (Recuperación de Líneas)

Para casos que ya pasaron por el flujo o quedaron completados y necesitan re-auditarse en IRIS Movistar (aprovechando el bypass de errores `CW` y auto-recuperación de tablas) y Telcos Cobro Express, sin volver a consumir cuotas de CuitOnline, Datuar o BCRA:

```powershell
# Estrategia 2 (Default / Recomendada): Todos los que tienen trámites en IRIS (~455 DNIs)
python scripts/reauditar_iris_telcos.py --iris-workers 5 --telcos-workers 15

# Estrategia 1 (Rápida): Únicamente los 36 DNIs con operaciones registradas pero 0 líneas
python scripts/reauditar_iris_telcos.py --zero-lines-only

# Estrategia 3 (Barrido Total de 0 líneas): Todos los completados con 0 líneas (~1.003 DNIs)
python scripts/reauditar_iris_telcos.py --modo all-zero-lines

# Prueba con un DNI específico
python scripts/reauditar_iris_telcos.py --dni 10206534
```

| Parámetro | Tipo | Descripción |
| :--- | :--- | :--- |
| `--modo all-ops` | Default | Re-audita a todos los DNIs que registran trámites en `operaciones_iris` (~455 DNIs). |
| `--zero-lines-only` | Flag | Atajo para auditar únicamente los casos con historial pero con 0 líneas descubiertas. |
| `--modo all-zero-lines`| Flag | Re-audita a todas las personas en estado completado que registran 0 líneas. |
| `--dni <DNI>` | String | Re-audita un DNI individual para validación inmediata. |
| `--limit <N>` | Entero | Límite máximo de personas a procesar. |
| `--iris-workers <N>` | Entero | Cantidad de sesiones HTTP concurrentes en IRIS (default: `5`). |
| `--telcos-workers <N>` | Entero | Cantidad de workers de Claro, Personal y Movistar en Tor (default: `15`). |
| `--skip-telcos` | Flag | Omite la consulta a Telcos y solo refresca IRIS. |

---

## 4. Exportación del Reporte Final a Excel

Una vez finalizado el procesamiento (o en cualquier momento durante la ejecución), se puede generar el consolidado con:

```powershell
python scripts/procesar_banco_macro_sqlite.py --export-excel
```

Genera el archivo `base_macro_enriquecida.xlsx` ordenado por volumen de deuda en Banco Macro y cantidad de operaciones.
