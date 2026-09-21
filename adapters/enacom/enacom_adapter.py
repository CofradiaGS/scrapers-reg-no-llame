# -*- coding: utf-8 -*-
"""
Adaptador Secundario (Driven Adapter): Asignador de Bloques ENACOM
Implementa el puerto IOperatorLookupPort para resolver con precisión matemática del 100%
la procedencia regulatoria, geográfica y de operador original de cualquier número de Argentina
a partir del Plan Fundamental de Numeración de ENACOM.
"""
import os
import re
import time
import pickle
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from core.ports.operator_lookup_port import IOperatorLookupPort
from core.domain.entities import DatosOrigenEnacom

logger = logging.getLogger("EnacomBlockAdapter")

# Mapeo oficial de provincias para los 300 indicativos telefónicos de Argentina
PROVINCIAS_POR_INDICATIVO: Dict[str, str] = {
    # AMBA / CABA y GBA
    "11": "Buenos Aires / CABA",
    # Buenos Aires Interior
    "220": "Buenos Aires", "221": "Buenos Aires", "223": "Buenos Aires", "2271": "Buenos Aires", "2272": "Buenos Aires",
    "2291": "Buenos Aires", "2262": "Buenos Aires", "230": "Buenos Aires", "2923": "Buenos Aires", "2932": "Buenos Aires",
    "2254": "Buenos Aires", "2396": "Buenos Aires", "2477": "Buenos Aires", "2297": "Buenos Aires", "2927": "Buenos Aires",
    "2324": "Buenos Aires", "237": "Buenos Aires", "2983": "Buenos Aires", "2266": "Buenos Aires", "2284": "Buenos Aires",
    "2314": "Buenos Aires", "2345": "Buenos Aires", "236": "Buenos Aires", "2473": "Buenos Aires", "2474": "Buenos Aires",
    "2475": "Buenos Aires", "291": "Buenos Aires", "2920": "Buenos Aires", "2926": "Buenos Aires", "2936": "Buenos Aires",
    "2224": "Buenos Aires", "2225": "Buenos Aires", "2226": "Buenos Aires", "2227": "Buenos Aires", "2241": "Buenos Aires",
    "2242": "Buenos Aires", "2243": "Buenos Aires", "2244": "Buenos Aires", "2245": "Buenos Aires", "2252": "Buenos Aires",
    "2255": "Buenos Aires", "2257": "Buenos Aires", "2261": "Buenos Aires", "2264": "Buenos Aires", "2265": "Buenos Aires",
    "2267": "Buenos Aires", "2268": "Buenos Aires", "2281": "Buenos Aires", "2285": "Buenos Aires", "2286": "Buenos Aires",
    "2316": "Buenos Aires", "2317": "Buenos Aires", "2323": "Buenos Aires", "2325": "Buenos Aires", "2326": "Buenos Aires",
    "2342": "Buenos Aires", "2344": "Buenos Aires", "2346": "Buenos Aires", "2352": "Buenos Aires", "2353": "Buenos Aires",
    "2354": "Buenos Aires", "2355": "Buenos Aires", "2356": "Buenos Aires", "2357": "Buenos Aires", "2358": "Buenos Aires",
    "2392": "Buenos Aires", "2393": "Buenos Aires", "2394": "Buenos Aires", "2395": "Buenos Aires", "2478": "Buenos Aires",
    "2921": "Buenos Aires", "2922": "Buenos Aires", "2924": "Buenos Aires", "2925": "Buenos Aires", "2928": "Buenos Aires",
    "2929": "Buenos Aires", "2933": "Buenos Aires", "2934": "Buenos Aires", "2935": "Buenos Aires",
    # Córdoba
    "351": "Córdoba", "353": "Córdoba", "358": "Córdoba", "3564": "Córdoba", "3541": "Córdoba", "3543": "Córdoba",
    "3544": "Córdoba", "3546": "Córdoba", "3547": "Córdoba", "3548": "Córdoba", "3549": "Córdoba", "3521": "Córdoba",
    "3522": "Córdoba", "3524": "Córdoba", "3525": "Córdoba", "3532": "Córdoba", "3533": "Córdoba", "3537": "Córdoba",
    "3562": "Córdoba", "3563": "Córdoba", "3571": "Córdoba", "3572": "Córdoba", "3573": "Córdoba", "3574": "Córdoba",
    "3575": "Córdoba", "3576": "Córdoba", "3582": "Córdoba", "3583": "Córdoba", "3584": "Córdoba", "3585": "Córdoba",
    # Santa Fe
    "341": "Santa Fe", "342": "Santa Fe", "3492": "Santa Fe", "3482": "Santa Fe", "3462": "Santa Fe", "3401": "Santa Fe",
    "3402": "Santa Fe", "3404": "Santa Fe", "3405": "Santa Fe", "3406": "Santa Fe", "3407": "Santa Fe", "3408": "Santa Fe",
    "3409": "Santa Fe", "3464": "Santa Fe", "3465": "Santa Fe", "3466": "Santa Fe", "3471": "Santa Fe", "3472": "Santa Fe",
    "3476": "Santa Fe", "3491": "Santa Fe", "3493": "Santa Fe", "3496": "Santa Fe", "3497": "Santa Fe", "3498": "Santa Fe",
    # Mendoza
    "261": "Mendoza", "260": "Mendoza", "2622": "Mendoza", "2625": "Mendoza", "263": "Mendoza", "2624": "Mendoza",
    # San Juan
    "264": "San Juan", "2644": "San Juan", "2647": "San Juan", "2648": "San Juan",
    # San Luis
    "266": "San Luis", "2657": "San Luis", "2651": "San Luis", "2652": "San Luis", "2655": "San Luis", "2656": "San Luis",
    # Entre Ríos
    "343": "Entre Ríos", "345": "Entre Ríos", "3442": "Entre Ríos", "3446": "Entre Ríos", "3447": "Entre Ríos",
    "3435": "Entre Ríos", "3436": "Entre Ríos", "3437": "Entre Ríos", "3438": "Entre Ríos", "3444": "Entre Ríos", "3445": "Entre Ríos",
    # Tucumán
    "381": "Tucumán", "3865": "Tucumán", "3863": "Tucumán", "3867": "Tucumán", "3869": "Tucumán",
    # Salta
    "387": "Salta", "3878": "Salta", "3876": "Salta", "3877": "Salta", "3873": "Salta", "3875": "Salta",
    # Jujuy
    "388": "Jujuy", "3888": "Jujuy", "3886": "Jujuy", "3887": "Jujuy", "3885": "Jujuy",
    # Chaco
    "362": "Chaco", "364": "Chaco", "3731": "Chaco", "3732": "Chaco", "3734": "Chaco", "3735": "Chaco",
    # Corrientes
    "379": "Corrientes", "3777": "Corrientes", "3772": "Corrientes", "3773": "Corrientes", "3774": "Corrientes", "3775": "Corrientes",
    # Misiones
    "376": "Misiones", "3755": "Misiones", "3751": "Misiones", "3756": "Misiones", "3757": "Misiones", "3758": "Misiones", "3754": "Misiones",
    # Santiago del Estero
    "385": "Santiago del Estero", "3854": "Santiago del Estero", "3855": "Santiago del Estero", "3856": "Santiago del Estero", "3857": "Santiago del Estero", "3858": "Santiago del Estero",
    # Catamarca
    "383": "Catamarca", "3835": "Catamarca", "3837": "Catamarca",
    # La Rioja
    "380": "La Rioja", "3825": "La Rioja", "3826": "La Rioja", "3827": "La Rioja",
    # Formosa
    "370": "Formosa", "3718": "Formosa", "3715": "Formosa", "3716": "Formosa", "3711": "Formosa",
    # Neuquén
    "299": "Neuquén", "2972": "Neuquén", "2942": "Neuquén", "2948": "Neuquén",
    # Río Negro
    "294": "Río Negro", "298": "Río Negro", "2931": "Río Negro", "2934": "Río Negro", "2944": "Río Negro",
    # Chubut
    "297": "Chubut", "2965": "Chubut", "2945": "Chubut",
    # Santa Cruz
    "2966": "Santa Cruz", "2962": "Santa Cruz", "2963": "Santa Cruz",
    # Tierra del Fuego
    "2964": "Tierra del Fuego", "2901": "Tierra del Fuego",
    # La Pampa
    "2954": "La Pampa", "2302": "La Pampa", "2331": "La Pampa", "2333": "La Pampa", "2334": "La Pampa", "2336": "La Pampa", "2337": "La Pampa", "2338": "La Pampa"
}

class EnacomBlockAdapter(IOperatorLookupPort):
    """
    Adaptador de alto rendimiento para el catálogo oficial de numeración ENACOM.
    Utiliza un mapa de prefijos en memoria serializado en binario (.dat) con carga sub-100ms.
    """

    def __init__(self, data_path: Optional[str] = None):
        self._data_path = self._resolve_data_path(data_path)
        self._lookup: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _resolve_data_path(self, custom_path: Optional[str]) -> str:
        if custom_path and os.path.exists(custom_path):
            return custom_path
        
        env_path = os.getenv("ENACOM_DATA_PATH")
        if env_path and os.path.exists(env_path):
            return env_path

        # Rutas estándar en el repositorio
        candidatos = [
            os.path.abspath("data/enacom/enacom_lookup.dat"),
            os.path.abspath("scratch/enacom_lookup.dat"),
            os.path.abspath("data/enacom/enacom_asignaciones.xls"),
            os.path.abspath("scratch/enacom_asignaciones.xls")
        ]
        for c in candidatos:
            if os.path.exists(c):
                return c
        return candidatos[0]

    def _normalize_operator(self, raw_op: str) -> Tuple[str, str]:
        u = str(raw_op).upper()
        if "TELECOM" in u:
            return "Personal", "Grupo Telecom (Personal / Flow)"
        if "TELEFONICA" in u or "COMPAÑIA DE RADIOCOMUNICACIONES" in u or "CRM" in u:
            return "Movistar", "Telefónica Hispanoamérica (Movistar)"
        if "AMX" in u or "CLARO" in u or "TELMEX" in u:
            return "Claro", "América Móvil (Claro)"
        if "TELECENTRO" in u:
            return "Telecentro", "Grupo Telecentro"
        return raw_op.strip(), "Cooperativa / Prestador Regional"

    def _resolve_province(self, indicativo: str, localidad: str) -> str:
        match = re.search(r'\(PROV\.\s*([^)]+)\)', localidad)
        if match:
            return match.group(1).strip().title()
        return PROVINCIAS_POR_INDICATIVO.get(indicativo, "Argentina")

    def _resolve_macro_region(self, indicativo: str) -> str:
        first_digit = str(indicativo)[0]
        if first_digit == "1":
            return "Región Metropolitana / AMBA"
        elif first_digit == "2":
            return "Región Sur / Patagónica / Bs. As. Interior"
        elif first_digit == "3":
            return "Región Centro / Norte / Litoral"
        return "Región Nacional"

    def _resolve_organismo(self, resolucion: str) -> str:
        u = str(resolucion).upper()
        if u.startswith("SC"):
            return "Secretaría de Comunicaciones"
        if u.startswith("CNC"):
            return "Comisión Nacional de Comunicaciones"
        if u.startswith("ENACOM"):
            return "Ente Nacional de Comunicaciones"
        if u.startswith("AFTIC"):
            return "Autoridad Federal de Tecnologías de la Información"
        if "DNAYRT" in u:
            return "Dirección Nacional de Autorizaciones y Registros TIC"
        return "Autoridad Regulatoria Nacional"

    def _compile_from_excel(self, excel_path: str, save_dat_path: Optional[str] = None):
        """Compila el dataset del Excel oficial a la estructura de consulta ultraveloz."""
        import pandas as pd
        t0 = time.time()
        logger.info(f"Compilando catálogo de numeración ENACOM desde Excel: {excel_path}")
        df = pd.read_excel(excel_path)
        df['INDICATIVO'] = df['INDICATIVO'].astype(str).str.strip().str.replace('.0', '', regex=False)
        df['BLOQUE'] = df['BLOQUE'].astype(str).str.strip().str.replace('.0', '', regex=False)

        compiled: Dict[str, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            indicativo = str(row['INDICATIVO']).strip()
            bloque = str(row['BLOQUE']).strip()
            prefix = indicativo + bloque

            raw_op = str(row.get('OPERADOR', '')).strip()
            norm_op, grupo = self._normalize_operator(raw_op)
            servicio = str(row.get('SERVICIO', '')).strip()
            modalidad = str(row.get('MODALIDAD', '')).strip()
            localidad = str(row.get('LOCALIDAD', '')).strip()
            resolucion = str(row.get('RESOLUCION', '')).strip()
            fecha_str = str(row.get('FECHA', ''))[:10]

            is_mobile = any(m in servicio.upper() for m in ['STM', 'PCS', 'SRMC', 'MOVIL', 'MÓVIL']) or modalidad in ['CPP', 'MPP']
            
            if modalidad == "CPP":
                mod_desc = "Calling Party Pays (El que llama paga)"
            elif modalidad == "MPP":
                mod_desc = "Mobile Party Pays (El móvil paga)"
            elif "BAS" in modalidad:
                mod_desc = "Servicio Básico Telefónico (Fija / Abono Tradicional)"
            else:
                mod_desc = modalidad

            provincia = self._resolve_province(indicativo, localidad)
            macro_reg = self._resolve_macro_region(indicativo)
            organismo = self._resolve_organismo(resolucion)

            prefix_len = len(prefix)
            if prefix_len == 6:
                capacidad = 10000
                rango = f"{prefix}0000 - {prefix}9999"
            elif prefix_len == 7:
                capacidad = 1000
                rango = f"{prefix}000 - {prefix}999"
            elif prefix_len == 8:
                capacidad = 100
                rango = f"{prefix}00 - {prefix}99"
            else:
                capacidad = 10000
                rango = f"{prefix}..."

            ano_asig = int(fecha_str[:4]) if (fecha_str and fecha_str[:4].isdigit()) else None

            compiled[prefix] = {
                "operador_origen": norm_op,
                "operador_oficial": raw_op,
                "grupo_economico": grupo,
                "tipo_linea": "Móvil / Celular" if is_mobile else "Fija / Red Básica",
                "es_celular": is_mobile,
                "soporta_whatsapp": is_mobile,
                "servicio_oficial": servicio,
                "modalidad": modalidad,
                "modalidad_descripcion": mod_desc,
                "codigo_area": indicativo,
                "bloque": bloque,
                "prefijo_completo": prefix,
                "capacidad_bloque": capacidad,
                "rango_asignado": rango,
                "localidad_origen": localidad,
                "provincia_origen": provincia,
                "macro_region": macro_reg,
                "resolucion": resolucion,
                "organismo_emisor": organismo,
                "fecha_asignacion": fecha_str,
                "ano_asignacion": ano_asig
            }

        self._lookup = compiled
        elapsed = round(time.time() - t0, 3)
        logger.info(f"Compilación finalizada: {len(self._lookup)} bloques procesados ({elapsed}s)")

        # Persistir a archivo .dat para cargas instantáneas subsecuentes
        dat_dest = save_dat_path or os.path.abspath("data/enacom/enacom_lookup.dat")
        os.makedirs(os.path.dirname(dat_dest), exist_ok=True)
        try:
            with open(dat_dest, "wb") as f:
                pickle.dump(compiled, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info(f"Caché binaria generada exitosamente en {dat_dest}")
        except Exception as e:
            logger.warning(f"No se pudo guardar la caché binaria .dat: {e}")

    def _load(self):
        t0 = time.time()
        if self._data_path.endswith(".dat") and os.path.exists(self._data_path):
            try:
                with open(self._data_path, "rb") as f:
                    self._lookup = pickle.load(f)
                elapsed = round(time.time() - t0, 3)
                logger.info(f"EnacomBlockAdapter listo: {len(self._lookup):,} bloques cargados desde binario ({elapsed}s)")
                return
            except Exception as e:
                logger.error(f"Error cargando archivo binario {self._data_path}: {e}. Intentando Excel...")

        # Si el .dat no existe o falló, buscar el Excel oficial
        excel_candidato = self._data_path.replace(".dat", ".xls")
        if not os.path.exists(excel_candidato):
            excel_candidato = os.path.abspath("data/enacom/enacom_asignaciones.xls")
        if not os.path.exists(excel_candidato):
            excel_candidato = os.path.abspath("scratch/enacom_asignaciones.xls")

        if os.path.exists(excel_candidato):
            self._compile_from_excel(excel_candidato)
        else:
            logger.error("No se encontró ningún archivo de datos de ENACOM (.dat ni .xls).")

    def esta_listo(self) -> bool:
        return bool(self._lookup)

    def obtener_total_bloques(self) -> int:
        return len(self._lookup)

    def consultar_bloque(self, ani: str) -> Optional[DatosOrigenEnacom]:
        res_dict = self.consultar_bloque_dict(ani)
        if not res_dict:
            return None
        return DatosOrigenEnacom(**res_dict)

    def consultar_bloque_dict(self, ani: str) -> Optional[Dict[str, Any]]:
        clean_ani = "".join(filter(str.isdigit, str(ani))).strip()
        if clean_ani.startswith("549") and len(clean_ani) == 13:
            clean_ani = clean_ani[3:]
        elif clean_ani.startswith("54") and len(clean_ani) == 12:
            clean_ani = clean_ani[2:]
        elif clean_ani.startswith("9") and len(clean_ani) == 11:
            clean_ani = clean_ani[1:]
        elif clean_ani.startswith("0") and len(clean_ani) == 11:
            clean_ani = clean_ani[1:]

        if len(clean_ani) != 10 or not clean_ani.isdigit():
            return None

        # Búsqueda por prefijo descendente (8, 7, 6 dígitos)
        for prefix_len in (8, 7, 6):
            pref = clean_ani[:prefix_len]
            base_data = self._lookup.get(pref)
            if base_data:
                res = dict(base_data)
                ind = res["codigo_area"]
                numero_local = clean_ani[len(ind):]
                numero_abonado = clean_ani[prefix_len:]

                res["ani"] = clean_ani
                res["numero_local"] = numero_local
                res["numero_abonado"] = numero_abonado

                # Formatos de marcación normalizados
                if res["es_celular"]:
                    res["formatos"] = {
                        "e164": f"+549{clean_ani}",
                        "whatsapp": f"549{clean_ani}",
                        "nacional_celular": f"0{ind} 15-{numero_local}"
                    }
                else:
                    res["formatos"] = {
                        "e164": f"+54{clean_ani}",
                        "whatsapp": None,
                        "nacional_fijo": f"0{ind} {numero_local}"
                    }
                res["ultima_modificacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return res
        return None
