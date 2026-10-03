# -*- coding: utf-8 -*-
"""
Script de Saneamiento y Corrección: Banco Macro SQLite
Aplica correcciones directas sobre los registros afectados identificados en la auditoría:
1. Normaliza géneros 'm'/'f' a 'Masculino'/'Femenino'.
2. Reemplaza el carácter '?' residual por 'Ñ' en nombre_oficial.
3. Resetea a 'pendiente' los 88 registros descartados por horario comercial de IRIS.
4. Resetea a 'pendiente' los 12 registros con CUIT corrupto (<11 dígitos) o texto honeypot.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("data/banco_macro.sqlite")

def sanear_base():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("Iniciando saneamiento de datos en", DB_PATH)

    # 1. Normalizar géneros 'm' y 'f'
    cursor.execute("UPDATE personas SET genero = 'Masculino' WHERE genero = 'm'")
    m_count = cursor.rowcount
    cursor.execute("UPDATE personas SET genero = 'Femenino' WHERE genero = 'f'")
    f_count = cursor.rowcount
    print(f"1. Géneros normalizados: {m_count} Masculinos, {f_count} Femeninos")

    # 2. Corregir '?' en nombre_oficial
    cursor.execute("SELECT count(*) FROM personas WHERE nombre_oficial LIKE '%?%'")
    q_count = cursor.fetchone()[0]
    cursor.execute("UPDATE personas SET nombre_oficial = replace(nombre_oficial, '?', 'Ñ') WHERE nombre_oficial LIKE '%?%'")
    print(f"2. Nombres corregidos ('?' -> 'Ñ'): {q_count} registros")

    # 3. Resetear registros de horario comercial de IRIS
    cursor.execute("SELECT count(*) FROM personas WHERE error_msg LIKE '%horario comercial%'")
    hc_count = cursor.fetchone()[0]
    cursor.execute("""
        UPDATE personas 
        SET estado_proceso = 'pendiente', error_msg = NULL 
        WHERE error_msg LIKE '%horario comercial%'
    """)
    print(f"3. Registros de horario comercial reseteados a 'pendiente': {hc_count}")

    # 4. Resetear CUITs corruptos
    cursor.execute("""
        SELECT count(*) FROM personas 
        WHERE cuit IS NOT NULL AND length(replace(cuit, '-', '')) != 11
    """)
    cuit_bad_count = cursor.fetchone()[0]
    cursor.execute("""
        UPDATE personas 
        SET estado_proceso = 'pendiente', cuit = NULL, genero = NULL, nombre_oficial = NULL, error_msg = NULL 
        WHERE cuit IS NOT NULL AND length(replace(cuit, '-', '')) != 11
    """)
    print(f"4. Registros con CUIT corrupto reseteados a 'pendiente': {cuit_bad_count}")

    conn.commit()
    conn.close()
    print("Saneamiento completado con éxito.")

if __name__ == "__main__":
    sanear_base()
