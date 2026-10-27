# -*- coding: utf-8 -*-
"""
Script de Remediación: Revertir registros de ENACOM Web erróneamente salteados hacia 'telcos'
Restaura las tareas de tareas_staging a scraper_actual = 'enacom_web', estado_local = 'pendiente'.
"""
import os
import sys
import time
import sqlite3

# Configurar codificación UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "staging_local.db")

def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ Base de datos no encontrada: {DB_PATH}")
        sys.exit(1)

    print("=" * 70)
    print("🛠️ REMEDIADOR DE REGISTROS SALTEADOS ENACOM WEB")
    print(f"Ruta BD: {DB_PATH}")
    print("=" * 70)

    max_intentos = 10
    for intento in range(1, max_intentos + 1):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            c = conn.cursor()
            c.execute("PRAGMA busy_timeout = 30000;")

            # 1. Detectar tareas erróneamente derivadas a 'telcos'
            c.execute("""
                SELECT count(*) 
                FROM tareas_staging 
                WHERE scraper_actual = 'telcos' 
                  AND (descripcion LIKE '%scraper_enacom%' OR resultado_json LIKE '%scraper_enacom%');
            """)
            afectados_telcos = c.fetchone()[0]
            print(f"📋 Tareas marcadas como 'telcos' por fallo en pre-flight: {afectados_telcos:,}")

            if afectados_telcos > 0:
                c.execute("""
                    UPDATE tareas_staging 
                    SET scraper_actual = 'enacom_web',
                        estado_local = 'pendiente',
                        descripcion = NULL,
                        fecha_lease = NULL
                    WHERE scraper_actual = 'telcos' 
                      AND (descripcion LIKE '%scraper_enacom%' OR resultado_json LIKE '%scraper_enacom%');
                """)
                reparados = c.rowcount
                print(f"✅ Se restauraron {reparados:,} tareas a [scraper_actual='enacom_web', estado_local='pendiente'].")

            # 2. Revertir tareas trabadas en 'en_proceso' de enacom
            c.execute("""
                UPDATE tareas_staging
                SET estado_local = 'pendiente',
                    fecha_lease = NULL
                WHERE scraper_actual IN ('enacom_web', 'scraper_enacom')
                  AND estado_local = 'en_proceso';
            """)
            huerfanos = c.rowcount
            if huerfanos > 0:
                print(f"🔄 Se liberaron {huerfanos:,} tareas de ENACOM que habían quedado 'en_proceso'.")

            conn.commit()

            # Resumen final de tareas listas para enacom_web
            c.execute("""
                SELECT count(*) 
                FROM tareas_staging 
                WHERE scraper_actual IN ('enacom_web', 'scraper_enacom')
                  AND estado_local = 'pendiente';
            """)
            total_pendientes_enacom = c.fetchone()[0]
            print(f"\n🎯 Total tareas listas y pendientes para ENACOM Web: {total_pendientes_enacom:,}")
            print("=" * 70)
            conn.close()
            break
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and intento < max_intentos:
                print(f"⏳ BD bloqueada por workers concurrentes. Reintentando ({intento}/{max_intentos})...")
                time.sleep(1.5)
            else:
                print(f"❌ Error al acceder a la base de datos: {e}")
                sys.exit(1)

if __name__ == "__main__":
    main()
