# -*- coding: utf-8 -*-
"""
Verificación de Estado de IP Local contra ENACOM
Comprueba si el mensaje 'Ha alcanzado el límite de 100 consultas diarias' sigue activo
o si se trataba de un rate limit temporal que ya fue levantado.
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.domain.entities import Linea, StatusScraping
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter

def main():
    print("=" * 60)
    print("TEST DE ESTADO DE IP LOCAL CONTRA ENACOM")
    print("=" * 60)
    
    # Usar una línea argentina típica
    test_ani = "1155986877"
    linea = Linea(ani=test_ani)
    
    print(f"Probando consulta directa para línea {test_ani} sin proxies...")
    adapter = EnacomWebAdapter(headless=True)
    try:
        adapter.iniciar()
        t0 = time.time()
        res = adapter.consultar_linea(linea)
        dt = round(time.time() - t0, 2)
        
        print("\n" + "-" * 50)
        print("RESULTADO DE LA CONSULTA:")
        print(f"Status: {res.status}")
        print(f"Descripción: {res.descripcion}")
        print(f"Detalles: {res.datos_json}")
        print(f"Tiempo: {dt}s")
        print("-" * 50)
        
        if res.status == StatusScraping.COINCIDENCIA:
            print("\n[CONCLUSION] ¡LA IP LOCAL ESTA TOTALMENTE DESBLOQUEADA!")
            print("El límite NO era diario fijo de 24hs o ya se reinició.")
        elif "diarias" in res.descripcion.lower() or "límite" in res.descripcion.lower():
            print("\n[CONCLUSION] EL BLOQUEO SIGUE ACTIVO:")
            print(f"Mensaje oficial de ENACOM: '{res.descripcion}'")
            print("Se confirma que el límite de 100 consultas es diario por IP pública.")
        else:
            print(f"\n[CONCLUSION] Respuesta: {res.status} - {res.descripcion}")
            
    except Exception as e:
        print(f"\n[ERROR]: {type(e).__name__}: {e}")
    finally:
        adapter.cerrar()

if __name__ == "__main__":
    main()
