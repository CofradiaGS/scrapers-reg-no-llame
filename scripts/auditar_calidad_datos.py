import sqlite3
import json
import re

conn = sqlite3.connect('data/banco_macro.sqlite')
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("="*60)
print("AUDITORIA DE CALIDAD DE DATOS Y ENCODING (banco_macro.sqlite)")
print("="*60)

# 1. Unicode Replacement Character (\ufffd)
print("\n--- 1. BUSQUEDA DE CARACTERES DE REEMPLAZO UNICODE (\\ufffd) ---")
tablas_columnas = {
    'personas': ['nombre_excel', 'nombre_oficial', 'domicilio_fiscal', 'localidad', 'provincia', 'condicion_afip', 'actividades_afip', 'error_msg'],
    'bcra_entidades': ['entidad'],
    'operaciones_iris': ['operador_receptor', 'tipo_operacion', 'estado', 'producto', 'tecnologia'],
    'lineas_descubiertas': ['ani'],
    'telcos_scraping': ['operador_detectado', 'detalles_json']
}

total_ufffd = 0
for tabla, cols in tablas_columnas.items():
    for col in cols:
        c.execute(f"SELECT count(*) FROM {tabla} WHERE {col} LIKE '%\ufffd%'")
        cnt = c.fetchone()[0]
        if cnt > 0:
            print(f"  [ALERTA] {tabla}.{col}: {cnt} registros con '\\ufffd'")
            total_ufffd += cnt
            c.execute(f"SELECT {col} FROM {tabla} WHERE {col} LIKE '%\ufffd%' LIMIT 3")
            for row in c.fetchall():
                print(f"     Ejemplo: {repr(row[0])}")

if total_ufffd == 0:
    print("  [OK] Cero caracteres '\\ufffd' en todas las tablas analizadas.")

# 2. Busqueda de Mojibake comun de UTF-8 decodificado como Latin-1
print("\n--- 2. BUSQUEDA DE MOJIBAKE (Ã¡, Ã©, Ã±, Â°, etc.) ---")
mojibake_pattern = r"(Ã¡|Ã©|Ã­|Ã³|Ãº|Ã±|Ã‘|Â°|Âº|Ã |Ã‰|Ã |Ã“|Ãš)"
total_mojibake = 0
for tabla, cols in tablas_columnas.items():
    for col in cols:
        c.execute(f"SELECT rowid, {col} FROM {tabla} WHERE {col} IS NOT NULL")
        col_mojibake = []
        for row in c.fetchall():
            val = str(row[1])
            if re.search(mojibake_pattern, val):
                col_mojibake.append(val)
        if col_mojibake:
            print(f"  [ALERTA] {tabla}.{col}: {len(col_mojibake)} registros con posible Mojibake")
            total_mojibake += len(col_mojibake)
            for ex in col_mojibake[:3]:
                print(f"     Ejemplo: {repr(ex)}")

if total_mojibake == 0:
    print("  [OK] Cero mojibake detectado en todas las tablas.")

# 3. Busqueda de signos de interrogacion '?' en nombres
print("\n--- 3. BUSQUEDA DE '?' EN NOMBRES Y DIRECCIONES ---")
c.execute("SELECT dni, nombre_oficial FROM personas WHERE nombre_oficial LIKE '%?%'")
filas_q = c.fetchall()
print(f"  Personas con '?' en nombre_oficial: {len(filas_q)}")
for r in filas_q[:5]:
    print(f"     DNI {r['dni']}: {r['nombre_oficial']}")

c.execute("SELECT dni, domicilio_fiscal FROM personas WHERE domicilio_fiscal LIKE '%?%'")
filas_qd = c.fetchall()
print(f"  Personas con '?' en domicilio_fiscal: {len(filas_qd)}")
for r in filas_qd[:5]:
    print(f"     DNI {r['dni']}: {r['domicilio_fiscal']}")

# 4. Auditoria de ANIs en lineas_descubiertas
print("\n--- 4. AUDITORIA DE ANIS (LINEAS TELEFONICAS) ---")
c.execute("SELECT ani, count(*) FROM lineas_descubiertas GROUP BY ani HAVING count(*) > 1")
dups = c.fetchall()
print(f"  ANIs duplicados en lineas_descubiertas: {len(dups)}")

c.execute("SELECT dni, ani FROM lineas_descubiertas")
anomalos_ani = []
for r in c.fetchall():
    ani = r['ani']
    if not ani.isdigit() or len(ani) != 10:
        anomalos_ani.append((r['dni'], ani))
print(f"  ANIs no estandar (no tienen exactamente 10 digitos numericos): {len(anomalos_ani)}")
for d, a in anomalos_ani[:5]:
    print(f"     DNI {d} -> ANI '{a}'")

# 5. Auditoria de CUITs
print("\n--- 5. AUDITORIA DE CUITS EN PERSONAS COMPLETADAS ---")
c.execute("SELECT dni, cuit FROM personas WHERE estado_proceso = 'completado'")
cuit_anomalos = []
cuit_nulos = 0
for r in c.fetchall():
    cuit = r['cuit']
    if not cuit:
        cuit_nulos += 1
    else:
        cuit_clean = "".join(filter(str.isdigit, str(cuit)))
        if len(cuit_clean) != 11:
            cuit_anomalos.append((r['dni'], cuit))

print(f"  Personas completadas sin CUIT: {cuit_nulos}")
print(f"  CUITs con formato invalido (distinto de 11 digitos): {len(cuit_anomalos)}")
for d, cu in cuit_anomalos[:5]:
    print(f"     DNI {d} -> CUIT '{cu}'")

# 6. Auditoria de JSON validos
print("\n--- 6. INTEGRIDAD DE CAMPOS JSON ---")
json_corruptos = 0
c.execute("SELECT id, detalles_json FROM telcos_scraping WHERE detalles_json IS NOT NULL")
for r in c.fetchall():
    try:
        json.loads(r['detalles_json'])
    except Exception:
        json_corruptos += 1
print(f"  JSONs corruptos en telcos_scraping: {json_corruptos}")

# 7. Cobertura: Lineas descubiertas vs Telcos scraping
print("\n--- 7. COBERTURA TELCOS SCRAPING ---")
c.execute("""
    SELECT count(DISTINCT l.ani) 
    FROM lineas_descubiertas l 
    LEFT JOIN telcos_scraping t ON l.ani = t.ani 
    WHERE t.id IS NULL
""")
lineas_sin_telco = c.fetchone()[0]
print(f"  Lineas descubiertas que NUNCA fueron auditadas en Telcos: {lineas_sin_telco}")

c.execute("SELECT count(*) FROM lineas_descubiertas")
total_lineas = c.fetchone()[0]
print(f"  Total lineas descubiertas en la base: {total_lineas}")

c.execute("SELECT operador_detectado, count(*) FROM telcos_scraping GROUP BY operador_detectado")
print("  Distribucion por operador en telcos_scraping:")
for r in c.fetchall():
    print(f"     {r[0]}: {r[1]}")

conn.close()
