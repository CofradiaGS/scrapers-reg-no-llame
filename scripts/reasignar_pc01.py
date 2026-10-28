# -*- coding: utf-8 -*-
import os
import sys
import time
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import mysql.connector
import config

def reasignar_registros():
    print("=" * 60)
    print("🔄 REASIGNACIÓN DE COBRO EXPRESS A PC-01 EN VPS")
    print("=" * 60)
    
    conn = mysql.connector.connect(
        host=config.VPS_DBHOST,
        port=config.VPS_DBPORT,
        user=config.COLA_AUTO_DBUSER,
        password=config.COLA_AUTO_DBPASS,
        database=config.VPS_DBNAME,
        autocommit=True,
        connection_timeout=15
    )
    cursor = conn.cursor(dictionary=True)
    
    # 1. Total inicial a reasignar
    cursor.execute("""
        SELECT COUNT(*) as pendientes
        FROM cola_automatizacion FORCE INDEX (idx_cola_auto_estado)
        WHERE auto_id = 'scraper_cobro_express'
          AND estado = 'pendiente'
          AND (target_pc != 'PC-01' OR target_pc IS NULL)
    """)
    total_inicial = cursor.fetchone()["pendientes"]
    print(f"📊 Registros pendientes a reasignar hacia 'PC-01': {total_inicial:,}")
    
    if total_inicial == 0:
        print("✅ No hay registros pendientes pendientes de reasignar.")
        conn.close()
        return

    chunk_size = 10000
    total_reasignados = 0
    t0 = time.time()
    
    query_update = f"""
        UPDATE cola_automatizacion
        SET target_pc = 'PC-01'
        WHERE auto_id = 'scraper_cobro_express'
          AND estado = 'pendiente'
          AND (target_pc != 'PC-01' OR target_pc IS NULL)
        LIMIT {chunk_size}
    """
    
    while True:
        cursor.execute(query_update)
        afectados = cursor.rowcount
        if afectados <= 0:
            break
        total_reasignados += afectados
        elapsed = time.time() - t0
        ritmo = int(total_reasignados / elapsed) if elapsed > 0 else 0
        porc = (total_reasignados / total_inicial) * 100 if total_inicial > 0 else 100
        print(f"  • Reasignados: {total_reasignados:,} / {total_inicial:,} ({porc:.1f}%) | Ritmo: {ritmo:,} reg/s")
        if afectados < chunk_size:
            break
            
    print("-" * 60)
    print(f"✅ Total reasignados con éxito a 'PC-01': {total_reasignados:,} en {time.time() - t0:.2f}s")
    
    # Verificación final de distribución
    print("\n🔍 Distribución actual de target_pc en cola_automatizacion (scraper_cobro_express, pendiente):")
    cursor.execute("""
        SELECT target_pc, COUNT(*) as c
        FROM cola_automatizacion FORCE INDEX (idx_cola_auto_estado)
        WHERE auto_id = 'scraper_cobro_express'
          AND estado = 'pendiente'
        GROUP BY target_pc
    """)
    for r in cursor.fetchall():
        print(f"  • target_pc: {r['target_pc']} -> {r['c']:,} registros")
        
    conn.close()

if __name__ == "__main__":
    reasignar_registros()
