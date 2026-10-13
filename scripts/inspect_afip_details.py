import sqlite3
import json

conn = sqlite3.connect('data/banco_macro.sqlite')
c = conn.cursor()
c.execute("SELECT dni, nombre_oficial, condicion_afip, datos_json FROM personas WHERE condicion_afip IS NOT NULL LIMIT 10")
for dni, nom, cond, dj in c.fetchall():
    data = json.loads(dj) if dj else {}
    cuit_info = data.get('cuitonline', {})
    print(f"DNI: {dni} | {nom}")
    print(f"  condicion_afip: {cond}")
    print(f"  iva: {cuit_info.get('iva')}")
    print(f"  ganancias: {cuit_info.get('ganancias')}")
    print(f"  impuestos_activos: {cuit_info.get('impuestos_activos')}")
    print(f"  regimenes_activos: {cuit_info.get('regimenes_activos')}")
conn.close()
