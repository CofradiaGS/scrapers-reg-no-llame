# -*- coding: utf-8 -*-
"""
Script de Ingesta: Banco Macro Excel a SQLite WAL
Crea el esquema relacional normalizado para el procesamiento multi-scraper
e importa las 11.675 filas (4.553 DNIs únicos) de base_macro.xlsx.
"""
import os
import sys
import sqlite3
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXCEL_PATH = PROJECT_ROOT / "base_macro.xlsx"
DB_PATH = PROJECT_ROOT / "data" / "banco_macro.sqlite"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DDL_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS personas (
    dni TEXT PRIMARY KEY,
    nombre_excel TEXT NOT NULL,
    total_operaciones_excel INTEGER DEFAULT 1,
    cuit TEXT,
    nombre_oficial TEXT,
    genero TEXT,
    edad INTEGER,
    domicilio_fiscal TEXT,
    localidad TEXT,
    ciudad TEXT,
    municipio TEXT,
    provincia TEXT,
    condicion_afip TEXT,
    actividades_afip TEXT,
    tipo_persona TEXT,
    empleador TEXT,
    constancia_afip_url TEXT,
    peor_situacion_bcra INTEGER,
    deuda_macro_miles REAL DEFAULT 0.0,
    deuda_macro_situacion INTEGER,
    deuda_total_bcra_miles REAL DEFAULT 0.0,
    total_lineas_iris INTEGER DEFAULT 0,
    lineas_activas_resumen TEXT,
    datos_json TEXT,
    estado_proceso TEXT NOT NULL DEFAULT 'pendiente',
    error_msg TEXT,
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion DATETIME
);

CREATE INDEX IF NOT EXISTS idx_personas_estado ON personas(estado_proceso);
CREATE INDEX IF NOT EXISTS idx_personas_cuit ON personas(cuit);

CREATE TABLE IF NOT EXISTS operaciones_excel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dni TEXT NOT NULL,
    nombre_apellido TEXT,
    FOREIGN KEY(dni) REFERENCES personas(dni) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_operaciones_dni ON operaciones_excel(dni);

CREATE TABLE IF NOT EXISTS bcra_entidades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dni TEXT NOT NULL,
    cuit TEXT,
    entidad TEXT NOT NULL,
    situacion INTEGER NOT NULL,
    monto_miles REAL NOT NULL,
    periodo TEXT,
    fecha_consulta DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(dni) REFERENCES personas(dni) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_bcra_dni ON bcra_entidades(dni);

CREATE TABLE IF NOT EXISTS operaciones_iris (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dni TEXT NOT NULL,
    nro_tramite_abd TEXT,
    id_tramite_spn TEXT,
    tipo_operacion TEXT,
    operador_receptor TEXT,
    fecha_operacion TEXT,
    estado TEXT,
    producto TEXT,
    tecnologia TEXT,
    FOREIGN KEY(dni) REFERENCES personas(dni) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_iris_dni ON operaciones_iris(dni);
CREATE INDEX IF NOT EXISTS idx_iris_tramite ON operaciones_iris(nro_tramite_abd);

CREATE TABLE IF NOT EXISTS lineas_descubiertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dni TEXT NOT NULL,
    ani TEXT NOT NULL,
    nro_tramite_abd TEXT,
    operador_origen TEXT,
    origen_extraccion TEXT NOT NULL DEFAULT 'iris_port_out',
    fecha_descubrimiento DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dni, ani),
    FOREIGN KEY(dni) REFERENCES personas(dni) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lineas_dni ON lineas_descubiertas(dni);
CREATE INDEX IF NOT EXISTS idx_lineas_ani ON lineas_descubiertas(ani);

CREATE TABLE IF NOT EXISTS telcos_scraping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dni TEXT NOT NULL,
    ani TEXT NOT NULL,
    operador_detectado TEXT,
    tiene_deuda BOOLEAN DEFAULT 0,
    deuda_total REAL DEFAULT 0.0,
    detalles_json TEXT,
    fecha_consulta DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(dni) REFERENCES personas(dni) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_telcos_dni ON telcos_scraping(dni);
CREATE INDEX IF NOT EXISTS idx_telcos_ani ON telcos_scraping(ani);
"""

def main():
    if not EXCEL_PATH.exists():
        print(f"❌ Error: No se encontró el archivo Excel en {EXCEL_PATH}")
        sys.exit(1)

    print(f"📖 Leyendo {EXCEL_PATH}...")
    df = pd.read_excel(EXCEL_PATH)
    total_filas = len(df)
    print(f"📊 Total filas leídas: {total_filas}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("🛠️ Inicializando esquema DDL en SQLite...")
    cursor.executescript(DDL_SCHEMA)
    conn.commit()

    # Limpieza de datos
    df["Documento"] = pd.to_numeric(df["Documento"], errors="coerce").fillna(0).astype(int)
    # Filtrar DNI 0 o inválidos
    df_validos = df[df["Documento"] > 0].copy()
    df_validos["dni_str"] = df_validos["Documento"].astype(str)
    df_validos["Nombre y Apellido"] = df_validos["Nombre y Apellido"].fillna("").astype(str).str.strip()

    # Agrupar por DNI único
    agrupados = df_validos.groupby("dni_str").agg(
        nombre_excel=("Nombre y Apellido", "first"),
        total_operaciones=("Documento", "count")
    ).reset_index()

    print(f"👥 Total personas / DNIs únicos a insertar: {len(agrupados)}")

    # Inserción de personas
    cursor.execute("SELECT COUNT(*) FROM personas")
    ya_existentes = cursor.fetchone()[0]

    if ya_existentes == 0:
        print("📥 Insertando personas maestras...")
        personas_data = [
            (row["dni_str"], row["nombre_excel"], int(row["total_operaciones"]))
            for _, row in agrupados.iterrows()
        ]
        cursor.executemany("""
            INSERT OR IGNORE INTO personas (dni, nombre_excel, total_operaciones_excel)
            VALUES (?, ?, ?)
        """, personas_data)
        conn.commit()

        print("📥 Insertando operaciones detalle...")
        operaciones_data = [
            (row["dni_str"], row["Nombre y Apellido"])
            for _, row in df_validos.iterrows()
        ]
        cursor.executemany("""
            INSERT INTO operaciones_excel (dni, nombre_apellido)
            VALUES (?, ?)
        """, operaciones_data)
        conn.commit()
    else:
        print(f"ℹ️ La tabla 'personas' ya cuenta con {ya_existentes} registros. Omitiendo reinserción.")

    cursor.execute("SELECT COUNT(*) FROM personas")
    c_personas = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM operaciones_excel")
    c_ops = cursor.fetchone()[0]

    print("=" * 60)
    print("✅ Ingesta inicial completada con éxito.")
    print(f"📍 Base de datos: {DB_PATH}")
    print(f"👤 Personas (DNIs únicos): {c_personas}")
    print(f"📋 Operaciones vinculadas: {c_ops}")
    print("=" * 60)
    conn.close()

if __name__ == "__main__":
    main()
