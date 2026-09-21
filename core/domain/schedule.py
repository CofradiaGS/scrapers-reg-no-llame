# -*- coding: utf-8 -*-
"""
Dominio: Política de Horario Comercial
Define las reglas de negocio para la ventana operativa de scrapers corporativos (IRIS / Movistar).
Aislamiento estricto de dominio: solo librerías estándar de Python (datetime, zoneinfo, typing).
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional, Dict, Any
import zoneinfo

@dataclass(frozen=True)
class HorarioVentana:
    """Representa una ventana horaria en un día específico."""
    inicio: time
    fin: time

    def contiene(self, t: time) -> bool:
        return self.inicio <= t < self.fin


class PoliticaHorarioComercial:
    """
    Política de dominio que gobierna cuándo está habilitado realizar consultas
    a portales comerciales de empleados (IRIS Movistar Argentina).
    
    Regla operativa:
    - Lunes a Viernes: 08:00 a 21:00 (UTC-3)
    - Sábados: 08:00 a 13:00 (UTC-3)
    - Domingos: Inactivo (00:00 a 23:59)
    """

    def __init__(
        self,
        activo: bool = True,
        hora_inicio_lv: str = "08:00",
        hora_fin_lv: str = "21:00",
        hora_inicio_sab: str = "08:00",
        hora_fin_sab: str = "13:00",
        timezone_name: str = "America/Argentina/Buenos_Aires"
    ):
        self.activo = activo
        self.timezone_name = timezone_name
        self.tz = self._resolver_timezone(timezone_name)

        h_ini_lv, m_ini_lv = map(int, hora_inicio_lv.split(":"))
        h_fin_lv, m_fin_lv = map(int, hora_fin_lv.split(":"))
        self.ventana_lv = HorarioVentana(inicio=time(h_ini_lv, m_ini_lv), fin=time(h_fin_lv, m_fin_lv))

        h_ini_sab, m_ini_sab = map(int, hora_inicio_sab.split(":"))
        h_fin_sab, m_fin_sab = map(int, hora_fin_sab.split(":"))
        self.ventana_sab = HorarioVentana(inicio=time(h_ini_sab, m_ini_sab), fin=time(h_fin_sab, m_fin_sab))

    def _resolver_timezone(self, tz_name: str):
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except Exception:
            # Fallback seguro para Argentina (UTC-3)
            return timezone(timedelta(hours=-3))

    def ahora(self, dt: Optional[datetime] = None) -> datetime:
        """Obtiene la fecha y hora actual en la zona horaria del negocio."""
        if dt is None:
            return datetime.now(self.tz)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def esta_en_horario(self, dt: Optional[datetime] = None) -> bool:
        """Determina si el momento dado (o el actual) está en horario comercial."""
        if not self.activo:
            return True

        ahora_tz = self.ahora(dt)
        weekday = ahora_tz.weekday()  # 0=Lunes ... 4=Viernes, 5=Sábado, 6=Domingo
        tiempo_actual = ahora_tz.time()

        if 0 <= weekday <= 4:
            return self.ventana_lv.contiene(tiempo_actual)
        elif weekday == 5:
            return self.ventana_sab.contiene(tiempo_actual)
        else:
            return False

    def proxima_apertura(self, dt: Optional[datetime] = None) -> datetime:
        """Calcula el próximo instante de apertura de horario comercial."""
        ahora_tz = self.ahora(dt)
        fecha_eval = ahora_tz.date()
        weekday = ahora_tz.weekday()
        t_actual = ahora_tz.time()

        # Si hoy es Lunes a Viernes y aún no abrió
        if 0 <= weekday <= 4 and t_actual < self.ventana_lv.inicio:
            return datetime.combine(fecha_eval, self.ventana_lv.inicio, tzinfo=self.tz)

        # Si hoy es Sábado y aún no abrió
        if weekday == 5 and t_actual < self.ventana_sab.inicio:
            return datetime.combine(fecha_eval, self.ventana_sab.inicio, tzinfo=self.tz)

        # Buscar el siguiente día con apertura
        for offset in range(1, 8):
            siguiente_fecha = fecha_eval + timedelta(days=offset)
            sig_weekday = siguiente_fecha.weekday()
            if 0 <= sig_weekday <= 4:
                return datetime.combine(siguiente_fecha, self.ventana_lv.inicio, tzinfo=self.tz)
            elif sig_weekday == 5:
                return datetime.combine(siguiente_fecha, self.ventana_sab.inicio, tzinfo=self.tz)

        # Fallback de seguridad
        return ahora_tz + timedelta(hours=1)

    def segundos_hasta_proxima_apertura(self, dt: Optional[datetime] = None) -> float:
        """Retorna la cantidad de segundos restantes hasta que abra el servicio."""
        if self.esta_en_horario(dt):
            return 0.0
        ahora_tz = self.ahora(dt)
        prox = self.proxima_apertura(ahora_tz)
        diff = (prox - ahora_tz).total_seconds()
        return max(0.0, diff)

    def obtener_estado(self, dt: Optional[datetime] = None) -> Dict[str, Any]:
        """Devuelve un diccionario estructurado con el estado operativo actual."""
        ahora_tz = self.ahora(dt)
        abierto = self.esta_en_horario(ahora_tz)
        segundos_espera = self.segundos_hasta_proxima_apertura(ahora_tz)
        prox_apertura = self.proxima_apertura(ahora_tz) if not abierto else None

        dias_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        dia_nom = dias_es[ahora_tz.weekday()]

        if abierto:
            if ahora_tz.weekday() == 5:
                desc = f"OPERATIVO (Sábado hasta las {self.ventana_sab.fin.strftime('%H:%M')})"
            else:
                desc = f"OPERATIVO (Lunes a Viernes hasta las {self.ventana_lv.fin.strftime('%H:%M')})"
        else:
            prox_str = prox_apertura.strftime("%d/%m %H:%M") if prox_apertura else "N/A"
            horas_espera = round(segundos_espera / 3600.0, 1)
            desc = f"PAUSADO (Fuera de horario comercial. Próxima apertura: {prox_str} [~{horas_espera}h])"

        return {
            "activo": self.activo,
            "abierto": abierto,
            "ahora": ahora_tz.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "dia": dia_nom,
            "segundos_hasta_apertura": segundos_espera,
            "proxima_apertura": prox_apertura.strftime("%Y-%m-%d %H:%M:%S %Z") if prox_apertura else None,
            "descripcion": desc
        }
