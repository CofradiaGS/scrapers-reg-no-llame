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
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

from core.domain.entities import Linea, ScrapeResult, StatusScraping
from core.domain.exceptions import ScraperTransientError
from adapters.scrapers.base_scraper import BaseScraperAdapter
from adapters.scrapers.enacom_web.captcha_solver import EnacomCaptchaSolver

logger = logging.getLogger("ScraperEnacomWeb")

from adapters.scrapers.enacom_web.ip_tracker import EnacomIPTracker

_ip_tracker_singleton = EnacomIPTracker()

def es_ip_bloqueada(ip: str) -> bool:
    """Verifica si la IP está registrada en la lista negra diaria o en enfriamiento por haber alcanzado el límite."""
    apta, _, _ = _ip_tracker_singleton.evaluar_ip(ip)
    return not apta

def registrar_ip_bloqueada(ip: str, motivo: str):
    """Registra una IP en la lista negra diaria con expiración automática a medianoche."""
    _ip_tracker_singleton.registrar_bloqueo_100(ip, motivo)


class EnacomWebAdapter(BaseScraperAdapter):
    """
    Adaptador de infraestructura para el portal oficial de Numeración de ENACOM.
    Consulta el estado oficial de asignación y portabilidad numérica activa de una línea.
    """

    PORTAL_URL = "https://numeracion.enacom.gob.ar/Numeracion.aspx"
    MAX_CONSULTAS_POR_IP: int = 25

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
        self.ip_tracker = _ip_tracker_singleton
        self._lock = threading.RLock()

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._consultas_realizadas = 0
        self._consultas_con_esta_ip = 0
        self._pagina_recien_cargada = False
        self._ultimo_captcha_bytes: Optional[bytes] = None
        self._ultima_consulta_timestamp: float = 0.0
        self._ip_actual: str = "Desconocida"

        self._static_cache_dir = Path(__file__).resolve().parents[3] / "data" / "enacom_static_cache"
        self._ensure_static_assets()

    def _setup_captcha_listener(self, page: Page) -> None:
        """No-op: Eliminado para prevenir MemoryError en Playwright y asegurar sincronía determinista."""
        pass

    def _ensure_static_assets(self) -> None:
        """Asegura que el directorio de cache estático exista."""
        try:
            self._static_cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"No se pudo inicializar directorio de cache estático ({e}).")

    def _setup_page_routes(self, target: Any) -> None:
        """
        Configura la interceptación y ruteo de red ultra-optimizado a nivel de BrowserContext:
        1. Bloqueo total (abort) de hojas de estilo (.css, fonts, media). Chromium headless no requiere render visual.
        2. Bloqueo total de imágenes superfluas (logos, favicons, banners), permitiendo exclusivamente Captcha.aspx.
        3. Bloqueo de rastreadores externos y analítica (google-analytics, doubleclick, etc.).
        4. Tráfico transaccional esencial (portal ENACOM, Captcha.aspx, Google reCAPTCHA v3 y jQuery) permitido de forma nativa.
        """
        def intercept_route(route):
            try:
                url = route.request.url.lower()
                res_type = route.request.resource_type

                # 1. Bloqueo de estilos, fuentes y multimedia
                if res_type in ("stylesheet", "font", "media"):
                    return route.abort()

                # 2. Bloqueo de imágenes excepto el Captcha oficial
                if res_type == "image":
                    if "captcha.aspx" not in url:
                        return route.abort()
                    return route.continue_()

                # 3. Bloqueo de scripts de soporte superfluos y rastreadores
                if any(k in url for k in [
                    "popper", "bootstrap", "font-awesome", "favicon",
                    "google-analytics", "doubleclick", "googletagmanager", "stats", "beacon"
                ]):
                    return route.abort()

                # 4. Continuar tráfico transaccional esencial
                return route.continue_()
            except Exception:
                try:
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
    def _formatear_proxy_playwright(proxy_url: Optional[str], force_new_session: bool = False) -> Optional[Dict[str, str]]:
        """
        Formatea una URL de proxy para Playwright soportando credenciales embebidas
        (ej: http://user:pass@gate.smartproxy.com:7000 -> server, username, password).
        Si force_new_session es True y se detecta Smartproxy, inyecta un identificador de sesión único
        (_session-xxxxxxxx) para garantizar una IP residencial fresca y limpia desde la red rotativa.
        """
        if not proxy_url:
            return None
        import random
        import string
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        if parsed.username and parsed.password:
            scheme = parsed.scheme or "http"
            server = f"{scheme}://{parsed.hostname}:{parsed.port}"
            username = parsed.username
            if force_new_session and "smartproxy" in (parsed.hostname or "").lower():
                # En Smartproxy, el carácter '_' es el delimitador oficial de parámetros
                # (ej: smart-user_area-AR_session-xxxx). Por tanto, el token de sesión
                # DEBE ser estrictamente alfanumérico sin guiones bajos para que el gateway
                # no lo fragmente y asigne una IP residencial única por cada rotación.
                rand_token = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
                base_user = username.split("_session-")[0]
                username = f"{base_user}_session-{rand_token}"
            return {
                "server": server,
                "username": username,
                "password": parsed.password,
            }
        return {"server": proxy_url}

    def _obtener_captcha_nativo(self, page: Page, timeout_ms: int = 5000) -> bytes:
        """
        Devuelve directamente en memoria el captcha servido por ENACOM mediante extracción de canvas DOM.
        """

        try:
            b64_img = page.evaluate("""async () => {
                const img = document.getElementById('imgCaptcha');
                if (!img) return '';
                if (img.decode) {
                    try { await img.decode(); } catch(e) {}
                }
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth || img.width || 180;
                canvas.height = img.naturalHeight || img.height || 50;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                return canvas.toDataURL('image/png').split(',')[1] || '';
            }""")
            if b64_img:
                import base64
                return base64.b64decode(b64_img)
        except Exception as e:
            logger.debug(f"Aviso lectura captcha nativo: {e}")

        try:
            return page.locator("#imgCaptcha").screenshot(timeout=3000)
        except Exception:
            return b""

    def _recargar_captcha_sincronico(self, page: Page, timeout_ms: int = 7000) -> bytes:
        """
        Solicita un captcha nuevo al servidor invocando la función oficial RecargarCaptcha()
        y esperando la llegada de la respuesta HTTP de red de Captcha.aspx.
        Garantiza sincronía estricta con la sesión ASP.NET antes de extraer la imagen.
        """
        if hasattr(self, "_ultimo_captcha_bytes"):
            self._ultimo_captcha_bytes = None
        try:
            with page.expect_response(
                lambda r: "captcha.aspx" in r.url.lower() and ("?v=" in r.url.lower() or "?d=" in r.url.lower()) and r.status == 200,
                timeout=timeout_ms
            ) as resp:
                page.evaluate("""() => {
                    if (typeof RecargarCaptcha === 'function') {
                        RecargarCaptcha();
                    } else {
                        const img = document.getElementById('imgCaptcha');
                        if (img) img.src = 'Captcha.aspx?d=' + Date.now();
                    }
                }""")
            if resp and resp.value:
                b = resp.value.body()
                if b and len(b) > 100:
                    if hasattr(self, "_ultimo_captcha_bytes"):
                        self._ultimo_captcha_bytes = b
                    return b
        except Exception as e:
            logger.debug(f"Aviso espera red recarga captcha: {e}")
        return self._obtener_captcha_nativo(page, timeout_ms)

    def _obtener_captcha_en_memoria(self, page: Page, timeout_ms: int = 5000, force_reload: bool = False) -> bytes:
        """
        Descarga o lee el captcha directamente en memoria dentro del navegador.
        Si force_reload es True, solicita nuevo captcha vía RecargarCaptcha().
        Si force_reload es False, lee la imagen nativa ya presente en memoria.
        """
        if force_reload:
            return self._recargar_captcha_sincronico(page, timeout_ms)
        return self._obtener_captcha_nativo(page, timeout_ms)

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
                p_cfg = self._formatear_proxy_playwright(self.proxy, force_new_session=True)
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
            p_cfg = self._formatear_proxy_playwright(self.proxy, force_new_session=True)
            if p_cfg:
                context_kwargs["proxy"] = p_cfg

        for intento_con in range(4):
            try:
                self._ultimo_captcha_bytes = None
                self._context = self._browser.new_context(**context_kwargs)
                self._setup_page_routes(self._context)
                self._page = self._context.new_page()
                self._setup_captcha_listener(self._page)
                self._page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

                # Detectar IP saliente real para trazabilidad y monitoreo
                try:
                    r_ip = self._page.request.get("http://api.ipify.org", timeout=4000)
                    self._ip_actual = r_ip.text().strip()
                except Exception:
                    try:
                        r_ip = self._page.request.get("http://icanhazip.com", timeout=3000)
                        self._ip_actual = r_ip.text().strip()
                    except Exception:
                        self._ip_actual = "Rotativa-Smartproxy"

                # Verificar estado de la IP con el IP Tracker centralizado
                apta, motivo_eval, espera_cd = self.ip_tracker.evaluar_ip(self._ip_actual)
                if not apta:
                    logger.warning(
                        f"⚠️ Smartproxy asignó IP no apta ({self._ip_actual} - {motivo_eval}). "
                        f"Rotando inmediatamente de sesión residencial sin consultar ENACOM (0 GB gastados)..."
                    )
                    try:
                        self._page.close()
                    except Exception:
                        pass
                    try:
                        self._context.close()
                    except Exception:
                        pass
                    self._page = None
                    self._context = None
                    if self.proxy:
                        p_cfg = self._formatear_proxy_playwright(self.proxy, force_new_session=True)
                        if p_cfg:
                            context_kwargs["proxy"] = p_cfg
                    continue

                if espera_cd > 0:
                    logger.debug(f"Pausa de seguridad anti-rate-limit en IP ({self._ip_actual}): {espera_cd}s...")
                    time.sleep(espera_cd)

                self._page.goto(self.PORTAL_URL, wait_until="domcontentloaded", timeout=15000)
                try:
                    self._page.wait_for_function(
                        "() => { const img = document.getElementById('imgCaptcha'); return img && img.complete && (img.naturalWidth > 50 || img.width > 50); }",
                        timeout=5000
                    )
                except Exception:
                    pass
                self._consultas_con_esta_ip = 0
                self._pagina_recien_cargada = True
                return self._page
            except Exception as e:
                err_str = str(e).lower()
                if any(w in err_str for w in ["closed", "shutting down", "target page, context or browser has been closed"]):
                    self._page = None
                    self._context = None
                    return None
                logger.warning(f"Aviso conexión con portal ENACOM (intento {intento_con + 1}/4): {e}")
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
                self._page = None
                self._context = None
                if intento_con == 3:
                    # En caso de agotarse los intentos con Smartproxy, reintentar reinicializando browser
                    self.close()
                    logger.error(f"Falla de conectividad persistente con Smartproxy hacia ENACOM: {e}")
                    return None
                if self.proxy:
                    p_cfg = self._formatear_proxy_playwright(self.proxy, force_new_session=True)
                    if p_cfg:
                        context_kwargs["proxy"] = p_cfg
        return self._page

    def invalidar_proxy(self) -> None:
        """Invalida inmediatamente el proxy actual y cierra el contexto de navegación para evitar reusar una IP bloqueada."""
        with self._lock:
            self.proxy = None
            self._ultimo_captcha_bytes = None
            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass
            self._context = None
            self._page = None

    def rotar_proxy(self, nuevo_proxy: Optional[str] = None) -> bool:
        """
        Rota el proxy residencial ágilmente (< 300ms) re-creando el BrowserContext con
        un nuevo session-id para obtener una IP limpia sin destruir el proceso Chromium.
        Ahorra más del 95% de tráfico de red y latencia comparado con reiniciar el navegador.
        """
        with self._lock:
            self._ultimo_captcha_bytes = None
            self._consultas_con_esta_ip = 0
            self._ip_actual = "Desconocida"
            if nuevo_proxy is not None:
                self.proxy = nuevo_proxy
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
            self._page = None
            self._context = None

            # Si el navegador sigue activo, recreará contexto con nueva sesión; sino reinicia
            if not self._browser or not self._browser.is_connected():
                self.close()
            page = self._ensure_browser()
            return page is not None



    def _normalizar_operador(self, nombre_raw: str) -> str:
        """
        Homologa las razones sociales regulatorias de ENACOM a marcas comerciales y prestadores oficiales.
        Cubre todo el ecosistema de telecomunicaciones de Argentina: telcos móviles, operadores de cable,
        telefonía fija, datos, cooperativas y prestadores regionales.
        """
        nom = (nombre_raw or "").upper().strip()
        if not nom or nom in ("DESCONOCIDO", "NONE", "S/D", "NULL"):
            return "Desconocido"

        # 1. Cooperativas Telefónicas y de Servicios Públicos (evaluar primero para evitar falsos positivos con 'TELEFONICA')
        if "COOPERATIVA" in nom or "COOP." in nom or "COOP " in nom:
            return nombre_raw.strip()

        # 2. Tríopolio Móvil / Telcos Principales
        if "AMX" in nom or "CLARO" in nom or "CTI" in nom:
            return "Claro"
        if any(k in nom for k in ["TELEFONICA", "MOVISTAR", "TMOVILES", "RADIOCOMUNICACIONES MOVILES", "CRM", "TUENTI"]):
            return "Movistar"
        if any(k in nom for k in ["TELECOM", "PERSONAL", "CABLEVISION", "FIBERTEL", "NEXTEL"]):
            return "Personal"

        # 3. Operadores Nacionales de Cable, Fibra, Telefonía Fija y Datos
        if "TELECENTRO" in nom:
            return "Telecentro"
        if "TELMEX" in nom or "TECHTEL" in nom:
            return "Telmex"
        if "IPLAN" in nom or "IPLACE" in nom:
            return "Iplan"
        if "METROTEL" in nom or "C.P.S. COMUNICACIONES" in nom or "CPS COMUNICACIONES" in nom:
            return "Metrotel"
        if "SUPERCANAL" in nom or "SUPER CANAL" in nom:
            return "Supercanal"
        if "GIGARED" in nom:
            return "Gigared"
        if "DIRECTV" in nom:
            return "DirecTV"
        if any(k in nom for k in ["CIRION", "LUMEN", "CENTURYLINK", "GLOBAL CROSSING"]):
            return "Cirion"
        if "CONVERGIA" in nom:
            return "Convergia"
        if "ANURA" in nom:
            return "Anura"
        if "ALVIS" in nom:
            return "Alvis"
        if "SION" in nom:
            return "Sion"
        if "CATEL" in nom or "IMOWI" in nom:
            return "iMOWI / Catel"
        if "CABLENET" in nom:
            return "Cablenet"
        if "CABLETEL" in nom:
            return "Cabletel"
        if "BROADBANDTECH" in nom:
            return "Broadbandtech"
        if "WILTEL" in nom:
            return "Wiltel"
        if "WAYCOM" in nom:
            return "Waycom"
        if "IP-TEL" in nom or "IPTEL" in nom:
            return "Ip-Tel"
        if "CITILAN" in nom:
            return "Citilan"
        if "AGUAS DEL COLORADO" in nom:
            return "Aguas del Colorado"
        if "MARANDU" in nom:
            return "Marandu"
        if "NEUTICS" in nom:
            return "Neutics"
        if "ECOM" in nom:
            return "Ecom Chaco"

        # 4. Cualquier otra razón social registrada en ENACOM: remover sufijos societarios
        import re
        clean = re.sub(r'\s+(S\.?A\.?U?|S\.?R\.?L\.?|S\.?A\.?S\.?|INC\.?|SUCURSAL ARGENTINA)$', '', nom).strip().title()
        return clean if clean else nombre_raw.strip()

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
            # 0. Rotación preventiva por IP (hasta MAX_CONSULTAS_POR_IP consultas limpias por IP)
            if self._consultas_con_esta_ip >= self.MAX_CONSULTAS_POR_IP:
                logger.info(
                    f"🔄 Rotación proactiva de IP alcanzada ({self._consultas_con_esta_ip}/{self.MAX_CONSULTAS_POR_IP} en {self._ip_actual}). "
                    f"Rotando a nueva IP residencial para preservar cuota y salud de IP..."
                )
                if not self.rotar_proxy():
                    self.close()
                    page = self._ensure_browser()
                else:
                    page = self._page
            else:
                page = self._ensure_browser()

            if page is None:
                return ScrapeResult(
                    ani=ani_clean,
                    status=StatusScraping.ERROR,
                    fuente_scraper="scraper_enacom",
                    descripcion="Worker detenido o contexto de navegación no disponible"
                )

            # Evaluar estado centralizado de la IP asignada (100 consultas/día y pacing multi-worker)
            es_apta, motivo_eval, pausa_ip = self.ip_tracker.evaluar_ip(self._ip_actual)
            if not es_apta:
                logger.warning(f"⚠️ [{ani_clean}] {motivo_eval}. Rotando proactivamente a nueva IP limpia...")
                if self.rotar_proxy():
                    page = self._page
                    es_apta, motivo_eval, pausa_ip = self.ip_tracker.evaluar_ip(self._ip_actual)

            if pausa_ip > 0:
                logger.debug(f"Pausa de seguridad anti-rate-limit en IP ({self._ip_actual}): {pausa_ip}s...")
                time.sleep(pausa_ip)

            self._consultas_realizadas += 1
            self._consultas_con_esta_ip += 1

            for intento in range(1, self.max_retries + 1):
                try:
                    if intento == 1:
                        logger.info(f"🔍 [{ani_clean}] Buscando en portal ENACOM (IP: {self._ip_actual}, #{self._consultas_con_esta_ip}/{self.MAX_CONSULTAS_POR_IP})...")
                    else:
                        logger.info(f"🔄 [{ani_clean}] Reintentando en portal ENACOM (#{intento}/{self.max_retries}, IP: {self._ip_actual})...")

                    # 1. Asegurar formulario limpio y sesión activa sin recargar innecesariamente el DOM
                    if not page.url.startswith(self.PORTAL_URL):
                        page.goto(self.PORTAL_URL, wait_until="domcontentloaded", timeout=15000)
                        self._pagina_recien_cargada = True
                    self._ultima_consulta_timestamp = time.time()

                    # 2. Capturar y resolver captcha en RAM con sincronización estricta de sesión ASP.NET
                    code = ""
                    for sub_intento in range(3):
                        if sub_intento == 0 and self._pagina_recien_cargada:
                            img_bytes = self._obtener_captcha_nativo(page)
                        else:
                            img_bytes = self._recargar_captcha_sincronico(page)
                        self._pagina_recien_cargada = False

                        if not img_bytes:
                            continue

                        cand = self.solver.solve_bytes(img_bytes)
                        if len(cand) == 5:
                            code = cand
                            logger.debug(f"[{ani_clean}] Captcha resuelto en memoria: '{code}' (Sub-intento #{sub_intento + 1}).")
                            break

                    if len(code) != 5:
                        logger.warning(f"[{ani_clean}] No se logró resolver captcha de 5 caracteres tras 3 sub-intentos. Recargando página...")
                        self._ultimo_captcha_bytes = None
                        page.reload(wait_until="domcontentloaded")
                        continue

                    # 3. Limpiar inputs previos y completar campos de forma atómica (< 1ms, zero-CDP roundtrips)
                    page.evaluate(f"""() => {{
                        const ids = ['TxtRtaNumero', 'TxtPrestadorOriginal', 'TxtPrestadorActual'];
                        ids.forEach(id => {{
                            const el = document.getElementById(id);
                            if (el) el.value = '';
                        }});
                        const msg = document.getElementById('DivMensaje');
                        if (msg) {{
                            msg.style.display = 'none';
                            msg.innerText = '';
                            msg.textContent = '';
                            msg.innerHTML = '';
                        }}
                        const elNum = document.getElementById('TxtNumero');
                        if (elNum) elNum.value = '{ani_clean}';
                        const elCap = document.getElementById('TxtCaptcha');
                        if (elCap) elCap.value = '{code}';
                    }}""")

                    # Generar token reCAPTCHA v3 fresco directamente y marcar recaptchaValidado = true
                    # Esto garantiza envío sincronizado y evita fallos de token expirado o retrasos async
                    try:
                        page.evaluate("""async () => {
                            return new Promise((resolve) => {
                                window.recaptchaValidado = false;
                                if (document.getElementById('hfRecaptchaToken')) {
                                    document.getElementById('hfRecaptchaToken').value = '';
                                }
                                if (typeof grecaptcha === 'undefined') {
                                    resolve('');
                                    return;
                                }
                                grecaptcha.ready(function () {
                                    grecaptcha.execute('6Lc7ETMaAAAAABEtagDEGehZ_xpYTo0gQSibJbxC', { action: 'buscar' })
                                        .then(function (token) {
                                            if (token) {
                                                const hf = document.getElementById('hfRecaptchaToken');
                                                if (hf) hf.value = token;
                                                window.recaptchaValidado = true;
                                            }
                                            resolve(token || '');
                                        })
                                        .catch(() => resolve(''));
                                });
                            });
                        }""")
                    except Exception as e_tok:
                        logger.debug(f"[{ani_clean}] Aviso obtención token reCAPTCHA: {e_tok}")

                    logger.debug(f"[{ani_clean}] Enviando formulario con captcha '{code}'...")

                    # 4. Esperar respuesta POST del servidor de forma reactiva por evento (< 1s)
                    post_html = ""
                    self._ultimo_captcha_bytes = None
                    try:
                        with page.expect_response(
                            lambda r: "Numeracion.aspx" in r.url and r.request.method == "POST",
                            timeout=20000
                        ) as post_resp_info:
                            page.click("#BtnBuscar")
                        if post_resp_info and post_resp_info.value:
                            post_html = post_resp_info.value.text()
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        try:
                            page.wait_for_function(
                                "() => { const img = document.getElementById('imgCaptcha'); return img && img.complete; }",
                                timeout=3000
                            )
                        except Exception:
                            pass
                    except Exception as e_post:
                        logger.debug(f"[{ani_clean}] Espera de evento POST finalizada ({e_post})")

                    # 5. Lectura atómica ultra-rápida (HTML directo < 1ms, zero-CDP overhead)
                    encontrado = False
                    orig = ""
                    act = ""
                    num_confirmado = ani_clean
                    msg_txt = ""

                    if post_html:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(post_html, "html.parser")
                        actual_el = soup.find(id="TxtPrestadorActual")
                        orig_el = soup.find(id="TxtPrestadorOriginal")
                        num_el = soup.find(id="TxtRtaNumero")
                        msg_el = soup.find(id="DivMensaje")

                        actual_c = (actual_el.get("value") or "").strip() if actual_el and actual_el.name == "input" else ""
                        orig_c = (orig_el.get("value") or "").strip() if orig_el and orig_el.name == "input" else ""
                        num_c = (num_el.get("value") or "").strip() if num_el and num_el.name == "input" else ""
                        msg_txt = msg_el.text.strip() if msg_el else ""

                        if (num_c == ani_clean or not num_c) and (actual_c or orig_c):
                            encontrado = True
                            orig = orig_c
                            act = actual_c
                            num_confirmado = num_c or ani_clean

                    if not encontrado and not msg_txt:
                        # Fallback seguro a sondeo DOM solo si no vino post_html
                        for _ in range(15):
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
                                break
                            page.wait_for_timeout(30)

                    # Evaluación inmediata de mensajes de respuesta
                    if msg_txt:
                        if any(kw in msg_txt.lower() for kw in ["no se encontr", "no asignado", "no existe", "inexistente", "no posee"]):
                            self.ip_tracker.registrar_consulta_exitosa(self._ip_actual)
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
                        if any(kw in msg_txt.lower() for kw in ["límite", "limite", "diarias", "minuto", "seguridad", "recaptcha"]):
                            logger.warning(
                                f"⚠️ [{ani_clean}] ENACOM Bloqueo/Rate Limit en IP {self._ip_actual}: '{msg_txt}'. "
                                f"Registrando en tracker y rotando a nueva IP residencial (Intento #{intento}/{self.max_retries})..."
                            )
                            if any(k in msg_txt.lower() for k in ["diarias", "100"]):
                                self.ip_tracker.registrar_bloqueo_100(self._ip_actual, msg_txt)
                            elif any(k in msg_txt.lower() for k in ["minuto", "5 consultas"]):
                                self.ip_tracker.registrar_rate_limit_minuto(self._ip_actual, segundos=65.0, motivo=msg_txt)
                            else:
                                self.ip_tracker.registrar_rate_limit_minuto(self._ip_actual, segundos=35.0, motivo=msg_txt)

                            if intento < self.max_retries:
                                rotado_ok = self.rotar_proxy()
                                if rotado_ok and self._page:
                                    page = self._page
                                else:
                                    self.close()
                                    page = self._ensure_browser()
                                continue
                            else:
                                logger.error(
                                    f"❌ [{ani_clean}] ENACOM Rate Limit persistente en IP {self._ip_actual} tras {self.max_retries} intentos: '{msg_txt}'"
                                )
                                raise ScraperTransientError(
                                    f"ENACOM Rate Limit de IP ({msg_txt}) en {self._ip_actual} persistente tras {self.max_retries} rotaciones para {ani_clean}"
                                )
                        if "incorrecto" in msg_txt.lower():
                            logger.info(f"🔄 [{ani_clean}] Captcha rechazado por portal: '{msg_txt}' (OCR='{code}'). Reintentando con captcha fresco sincronizado...")
                            try:
                                rej_dir = os.path.abspath("data/rejected_captchas")
                                os.makedirs(rej_dir, exist_ok=True)
                                if img_bytes:
                                    with open(os.path.join(rej_dir, f"{ani_clean}_{code}.png"), "wb") as f_rej:
                                        f_rej.write(img_bytes)
                            except Exception:
                                pass
                            self._ultimo_captcha_bytes = None
                            self._recargar_captcha_sincronico(page)
                            continue

                    if encontrado:
                        self.ip_tracker.registrar_consulta_exitosa(self._ip_actual)

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

                except ScraperTransientError:
                    raise
                except Exception as exc:
                    if any(w in str(exc).lower() for w in ["closed", "target page, context or browser has been closed", "session closed"]):
                        logger.debug(f"Página cerrada durante detención de worker para ANI {ani_clean}")
                        break
                    logger.warning(f"Excepción en intento {intento} para ANI {ani_clean}: {exc}")
                    try:
                        page.reload(wait_until="domcontentloaded")
                        self._pagina_recien_cargada = True
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
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
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
