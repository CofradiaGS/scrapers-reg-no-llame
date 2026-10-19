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
        if not self.proxy:
            try:
                import config
                self.proxy = getattr(config, "ENACOM_PROXY", None)
            except Exception:
                pass
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

    def _setup_page_routes(self, target: Any) -> None:
        """
        Configura la interceptación y ruteo de red ultra-optimizado a nivel de BrowserContext:
        1. Fulfill local dinámico de scripts pesados:
           - Google reCAPTCHA v3 runtime (~850 KB ahorrados por carga)
           - Google reCAPTCHA loader api.js (~1.6 KB)
           - Google reCAPTCHA webworker.js (102 B)
           - jQuery (~70 KB ahorrados por carga)
           Garantiza 0 bytes consumidos de proxy en todos los assets estáticos de reCAPTCHA y scripts de soporte.
        2. Bloqueo universal (abort) de:
           - Todas las hojas de estilo (.css, fonts, media). Chromium headless no requiere render visual.
           - Todas las imágenes excepto Captcha.aspx (logos, favicons, etc.).
           - Scripts y rastreadores externos superfluos (bootstrap, popper, font-awesome, analytics).
        3. Paso libre exclusivo para peticiones transaccionales esenciales (portal ENACOM, Captcha.aspx y tokens api2).
        """
        cache_dir = self._static_cache_dir

        def intercept_route(route):
            try:
                url = route.request.url
                clean_url = url.split("?")[0]
                clean_lower = clean_url.lower()
                res_type = route.request.resource_type

                # 1. Bloqueo total de estilos, fuentes y multimedia (Chromium headless no renderiza visualmente)
                if res_type in ("stylesheet", "font", "media"):
                    return route.abort()

                # 2. Bloqueo total de imágenes excepto el Captcha oficial
                if res_type == "image":
                    if "captcha.aspx" not in clean_lower:
                        return route.abort()
                    return route.continue_()

                # 3. Local Fulfill dinámico para Google reCAPTCHA v3 (~850 KB ahorrados por carga)
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
                        try:
                            return route.continue_()
                        except Exception:
                            return None

                # 4. Local Fulfill para Google reCAPTCHA api.js loader
                if "recaptcha/api.js" in clean_lower:
                    local_api = cache_dir / "recaptcha_api.js"
                    if not local_api.exists() or local_api.stat().st_size == 0:
                        try:
                            import urllib.request
                            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                            with urllib.request.urlopen(req, timeout=10) as resp:
                                local_api.write_bytes(resp.read())
                        except Exception:
                            pass
                    if local_api.exists() and local_api.stat().st_size > 0:
                        return route.fulfill(
                            path=str(local_api),
                            content_type="application/javascript",
                            status=200
                        )

                # 5. Local Fulfill para webworker.js
                if "recaptcha/api2/webworker.js" in clean_lower:
                    local_ww = cache_dir / "webworker.js"
                    if not local_ww.exists() or local_ww.stat().st_size == 0:
                        try:
                            local_ww.write_text("importScripts('https://www.gstatic.com/recaptcha/releases//recaptcha__en.js');", encoding="utf-8")
                        except Exception:
                            pass
                    if local_ww.exists() and local_ww.stat().st_size > 0:
                        return route.fulfill(
                            path=str(local_ww),
                            content_type="application/javascript",
                            status=200
                        )

                # 6. Local Fulfill para jQuery (~70 KB ahorrados por carga)
                if "jquery" in clean_lower and clean_lower.endswith(".js"):
                    local_jq = cache_dir / "jquery-3.4.1.slim.min.js"
                    if local_jq.exists() and local_jq.stat().st_size > 0:
                        return route.fulfill(
                            path=str(local_jq),
                            content_type="application/javascript",
                            status=200
                        )

                # 7. Bloqueo de scripts de soporte y rastreadores innecesarios para el scraping
                if any(k in clean_lower for k in [
                    "popper", "bootstrap", "font-awesome", "favicon",
                    "google-analytics", "doubleclick", "googletagmanager", "stats", "beacon"
                ]):
                    return route.abort()

                # 8. Continuar tráfico transaccional esencial (Numeracion.aspx, Captcha.aspx, tokens api2 de Google)
                return route.continue_()
            except Exception:
                pass

        target.route("**/*", intercept_route)

    @property
    def nombre(self) -> str:
        return "scraper_enacom"

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

    @staticmethod
    def _formatear_proxy_playwright(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
        """
        Formatea una URL de proxy para Playwright soportando credenciales embebidas
        (ej: http://user:pass@gate.smartproxy.com:7000 -> server, username, password).
        """
        if not proxy_url:
            return None
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        if parsed.username and parsed.password:
            scheme = parsed.scheme or "http"
            server = f"{scheme}://{parsed.hostname}:{parsed.port}"
            return {
                "server": server,
                "username": parsed.username,
                "password": parsed.password,
            }
        return {"server": proxy_url}

    def _ensure_browser(self) -> Page:
        """Inicializa o restablece la instancia de Playwright optimizada."""
        if self._page and not self._page.is_closed():
            return self._page

        logger.info(f"Inicializando motor Chromium headless para ENACOM Web (Proxy: {self.proxy or 'Directo'})...")
        if not self._playwright:
            os.environ.setdefault("NODE_OPTIONS", "--max-old-space-size=512")
            self._playwright = sync_playwright().start()

        if not self._browser or not self._browser.is_connected():
            launch_kwargs: Dict[str, Any] = {
                "headless": self.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--disable-blink-features=AutomationControlled",
                    "--js-flags=--max-old-space-size=128 --lite-mode",
                    "--mute-audio",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-background-timer-throttling",
                    "--disable-client-side-phishing-detection",
                    "--disable-default-apps",
                    "--disable-hang-monitor",
                    "--disable-popup-blocking",
                    "--disable-prompt-on-repost",
                    "--disable-sync",
                    "--disable-translate",
                    "--metrics-recording-only",
                    "--renderer-process-limit=1",
                    "--disable-site-isolation-trials",
                    "--disable-breakpad",
                    "--disable-ipc-flooding-protection",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-component-update",
                    "--disable-domain-reliability",
                    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints,AudioServiceOutOfProcess",
                    "--force-device-scale-factor=1",
                    "--window-size=400,300",
                ]
            }
            if self.proxy:
                p_cfg = self._formatear_proxy_playwright(self.proxy)
                if p_cfg:
                    launch_kwargs["proxy"] = p_cfg
            self._browser = self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: Dict[str, Any] = {
            "ignore_https_errors": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "viewport": {"width": 400, "height": 300},
            "device_scale_factor": 1,
            "has_touch": False,
            "is_mobile": False,
        }
        if self.proxy:
            p_cfg = self._formatear_proxy_playwright(self.proxy)
            if p_cfg:
                context_kwargs["proxy"] = p_cfg

        self._context = self._browser.new_context(**context_kwargs)
        self._setup_page_routes(self._context)
        self._page = self._context.new_page()
        self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

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
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "viewport": {"width": 400, "height": 300},
                "device_scale_factor": 1,
                "has_touch": False,
                "is_mobile": False,
            }
            if self.proxy:
                p_cfg = self._formatear_proxy_playwright(self.proxy)
                if p_cfg:
                    context_kwargs["proxy"] = p_cfg

            try:
                self._context = self._browser.new_context(**context_kwargs)
                self._setup_page_routes(self._context)
                self._page = self._context.new_page()
                self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

                self._page.goto(self.PORTAL_URL, wait_until="domcontentloaded", timeout=25000)
                return True
            except Exception as e:
                logger.warning(f"Error al inicializar portal con nuevo proxy {self.proxy}: {e}")
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
                self._page = None
                self._context = None
                self._browser = None
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
                fuente_scraper="scraper_enacom",
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
                    logger.info(f"[{ani_clean}] Iniciando consulta en portal ENACOM (Intento #{intento}/{self.max_retries})...")
                    # 1. Asegurar formulario limpio
                    if not page.url.startswith(self.PORTAL_URL):
                        page.goto(self.PORTAL_URL, wait_until="domcontentloaded", timeout=15000)

                    # 2. Capturar y resolver captcha en RAM (< 5ms vía DOM Canvas)
                    code = ""
                    for sub_intento in range(6):
                        try:
                            # Asegurar que la imagen del captcha esté completamente cargada en el DOM
                            page.wait_for_function(
                                "() => { const img = document.getElementById('imgCaptcha'); return img && img.complete && (img.naturalWidth > 50 || img.width > 50); }",
                                timeout=4000
                            )
                        except Exception:
                            page.reload(wait_until="domcontentloaded")
                            continue

                        b64_img = page.evaluate("""() => {
                            const img = document.getElementById('imgCaptcha');
                            if (!img) return '';
                            const canvas = document.createElement('canvas');
                            canvas.width = img.naturalWidth || img.width || 180;
                            canvas.height = img.naturalHeight || img.height || 50;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(img, 0, 0);
                            return canvas.toDataURL('image/png').split(',')[1] || '';
                        }""")

                        if not b64_img:
                            # Fallback seguro a screenshot si canvas está vacío
                            img_bytes = page.locator("#imgCaptcha").screenshot(timeout=3000)
                        else:
                            import base64
                            img_bytes = base64.b64decode(b64_img)

                        cand = self.solver.solve_bytes(img_bytes)
                        if len(cand) == 5:
                            code = cand
                            logger.info(f"[{ani_clean}] Captcha resuelto en memoria: '{code}' (Sub-intento #{sub_intento + 1}).")
                            break

                        # Si el OCR leyó menos o más de 5 caracteres, recargar imagen sin gastar intento de envío
                        page.evaluate("() => RecargarCaptcha()")
                        page.wait_for_timeout(200)

                    if len(code) != 5:
                        logger.warning(f"[{ani_clean}] No se logró resolver captcha de 5 caracteres tras 6 sub-intentos. Recargando página...")
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

                    # Completar campos
                    page.fill("#TxtNumero", ani_clean)
                    page.fill("#TxtCaptcha", code)
                    logger.info(f"[{ani_clean}] Enviando formulario con captcha '{code}'...")

                    # 4. Esperar respuesta POST del servidor de forma reactiva por evento
                    try:
                        with page.expect_response(
                            lambda r: "Numeracion.aspx" in r.url and r.request.method == "POST",
                            timeout=12000
                        ):
                            page.click("#BtnBuscar")
                    except Exception as e_post:
                        logger.debug(f"[{ani_clean}] Espera de evento POST finalizada ({e_post})")

                    # 5. Lectura atómica instantánea del resultado (1 sola llamada DOM evaluate por ciclo)
                    encontrado = False
                    orig = ""
                    act = ""
                    num_confirmado = ani_clean
                    for _ in range(30):
                        try:
                            dom_res = page.evaluate("""() => {
                                const num = document.getElementById('TxtRtaNumero')?.value?.trim() || '';
                                const actual = document.getElementById('TxtPrestadorActual')?.value?.trim() || '';
                                const orig = document.getElementById('TxtPrestadorOriginal')?.value?.trim() || '';
                                const msgEl = document.getElementById('DivMensaje');
                                const msg = (msgEl && msgEl.style.display !== 'none' && msgEl.offsetWidth > 0) ? (msgEl.innerText || msgEl.textContent).trim() : '';
                                return { num, actual, orig, msg };
                            }""")
                        except Exception:
                            dom_res = {}

                        num_c = dom_res.get("num", "")
                        actual_c = dom_res.get("actual", "")
                        orig_c = dom_res.get("orig", "")
                        msg_txt = dom_res.get("msg", "")

                        if (num_c == ani_clean or not num_c) and (actual_c or orig_c):
                            encontrado = True
                            orig = orig_c
                            act = actual_c
                            num_confirmado = num_c or ani_clean
                            break

                        if msg_txt:
                            if "incorrecto" in msg_txt.lower():
                                break
                            if any(kw in msg_txt.lower() for kw in ["no se encontr", "no asignado", "no existe", "inexistente", "no posee"]):
                                return ScrapeResult(
                                    ani=ani_clean,
                                    status=StatusScraping.SIN_COINCIDENCIA,
                                    fuente_scraper="scraper_enacom",
                                    detalles={
                                        "operador_original": None,
                                        "razon_social": None,
                                        "razon_social_original": None,
                                        "es_portado": None,
                                        "intentos": intento,
                                    },
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
                                    fuente_scraper="scraper_enacom",
                                    detalles={
                                        "operador_original": None,
                                        "razon_social": None,
                                        "razon_social_original": None,
                                        "es_portado": None,
                                        "intentos": intento,
                                        "error_tipo": err_tipo,
                                    },
                                    descripcion=f"Límite alcanzado: {msg_txt} (Requiere rotar de IP/Proxy)"
                                )
                            if "seguridad" in msg_txt.lower() or "recaptcha" in msg_txt.lower():
                                logger.warning(f"ENACOM Bloqueo reCAPTCHA v3 detectado: '{msg_txt}'")
                                return ScrapeResult(
                                    ani=ani_clean,
                                    status=StatusScraping.ERROR,
                                    fuente_scraper="scraper_enacom",
                                    detalles={
                                        "operador_original": None,
                                        "razon_social": None,
                                        "razon_social_original": None,
                                        "es_portado": None,
                                        "intentos": intento,
                                        "error_tipo": "seguridad_recaptcha",
                                    },
                                    descripcion=f"Fallo de seguridad reCAPTCHA v3: {msg_txt} (Requiere rotar de IP/Proxy)"
                                )

                        page.wait_for_timeout(40)

                    if encontrado:

                        es_portado = (orig.lower() != act.lower())
                        op_norm = self._normalizar_operador(act)
                        op_orig_norm = self._normalizar_operador(orig)

                        if es_portado:
                            detalles = {
                                "operador_original": op_orig_norm,
                                "razon_social": act,
                                "razon_social_original": orig,
                                "es_portado": True,
                                "intentos": intento,
                            }
                            desc = (
                                f"ENACOM Oficial: Línea {num_confirmado} asignada a {orig} "
                                f"y operada actualmente por {act} (PORTABILIDAD CONFIRMADA)"
                            )
                        else:
                            detalles = {
                                "operador_original": None,
                                "razon_social": act,
                                "razon_social_original": None,
                                "es_portado": False,
                                "intentos": intento,
                            }
                            desc = (
                                f"ENACOM Oficial: Línea {num_confirmado} asignada a {orig} "
                                f"y operada actualmente por {act}"
                            )

                        return ScrapeResult(
                            ani=ani_clean,
                            status=StatusScraping.COINCIDENCIA,
                            fuente_scraper="scraper_enacom",
                            operador=op_norm,
                            detalles=detalles,
                            descripcion=desc
                        )

                    # Si el captcha falló, recargar para siguiente intento
                    page.evaluate("() => RecargarCaptcha()")
                    page.wait_for_timeout(300)

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
                fuente_scraper="scraper_enacom",
                detalles={
                    "operador_original": None,
                    "razon_social": None,
                    "razon_social_original": None,
                    "es_portado": None,
                    "intentos": self.max_retries,
                },
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
