# -*- coding: utf-8 -*-
"""
Entidades de Dominio - Arquitectura Hexagonal
Modelos puros e independientes de cualquier librería externa, ORM o motor de base de datos.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from core.domain.enums import Prioridad, EstadoRegistro, StatusScraping

@dataclass(frozen=True)
class Linea:
    """Value Object que representa una línea telefónica normalizada en Argentina."""
    ani: str
    dni: Optional[str] = None

    def __post_init__(self):
        clean_ani = "".join(filter(str.isdigit, str(self.ani))).strip()
        object.__setattr__(self, "ani", clean_ani)
        if self.dni is not None:
            clean_dni = "".join(filter(str.isdigit, str(self.dni))).strip()
            object.__setattr__(self, "dni", clean_dni)

    @property
    def es_valida(self) -> bool:
        return len(self.ani) == 10 and self.ani.isdigit()

    @property
    def codigo_area(self) -> str:
        if not self.es_valida:
            return ""
        if self.ani.startswith("11"):
            return "11"
        if self.ani.startswith(("261", "260", "263", "280", "299", "298", "294", "297", "291")):
            return self.ani[:3]
        return self.ani[:4]

    @property
    def numero_local(self) -> str:
        ca = self.codigo_area
        return self.ani[len(ca):] if ca else self.ani


@dataclass
class Titular:
    """Value Object que representa la información de titularidad obtenida."""
    nombre: str = ""
    apellido: str = ""
    razon_social: str = ""
    tipo_documento: str = ""
    nro_documento: str = ""
    tipo_persona: str = ""
    telefono_contacto: str = ""
    email: str = ""
    cuil: str = ""
    edad: Optional[str] = ""
    genero: Optional[str] = ""
    provincia: Optional[str] = ""
    ciudad: Optional[str] = ""
    municipio: Optional[str] = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nombre": self.nombre,
            "apellido": self.apellido,
            "razon_social": self.razon_social,
            "tipo_documento": self.tipo_documento,
            "nro_documento": self.nro_documento,
            "tipo_persona": self.tipo_persona,
            "telefono_contacto": self.telefono_contacto,
            "email": self.email,
            "cuil": self.cuil,
            "edad": self.edad,
            "genero": self.genero,
            "provincia": self.provincia,
            "ciudad": self.ciudad,
            "municipio": self.municipio
        }


@dataclass
class Servicio:
    """Value Object que representa detalles técnicos del servicio extraído."""
    tecnologia: str = ""
    producto: str = ""
    modalidad_factura: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tecnologia": self.tecnologia,
            "producto": self.producto,
            "modalidad_factura": self.modalidad_factura
        }


@dataclass
class ScrapeResult:
    """
    Entidad que representa el resultado normalizado de un scraper.
    Permite encapsular datos bajo su propio namespace para enriquecimiento acumulativo.
    """
    ani: str
    status: StatusScraping
    fuente_scraper: str
    operador: str = ""
    operador_receptor: str = ""
    titular: Optional[Titular] = None
    servicio: Optional[Servicio] = None
    fechas: Dict[str, Any] = field(default_factory=dict)
    detalles: Dict[str, Any] = field(default_factory=dict)
    raw: Any = field(default_factory=dict)
    descripcion: str = ""
    ultima_modificacion: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_namespace_dict(self) -> Dict[str, Any]:
        """Devuelve el diccionario formateado para persistencia en datos_json."""
        ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "status": self.status.value,
            "fuente": self.fuente_scraper,
            "operador": self.operador,
            "operador_receptor": self.operador_receptor,
            "titular": self.titular.to_dict() if self.titular else {},
            "servicio": self.servicio.to_dict() if self.servicio else {},
            "fechas": self.fechas,
            "detalles": self.detalles,
            "raw": self.raw,
            "ultima_modificacion": self.ultima_modificacion or ahora_str
        }
        return {self.fuente_scraper: payload}


@dataclass
class RegistroCola:
    """Entidad que representa un registro reclamado de la cola de procesamiento."""
    id: int
    linea: Linea
    prioridad: Prioridad = Prioridad.P3_RESTO
    prioridad_nombre: str = "P3 (Resto del País)"
    estado: EstadoRegistro = EstadoRegistro.PENDIENTE
    scraper_actual: str = "iris"
    fuente: Optional[str] = None
    datos_existentes: Dict[str, Any] = field(default_factory=dict)
    dni: Optional[str] = None

    def __post_init__(self):
        if self.dni and self.linea and not self.linea.dni:
            object.__setattr__(self, "linea", Linea(ani=self.linea.ani, dni=str(self.dni)))
        elif self.linea and self.linea.dni and not self.dni:
            self.dni = str(self.linea.dni)



class ReglaPipeline:
    """
    Regla de Dominio: Cadena de Responsabilidad con Cortocircuito, Condiciones Dinámicas
    y Ventana Temporal de 7 Días (TTL).
    Determina la siguiente etapa de una línea telefónica en el pipeline y la elegibilidad
    para el modelo de piscina autónoma distribuida multi-PC.
    """
    TELCOS = ("claro", "personal", "movistar")
    REQUIEREN_DNI = ("claro", "datuar", "cuitonline")
    CADENA_DEFAULT = ["iris", "claro", "personal", "movistar", "datuar", "cuitonline"]

    @classmethod
    def es_reciente(cls, fecha_str: Optional[str], max_dias: int = 7) -> bool:
        """
        Determina si una marca temporal está dentro de la ventana de validez de N días (default 7).
        Admite formatos 'YYYY-MM-DD HH:MM:SS' y variantes ISO.
        """
        if not fecha_str or not isinstance(fecha_str, str):
            return False
        try:
            limpia = fecha_str.replace("T", " ").strip()
            if len(limpia) >= 19:
                limpia = limpia[:19]
                dt = datetime.strptime(limpia, "%Y-%m-%d %H:%M:%S")
            elif len(limpia) == 10:
                dt = datetime.strptime(limpia, "%Y-%m-%d")
            else:
                dt = datetime.fromisoformat(fecha_str)
            delta = datetime.now() - dt
            return 0 <= delta.total_seconds() <= (max_dias * 86400)
        except Exception:
            return False

    @classmethod
    def extraer_dni(cls, datos_json: Optional[Dict[str, Any]], dni_directo: Optional[str] = None) -> Optional[str]:
        """Extrae el DNI disponible desde la línea o desde cualquier namespace de datos_json."""
        if dni_directo:
            clean = "".join(filter(str.isdigit, str(dni_directo))).strip()
            if clean:
                return clean
        if not datos_json or not isinstance(datos_json, dict):
            return None

        # 1. Namespace iris
        iris = datos_json.get("iris", {})
        if isinstance(iris, dict):
            titular = iris.get("titular", {})
            if isinstance(titular, dict):
                doc = titular.get("nro_documento") or titular.get("documento") or titular.get("dni")
                if doc:
                    return str(doc).strip()
            detalles = iris.get("detalles", {})
            if isinstance(detalles, dict) and detalles.get("dni"):
                return str(detalles["dni"]).strip()

        # 2. Namespace datuar
        datuar = datos_json.get("datuar", {})
        if isinstance(datuar, dict):
            doc = datuar.get("dni") or datuar.get("detalles", {}).get("dni")
            if doc:
                return str(doc).strip()

        # 3. Namespace cuitonline
        cuit = datos_json.get("cuitonline", {})
        if isinstance(cuit, dict):
            doc = cuit.get("dni") or cuit.get("detalles", {}).get("dni")
            if doc:
                return str(doc).strip()

        # 4. Raíz
        if datos_json.get("dni"):
            return str(datos_json["dni"]).strip()

        return None

    @classmethod
    def es_elegible_para_scraper(
        cls,
        scraper_nombre: str,
        ani: str,
        dni: Optional[str] = None,
        datos_json: Optional[Dict[str, Any]] = None,
        dias_validez: int = 7,
        solo_sin_coincidencia: bool = False
    ) -> tuple[bool, str]:
        """
        Determina si un registro es apto para ser procesado por un scraper específico
        según las reglas de precedencia, dependencias de datos, ventana temporal de 7 días
        y la bandera opcional solo_sin_coincidencia.

        Reglas:
        1. IRIS: Solo necesita ANI. No ejecutado en los últimos 7 días.
        2. CLARO: Exige DNI disponible. Sin coincidencia previa en Personal o Movistar en los últimos 7 días.
           No ejecutado en los últimos 7 días.
        3. PERSONAL: Sin coincidencia previa en Claro o Movistar en los últimos 7 días.
           No ejecutado en los últimos 7 días.
           Condición de entrada: IRIS pasó sin coincidencia, O Claro pasó en los últimos 7 días sin coincidencia.
           (No requiere DNI. No requiere que Claro haya pasado si IRIS ya descartó el port-out.)
        4. MOVISTAR: 'De nadie' (no requiere titular ni DNI, solo ANI).
           Sin coincidencia previa en Claro o Personal en los últimos 7 días.
           No ejecutado en los últimos 7 días.
           EXIGE haber pasado por Personal en los últimos 7 días.
        5. DATUAR: Exige DNI disponible. No ejecutado en los últimos 7 días.
        6. CUITONLINE: Exige DNI disponible. No ejecutado en los últimos 7 días.
           EXIGE haber pasado por Datuar en los últimos 7 días.
        7. FLAG solo_sin_coincidencia: Si es True en telcos, descarta registros con coincidencia en cualquiera
           de las 3 telcos (o status raíz), independientemente de la fecha.
        """
        nombre = scraper_nombre.lower().strip()
        if "iris" in nombre:
            nombre = "iris"
        elif "claro" in nombre:
            nombre = "claro"
        elif "personal" in nombre:
            nombre = "personal"
        elif "movistar" in nombre:
            nombre = "movistar"
        elif "cuit" in nombre:
            nombre = "cuitonline"
        elif "datuar" in nombre:
            nombre = "datuar"

        # 0. Validación de formato de ANI (debe ser numérico de exactamente 10 dígitos)
        clean_ani = "".join(filter(str.isdigit, str(ani or ""))).strip()
        if len(clean_ani) != 10:
            return False, f"ANI telefónico inválido: '{ani}' (debe contener exactamente 10 dígitos)"

        datos = datos_json if isinstance(datos_json, dict) else {}
        dni_activo = cls.extraer_dni(datos, dni)

        # Bandera de auditoría inicial: Si solo_sin_coincidencia está activa en telcos,
        # exige que NINGUNA de las 3 compañías (Claro, Personal, Movistar) posea coincidencia previa,
        # independientemente de la fecha o ventana de días, incluyendo el status raíz legado.
        if solo_sin_coincidencia and nombre in cls.TELCOS:
            for t in cls.TELCOS:
                t_data = datos.get(t, {})
                if isinstance(t_data, dict) and t_data.get("status") == StatusScraping.COINCIDENCIA.value:
                    return False, f"Flag solo_sin_coincidencia: {t} posee coincidencia previa"
            if datos.get("status") == StatusScraping.COINCIDENCIA.value:
                return False, "Flag solo_sin_coincidencia: posee coincidencia previa en raíz"

        # Para motores que NO son de compañía (iris, datuar, cuitonline):
        # NO aplica regla de 7 días. Solo nutrir registros que aún NO tengan datos del motor.
        if nombre not in cls.TELCOS:
            sc_data = datos.get(nombre, {})
            if isinstance(sc_data, dict) and sc_data.get("status"):
                return False, f"{nombre} ya posee datos enriquecidos previamente (no requiere re-consulta)"

        # Para motores de compañía (claro, personal, movistar):
        # Aplica regla de 7 días: no consultar de más si ya corrió en los últimos 7 días.
        if nombre in cls.TELCOS:
            sc_data = datos.get(nombre, {})
            if isinstance(sc_data, dict):
                ts = sc_data.get("ultima_modificacion")
                if cls.es_reciente(ts, max_dias=dias_validez):
                    return False, f"{nombre} ya ejecutado recientemente dentro de la ventana de {dias_validez} días ({ts})"

            # Exclusividad Telco: Si Claro, Personal o Movistar dieron coincidencia en los últimos 7 días
            for telco in cls.TELCOS:
                if telco == nombre:
                    continue
                t_data = datos.get(telco, {})
                if isinstance(t_data, dict) and t_data.get("status") == StatusScraping.COINCIDENCIA.value:
                    t_ts = t_data.get("ultima_modificacion")
                    if cls.es_reciente(t_ts, max_dias=dias_validez):
                        return False, f"Exclusividad telco: {telco} dio coincidencia el {t_ts}"

        # Reglas específicas por motor
        if nombre == "iris":
            return True, "Apto para IRIS"

        if nombre == "claro":
            if not dni_activo:
                return False, "Claro requiere DNI disponible"
            return True, "Apto para Claro"

        if nombre == "personal":
            # Si la línea NO tiene DNI, entra libre directo sin esperar a Claro (Claro exige DNI)
            if not dni_activo:
                return True, "Apto para Personal (línea sin DNI, entra libre directo)"
            # Si la línea TIENE DNI, exige haber pasado por Claro en los últimos 7 días
            c_data = datos.get("claro", {})
            c_ts = c_data.get("ultima_modificacion") if isinstance(c_data, dict) else None
            if not cls.es_reciente(c_ts, max_dias=dias_validez):
                return False, "Personal con DNI exige haber pasado por Claro en los últimos 7 días"
            return True, "Apto para Personal"

        if nombre == "movistar":
            # Movistar solo necesita ANI ("de nadie", no requiere DNI), pero exige haber pasado por Personal en últimos 7 días
            p_data = datos.get("personal", {})
            p_ts = p_data.get("ultima_modificacion") if isinstance(p_data, dict) else None
            if not cls.es_reciente(p_ts, max_dias=dias_validez):
                return False, "Movistar exige haber pasado por Personal en los últimos 7 días"
            return True, "Apto para Movistar"

        if nombre == "datuar":
            if not dni_activo:
                return False, "Datuar requiere DNI disponible"
            return True, "Apto para Datuar"

        if nombre == "cuitonline":
            if not dni_activo:
                return False, "CuitOnline requiere DNI disponible"
            # CuitOnline NO tiene regla de 7 días: solo exige haber pasado previamente por Datuar
            d_data = datos.get("datuar", {})
            if not d_data or not isinstance(d_data, dict) or not d_data.get("status"):
                return False, "CuitOnline exige haber pasado previamente por Datuar"
            return True, "Apto para CuitOnline"

        return False, f"Scraper desconocido: {nombre}"

    @classmethod
    def resolver_siguiente_etapa(
        cls, 
        scraper_actual: str, 
        resultado: ScrapeResult,
        cadena: Optional[List[str]] = None,
        dni_disponible: Optional[bool] = None,
        fuentes_previas: Optional[List[str]] = None,
        coincidencia_telco_previa: bool = False,
        datos_previos: Optional[Dict[str, Any]] = None,
        dias_validez: int = 7
    ) -> tuple[str, str]:
        """
        Calcula (siguiente_scraper, siguiente_estado) aplicando las reglas condicionales y temporales:
        
        Reglas Forzadas de Dominio:
        1. Si una telco (Claro, Personal, Movistar) da COINCIDENCIA:
           - No se consultan las demás telcos (cortocircuito telco).
           - Si en la cadena aún quedan scrapers de identidad pendientes (Datuar, CuitOnline) 
             y se cuenta con DNI previo, la línea continúa hacia ellos.
           - Si no quedan scrapers pendientes (o no hay DNI), pasa a ('finalizado', 'completado').
        2. Requisito estricto de DNI (Claro, Datuar, CuitOnline):
           - Ni Personal ni Movistar generan DNI. El DNI solo proviene del origen, IRIS, Datuar o CuitOnline.
           - Si la línea NO tiene DNI:
             * Se saltean automáticamente Claro, Datuar y CuitOnline.
             * Avanza directamente hacia la siguiente telco que no requiera DNI (Personal o Movistar).
        3. Precedencia y Temporalidad de 7 Días (TTL):
           - Personal con DNI exige haber pasado por Claro en los últimos 7 días.
           - Movistar ('de nadie') solo necesita ANI y exige haber pasado por Personal en los últimos 7 días.
           - CuitOnline NUNCA se ejecuta si no pasó previamente por Datuar en los últimos 7 días.
        4. Fin de cadena:
           - Si no quedan scrapers aplicables -> ('finalizado', 'completado'|'no_coincidencia').
        """
        # Si el ANI es inválido, abortar la cadena y finalizar como error
        clean_ani = "".join(filter(str.isdigit, str(resultado.ani or ""))).strip()
        if len(clean_ani) != 10:
            return ("finalizado", EstadoRegistro.ERROR.value)

        pipeline = list(cadena or cls.CADENA_DEFAULT)
        datos = dict(datos_previos) if isinstance(datos_previos, dict) else {}
        datos.update(resultado.to_namespace_dict())
        sc_canonico = scraper_actual.lower()
        if "iris" in sc_canonico:
            sc_canonico = "iris"
        datos[sc_canonico] = {
            "status": resultado.status.value,
            "ultima_modificacion": resultado.ultima_modificacion or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # Determinar si hay DNI activo
        tiene_dni = bool(
            dni_disponible
            or cls.extraer_dni(datos)
            or (resultado.titular and resultado.titular.nro_documento)
            or (isinstance(resultado.detalles, dict) and (
                resultado.detalles.get("titular", {}).get("nro_documento") 
                or resultado.detalles.get("dni")
            ))
        )

        # Determinar si ya hubo coincidencia en alguna telco en los últimos 7 días
        telco_coincidio = (
            coincidencia_telco_previa 
            or (resultado.status == StatusScraping.COINCIDENCIA and scraper_actual in cls.TELCOS)
        )
        if not telco_coincidio and datos:
            for t in cls.TELCOS:
                t_info = datos.get(t, {})
                if isinstance(t_info, dict) and t_info.get("status") == StatusScraping.COINCIDENCIA.value:
                    if cls.es_reciente(t_info.get("ultima_modificacion"), max_dias=dias_validez):
                        telco_coincidio = True
                        break

        # Historial de scrapers visitados
        pasados = set(fuentes_previas or [])
        pasados.add(scraper_actual)
        pasados.add(sc_canonico)

        def paso_reciente(sc_name: str) -> bool:
            if sc_name == scraper_actual or sc_name == sc_canonico:
                return True
            if sc_name not in pasados:
                return False
            sc_info = datos.get(sc_name, {})
            if isinstance(sc_info, dict):
                ts = sc_info.get("ultima_modificacion")
                if ts:
                    return cls.es_reciente(ts, max_dias=dias_validez)
            return True

        # Buscar la siguiente posta válida en la cadena
        idx = pipeline.index(scraper_actual) if scraper_actual in pipeline else -1
        if idx == -1 and sc_canonico in pipeline:
            idx = pipeline.index(sc_canonico)

        for candidato in pipeline[idx + 1:]:
            # Si ya encontramos la telco ganadora, saltear cualquier otra telco restante
            if telco_coincidio and candidato in cls.TELCOS:
                continue

            # Si el candidato requiere DNI obligatoriamente y NO tenemos DNI, saltearlo
            if candidato in cls.REQUIEREN_DNI and not tiene_dni:
                continue

            # Para motores que NO son de compañía (datuar, cuitonline): solo nutrir si no tienen datos previos
            if candidato not in cls.TELCOS:
                sc_info = datos.get(candidato, {})
                if isinstance(sc_info, dict) and sc_info.get("status"):
                    continue

            # Precedencias temporales de 7 días (exclusivas para compañías: Claro, Personal, Movistar):
            # 1. Personal con DNI requiere haber pasado por Claro en los últimos 7 días (si Claro forma parte de la cadena).
            #    Si la línea no tiene DNI, Claro no aplica y Personal entra libre directo.
            if candidato == "personal" and tiene_dni and "claro" in pipeline and not paso_reciente("claro"):
                continue

            # 2. Movistar ('de nadie', solo ANI) requiere haber pasado por Personal en los últimos 7 días
            if candidato == "movistar" and not paso_reciente("personal"):
                continue

            # Dependencia de Identidad (NO aplica regla de 7 días):
            # 3. CuitOnline requiere haber pasado por Datuar previamente
            paso_datuar = ("datuar" in pasados or (isinstance(datos.get("datuar"), dict) and datos["datuar"].get("status")))
            if candidato == "cuitonline" and not paso_datuar:
                continue

            # Candidato válido encontrado
            return (candidato, EstadoRegistro.PENDIENTE.value)

        # Si se agotó la cadena sin más candidatos aplicables:
        estado_final = (
            EstadoRegistro.COMPLETADO.value 
            if (telco_coincidio or resultado.status == StatusScraping.COINCIDENCIA)
            else EstadoRegistro.NO_COINCIDENCIA.value
        )
        return ("finalizado", estado_final)


@dataclass(frozen=True)
class DatosOrigenEnacom:
    """
    Value Object inmutable que representa la información regulatoria y técnica oficial de ENACOM
    para el bloque de numeración asignado a una línea telefónica.
    """
    operador_origen: str
    operador_oficial: str
    grupo_economico: str
    tipo_linea: str
    es_celular: bool
    soporta_whatsapp: bool
    servicio_oficial: str
    modalidad: str
    modalidad_descripcion: str
    codigo_area: str
    bloque: str
    prefijo_completo: str
    capacidad_bloque: int
    rango_asignado: str
    localidad_origen: str
    provincia_origen: str
    macro_region: str
    resolucion: str
    organismo_emisor: str
    fecha_asignacion: str
    ano_asignacion: Optional[int] = None
    ani: str = ""
    numero_local: str = ""
    numero_abonado: str = ""
    formatos: Dict[str, Optional[str]] = field(default_factory=dict)
    ultima_modificacion: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def to_dict(self) -> Dict[str, Any]:
        """Devuelve el payload normalizado para persistencia dentro de datos_json['enacom']."""
        ahora_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "operador_origen": self.operador_origen,
            "operador_oficial": self.operador_oficial,
            "grupo_economico": self.grupo_economico,
            "tipo_linea": self.tipo_linea,
            "es_celular": self.es_celular,
            "soporta_whatsapp": self.soporta_whatsapp,
            "servicio_oficial": self.servicio_oficial,
            "modalidad": self.modalidad,
            "modalidad_descripcion": self.modalidad_descripcion,
            "codigo_area": self.codigo_area,
            "bloque": self.bloque,
            "prefijo_completo": self.prefijo_completo,
            "capacidad_bloque": self.capacidad_bloque,
            "rango_asignado": self.rango_asignado,
            "localidad_origen": self.localidad_origen,
            "provincia_origen": self.provincia_origen,
            "macro_region": self.macro_region,
            "resolucion": self.resolucion,
            "organismo_emisor": self.organismo_emisor,
            "fecha_asignacion": self.fecha_asignacion,
            "ano_asignacion": self.ano_asignacion,
            "ani": self.ani,
            "numero_local": self.numero_local,
            "numero_abonado": self.numero_abonado,
            "formatos": self.formatos,
            "ultima_modificacion": self.ultima_modificacion or ahora_str
        }

