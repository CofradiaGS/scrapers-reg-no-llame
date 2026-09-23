#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cliente HTTP Puro de Alto Rendimiento para IRIS Movistar (BPM)
Implementa la misma interfaz que `IrisBot` pero ejecutando peticiones HTTP
directas contra los servlets de WebLogic/Plumtree/Fuego Engine.

Ventajas frente a Playwright:
- De 15s a 4-8s por consulta (más de 3x a 4x de aceleración por hilo).
- 35 MB de memoria RAM por worker frente a 1.2 GB de Chromium (-97% RAM).
- Cero fugas de memoria V8 y cero sobrecarga de renderizado de DOM/CSS/JS.
- Permite escalar a 20-30 workers concurrentes en la misma máquina.
"""

import re
import time
import random
import logging
from typing import Optional, Dict, Any, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

import config
from adapters.scrapers.iris.parser import parse_iris_detail

logger = logging.getLogger("IrisHttpBot")


class IrisHttpBot:
    def __init__(self, **kwargs):
        """Inicializa la sesión HTTP con pooling de conexiones y reintentos."""
        self.session: Optional[requests.Session] = None
        self.url_ws = "http://iris.tmoviles.com.ar/workspace/faces/jsf/workspace/workspace.xhtml"
        self.app_link_id = "portletComponentApplications:menuActionNormalModeApplications:_id185:2:applicationLink"
        self.logged_in = False
        self.search_doc_key: Optional[str] = None
        self.search_form_action: Optional[str] = None

    def start(self):
        """Inicializa la sesión requests con pool y headers estándar de navegador."""
        # Fijar resolución DNS de iris.tmoviles.com.ar a la IP estable de WebLogic (10.167.27.185)
        # para evitar el round-robin intermitente hacia 10.167.46.191 (IIS que arroja 404).
        try:
            import urllib3.util.connection as urllib_conn
            if not getattr(urllib_conn, "_iris_pinned", False):
                _orig_create_conn = urllib_conn.create_connection
                def _pinned_create_conn(address, *args, **kwargs):
                    host, port = address
                    if host == "iris.tmoviles.com.ar":
                        host = "10.167.27.185"
                    return _orig_create_conn((host, port), *args, **kwargs)
                urllib_conn.create_connection = _pinned_create_conn
                urllib_conn._iris_pinned = True
        except Exception as e:
            logger.debug(f"No se pudo fijar IP de iris en urllib3: {e}")

        self.session = requests.Session()
        
        # Estrategia de reintentos a nivel socket para mayor estabilidad
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Connection": "keep-alive"
        })
        logger.info("Sesión HTTP inicializada con connection pooling.")

    def close(self):
        """Cierra la sesión HTTP y libera los sockets."""
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
            self.logged_in = False
            self.search_doc_key = None
            self.search_form_action = None
        logger.info("Sesión HTTP cerrada correctamente.")

    def close_execution_dialog(self):
        """Envía la petición AJAX de aborto de diálogo al motor JSF de IRIS para liberar el estado en el servidor."""
        if not self.session or not self.logged_in:
            return
        try:
            headers_ajax = {
                "alui_request_type": "AJAX_CALLBACK",
                "pt-httprequest-type": "CLIENT_SIDE",
                "alui_ajax_controls": "portletComponentApplications:menuActionNormalModeApplications:executionDialogViewApplications:abortExecutionListener",
                "content-type": "application/x-www-form-urlencoded",
                "referer": self.url_ws
            }
            data_ajax = {
                "portletComponentApplications:menuActionNormalModeApplications:executionDialogViewApplications:abortExecutionListener": "",
                "portletComponentApplications": "portletComponentApplications"
            }
            self.session.post(self.url_ws, headers=headers_ajax, data=data_ajax, timeout=10)
        except Exception as e:
            logger.debug(f"Error al cerrar diálogo de ejecución HTTP en IRIS: {e}")

    def _volver_a_busqueda(self, current_doc_key: str, current_action: str) -> bool:
        """Presiona att$button0 (Volver) desde la tabla de resultados para regresar a la pantalla de búsqueda."""
        try:
            ts = int(time.time() * 1000)
            back_url = f"http://iris.tmoviles.com.ar{current_action}&C=undefined&U={ts}"
            back_payload = {
                "xo$Action": "11",
                "xo$AttName": "att$button0",
                "xo$ChangedAtts": "",
                "xo$DocSessKey": current_doc_key,
                "xo$ScreenSessKey": "0",
                "xo$executionType": "rscript"
            }
            r_back = self.session.post(back_url, data=back_payload, timeout=20)
            finish_back_match = re.search(r'url="([^"]+)"', r_back.text)
            if finish_back_match:
                r_screen = self.session.get("http://iris.tmoviles.com.ar" + finish_back_match.group(1).replace("'", ""), timeout=20)
                d_match = re.search(r"var docKey\s*=\s*'([^']+)'", r_screen.text)
                a_match = re.search(r'<FORM[^>]*action="([^"]+)"', r_screen.text, re.IGNORECASE)
                if d_match and a_match and "att$nroLinea" in r_screen.text:
                    self.search_doc_key = d_match.group(1)
                    self.search_form_action = a_match.group(1)
                    return True
        except Exception as e:
            logger.debug(f"Error al volver a la pantalla de búsqueda: {e}")
        self.search_doc_key = None
        self.search_form_action = None
        return False

    def sleep_jitter(self):
        """Pausa de cortesía entre consultas."""
        delay = random.uniform(config.DELAY_MIN, config.DELAY_MAX)
        time.sleep(delay)

    def login(self) -> bool:
        """
        Ejecuta el login directo vía HTTP en el portal IRIS BPM.
        Fuerza la recreación limpia de la sesión HTTP (destruye cookies obsoletas).
        """
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None

        self.start()

        logger.info(f"Navegando a login HTTP: {config.IRIS_LOGIN_URL}")
        try:
            # 1. GET login inicial
            r1 = self.session.get(config.IRIS_LOGIN_URL, timeout=20)
            if r1.status_code != 200:
                logger.error(f"Fallo en GET login.xhtml: Status {r1.status_code}")
                return False

            # 2. POST de autenticación
            payload = {
                "loginForm:workspace_login_user_name": config.IRIS_USER,
                "loginForm:workspace_login_password": config.IRIS_PASS,
                "loginForm:fix_enter_4_ie": "",
                "loginForm:submitbutton": "Login",
                "loginForm": "loginForm"
            }
            r2 = self.session.post(r1.url, data=payload, timeout=25, allow_redirects=True)
            if "workspace.xhtml" not in r2.url:
                logger.error(f"Fallo en login HTTP. URL resultante: {r2.url}")
                return False

            # 3. Inicializar Workspace (UserAgentInfo Form)
            r_uai = self.session.post(
                self.url_ws,
                data={
                    "useragentinfo": "true",
                    "useragentinfojavapluginenabled": "false",
                    "useragentinfotimezone": "180"
                },
                timeout=20
            )

            # 4. Localizar el callback específico para CCConsultaDeOperacion
            app_link = re.search(r"oc\.ajax\.jsf\.doCallback\('portletComponentApplications','([^']+)'\)[^>]*title=\"[^\"]*CCConsultaDeOperacion\"", r_uai.text)
            if not app_link:
                app_link = re.search(r"title=\"[^\"]*CCConsultaDeOperacion\"[^>]*oc\.ajax\.jsf\.doCallback\('portletComponentApplications','([^']+)'\)", r_uai.text)

            if app_link:
                self.app_link_id = app_link.group(1)

            logger.info(f"Login HTTP exitoso en Workspace (app_link: {self.app_link_id}).")
            self.logged_in = True
            return True

        except Exception as e:
            logger.error(f"Error durante el login HTTP: {e}")
            return False

    def check_health(self) -> bool:
        """Verifica si la sesión HTTP de IRIS está inicializada y activa."""
        return self.logged_in and self.session is not None

    def _open_query_screen(self, max_retries: int = 2) -> tuple:
        """
        Dispara el callback AJAX para obtener la pantalla de formulario inicial de consulta.
        Implementa auto-sanación de sesión: si el callback falla o expira, fuerza un login limpio.
        """
        for intento in range(1, max_retries + 1):
            try:
                # Cierre preventivo de cualquier diálogo colgado previo en el servidor WebLogic
                self.close_execution_dialog()

                headers_ajax = {
                    "alui_request_type": "AJAX_CALLBACK",
                    "pt-httprequest-type": "CLIENT_SIDE",
                    "alui_ajax_controls": self.app_link_id,
                    "content-type": "application/x-www-form-urlencoded",
                    "referer": self.url_ws
                }
                data_ajax = {
                    "portletComponentApplications:menuActionNormalModeApplications:executionDialogViewApplications:abortExecutionListener": "",
                    "portletComponentApplications": "portletComponentApplications"
                }
                r_cb = self.session.post(self.url_ws, headers=headers_ajax, data=data_ajax, timeout=20)

                # Auto-sanación si la sesión expiró en el servidor
                es_sesion_expirada = (
                    r_cb.status_code != 200 or
                    "SESSION_TIMED_OUT" in r_cb.text or
                    "login.xhtml" in r_cb.url or
                    "loginForm" in r_cb.text or
                    "session expired" in r_cb.text.lower()
                )

                if es_sesion_expirada:
                    logger.warning(f"Expiración/Fallo de sesión en servidor IRIS (Status {r_cb.status_code}). Re-autenticando sesión HTTP...")
                    self.logged_in = False
                    if not self.login():
                        raise RuntimeError("Fallo al re-autenticar sesión HTTP en IRIS.")
                    headers_ajax["alui_ajax_controls"] = self.app_link_id
                    r_cb = self.session.post(self.url_ws, headers=headers_ajax, data=data_ajax, timeout=20)

                match_dialog = re.search(r'executeDialogApplications\(["\']([^"\']+)["\']', r_cb.text)
                if not match_dialog:
                    logger.warning(
                        f"No se halló diálogo de consulta AJAX en respuesta (intento {intento}/{max_retries}, len: {len(r_cb.text)}). "
                        "Forzando re-login limpio y reintento..."
                    )
                    self.logged_in = False
                    if self.login():
                        headers_ajax["alui_ajax_controls"] = self.app_link_id
                        r_cb = self.session.post(self.url_ws, headers=headers_ajax, data=data_ajax, timeout=20)
                        match_dialog = re.search(r'executeDialogApplications\(["\']([^"\']+)["\']', r_cb.text)

                if not match_dialog:
                    if intento < max_retries:
                        time.sleep(1.0)
                        continue
                    self.logged_in = False
                    raise RuntimeError("No se pudo obtener la URL del diálogo de consulta en la respuesta AJAX.")

                dialog_url = match_dialog.group(1)
                r_form = self.session.get("http://iris.tmoviles.com.ar" + dialog_url, timeout=20)

                doc_key_match = re.search(r"var docKey\s*=\s*'([^']+)';", r_form.text)
                form_action_match = re.search(r'<FORM[^>]*action="([^"]+)"', r_form.text, re.IGNORECASE)

                if doc_key_match and form_action_match:
                    return doc_key_match.group(1), form_action_match.group(1)

                logger.warning(
                    f"Tokens docKey/form_action no encontrados en pantalla de consulta (intento {intento}/{max_retries})."
                )
                self.logged_in = False
                if intento < max_retries:
                    self.login()
                    time.sleep(1.0)

            except Exception as e:
                logger.warning(f"Error en _open_query_screen (intento {intento}/{max_retries}): {e}")
                self.logged_in = False
                if intento < max_retries:
                    try:
                        self.login()
                    except Exception:
                        pass
                    time.sleep(1.0)
                else:
                    raise

        self.logged_in = False
        raise RuntimeError("No se pudieron extraer los tokens docKey y form_action del formulario de consulta.")

    def consultar_linea(self, nro_linea: str, reintento: bool = False) -> Optional[Dict[str, Any]]:
        """
        Ejecuta la consulta completa de una línea en IRIS 100% vía HTTP:
        1. Utiliza la pantalla de consulta activa o abre una nueva si no está disponible.
        2. Envía POST con el número de línea SIN filtros restrictivos (captura todo el historial).
        3. Obtiene la tabla de resultados. Si no hay filas de datos -> retorna a búsqueda y retorna None.
        4. Si hay operaciones -> itera sobre todas las filas, abre su detalle con la lupa,
           extrae sus datos mediante parse_iris_detail, retorna a la tabla y finalmente a la pantalla de búsqueda.
        5. En caso de desincronización de diálogo, auto-recupera la pantalla de búsqueda en vez de emitir falso negativo.
        """
        if not self.logged_in or not self.session:
            if not self.login():
                raise RuntimeError("No autenticado en IRIS HTTP.")

        linea_limpia = "".join(filter(str.isdigit, str(nro_linea).strip()))
        logger.info(f"--- [HTTP] Consultando línea: {linea_limpia} ---")

        try:
            # 1. Obtener pantalla de búsqueda activa o reabrirla limpiamente
            if self.search_doc_key and self.search_form_action:
                doc_key, form_action = self.search_doc_key, self.search_form_action
            else:
                doc_key, form_action = self._open_query_screen()
                self.search_doc_key = doc_key
                self.search_form_action = form_action

            # 2. POST de búsqueda de línea (SIN filtro combo3 de Port Out)
            ts_now = int(time.time() * 1000)
            post_url = f"http://iris.tmoviles.com.ar{form_action}&C=undefined&U={ts_now}"
            query_payload = {
                "xo$Action": "11",
                "xo$AttName": "att$button6",
                "xo$ChangedAtts": "att$nroLinea,",
                "xo$DocSessKey": doc_key,
                "xo$ScreenSessKey": "0",
                "xo$executionType": "rscript",
                "att$nroLinea": linea_limpia,
                "": "undefined"
            }

            r_post = self.session.post(post_url, data=query_payload, timeout=25)

            # Sanidad: Si durante el POST la sesión expiró o redirigió al login
            if "login.xhtml" in r_post.url or "loginForm" in r_post.text:
                logger.warning(f"Línea {linea_limpia}: Sesión expirada en POST de búsqueda. Re-autenticando...")
                self.logged_in = False
                if self.login():
                    self.search_doc_key = None
                    self.search_form_action = None
                    doc_key, form_action = self._open_query_screen()
                    self.search_doc_key = doc_key
                    self.search_form_action = form_action
                    post_url = f"http://iris.tmoviles.com.ar{form_action}&C=undefined&U={int(time.time() * 1000)}"
                    query_payload["xo$DocSessKey"] = doc_key
                    r_post = self.session.post(post_url, data=query_payload, timeout=25)

            finish_url_match = re.search(r'url="([^"]+)"', r_post.text)
            if not finish_url_match:
                if not reintento:
                    logger.warning(f"Línea {linea_limpia}: Desincronización de pantalla (sin finishUrl). Reabriendo formulario y reintentando...")
                    self.search_doc_key = None
                    self.search_form_action = None
                    self._open_query_screen()
                    return self.consultar_linea(nro_linea, reintento=True)
                else:
                    self.search_doc_key = None
                    self.search_form_action = None
                    raise RuntimeError(f"Respuesta inesperada de IRIS al consultar línea {linea_limpia} (sin finishUrl).")

            finish_url = finish_url_match.group(1).replace("'", "")

            # 3. GET de la tabla de resultados
            r_table = self.session.get("http://iris.tmoviles.com.ar" + finish_url, timeout=25)

            doc_key_match = re.search(r"var docKey\s*=\s*'([^']+)'", r_table.text)
            action_match = re.search(r'<FORM[^>]*action="([^"]+)"', r_table.text, re.IGNORECASE)
            table_doc_key = doc_key_match.group(1) if doc_key_match else None
            table_action = action_match.group(1) if action_match else None

            # 4. Extraer filas de la tabla de operaciones
            soup_table = BeautifulSoup(r_table.text, "html.parser")
            filas_operaciones: List[Dict[str, Any]] = []

            for tr in soup_table.find_all("tr"):
                btn = tr.find(lambda e: e.name in ["input", "button"] and e.get("id", "").startswith("grp$array1$detalle$"))
                if btn:
                    tds = tr.find_all("td", recursive=False)
                    if len(tds) >= 7:
                        filas_operaciones.append({
                            "lupa_id": btn.get("id"),
                            "canal": tds[0].get_text(strip=True),
                            "operacion": tds[1].get_text(strip=True),
                            "producto": tds[2].get_text(strip=True),
                            "formulario": tds[3].get_text(strip=True),
                            "nro_tramite": tds[4].get_text(strip=True),
                            "fecha_alta": tds[5].get_text(strip=True),
                            "estado": tds[6].get_text(strip=True),
                            "detalle": {}
                        })

            # Si no hay operaciones: volver a la pantalla de búsqueda y retornar None
            if not filas_operaciones:
                logger.info(f"Línea {linea_limpia}: Sin registros en IRIS.")
                if table_doc_key and table_action:
                    self._volver_a_busqueda(table_doc_key, table_action)
                return None

            logger.info(f"Línea {linea_limpia}: Se encontraron {len(filas_operaciones)} operaciones en IRIS. Obteniendo detalles...")

            if not table_doc_key or not table_action:
                logger.warning(f"Línea {linea_limpia}: No se extrajo docKey/action de la tabla de resultados.")
                return {"registros": filas_operaciones, "total_operaciones": len(filas_operaciones)}

            current_doc_key = table_doc_key
            current_action = table_action

            # 5. Iterar sobre cada operación para extraer su detalle navegando y retornando con Volver
            for idx, op in enumerate(filas_operaciones):
                lupa_id = op["lupa_id"]
                try:
                    ts_now2 = int(time.time() * 1000)
                    detail_post_url = f"http://iris.tmoviles.com.ar{current_action}&C=undefined&U={ts_now2}"
                    detail_payload = {
                        "xo$Action": "11",
                        "xo$AttName": lupa_id,
                        "xo$ChangedAtts": "",
                        "xo$DocSessKey": current_doc_key,
                        "xo$ScreenSessKey": "0",
                        "xo$executionType": "rscript"
                    }

                    r_detail_post = self.session.post(detail_post_url, data=detail_payload, timeout=25)
                    detail_match = re.search(r'url="([^"]+)"', r_detail_post.text)
                    if not detail_match:
                        logger.warning(f"Línea {linea_limpia}: No se extrajo URL de detalle para {lupa_id}.")
                        continue

                    detail_finish_url = detail_match.group(1).replace("'", "")
                    r_detail = self.session.get("http://iris.tmoviles.com.ar" + detail_finish_url, timeout=25)

                    det_datos = parse_iris_detail(r_detail.text)
                    op["detalle"] = det_datos

                    # Volver SIEMPRE a la tabla de resultados usando att$button0 (Volver)
                    d_match = re.search(r"var docKey\s*=\s*'([^']+)'", r_detail.text)
                    a_match = re.search(r'<FORM[^>]*action="([^"]+)"', r_detail.text, re.IGNORECASE)
                    if not d_match or not a_match:
                        logger.warning(f"Línea {linea_limpia}: No se hallaron tokens para volver tras {lupa_id}.")
                        break

                    det_doc_key = d_match.group(1)
                    det_action = a_match.group(1)

                    back_url = f"http://iris.tmoviles.com.ar{det_action}&C=undefined&U={int(time.time() * 1000)}"
                    back_payload = {
                        "xo$Action": "11",
                        "xo$AttName": "att$button0",
                        "xo$ChangedAtts": "",
                        "xo$DocSessKey": det_doc_key,
                        "xo$ScreenSessKey": "0",
                        "xo$executionType": "rscript"
                    }
                    r_back = self.session.post(back_url, data=back_payload, timeout=25)
                    back_match = re.search(r'url="([^"]+)"', r_back.text)
                    if not back_match:
                        logger.warning(f"Línea {linea_limpia}: No se obtuvo URL de retorno a la tabla tras {lupa_id}.")
                        break

                    r_table_next = self.session.get("http://iris.tmoviles.com.ar" + back_match.group(1).replace("'", ""), timeout=25)
                    k_match = re.search(r"var docKey\s*=\s*'([^']+)'", r_table_next.text)
                    act_match = re.search(r'<FORM[^>]*action="([^"]+)"', r_table_next.text, re.IGNORECASE)
                    if not k_match or not act_match:
                        break
                    current_doc_key = k_match.group(1)
                    current_action = act_match.group(1)

                except Exception as e_row:
                    logger.warning(f"Línea {linea_limpia}: Error al abrir detalle de {lupa_id}: {e_row}")
                    break

            # Retornar desde la tabla de resultados a la pantalla de búsqueda con att$button0
            if current_doc_key and current_action:
                self._volver_a_busqueda(current_doc_key, current_action)

            # 6. Consolidar el registro principal para compatibilidad de campos raíz
            registro_principal = None
            for op in filas_operaciones:
                det = op.get("detalle", {})
                if det.get("nro_documento") or det.get("nombre") or op.get("operacion", "").lower() == "port out":
                    registro_principal = det
                    if det.get("nombre") and det.get("nro_documento"):
                        break

            if not registro_principal and filas_operaciones:
                registro_principal = filas_operaciones[0].get("detalle", {})

            resultado_consolidado = dict(registro_principal) if registro_principal else {}
            resultado_consolidado["registros"] = filas_operaciones
            resultado_consolidado["total_operaciones"] = len(filas_operaciones)

            logger.info(
                f"Línea {linea_limpia}: Extraído con éxito vía HTTP - "
                f"Total operaciones: {len(filas_operaciones)}, "
                f"Titular: {resultado_consolidado.get('nombre')}, "
                f"DNI: {resultado_consolidado.get('nro_documento')}"
            )
            return resultado_consolidado

        except Exception as e:
            logger.error(f"Error procesando línea {linea_limpia}: {e}")
            self.logged_in = False
            raise
        finally:
            self.close_execution_dialog()

