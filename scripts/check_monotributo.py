import sqlite3
import json

conn = sqlite3.connect('data/banco_macro.sqlite')
c = conn.cursor()
c.execute("SELECT dni, nombre_oficial, condicion_afip, datos_json FROM personas WHERE datos_json LIKE '%monotribut%'")
for dni, nom, cond, dj in c.fetchall():
    print(f"DNI {dni} | {nom} | Condicion actual: {cond}")
    d = json.loads(dj)
    print("  CUITOnline:", d.get('cuitonline'))
conn.close()
