# -*- coding: utf-8 -*-
"""
Script de Inyección ETL: Carga masiva de bases descargadas a `cola_automatizacion`
Sección diferenciada: `auto_id = 'ce_bases_produccion'`
Asignado a: PC-01, GS-01, GS-542
"""
import os
import sys
import io
import re
import csv
import json
import time
import zipfile
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
import mysql.connector
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Cargar variables de entorno
load_dotenv(r"c:\Users\automatizacion.crm\Documents\GitHub\scrapers-reg-no-llame\.env")

ZIP_PATH = r"c:\Users\automatizacion.crm\Documents\GitHub\scrapers-reg-no-llame\drive-download-20261008T183652Z-1-001.zip"
AUTO_ID = "ce_bases_produccion"
TARGET_PC = "PC-01,GS-01,GS-542"
BATCH_SIZE = 2000

# Prefijos patagónicos y prioritarios
PREFIJOS_P1 = ("11", "261")
PREFIJOS_P2 = ("2901", "2920", "294", "2945", "2966", "297", "298", "299", "280", "2954", "2302", "2931", "2934", "2942")


def determinar_prioridad(ani: str) -> int:
    """Determina la prioridad de la línea según la zona geográfica."""
    if any(ani.startswith(p) for p in PREFIJOS_P1):
        return 1
    if any(ani.startswith(p) for p in PREFIJOS_P2):
        return 2
    return 3


def normalizar_linea(tel_str: str, prefijo: Optional[str] = None, line_sin_15: Optional[str] = None) -> Optional[str]:
    """Extrae y normaliza el número telefónico a exactamente 10 dígitos (ANI sin 15)."""
    if line_sin_15:
        s = line_sin_15.strip()
        if len(s) == 10 and s.isdigit():
            return s
    
    digits = re.sub(r'\D', '', str(tel_str or ''))
    if digits.startswith('549'):
        digits = digits[3:]
    elif digits.startswith('54'):
        digits = digits[2:]
    if digits.startswith('0'):
        digits = digits[1:]
    
    if len(digits) == 10:
        return digits

    # Quitar 15 si tiene prefijo conocido
    if prefijo:
        pref = str(prefijo).strip()
        if digits.startswith(pref + '15'):
            cand = pref + digits[len(pref) + 2:]
            if len(cand) == 10:
                return cand

    # Patrón común AMBA: 11 + 15 + 8 dígitos
    if digits.startswith('1115') and len(digits) == 12:
        return '11' + digits[4:]

    # Quitar 15 tras prefijo de 2, 3 o 4 dígitos
    for p_len in (2, 3, 4):
        if len(digits) == 12 and digits[p_len:p_len+2] == '15':
            cand = digits[:p_len] + digits[p_len+2:]
            if len(cand) == 10:
                return cand

    return None


def limpiar_dni(dni_raw: Any) -> Optional[str]:
    """Limpia el DNI/CUIT dejándolo solo con dígitos."""
    if not dni_raw:
        return None
    d = re.sub(r'\D', '', str(dni_raw).strip())
    return d if (6 <= len(d) <= 11) else None


def conectar_bd():
    """Establece conexión con MySQL VPS."""
    return mysql.connector.connect(
        host=os.getenv("VPS_DBHOST", "172.16.20.15"),
        user=os.getenv("COLA_AUTO_DBUSER", "automatizaciones"),
        password=os.getenv("COLA_AUTO_DBPASS", "Mg1ZOGk3mE!2_q1Q"),
        database=os.getenv("VPS_DBNAME", "bases"),
        port=int(os.getenv("VPS_DBPORT", 3306)),
        autocommit=False,
        connection_timeout=30
    )


def inyectar_bases():
    print("=" * 70)
    print("INYECCIÓN DE BASES A COLA DE AUTOMATIZACIÓN")
    print(f"Sección destino (`auto_id`): {AUTO_ID}")
    print(f"Target PC asignado:          {TARGET_PC}")
    print(f"Archivo origen:              {ZIP_PATH}")
    print("=" * 70)

    if not os.path.exists(ZIP_PATH):
        print(f"[ERROR] No se encontró el archivo: {ZIP_PATH}")
        return

    conn = conectar_bd()
    cur = conn.cursor()

    # Verificar estado previo
    cur.execute("SELECT COUNT(*) FROM cola_automatizacion WHERE auto_id = %s", (AUTO_ID,))
    prev_count = cur.fetchone()[0]
    print(f"[INFO] Registros existentes en '{AUTO_ID}': {prev_count:,}")

    query_insert = """
        INSERT IGNORE INTO cola_automatizacion (
            prioridad,
            target_pc,
            estado,
            auto_id,
            payload,
            dias_desde_ultimo_cambio,
            numero_de_linea,
            dni
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    total_procesadas = 0
    total_insertadas = 0
    total_duplicadas_o_ignoradas = 0
    total_invalidas = 0
    buffer_insert = []

    start_time = time.time()
    vistas_ani = set()

    with zipfile.ZipFile(ZIP_PATH, 'r') as z_outer:
        archivos_zip = [n for n in z_outer.namelist() if n.endswith('.zip')]
        print(f"[INFO] Paquetes zip a procesar: {len(archivos_zip)}")

        for p_idx, zip_name in enumerate(archivos_zip, 1):
            print(f"\n--- [{p_idx}/{len(archivos_zip)}] Desempaquetando {zip_name} ---")
            nested_bytes = z_outer.read(zip_name)

            with zipfile.ZipFile(io.BytesIO(nested_bytes)) as z_inner:
                csv_files = [f for f in z_inner.infolist() if f.filename.endswith('.csv')]

                for c_idx, csv_info in enumerate(csv_files, 1):
                    raw_data = z_inner.read(csv_info.filename)
                    # Detectar encoding
                    text = None
                    for enc in ('utf-8', 'latin1', 'cp1252'):
                        try:
                            text = raw_data.decode(enc)
                            break
                        except Exception:
                            pass
                    if not text:
                        continue

                    reader = csv.reader(io.StringIO(text), delimiter=';')
                    try:
                        headers = [h.strip() for h in next(reader)]
                    except StopIteration:
                        continue

                    # Mapear columnas disponibles
                    h_map = {col: i for i, col in enumerate(headers)}

                    for row in reader:
                        total_procesadas += 1

                        def get_val(key, default=""):
                            idx = h_map.get(key)
                            return row[idx].strip() if (idx is not None and idx < len(row)) else default

                        tel_raw = get_val('telefono')
                        prefijo_raw = get_val('prefijo_telefonico')
                        sin15_raw = get_val('line_sin_15')

                        ani = normalizar_linea(tel_raw, prefijo_raw, sin15_raw)
                        if not ani or len(ani) != 10:
                            total_invalidas += 1
                            continue

                        # Deduplicar a nivel de extracción para no enviar basura duplicada a MySQL
                        if ani in vistas_ani:
                            total_duplicadas_o_ignoradas += 1
                            continue
                        vistas_ani.add(ani)

                        # DNI
                        dni_raw = get_val('dni')
                        dni_limpio = limpiar_dni(dni_raw)

                        # Días desde portabilidad / cambio
                        dias_porta_raw = get_val('dias_desde_porta')
                        dias_int = None
                        if dias_porta_raw and dias_porta_raw.isdigit():
                            dias_int = int(dias_porta_raw)

                        # Prioridad geográfica
                        prio = determinar_prioridad(ani)

                        # Payload enriquecido completo
                        payload_data = {
                            "numero_de_linea": ani,
                            "dni": dni_limpio,
                            "telefono_original": tel_raw,
                            "nombre": get_val('name'),
                            "email": get_val('email'),
                            "direccion": get_val('address'),
                            "provincia": get_val('provincia'),
                            "localidad": get_val('localidad'),
                            "prefijo_telefonico": prefijo_raw,
                            "dias_desde_porta": dias_int,
                            "dias_desde_ultimo_cambio": dias_int,
                            "cur_port_date": get_val('cur_port_date'),
                            "cnt_lineas_ligadas": get_val('cnt_lineas_ligadas'),
                            "lineas_ligadas": get_val('lineas_ligadas'),
                            "evento": get_val('evento'),
                            "etiqueta_contactabilidad": get_val('etiqueta_contactabilidad'),
                            "horario_contactabilidad": get_val('horario_contactabilidad'),
                            "ce_nombre_empresa_previo": get_val('ce_nombre_empresa'),
                            "ce_primer_importe_previo": get_val('ce_primer_importe'),
                            "paquete_origen": zip_name,
                            "archivo_origen": csv_info.filename
                        }
                        payload_json = json.dumps(payload_data, ensure_ascii=False)

                        # Registro para SQL
                        record = (
                            prio,
                            TARGET_PC,
                            "pendiente",
                            AUTO_ID,
                            payload_json,
                            dias_int,
                            ani,
                            dni_limpio
                        )
                        buffer_insert.append(record)

                        # Flush por lotes
                        if len(buffer_insert) >= BATCH_SIZE:
                            cur.executemany(query_insert, buffer_insert)
                            conn.commit()
                            total_insertadas += cur.rowcount
                            buffer_insert.clear()

                            elapsed = time.time() - start_time
                            rate = total_procesadas / elapsed if elapsed > 0 else 0
                            print(
                                f"   -> Procesadas: {total_procesadas:,} | "
                                f"Enviadas: {len(vistas_ani):,} | "
                                f"En cola: {total_insertadas:,} | "
                                f"Vel: {rate:.0f} reg/s",
                                end="\r",
                                flush=True
                            )

    # Flush final de registros remanentes
    if buffer_insert:
        cur.executemany(query_insert, buffer_insert)
        conn.commit()
        total_insertadas += cur.rowcount
        buffer_insert.clear()

    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("INYECCIÓN COMPLETADA EXITOSAMENTE")
    print(f"Total filas procesadas del ZIP:    {total_procesadas:,}")
    print(f"Total líneas únicas válidas:       {len(vistas_ani):,}")
    print(f"Total tareas insertadas en cola:   {total_insertadas:,}")
    print(f"Líneas descartadas / inválidas:    {total_invalidas:,}")
    print(f"Duplicadas omitidas:               {total_duplicadas_o_ignoradas:,}")
    print(f"Tiempo total de inyección:         {elapsed:.1f} segundos")
    print("=" * 70)

    # Verificación final en BD
    cur.execute("SELECT estado, COUNT(*) FROM cola_automatizacion WHERE auto_id = %s GROUP BY estado", (AUTO_ID,))
    print(f"\nEstado actual en MySQL VPS para auto_id='{AUTO_ID}':")
    for est, cnt in cur.fetchall():
        print(f"  estado='{est}': {cnt:,} registros")

    cur.close()
    conn.close()


if __name__ == '__main__':
    inyectar_bases()
