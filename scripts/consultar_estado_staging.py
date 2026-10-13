# -*- coding: utf-8 -*-
"""
Monitor en Vivo de Base de Datos Staging Local (data/staging_local.db)
Muestra:
1. Resumen de estados (Pendientes, En Proceso, Completados, Sincronizados).
2. Distribución por prestador/compañía (Claro, Movistar, Personal, etc.).
3. Últimas líneas procesadas en tiempo real.
"""
import sqlite3
import json
import os
import sys

# Asegurar UTF-8 en Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "staging_local.db")

import argparse

def main():
    parser = argparse.ArgumentParser(description="Monitor de Base de Datos Staging Local")
    parser.add_argument("--revertir-huerfanos", action="store_true", help="Revertir tareas trabadas en 'en_proceso' de vuelta a 'pendiente'")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"❌ No se encontró la base de datos local en: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if args.revertir_huerfanos:
        c.execute("UPDATE tareas_staging SET estado_local = 'pendiente' WHERE estado_local = 'en_proceso'")
        cambiados = c.rowcount
        conn.commit()
        print(f"🔄 Se revirtieron {cambiados:,} registros de 'en_proceso' a 'pendiente' exitosamente.\n")

    print("=" * 80)
    print("📊 MONITOR EN VIVO: STAGING LOCAL ENACOM / REGISTRO NO LLAME")
    print(f"Ruta BD: {DB_PATH}")
    print("=" * 80)

    # 1. Resumen general por estado y scraper
    c.execute("""
        SELECT estado_local, scraper_actual, COUNT(*) 
        FROM tareas_staging 
        GROUP BY estado_local, scraper_actual
        ORDER BY estado_local, scraper_actual
    """)
    estados = c.fetchall()
    
    print("\n📌 ESTADO GENERAL DE LA COLA:")
    print(f"{'Estado Local':<15} | {'Scraper / Posta':<18} | {'Cantidad':>10}")
    print("-" * 50)
    total_filas = 0
    for est, scr, cnt in estados:
        total_filas += cnt
        print(f"{est:<15} | {str(scr):<18} | {cnt:>10,}")
    print("-" * 50)
    print(f"{'TOTAL EN STAGING':<36} | {total_filas:>10,}")

    # 2. Distribución de Compañías / Prestadores confirmados
    c.execute("""
        SELECT resultado_json, payload_origen, descripcion, scraper_actual, estado_local
        FROM tareas_staging
    """)
    rows = c.fetchall()

    companias = {}
    portadas = 0
    total_con_datos = 0

    for r_json, p_origen, desc, scr, est in rows:
        op = None
        # Probar resultado_json
        if r_json:
            try:
                data = json.loads(r_json)
                if "enacom_web" in data:
                    ew = data["enacom_web"]
                    op = ew.get("operador_comercial_actual") or ew.get("prestador_actual") or ew.get("operador_origen")
                    if ew.get("es_portado"):
                        portadas += 1
                elif "claro" in data and data["claro"].get("status") == "coincidencia":
                    op = "Claro"
                elif "personal" in data and data["personal"].get("status") == "coincidencia":
                    op = "Personal"
                elif "movistar" in data and data["movistar"].get("status") == "coincidencia":
                    op = "Movistar"
            except Exception:
                pass

        # Probar payload_origen si no está en resultado_json
        if not op and p_origen:
            try:
                p_data = json.loads(p_origen)
                if isinstance(p_data, dict):
                    if "enacom" in p_data and isinstance(p_data["enacom"], dict):
                        op = p_data["enacom"].get("operador_origen")
                    elif p_data.get("operador"):
                        op = p_data.get("operador")
            except Exception:
                pass

        if not op and desc:
            if "ENACOM:" in desc:
                op = desc.split("ENACOM:")[1].split("(")[0].strip()
            elif "Claro" in desc and "Sin registros" not in desc:
                op = "Claro"
            elif "Personal" in desc and "Sin registros" not in desc:
                op = "Personal"
            elif "Movistar" in desc and "Sin registros" not in desc:
                op = "Movistar"

        if op:
            total_con_datos += 1
            op_clean = op.strip().title()
            companias[op_clean] = companias.get(op_clean, 0) + 1

    print("\n🏢 DISTRIBUCIÓN POR COMPAÑÍA / PRESTADOR (TOP 10):")
    if companias:
        print(f"{'Compañía':<30} | {'Líneas':>10} | {'Porcentaje':>10}")
        print("-" * 57)
        sorted_comp = sorted(companias.items(), key=lambda x: x[1], reverse=True)
        top_10 = sorted_comp[:10]
        resto = sorted_comp[10:]
        
        for comp, cnt in top_10:
            pct = (cnt / total_con_datos * 100) if total_con_datos else 0
            print(f"{comp:<30} | {cnt:>10,} | {pct:>9.1f}%")
            
        if resto:
            resto_cnt = sum(c[1] for c in resto)
            resto_pct = (resto_cnt / total_con_datos * 100) if total_con_datos else 0
            print(f"{f'Otras ({len(resto)} operadores/coop)':<30} | {resto_cnt:>10,} | {resto_pct:>9.1f}%")

        print("-" * 57)
        print(f"{'TOTAL IDENTIFICADAS':<30} | {total_con_datos:>10,} | {100.0:>9.1f}%")
        if portadas > 0:
            print(f"🚨 Líneas con Portabilidad Numérica confirmada: {portadas:,}")
    else:
        print("  (Aún no hay resultados de compañías procesados en esta tanda)")

    # 3. Muestra de las últimas 10 líneas procesadas
    c.execute("""
        SELECT numero_de_linea, estado_local, scraper_actual, descripcion, fecha_procesado 
        FROM tareas_staging 
        WHERE descripcion IS NOT NULL AND descripcion != ''
        ORDER BY rowid DESC 
        LIMIT 10
    """)
    ultimas = c.fetchall()

    print("\n🔍 ÚLTIMAS LÍNEAS PROCESADAS CON RESULTADO:")
    if ultimas:
        print(f"{'Línea (ANI)':<12} | {'Estado':<12} | {'Descripción / Prestador':<45}")
        print("-" * 75)
        for ani, est, scr, desc, fp in ultimas:
            d_short = (desc or '')[:42] + "..." if len(desc or '') > 42 else (desc or '')
            print(f"{ani:<12} | {est:<12} | {d_short:<45}")
    else:
        print("  (No hay líneas con descripción registrada aún)")
    print("=" * 80)

    conn.close()

if __name__ == "__main__":
    main()
