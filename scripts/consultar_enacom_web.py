# -*- coding: utf-8 -*-
"""
Script de Consola: Consultor Oficial de Numeración y Portabilidad ENACOM Web
Permite verificar la asignación original y el prestador actual en vivo de cualquier
línea telefónica de Argentina consultando el portal oficial de ENACOM con resolución
autónoma de captcha OCR y evasión de reCAPTCHA v3.

Uso:
    python scripts/consultar_enacom_web.py 1161234567
    python scripts/consultar_enacom_web.py 1150000000 1140000000 3515123456
    python scripts/consultar_enacom_web.py --file lineas.txt
"""
import sys
import os
import argparse
import time

# Asegurar UTF-8 en stdout/stderr para Windows PowerShell
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Asegurar path raíz del proyecto
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.domain.entities import Linea, StatusScraping
from adapters.scrapers.registry import ScraperRegistry


def main():
    parser = argparse.ArgumentParser(description="Consultor Oficial de Numeración y Portabilidad ENACOM Web")
    parser.add_argument("lineas", nargs="*", help="Líneas telefónicas de 10 dígitos (ej: 1161234567)")
    parser.add_argument("--file", "-f", help="Archivo de texto con un número por línea")
    parser.add_argument("--headless", action="store_true", default=True, help="Ejecutar Chromium en modo headless (por defecto True)")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Abrir ventana visible del navegador")
    parser.add_argument("--proxy", help="URL del servidor proxy (ej: http://ip:puerto o socks5://ip:puerto)")
    parser.add_argument("--tor", action="store_true", help="Enrutar a través de Tor SOCKS5 local (socks5://127.0.0.1:9050)")

    args = parser.parse_args()

    numeros = list(args.lineas)
    if args.file:
        if os.path.exists(args.file):
            with open(args.file, "r", encoding="utf-8") as f:
                for line in f:
                    clean = "".join(filter(str.isdigit, line)).strip()
                    if clean:
                        numeros.append(clean)
        else:
            print(f"❌ Error: Archivo no encontrado: {args.file}")
            sys.exit(1)

    if not numeros:
        print("⚠️ Debe especificar al menos una línea telefónica o un archivo --file.")
        print("Ejemplo: python scripts/consultar_enacom_web.py 1161234567")
        sys.exit(1)

    if args.tor:
        print("ℹ️ Modo Tor activo: tenga en cuenta que el firewall perimetral de ENACOM suele bloquear IPs anónimas.")

    print("=" * 75)
    print("📡 CONSULTOR OFICIAL DE NUMERACIÓN Y PORTABILIDAD ENACOM")
    print(f"🎯 Total de líneas a verificar: {len(numeros)}")
    print(f"🌐 Enrutamiento: {'Tor SOCKS5' if args.tor else (args.proxy or 'Conexión Directa')}")
    print("=" * 75)

    adapter = ScraperRegistry.obtener("enacom_web", headless=args.headless, proxy=args.proxy, use_tor=args.tor)

    try:
        for idx, ani in enumerate(numeros, 1):
            t0 = time.time()
            res = adapter.consultar_linea(Linea(ani=ani))
            dt = time.time() - t0

            if res.status == StatusScraping.COINCIDENCIA:
                d = res.detalles
                es_portado = d.get("es_portado", False)
                tag_port = "🚨 [PORTADO]" if es_portado else "🟢 [ORIGINAL]"

                print(f"[{idx}/{len(numeros)}] ANI: {ani} | {tag_port} (⏱️ {dt:.2f}s)")
                print(f"   🏢 Prestador Original : {d.get('prestador_original')} ({d.get('operador_comercial_original')})")
                print(f"   📲 Prestador Actual   : {d.get('prestador_actual')} ({d.get('operador_comercial_actual')})")
                print(f"   🔄 Intentos Captcha   : {d.get('intentos_resolucion', 1)}")
            else:
                print(f"[{idx}/{len(numeros)}] ANI: {ani} | ⚠️ {res.status.value}: {res.descripcion} (⏱️ {dt:.2f}s)")

            print("-" * 75)

    finally:
        adapter.cerrar()
        print("\n✅ Proceso completado con éxito.")


if __name__ == "__main__":
    main()
