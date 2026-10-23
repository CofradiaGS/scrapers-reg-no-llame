import os
import sys
import time
from datetime import datetime

# Asegurar encoding UTF-8 en consola Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Asegurar path raíz
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.domain.entities import Linea
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter

def main():
    print("=" * 75)
    print("🚀 INICIANDO BENCHMARK DE 100 CONSULTAS ENACOM CON SMARTPROXY")
    print("   Meta de calidad: >= 95% de éxito al primer intento (sin reintentos)")
    print(f"   Inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)

    adapter = EnacomWebAdapter(headless=True)
    pasan_de_1 = 0
    con_reintentos = 0
    fallidos = 0
    total = 100

    # Generar 100 números telefónicos representativos de Argentina
    numeros = [f"114{i:07d}" for i in range(3000, 3100)]

    try:
        for idx, ani in enumerate(numeros, 1):
            t0 = time.time()
            linea = Linea(ani=ani)
            res = adapter.consultar_linea(linea)
            dur = round(time.time() - t0, 2)
            
            intentos = res.detalles.get("intentos", 1) if res.detalles else 1
            op_act = (res.detalles or {}).get("razon_social") or res.status.value
            
            if res.status.value in ("coincidencia", "sin_coincidencia"):
                if intentos == 1:
                    pasan_de_1 += 1
                    status_lbl = "✅ 1er intento (DIRECTO)"
                else:
                    con_reintentos += 1
                    status_lbl = f"🔄 Con reintento (#{intentos})"
            else:
                fallidos += 1
                status_lbl = f"❌ Error ({res.descripcion})"

            tasa_pasan = (pasan_de_1 / idx) * 100.0
            print(f"[{idx:3d}/100] ANI:{ani} -> {status_lbl:25} ({dur:5.2f}s) | Efectividad 1er intento: {pasan_de_1}/{idx} ({tasa_pasan:5.1f}%) | {op_act[:35]}")
            
    finally:
        adapter.cerrar()

    tasa_final = (pasan_de_1 / total) * 100.0
    print("\n" + "=" * 75)
    print("📊 RESULTADO FINAL DEL BENCHMARK DE 100 CONSULTAS:")
    print(f"  • Pasan de 1 (sin reintentos): {pasan_de_1:3d} ({tasa_final:5.1f}%)")
    print(f"  • Con reintentos:              {con_reintentos:3d} ({(con_reintentos/total)*100:5.1f}%)")
    print(f"  • Fallos totales:              {fallidos:3d} ({(fallidos/total)*100:5.1f}%)")
    print("=" * 75)

if __name__ == "__main__":
    main()
