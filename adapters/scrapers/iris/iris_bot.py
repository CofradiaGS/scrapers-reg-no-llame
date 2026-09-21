import time
import random
import logging
from typing import Optional, Dict, Any
from pathlib import Path
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Frame, TimeoutError as PlaywrightTimeoutError

import config
from adapters.scrapers.iris.parser import parse_iris_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("IrisBot")

class IrisBot:
    def __init__(self, headless: Optional[bool] = None):
        self.headless = config.HEADLESS if headless is None else headless
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.frame: Optional[Frame] = None

    def start(self):
        """Inicia Playwright y la instancia de Chromium."""
        logger.info(f"Lanzando Chromium (Headless={self.headless})...")
        self.playwright = sync_playwright().start()

        launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
        if not self.headless:
            launch_args.append("--start-maximized")
            self.browser = self.playwright.chromium.launch(
                headless=False,
                slow_mo=80,
                args=launch_args
            )
            self.context = self.browser.new_context(no_viewport=True)
        else:
            launch_args.extend(["--window-size=1920,1080"])
            self.browser = self.playwright.chromium.launch(
                headless=True,
                slow_mo=20,
                args=launch_args
            )
            self.context = self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        self.page = self.context.new_page()
        # Manejador automático de diálogos/alertas JS para no congelar la ejecución
        self.page.on("dialog", lambda dialog: dialog.dismiss())

    def close(self):
        """Cierra la sesión y el navegador de forma segura."""
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            logger.info("Navegador cerrado correctamente.")
        except Exception as e:
            logger.warning(f"Error al cerrar navegador: {e}")

    def sleep_jitter(self):
        """Pausa aleatoria entre consultas para proteger el servidor de IRIS."""
        delay = random.uniform(config.DELAY_MIN, config.DELAY_MAX)
        logger.info(f"Pausa de seguridad: {delay:.2f}s...")
        time.sleep(delay)

    def login(self) -> bool:
        """Autentica al usuario en el portal IRIS BPM."""
        logger.info(f"Navegando a login: {config.IRIS_LOGIN_URL}")
        self.page.goto(config.IRIS_LOGIN_URL, timeout=config.DEFAULT_TIMEOUT)
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # Verificar si ya estamos en workspace
        if "workspace.xhtml" in self.page.url:
            logger.info("Ya en sesión activa de Workspace.")
            return True

        # Completar formulario de login
        logger.info(f"Ingresando usuario: {config.IRIS_USER}")
        user_input = self.page.locator("input[id='loginForm:workspace_login_user_name']")
        pass_input = self.page.locator("input[id='loginForm:workspace_login_password']")
        btn_login = self.page.locator("input[id='loginForm:submitbutton']")

        user_input.fill(config.IRIS_USER)
        pass_input.fill(config.IRIS_PASS)
        time.sleep(0.3)
        btn_login.click()

        try:
            self.page.wait_for_url("**/workspace.xhtml*", timeout=15000)
        except Exception:
            try:
                self.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        time.sleep(2)

        if "workspace.xhtml" in self.page.url:
            logger.info("Login exitoso en Workspace.")
            return True
        else:
            logger.error(f"Fallo en login. URL actual: {self.page.url}")
            return False

    def close_execution_dialog(self):
        """
        Cierra el diálogo modal de ejecución tocando el botón 'Cerrar' indicado:
        id="portletComponentApplications_menuActionNormalModeApplications_executionDialogViewApplications_executionDialogApplications_CloseButton"
        para volver a la vista principal de Workspace.
        """
        try:
            close_btn = self.page.locator(
                "#portletComponentApplications_menuActionNormalModeApplications_executionDialogViewApplications_executionDialogApplications_CloseButton"
            ).first
            if close_btn.count() > 0 and close_btn.is_visible():
                logger.info("Cerrando diálogo de consulta (botón Cerrar)...")
                close_btn.click()
                time.sleep(1.2)
            else:
                close_alt = self.page.locator("//input[@title='Cerrar'][contains(@id, 'executionDialogApplications_CloseButton')]").first
                if close_alt.count() > 0 and close_alt.is_visible():
                    close_alt.click()
                    time.sleep(1.2)
                else:
                    self.page.evaluate("""() => {
                        const dlg = oc?.Page?.['portletComponentApplications_menuActionNormalModeApplications_executionDialogViewApplications_executionDialogApplications'];
                        if (dlg && dlg.close) dlg.close();
                    }""")
                    time.sleep(1.0)
        except Exception as e:
            logger.debug(f"Aviso al cerrar diálogo de consulta: {e}")
        finally:
            self.frame = None

    def ensure_in_query_screen(self) -> Frame:
        """
        Garantiza que el bot se encuentre en el formulario inicial de consulta
        (donde está el campo att$nroLinea y el botón Consultar).
        
        Incluye Self-Healing:
        - Detecta si el servidor invalidó la sesión y re-autentica automáticamente.
        - Si la pantalla se congeló, recarga workspace.xhtml de forma transparente.
        - Cierra modales previos para asegurar un estado limpio.
        """
        # 0. Verificación de Auto-Sanación de Sesión (Self-Healing)
        if "login.xhtml" in self.page.url or self.page.locator("input[id='loginForm:workspace_login_user_name']").count() > 0:
            logger.warning("Detectada expiración de sesión del servidor IRIS. Auto-sanando sesión...")
            if not self.login():
                raise RuntimeError("Fallo en auto-sanación de sesión de IRIS.")

        if "workspace.xhtml" not in self.page.url:
            logger.warning(f"Navegando a Workspace desde URL inesperada: {self.page.url}")
            self.page.goto("http://iris.tmoviles.com.ar/workspace/faces/jsf/workspace/workspace.xhtml", timeout=20000)
            time.sleep(1.5)
            if "login.xhtml" in self.page.url:
                self.login()

        # 1. Si hay diálogo abierto, verificar si ya está en el buscador; si no, cerrarlo
        close_btn = self.page.locator(
            "#portletComponentApplications_menuActionNormalModeApplications_executionDialogViewApplications_executionDialogApplications_CloseButton"
        ).first
        if close_btn.count() > 0 and close_btn.is_visible():
            target_frame = self.page.frame("executionPanelApplications")
            try:
                if target_frame and target_frame.locator("input[name='att$nroLinea']").is_visible():
                    self.frame = target_frame
                    return target_frame
            except Exception:
                pass
            self.close_execution_dialog()

        # 2. Abrir menú lateral 'CCConsultaDeOperacion' o 'ConsultaDeOperacion'
        logger.info("Abriendo menú de consulta de operación...")
        menu_btn = self.page.locator(
            "//a[contains(@title, 'CCConsultaDeOperacion')] | "
            "//span[contains(text(), 'CCConsultaDeOperacion')] | "
            "//a[contains(@title, 'ConsultaDeOperacion')] | "
            "//span[contains(text(), 'ConsultaDeOperacion')] | "
            "//span[contains(text(), 'Consulta de operacion')]"
        ).first

        try:
            menu_btn.wait_for(state="visible", timeout=12000)
        except Exception:
            logger.warning("Menú no visible en primer intento. Recargando workspace...")
            self.page.goto("http://iris.tmoviles.com.ar/workspace/faces/jsf/workspace/workspace.xhtml", timeout=20000)
            time.sleep(2.0)
            menu_btn.wait_for(state="visible", timeout=15000)

        menu_btn.click()
        time.sleep(2.5)

        # 3. Localizar el iframe de consulta
        target_frame = None
        for _ in range(15):
            target_frame = self.page.frame("executionPanelApplications")
            if not target_frame:
                for f in self.page.frames:
                    try:
                        if f.locator("input[name='att$nroLinea']").count() > 0:
                            target_frame = f
                            break
                    except Exception:
                        pass
            if target_frame:
                try:
                    if target_frame.locator("input[name='att$nroLinea']").is_visible():
                        break
                except Exception:
                    pass
            time.sleep(1)

        if not target_frame:
            raise RuntimeError("No se pudo localizar el iframe de consulta tras abrir el menú.")

        target_frame.locator("input[name='att$nroLinea']").wait_for(state="visible", timeout=15000)
        self.frame = target_frame
        return target_frame

    def consultar_linea(self, nro_linea: str) -> Optional[Dict[str, Any]]:
        """
        Ejecuta la consulta completa de una línea en IRIS:
        1. Abre la pantalla de consulta.
        2. Escribe el número en att$nroLinea.
        3. Pulsa Consultar (att$button6).
        4. Si no hay fila de lupa -> retorna None (SIN_DATOS).
        5. Si hay fila -> pulsa la lupa, extrae el detalle y retorna el dict estructurado.
        6. Siempre cierra el diálogo de consulta al finalizar para reiniciar el ciclo.
        """
        linea_limpia = "".join(filter(str.isdigit, str(nro_linea).strip()))
        logger.info(f"--- Consultando línea: {linea_limpia} ---")

        frame = self.ensure_in_query_screen()
        datos_extraidos = None

        try:
            # 1. Completar campo de línea
            input_linea = frame.locator("input[name='att$nroLinea']")
            input_linea.click()
            input_linea.fill(linea_limpia)
            self.page.keyboard.press("Tab")
            time.sleep(0.3)

            # 2. Seleccionar filtro 'Port Out' (value='4') en Tipo de Operación
            combo_operacion = frame.locator("select[name='att$combo3']")
            if combo_operacion.count() > 0 and combo_operacion.first.is_visible():
                logger.info("Aplicando filtro previo: Tipo de Operación = 'Port Out' (value 4)...")
                combo_operacion.first.select_option("4")
                time.sleep(0.5)

            # 3. Clic en Consultar
            btn_consultar = frame.locator("#att\\$button6")
            btn_consultar.click()

            # 4. Esperar que aparezca cualquier resultado en la tabla
            any_lupa = frame.locator("input[id*='grp$array1$detalle']").first
            try:
                any_lupa.wait_for(state="visible", timeout=12000)
            except PlaywrightTimeoutError:
                logger.info(f"Línea {linea_limpia}: Sin registros o no encontrada en el sistema.")
                return None

            # 5. Localizar el botón de lupa específico de la fila 'Port Out'
            logger.info(f"Línea {linea_limpia}: Tabla cargada. Buscando fila 'Port Out'...")
            find_btn_js = """() => {
                const btns = Array.from(document.querySelectorAll("input[id*='grp$array1$detalle']"));
                for (const btn of btns) {
                    const row = btn.closest("tr");
                    const rowText = row ? row.innerText.toLowerCase() : '';
                    if (rowText.includes("port out") || rowText.includes("portout") || rowText.includes("solicitud portout")) {
                        return btn.id;
                    }
                }
                return null;
            }"""
            lupa_id = frame.evaluate(find_btn_js)

            if not lupa_id:
                logger.warning(f"Línea {linea_limpia}: Tiene registros pero ninguno corresponde a 'Port Out'.")
                return None

            # 6. Clic en la lupa de Port Out para abrir el Detalle
            logger.info(f"Línea {linea_limpia}: Botón Port Out identificado ({lupa_id}). Abriendo detalle...")
            lupa_btn = frame.locator(f"input[id='{lupa_id}']")
            lupa_btn.click()

            # Esperar a que el frame complete la navegación hacia la pantalla de Detalle
            detalle_locator = frame.locator("#suscriptorT_comp, #nroTramiteABD_comp").first
            try:
                detalle_locator.wait_for(state="visible", timeout=20000)
            except PlaywrightTimeoutError:
                logger.info("Detalle tardando en renderizar, esperando 4 segundos adicionales...")
                time.sleep(4)

            time.sleep(1.5)

            # 7. Extraer y parsear datos del detalle de forma segura
            html_detalle = ""
            for attempt in range(5):
                try:
                    html_detalle = frame.content()
                    break
                except Exception:
                    time.sleep(1)

            if not html_detalle:
                logger.warning(f"Línea {linea_limpia}: No se pudo leer el HTML del detalle.")
                return None

            datos_extraidos = parse_iris_detail(html_detalle)
            logger.info(f"Línea {linea_limpia}: Extraído con éxito - Trámite ABD: {datos_extraidos.get('nro_tramite_abd')}, Titular: {datos_extraidos.get('nombre')}")
            return datos_extraidos

        finally:
            # Tocar el botón de cerrar diálogo para volver a la pantalla principal limpia
            self.close_execution_dialog()
