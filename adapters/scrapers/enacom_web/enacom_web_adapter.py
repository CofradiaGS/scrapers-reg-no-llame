# -*- coding: utf-8 -*-
"""
Adaptador Secundario: Scraper ENACOM Numeración Web (https://numeracion.enacom.gob.ar/)
Implementa IScraperEnginePort para la identificación del prestador original y prestador actual
en tiempo real de cualquier línea telefónica de la República Argentina.

Características de Ingeniería:
- Extracción de Portabilidad Numérica en Vivo (TxtPrestadorActual vs TxtPrestadorOriginal).
- Motor de evasión híbrido:
  1. Resolución OCR local ultra-rápida (OpenCV + Windows.Media.Ocr vía winsdk).
  2. Ejecución automatizada de Google reCAPTCHA v3 con navegador Chromium headless optimizado.
- Interceptación y bloqueo de recursos pesados (fuentes, CSS externos) para latencias < 3s.
- Reutilización de contexto y sesión con reconexión automática anti-fuga de memoria.
"""
import os
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

from core.domain.entities import Linea, ScrapeResult, StatusScraping
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.scrapers.enacom_web.captcha_solver import EnacomCaptchaSolver

logger = logging.getLogger("ScraperEnacomWeb")


class EnacomWebAdapter(BaseScraperAdapter):
    """
    Adaptador de infraestructura para el portal oficial de Numeración de ENACOM.
    Consulta el estado oficial de asignación y portabilidad numérica activa de una línea.
    """

    PORTAL_URL = "https://numeracion.enacom.gob.ar/Numeracion.aspx"

    def __init__(
        self,
        headless: bool = True,
        max_retries: int = 4,
        proxy: Optional[str] = None,
        use_tor: bool = False,
        tor_socks_port: int = 9050,
        **kwargs
    ):
        self.headless = headless
        self.max_retries = max_retries
        self.proxy = proxy
        self.use_tor = use_tor or kwargs.get("tor", False)
        self.tor_socks_port = tor_socks_port
        if self.use_tor and not self.proxy:
            self.proxy = f"socks5://127.0.0.1:{self.tor_socks_port}"

        self.solver = EnacomCaptchaSolver()
        self._lock = threading.Lock()

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._consultas_realizadas = 0

        self._static_cache_dir = Path(__file__).resolve().parents[3] / "data" / "enacom_static_cache"
        self._ensure_static_assets()

    def _ensure_static_assets(self) -> None:
        """Asegura que el directorio de cache estático exista."""
        try:
            self._static_cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"No se pudo inicializar directorio de cache estático ({e}).")

    def _setup_page_routes(self, page: Page) -> None:
        """
        Configura la interceptación y ruteo de red ultra-optimizado:
        1. Fulfill local dinámico de scripts pesados (Google reCAPTCHA v3 runtime ~850 KB por versión).
           Si la versión de release solicitada por Google no está en disco, se descarga una única vez
           directamente y se almacena en `data/enacom_static_cache/<version>_<lang>.js`.
           Para todas las cargas y rotaciones subsecuentes, se sirve en 0ms con 0 bytes consumidos de proxy.
        2. Bloqueo (abort) de fuentes (.woff, .woff2, .ttf), librerías CSS pesadas (font-awesome, bootstrap),
           logos e íconos estéticos que no aportan a la resolución.
        3. Paso libre exclusivo para peticiones transaccionales esenciales (portal ENACOM, ASP.NET WebResource
           y tokens reCAPTCHA v3).
        """
        cache_dir = self._static_cache_dir

        def intercept_route(route):
            url = route.request.url
            clean_url = url.split("?")[0]

            # 1. Local Fulfill dinámico para Google reCAPTCHA v3 (~850 KB ahorrados por carga)
            if "/recaptcha/releases/" in clean_url and ("recaptcha__" in clean_url and clean_url.endswith(".js")):
                try:
                    file_key = clean_url.split("/releases/")[1].replace("/", "_")
                    local_path = cache_dir / file_key
                    if not local_path.exists() or local_path.stat().st_size == 0:
                        import urllib.request
                        req = urllib.request.Request(clean_url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            local_path.write_bytes(resp.read())
                    return route.fulfill(
                        path=str(local_path),
                        content_type="application/javascript",
                        status=200
                    )
                except Exception as e:
                    logger.debug(f"Fallback a red para reCAPTCHA ({e})")
                    return route.continue_()

            # 2. Bloqueo de recursos estáticos innecesarios para el scraping
            if any(ext in clean_url.lower() for ext in [
                ".woff", ".woff2", ".ttf", ".eot",
                "font-awesome", "all.min.css", "bootstrap", "popper",
                "favicon.ico", "logo-enacom"
            ]):
                return route.abort()

            # 3. Continuar tráfico esencial (ENACOM Numeracion.aspx, WebResource, tokens api.js)
            return route.continue_()

        page.route("**/*", intercept_route)

    @property
    def nombre(self) -> str:
        return "enacom_web"

    def iniciar(self) -> None:
        """Inicializa los recursos de Playwright."""
        with self._lock:
            self._ensure_browser()

    def autenticar(self) -> bool:
        """El portal de ENACOM es de acceso público; no requiere credenciales."""
        return True

    def verificar_salud(self) -> bool:
        """Verifica conectividad y disponibilidad del portal oficial de ENACOM."""
        try:
            import urllib3, requests
            urllib3.disable_warnings()
            proxies = None
            if self.proxy:
                proxies = {"http": self.proxy, "https": self.proxy}
            r = requests.get(self.PORTAL_URL, verify=False, proxies=proxies, timeout=8)
            return r.status_code == 200
        except Exception:
            return False

    def cerrar(self) -> None:
        """Alias para close() requerido por IScraperEnginePort."""
        self.close()

    def _ensure_browser(self) -> Page:
        """Inicializa o restablece la instancia de Playwright optimizada."""
        if self._page and not self._page.is_closed():
            return self._page

        logger.info(f"Inicializando motor Chromium headless para ENACOM Web (Proxy: {self.proxy or 'Directo'})...")
        if not self._playwright:
            self._playwright = sync_playwright().start()

        if not self._browser or not self._browser.is_connected():
            launch_kwargs: Dict[str, Any] = {
                "headless": self.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-blink-features=AutomationControlled",
                ]
            }
            if self.proxy:
                launch_kwargs["proxy"] = {"server": self.proxy}
            self._browser = self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: Dict[str, Any] = {
            "ignore_https_errors": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        if self.proxy:
            context_kwargs["proxy"] = {"server": self.proxy}

        self._context = self._browser.new_context(**context_kwargs)
        self._page = self._context.new_page()
        self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

        self._setup_page_routes(self._page)
        try:
            self._page.goto(self.PORTAL_URL, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            logger.warning(f"Fallo crítico al conectar al portal con proxy {self.proxy}: {e}")
            try:
                self._context.close()
            except Exception:
                pass
            self._page = None
            self._context = None
            raise RuntimeError(f"Proxy no funcional o rechazado por ENACOM ({self.proxy}): {e}")
        return self._page

    def invalidar_proxy(self) -> None:
        """Invalida inmediatamente el proxy actual y cierra el contexto de navegación para evitar reusar una IP bloqueada."""
        with self._lock:
            self.proxy = None
            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None

    def rotar_proxy(self, nuevo_proxy: Optional[str]) -> bool:
        """
        Rota el proxy activo en caliente cerrando el contexto previo y creando uno nuevo
        sin necesidad de reiniciar el proceso de Chromium (cambio en < 250ms).
        """
        with self._lock:
            self.proxy = nuevo_proxy
            if not self._browser or not self._browser.is_connected():
                self._ensure_browser()
                return True

            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass

            context_kwargs: Dict[str, Any] = {
                "ignore_https_errors": True,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
            if self.proxy:
                context_kwargs["proxy"] = {"server": self.proxy}

            try:
                self._context = self._browser.new_context(**context_kwargs)
                self._page = self._context.new_page()
                self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

                self._setup_page_routes(self._page)
                self._page.goto(self.PORTAL_URL, wait_until="domcontentloaded", timeout=25000)
                return True
            except Exception as e:
                logger.warning(f"Error al inicializar portal con nuevo proxy {self.proxy}: {e}")
                self.proxy = None
                self._page = None
                self._context = None
                return False

    def _normalizar_operador(self, nombre_raw: str) -> str:
        """Homologa las razones sociales regulatorias a marcas comerciales."""
        nom = (nombre_raw or "").upper().strip()
        if "COOPERATIVA" in nom:
            return nombre_raw.strip()
        if "AMX" in nom or "CLARO" in nom:
            return "Claro"
        if "TELEFONICA" in nom or "MOVISTAR" in nom:
            return "Movistar"
        if "TELECOM" in nom or "PERSONAL" in nom:
            return "Personal"
        if "TELMEX" in nom:
            return "Telmex"
        if "TELECENTRO" in nom:
            return "Telecentro"
        return nombre_raw.strip()

    def consultar_linea(self, linea: Linea) -> ScrapeResult:
        """
        Ejecuta la consulta de la línea ante ENACOM Web.
        Devuelve ScrapeResult con la asignación original y el prestador actual en vivo.
        """
        ani_clean = linea.ani.strip()
        if len(ani_clean) != 10 or not ani_clean.isdigit():
            return ScrapeResult(
                ani=ani_clean,
                status=StatusScraping.ERROR,
                fuente_scraper="enacom_web",
                descripcion=f"ANI inválido para ENACOM (requiere 10 dígitos sin 0 ni 15): {ani_clean}"
            )

        with self._lock:
            # Rotación preventiva preventiva cada 350 consultas
            if self._consultas_realizadas >= 350:
                logger.info("Rotación de ciclo preventivo anti-leak de ENACOM Web...")
                self.close()

            page = self._ensure_browser()
            self._consultas_realizadas += 1

            for intento in range(1, self.max_retries + 1):
                try:
                    # 1. Asegurar formulario limpio
                    if not page.url.startswith(self.PORTAL_URL):
                        page.goto(self.PORTAL_URL, wait_until="domcontentloaded", timeout=15000)

                    # 2. Capturar y resolver captcha (bucle interno hasta asegurar 5 caracteres alfanuméricos válidos)
                    code = ""
                    for sub_intento in range(6):
                        captcha_elem = page.locator("#imgCaptcha")
                        if captcha_elem.count() == 0:
                            page.reload(wait_until="domcontentloaded")
                            continue

                        img_bytes = captcha_elem.screenshot()
                        cand = self.solver.solve_bytes(img_bytes)
                        if len(cand) == 5:
                            code = cand
                            break

                        # Si el OCR leyó menos o más de 5 caracteres, recargar imagen sin gastar intento de envío
                        page.evaluate("() => RecargarCaptcha()")
                        page.wait_for_timeout(500)

                    if len(code) != 5:
                        page.reload(wait_until="domcontentloaded")
                        continue

                    # 3. Limpiar inputs previos para evitar lecturas de consultas anteriores
                    page.evaluate("""() => {
                        const ids = ['TxtRtaNumero', 'TxtPrestadorOriginal', 'TxtPrestadorActual', 'TxtNumero', 'TxtCaptcha'];
                        ids.forEach(id => {
                            const el = document.getElementById(id);
                            if (el) el.value = '';
                        });
                        const msg = document.getElementById('DivMensaje');
                        if (msg) msg.style.display = 'none';
                    }""")

                    # Completar campos y simular ritmo humano para alimentar reCAPTCHA v3
                    page.fill("#TxtNumero", ani_clean)
                    page.wait_for_timeout(80)
                    page.fill("#TxtCaptcha", code)
                    page.wait_for_timeout(120)
                    page.click("#BtnBuscar")

                    # 4. Esperar respuesta (resultado poblado para este número específico o mensaje de error)
                    encontrado = False
                    for _ in range(40):
                        val_num = page.input_value("#TxtRtaNumero").strip()
                        val_actual = page.input_value("#TxtPrestadorActual").strip()
                        if val_num == ani_clean and val_actual:
                            encontrado = True
                            break

                        # Verificar si saltó error de captcha incorrecto u otro mensaje
                        div_msg = page.locator("#DivMensaje")
                        if div_msg.count() > 0 and div_msg.is_visible():
                            msg_txt = div_msg.inner_text().strip()
                            if "incorrecto" in msg_txt.lower():
                                break
                            if any(kw in msg_txt.lower() for kw in ["no se encontr", "no asignado", "no existe", "inexistente", "no posee"]):
                                return ScrapeResult(
                                    ani=ani_clean,
                                    status=StatusScraping.SIN_COINCIDENCIA,
                                    fuente_scraper="enacom_web",
                                    descripcion=f"ENACOM Oficial: {msg_txt}"
                                )
                            if "límite" in msg_txt.lower() or "limite" in msg_txt.lower() or "diarias" in msg_txt.lower() or "minuto" in msg_txt.lower():
                                logger.warning(f"ENACOM Rate Limit detectado: '{msg_txt}'")
                                es_diario = ("100" in msg_txt or "diaria" in msg_txt.lower())
                                es_minuto = ("5" in msg_txt or "minuto" in msg_txt.lower())
                                err_tipo = "limite_diario" if es_diario else ("limite_minuto" if es_minuto else "rate_limit")
                                return ScrapeResult(
                                    ani=ani_clean,
                                    status=StatusScraping.ERROR,
                                    fuente_scraper="enacom_web",
                                    detalles={"error_tipo": err_tipo, "mensaje_enacom": msg_txt},
                                    descripcion=f"Límite alcanzado: {msg_txt} (Requiere rotar de IP/Proxy)"
                                )
                            if "seguridad" in msg_txt.lower() or "recaptcha" in msg_txt.lower():
                                logger.warning(f"ENACOM Bloqueo reCAPTCHA v3 detectado: '{msg_txt}'")
                                return ScrapeResult(
                                    ani=ani_clean,
                                    status=StatusScraping.ERROR,
                                    fuente_scraper="enacom_web",
                                    detalles={"error_tipo": "seguridad_recaptcha", "mensaje_enacom": msg_txt},
                                    descripcion=f"Fallo de seguridad reCAPTCHA v3: {msg_txt} (Requiere rotar de IP/Proxy)"
                                )

                        page.wait_for_timeout(100)

                    if encontrado:
                        orig = page.input_value("#TxtPrestadorOriginal").strip()
                        act = page.input_value("#TxtPrestadorActual").strip()
                        num_confirmado = page.input_value("#TxtRtaNumero").strip() or ani_clean

                        es_portado = (orig.lower() != act.lower())
                        op_norm = self._normalizar_operador(act)
                        op_orig_norm = self._normalizar_operador(orig)

                        detalles = {
                            "numero": num_confirmado,
                            "prestador_original": orig,
                            "prestador_actual": act,
                            "operador_comercial_actual": op_norm,
                            "operador_comercial_original": op_orig_norm,
                            "es_portado": es_portado,
                            "intentos_resolucion": intento,
                        }

                        desc = (
                            f"ENACOM Oficial: Línea {num_confirmado} asignada a {orig} "
                            f"y operada actualmente por {act}"
                        )
                        if es_portado:
                            desc += " (PORTABILIDAD CONFIRMADA)"

                        return ScrapeResult(
                            ani=ani_clean,
                            status=StatusScraping.COINCIDENCIA,
                            fuente_scraper="enacom_web",
                            operador=op_orig_norm,
                            operador_receptor=op_norm,
                            detalles=detalles,
                            raw={"prestador_original": orig, "prestador_actual": act},
                            descripcion=desc
                        )

                    # Si el captcha falló, recargar para siguiente intento
                    page.evaluate("() => RecargarCaptcha()")
                    page.wait_for_timeout(600)

                except Exception as exc:
                    logger.warning(f"Excepción en intento {intento} para ANI {ani_clean}: {exc}")
                    try:
                        page.reload(wait_until="domcontentloaded")
                    except Exception:
                        self.close()
                        page = self._ensure_browser()

            # Agotados los reintentos
            return ScrapeResult(
                ani=ani_clean,
                status=StatusScraping.SIN_COINCIDENCIA,
                fuente_scraper="enacom_web",
                descripcion=f"No se pudo resolver o línea no localizada en ENACOM tras {self.max_retries} intentos."
            )

    def close(self) -> None:
        """Cierra de manera ordenada los recursos de Playwright."""
        with self._lock:
            try:
                if self._page and not self._page.is_closed():
                    self._page.close()
            except Exception:
                pass
            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass
            try:
                if self._browser:
                    self._browser.close()
            except Exception:
                pass
            try:
                if self._playwright:
                    self._playwright.stop()
            except Exception:
                pass

            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            self._consultas_realizadas = 0

    # Alias en español
    cerrar = close

    def __del__(self):
        self.close()
