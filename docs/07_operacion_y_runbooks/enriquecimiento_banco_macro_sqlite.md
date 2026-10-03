# Runbook: Enriquecimiento Total Banco Macro a SQLite

> **Capa 07: Operación y Runbooks** | Manual Operativo  
> **Scripts**: [`scripts/import_banco_macro_sqlite.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/import_banco_macro_sqlite.py) | [`scripts/procesar_banco_macro_sqlite.py`](file:///c:/Users/Usuario/Documents/GitHub/scrapers-reg-no-llame/scripts/procesar_banco_macro_sqlite.py)  
> **Base de Datos**: `data/banco_macro.sqlite` (SQLite WAL)

---

## 1. Visión General del Proceso

El flujo de enriquecimiento para carteras de deudores (como `base_macro.xlsx`) desacopla la carga inicial del procesamiento distribuido:
1. **Ingesta y Deduplicación**: Extrae las 11.675 filas originales, deduplica los 4.553 DNIs únicos en la tabla `personas` y conserva el detalle transaccional en `operaciones_excel`.
2. **Cascada Exhaustiva de Enriquecimiento**:
   * **CuitOnline**: Resuelve CUIT, constancia AFIP, actividades y domicilio fiscal.
   * **BCRA**: Consulta la API oficial de la Central de Deudores con el CUIT obtenido (deuda en Banco Macro, otras entidades, situación 1 a 5 y cheques).
   * **Datuar**: Nutre campos demográficos (edad, género, localidad).
   * **IRIS por DNI**: Consulta `att$nroIdentificacion` e itera sobre todas las lupas históricas, descubriendo las líneas telefónicas asociadas.
   * **Telcos Cobro Express**: Para cada línea hallada, audita en paralelo Claro (Línea + DNI), Personal (Línea) y Movistar (Característica + Línea), determinando prestador actual y deuda activa.
3. **Resumable**: Si el proceso se detiene o cancela con `Ctrl+C`, se puede relanzar inmediatamente y continuará con los registros en estado `pendiente`.

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

---

## 4. Exportación del Reporte Final a Excel

Una vez finalizado el procesamiento (o en cualquier momento durante la ejecución), se puede generar el consolidado con:

```powershell
python scripts/procesar_banco_macro_sqlite.py --export-excel
```

Genera el archivo `base_macro_enriquecida.xlsx` ordenado por volumen de deuda en Banco Macro y cantidad de operaciones.
