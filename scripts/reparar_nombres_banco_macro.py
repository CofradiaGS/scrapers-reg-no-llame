# -*- coding: utf-8 -*-
"""
Script de Reparación de Nombres Oficiales: Banco Macro
Elimina los textos ofuscados de CuitOnline y restaura los nombres oficiales legítimos
a partir de la Central de Deudores del BCRA o el padrón original de Banco Macro.
"""
import sqlite3
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"


def es_texto_ofuscado(nombre_oficial: str, nombre_excel: str) -> bool:
    """Determina si un nombre oficial es una cadena ofuscada proveniente de CuitOnline."""
    if not nombre_oficial or not nombre_oficial.strip():
        return True
    
    # 1. Comparar palabras con el nombre real de Excel
    words_excel = set(w.upper() for w in nombre_excel.split() if len(w) > 2)
    words_oficial = set(w.upper() for w in nombre_oficial.split() if len(w) > 2)
    overlap = words_excel.intersection(words_oficial)
    
    # Si comparten palabras clave significativas, es un nombre real (ej: 'PEREZ MARIA CRISTINA' vs 'MARIA CRISTINA PEREZ')
    if overlap:
        return False
        
    return True


def reparar_nombres():
    print(f"🔌 Conectando a {DB_PATH}...")
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    rows = c.execute("SELECT dni, nombre_excel, nombre_oficial, datos_json FROM personas").fetchall()
    print(f"📊 Total personas a auditar: {len(rows)}")
    
    restaurados_bcra = 0
    restaurados_excel = 0
    ya_correctos = 0
    
    updates = []
    
    for r in rows:
        dni, n_excel, n_oficial, dj_str = r
        n_oficial = (n_oficial or "").strip()
        n_excel = (n_excel or "").strip()
        
        dj = json.loads(dj_str) if dj_str else {}
        bcra_name = dj.get("bcra", {}).get("denominacion")
        if bcra_name:
            bcra_name = bcra_name.strip()
            
        if es_texto_ofuscado(n_oficial, n_excel):
            # Prioridad 1: Nombre oficial verificado de BCRA
            if bcra_name and not es_texto_ofuscado(bcra_name, n_excel):
                nuevo_nombre = bcra_name
                restaurados_bcra += 1
            else:
                # Prioridad 2: Nombre limpio original de Banco Macro
                nuevo_nombre = n_excel
                restaurados_excel += 1
                
            updates.append((nuevo_nombre, dni))
        else:
            ya_correctos += 1
            
    print(f"✅ Ya eran correctos (BCRA/Datuar): {ya_correctos}")
    print(f"🔄 Restaurados con Denominación Oficial BCRA: {restaurados_bcra}")
    print(f"🔄 Restaurados con Nombre Original Banco Macro: {restaurados_excel}")
    print(f"💾 Aplicando {len(updates)} actualizaciones en SQLite...")
    
    c.executemany("UPDATE personas SET nombre_oficial = ? WHERE dni = ?", updates)
    conn.commit()
    conn.close()
    print("✨ Reparación de nombres completada con éxito.")


if __name__ == "__main__":
    reparar_nombres()
