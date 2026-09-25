#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script de Mantenimiento de Cola VPS: Reactivación de Registros para Personal.
1. Re-encamina registros desviados a 'finalizado' con resultado 'no_coincidencia'
   provenientes de IRIS hacia 'personal' en estado 'pendiente'.
2. Reactiva registros de 'personal' que quedaron en estado 'error' por timeouts.
"""
import sys
from pathlib import Path

# Configuración de codificación UTF-8 en Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from adapters.queue.mysql_vps_adapter import MySQLQueueAdapter

def main():
    print("=" * 70)
    print("🔧 REACTIVACIÓN Y RE-ENRUTAMIENTO DE REGISTROS PARA PERSONAL")
    print("=" * 70)

    adapter = MySQLQueueAdapter(pool_size=1, pool_name="script_reactivate_personal")
    stats_antes = adapter.obtener_estadisticas()
    print(f"📊 Estadísticas antes de la migración:")
    print(f"   • IRIS pendientes:        {stats_antes.get('iris_pendiente', 0):,}")
    print(f"   • Personal pendientes:    {stats_antes.get('personal_pendiente', 0):,}")
    print(f"   • Personal con error:     {stats_antes.get('personal_error', 0):,}")
    print(f"   • Finalizado no-coinc:    {stats_antes.get('finalizado_no_coincidencia', 0):,}")
    print(f"   • Total global pendiente: {stats_antes.get('pendiente', 0):,}")
    print("-" * 70)

    conn = adapter._get_connection()
    cur = conn.cursor(dictionary=True)

    try:
        # 1. Re-enrutar desviados de IRIS sin coincidencia
        print("⏳ [1/2] Re-enrutando registros de IRIS sin coincidencia hacia Personal...")
        sql_desviados = """
            UPDATE queue_registro_no_llame
            SET scraper_actual = 'personal',
                estado = 'pendiente',
                updated_at = NOW()
            WHERE scraper_actual = 'finalizado'
              AND estado = 'no_coincidencia'
              AND datos_json LIKE '%"iris"%'
              AND datos_json NOT LIKE '%"personal"%'
        """
        cur.execute(sql_desviados)
        filas_desviadas = cur.rowcount
        print(f"   ✅ {filas_desviadas:,} registros re-enrutados a 'personal' (estado: pendiente).")

        # 2. Reactivar registros con error en Personal
        print("⏳ [2/2] Reactivando registros con error en Personal...")
        sql_errores = """
            UPDATE queue_registro_no_llame
            SET estado = 'pendiente',
                updated_at = NOW()
            WHERE scraper_actual = 'personal'
              AND estado = 'error'
        """
        cur.execute(sql_errores)
        filas_errores = cur.rowcount
        print(f"   ✅ {filas_errores:,} registros de error reactivados a estado 'pendiente'.")

        conn.commit()
        print("💾 Transacción confirmada exitosamente en el VPS.")

    except Exception as e:
        conn.rollback()
        print(f"❌ Error en la actualización: {e}")
        return
    finally:
        cur.close()
        conn.close()

    print("-" * 70)
    stats_despues = adapter.obtener_estadisticas()
    print(f"📊 Estadísticas DESPUÉS de la migración:")
    print(f"   • IRIS pendientes:        {stats_despues.get('iris_pendiente', 0):,}")
    print(f"   • Personal pendientes:    {stats_despues.get('personal_pendiente', 0):,}")
    print(f"   • Personal con error:     {stats_despues.get('personal_error', 0):,}")
    print(f"   • Finalizado no-coinc:    {stats_despues.get('finalizado_no_coincidencia', 0):,}")
    print(f"   • Total global pendiente: {stats_despues.get('pendiente', 0):,}")
    print("=" * 70)
    total_listos = stats_despues.get('personal_pendiente', 0)
    print(f"🚀 ¡LISTO! Personal tiene ahora {total_listos:,} registros pendientes para procesar.")

if __name__ == "__main__":
    main()
