"""
Script de normalización y limpieza de la columna `condicion_afip` en SQLite y Excel.

Traduce los registros técnicos de AFIP / CuitOnline a categorías impositivas reales:
- 'Monotributista'
- 'Monotributo Social'
- 'Responsable Inscripto'
- 'IVA Exento'
- 'No Inscripto (Solo CUIL / Empleado o Jubilado)'
"""

import os
import sys
import sqlite3
import json
import argparse
from collections import Counter

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "banco_macro.sqlite")

def clasificar_condicion(cuit_data: dict, cond_actual: str = None) -> str:
    """
    Determina la condición impositiva oficial en base a las reglas de AFIP / CuitOnline.
    """
    if not cuit_data:
        cuit_data = {}

    iva = (cuit_data.get("iva") or "").strip().lower()
    ganancias = (cuit_data.get("ganancias") or "").strip().lower()
    impuestos = [str(x).upper() for x in (cuit_data.get("impuestos_activos") or [])]
    
    # 1. Monotributo (Régimen Simplificado)
    if any("MONOTRIBUTO" in imp for imp in impuestos):
        if any("SOCIAL" in imp for imp in impuestos):
            return "Monotributo Social"
        return "Monotributista"

    # 2. Responsable Inscripto (Régimen General: IVA y/o Ganancias)
    if "inscripto" in iva or "personas fisicas" in ganancias or "ganancias" in ganancias:
        return "Responsable Inscripto"
    
    # Evaluar si el texto anterior guardado era de Responsable Inscripto
    if cond_actual and ("iva: inscripto" in cond_actual.lower() or "ganancias: personas" in cond_actual.lower() or "ganancias: ganancias" in cond_actual.lower()):
        return "Responsable Inscripto"

    # 3. IVA Exento
    if "exento" in iva or (cond_actual and "iva exento" in cond_actual.lower()):
        return "IVA Exento"

    # 4. No registra impuestos activos explícitamente en AFIP
    if any("NO REGISTRA IMPUESTOS" in imp for imp in impuestos):
        return "No Inscripto (Solo CUIL / Empleado o Jubilado)"

    # 5. Si tiene datos de CuitOnline pero sin impuestos comerciales activos (es empleado/jubilado)
    if cuit_data.get("cuit_limpio") or cuit_data.get("cuit"):
        return "No Inscripto (Solo CUIL / Empleado o Jubilado)"

    return None


def normalizar_base_datos(export_excel: bool = False):
    if not os.path.exists(DB_PATH):
        print(f"❌ Base de datos no encontrada: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("🔍 Analizando tabla 'personas' en banco_macro.sqlite...")
    cursor.execute("SELECT dni, condicion_afip, datos_json FROM personas")
    rows = cursor.fetchall()
    print(f"📊 Total personas a evaluar: {len(rows)}")

    modificados = 0
    conteo_nuevas_condiciones = Counter()

    for dni, cond_actual, dj in rows:
        data = json.loads(dj) if dj else {}
        co_data = data.get("cuitonline", {})

        nueva_cond = clasificar_condicion(co_data, cond_actual)

        # Si cambió el valor o no estaba seteado
        if nueva_cond != cond_actual and nueva_cond is not None:
            cursor.execute(
                "UPDATE personas SET condicion_afip = ? WHERE dni = ?",
                (nueva_cond, dni)
            )
            modificados += 1

        val_final = nueva_cond if nueva_cond is not None else (cond_actual or "Sin Información")
        conteo_nuevas_condiciones[val_final] += 1

    conn.commit()
    conn.close()

    print(f"\n✅ Actualización finalizada exitosamente.")
    print(f"   Registros modificados/normalizados: {modificados}")
    print("\n📋 Resumen de Condición AFIP en la base de datos:")
    for cond, total in conteo_nuevas_condiciones.most_common():
        print(f"   - {cond:45s}: {total:4d}")

    if export_excel:
        print("\n📊 Regenerando archivo Excel con las nuevas condiciones...")
        script_excel = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exportar_banco_macro_excel.py")
        os.system(f'python "{script_excel}"')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalizar y clasificar la Condición AFIP en SQLite")
    parser.add_argument("--export-excel", action="store_true", help="Regenerar base_macro_enriquecida.xlsx tras normalizar")
    args = parser.parse_args()

    normalizar_base_datos(export_excel=args.export_excel)
