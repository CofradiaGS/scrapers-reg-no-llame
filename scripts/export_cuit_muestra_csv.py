# -*- coding: utf-8 -*-
"""
Exporta una muestra en CSV con los registros recientemente procesados por CuitOnline Perfecto.
"""
import sys
import sqlite3
import json
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"
CSV_OUTPUT = PROJECT_ROOT / "muestra_cuitonline_procesados.csv"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def exportar_csv():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    query = """
        SELECT 
            dni,
            nombre_excel,
            nombre_oficial,
            cuit,
            condicion_afip,
            domicilio_fiscal,
            localidad,
            provincia,
            genero,
            empleador,
            actividades_afip,
            constancia_afip_url,
            fecha_actualizacion,
            datos_json
        FROM personas
        WHERE datos_json LIKE '%cuitonline_perfecto%'
        ORDER BY fecha_actualizacion DESC, dni ASC
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()

    print(f"📊 Registros procesados por CuitOnline Perfecto encontrados: {len(rows)}")

    registros = []
    for r in rows:
        (dni, nom_ex, nom_of, cuit, cond, dom, loc, prov, gen, emp, act, const_url, f_act, dj) = r
        data = json.loads(dj) if dj else {}
        co = data.get("cuitonline", {})

        # Impuestos formateados
        impuestos_list = co.get("impuestos_activos") or []
        impuestos_str = " | ".join(impuestos_list) if impuestos_list else "Sin impuestos activos"

        # Actividades formateadas
        actividades_list = co.get("actividades") or []
        actividades_str = " | ".join(actividades_list) if actividades_list else ""

        registros.append({
            "DNI": dni,
            "Nombre Excel": nom_ex,
            "Nombre Oficial (AFIP)": nom_of,
            "CUIT": cuit,
            "Condición AFIP": cond,
            "Impuestos Activos": impuestos_str,
            "Actividades Económicas": actividades_str,
            "Domicilio Fiscal": dom,
            "Localidad": loc,
            "Provincia": prov,
            "Género": gen,
            "Empleador": emp,
            "Constancia AFIP URL": const_url,
            "Fecha Procesado": f_act
        })

    df = pd.DataFrame(registros)
    # Guardar con codificación UTF-8 con BOM para que Excel en español lo abra perfecto sin romper tildes ni caracteres especiales
    df.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig", sep=";")
    print(f"✨ Archivo CSV exportado exitosamente en: {CSV_OUTPUT}")

    # Mostrar preview
    print("\n🔍 Preview de los primeros 10 registros:")
    for i, reg in enumerate(registros[:10]):
        print(f"[{i+1:2d}] DNI: {reg['DNI']} | CUIT: {reg['CUIT']} | {reg['Nombre Oficial (AFIP)']}")
        print(f"     Condición: {reg['Condición AFIP']} | Empleador: {reg['Empleador']}")
        print(f"     Impuestos: {reg['Impuestos Activos'][:80]}...")
        print(f"     Domicilio: {reg['Domicilio Fiscal']}, {reg['Localidad']}, {reg['Provincia']}")
        print("-" * 75)

if __name__ == "__main__":
    exportar_csv()
