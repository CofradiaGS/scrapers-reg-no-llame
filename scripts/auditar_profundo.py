import sqlite3
import json
import re

conn = sqlite3.connect('data/banco_macro.sqlite')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("="*60)
print("ANALISIS DETALLADO DE ANOMALIAS EN banco_macro.sqlite")
print("="*60)

# 1. Nombres con '?'
print("\n--- 1. PERSONAS CON '?' EN nombre_oficial (31 casos) ---")
c.execute("SELECT dni, nombre_excel, nombre_oficial, cuit FROM personas WHERE nombre_oficial LIKE '%?%'")
filas_q = c.fetchall()
for r in filas_q:
    print(f"DNI: {r['dni']:<10} | CUIT: {str(r['cuit']):<15} | Excel: {r['nombre_excel']:<30} | Oficial: {r['nombre_oficial']}")

# 2. CUITs corruptos
print("\n--- 2. CUITS CORRUPTOS (< 11 digitos) (12 casos) ---")
c.execute("SELECT dni, nombre_excel, nombre_oficial, cuit, genero, error_msg FROM personas WHERE cuit IS NOT NULL AND length(replace(cuit, '-', '')) != 11")
for r in c.fetchall():
    print(f"DNI: {r['dni']:<10} | CUIT: {str(r['cuit']):<12} | Gen: {str(r['genero']):<12} | Excel: {r['nombre_excel']}")

# 3. Personas con Error de Horario Comercial en IRIS
print("\n--- 3. REGISTROS RECHAZADOS POR HORARIO COMERCIAL DE IRIS (88 casos) ---")
c.execute("SELECT count(*) FROM personas WHERE error_msg LIKE '%horario comercial%'")
print(f"Total registros afectados: {c.fetchone()[0]}")
c.execute("SELECT dni, nombre_excel, total_lineas_iris FROM personas WHERE error_msg LIKE '%horario comercial%' LIMIT 5")
for r in c.fetchall():
    print(f"DNI: {r['dni']:<10} | Lineas IRIS: {r['total_lineas_iris']} | Excel: {r['nombre_excel']}")

# 4. Registros completados pero sin CUIT
print("\n--- 4. REGISTROS COMPLETADOS SIN CUIT (FALLO CUITONLINE) ---")
c.execute("SELECT count(*) FROM personas WHERE estado_proceso = 'completado' AND (cuit IS NULL OR cuit = '')")
print(f"Total completados sin CUIT: {c.fetchone()[0]}")

# 5. Generos 'm' y 'f' de Datuar
print("\n--- 5. GENEROS 'm' / 'f' SIN NORMALIZAR ---")
c.execute("SELECT count(*) FROM personas WHERE genero IN ('m', 'f')")
print(f"Total personas con genero 'm'/'f': {c.fetchone()[0]}")

conn.close()
