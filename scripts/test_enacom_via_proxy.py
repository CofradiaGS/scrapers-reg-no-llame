# -*- coding: utf-8 -*-
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.domain.entities import Linea, StatusScraping
from adapters.scrapers.enacom_web.enacom_web_adapter import EnacomWebAdapter

def main():
    proxy = "http://181.192.2.23:8080"
    print(f"Probando consulta ENACOM completa vía proxy: {proxy}")
    
    test_ani = "1155986877"
    linea = Linea(ani=test_ani)
    
    adapter = EnacomWebAdapter(headless=True, proxy=proxy)
    try:
        adapter.iniciar()
        t0 = time.time()
        res = adapter.consultar_linea(linea)
        dt = round(time.time() - t0, 2)
        
        print("\n" + "=" * 50)
        print("RESULTADO DE LA CONSULTA VIA PROXY:")
        print(f"Status: {res.status}")
        print(f"Descripción: {res.descripcion}")
        print(f"Detalles: {res.datos}")
        print(f"Tiempo total: {dt}s")
        print("=" * 50)
        
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")
    finally:
        adapter.cerrar()

if __name__ == "__main__":
    main()
