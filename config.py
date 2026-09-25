import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

IRIS_USER = os.getenv("IRIS_USER", "enorozco")
IRIS_PASS = os.getenv("IRIS_PASS", "TucaChorron2026.")
IRIS_LOGIN_URL = os.getenv("IRIS_LOGIN_URL", "http://iris.tmoviles.com.ar/workspace/faces/jsf/security/login.xhtml")
IRIS_LOGOUT_URL = os.getenv("IRIS_LOGOUT_URL", "http://iris.tmoviles.com.ar/workspace/faces/jsf/security/logout.xhtml")
IRIS_WORKSPACE_URL = os.getenv("IRIS_WORKSPACE_URL", "http://iris.tmoviles.com.ar/workspace/faces/jsf/workspace/workspace.xhtml")

HEADLESS = os.getenv("HEADLESS", "False").strip().lower() in ("true", "1", "yes")
DELAY_MIN = float(os.getenv("DELAY_MIN", "2.0"))
DELAY_MAX = float(os.getenv("DELAY_MAX", "4.5"))
DEFAULT_TIMEOUT = int(os.getenv("DEFAULT_TIMEOUT", "30000"))  # 30s

# VPS Central MySQL Configuration
VPS_DBHOST = os.getenv("VPS_DBHOST", "172.16.20.15")
VPS_DBPORT = int(os.getenv("VPS_DBPORT", "3306"))
VPS_DBUSER = os.getenv("VPS_DBUSER", "ignacio_acuna")
VPS_DBPASS = os.getenv("VPS_DBPASS", "goodTimes2026*")
VPS_DBNAME = os.getenv("VPS_DBNAME", "bases")
VPS_DB_TABLE = os.getenv("VPS_DB_TABLE", "queue_registro_no_llame")
VPS_DB_USE_PURE = os.getenv("VPS_DB_USE_PURE", "True").strip().lower() in ("true", "1", "yes")

# Optimización de I/O y Reducción Masiva de Binlog (Staging Buffer en RAM)
BUFFER_FLUSH_SIZE = int(os.getenv("BUFFER_FLUSH_SIZE", "500"))
BUFFER_MAX_DELAY = float(os.getenv("BUFFER_MAX_DELAY", "300.0"))
HEARTBEAT_INTERVAL_SEC = float(os.getenv("HEARTBEAT_INTERVAL_SEC", "180.0"))
STATS_FLUSH_INTERVAL_SEC = float(os.getenv("STATS_FLUSH_INTERVAL_SEC", "600.0"))
WATCHDOG_SWEEP_INTERVAL_SEC = float(os.getenv("WATCHDOG_SWEEP_INTERVAL_SEC", "600.0"))
EMPTY_QUEUE_PAUSE_MAX_SEC = float(os.getenv("EMPTY_QUEUE_PAUSE_MAX_SEC", "900.0"))

# Configuración Modular de Cola (registro_no_llame / cola_automatizacion)
import socket
QUEUE_TYPE = os.getenv("QUEUE_TYPE", "registro_no_llame").strip().lower()
COLA_AUTO_ID = os.getenv("COLA_AUTO_ID", "iris_scraper").strip()
WORKER_PC_ID = os.getenv("WORKER_PC_ID", socket.gethostname()).strip()

# Política de Horario Comercial Oficial IRIS (Movistar Argentina)
HORARIO_COMERCIAL_ACTIVO = os.getenv("HORARIO_COMERCIAL_ACTIVO", "True").strip().lower() in ("true", "1", "yes")
HORARIO_COMERCIAL_INICIO_LV = os.getenv("HORARIO_COMERCIAL_INICIO_LV", "08:00")
HORARIO_COMERCIAL_FIN_LV = os.getenv("HORARIO_COMERCIAL_FIN_LV", "21:00")
HORARIO_COMERCIAL_INICIO_SAB = os.getenv("HORARIO_COMERCIAL_INICIO_SAB", "08:00")
HORARIO_COMERCIAL_FIN_SAB = os.getenv("HORARIO_COMERCIAL_FIN_SAB", "13:00")
HORARIO_COMERCIAL_TIMEZONE = os.getenv("HORARIO_COMERCIAL_TIMEZONE", "America/Argentina/Buenos_Aires")

# Configuración Scraper Cobro Express (Claro Telefonía)
COBRO_EXPRESS_URL = os.getenv("COBRO_EXPRESS_URL", "https://pagosce.cobroexpress.com.ar")
COBRO_EXPRESS_PROXY = os.getenv("COBRO_EXPRESS_PROXY", None)
COBRO_EXPRESS_TIMEOUT = int(os.getenv("COBRO_EXPRESS_TIMEOUT", "20"))
COBRO_EXPRESS_DELAY_MIN = float(os.getenv("COBRO_EXPRESS_DELAY_MIN", "0.2"))
COBRO_EXPRESS_DELAY_MAX = float(os.getenv("COBRO_EXPRESS_DELAY_MAX", "0.6"))

# Configuración Scraper CuitOnline (Enriquecimiento Fiscal / CUIT)
CUITONLINE_URL = os.getenv("CUITONLINE_URL", "https://www.cuitonline.com")
CUITONLINE_TIMEOUT = int(os.getenv("CUITONLINE_TIMEOUT", "15"))
CUITONLINE_DELAY_MIN = float(os.getenv("CUITONLINE_DELAY_MIN", "0.2"))
CUITONLINE_DELAY_MAX = float(os.getenv("CUITONLINE_DELAY_MAX", "0.6"))


def _detectar_tor_path() -> str:
    env_val = os.getenv("TOR_PATH")
    if env_val and os.path.exists(env_val):
        return env_val
    user_home = Path.home()
    user_profile = Path(os.getenv("USERPROFILE", str(user_home)))
    candidatos = [
        Path(__file__).parent / "tor" / "tor.exe",
        Path(__file__).parent / "tor" / "Tor" / "tor.exe",
        Path(__file__).parent / "Tor" / "tor.exe",
        user_home / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
        user_profile / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
        Path(os.getenv("PROGRAMFILES", r"C:\Program Files")) / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
        Path(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Tor Browser" / "Browser" / "TorBrowser" / "Tor" / "tor.exe",
    ]
    for c in candidatos:
        if c.exists():
            return str(c.resolve())
    import shutil
    w = shutil.which("tor")
    return w or env_val or str(candidatos[0])

def _detectar_geoip_path(nombre: str = "geoip") -> str:
    env_key = f"TOR_{nombre.upper()}_PATH"
    env_val = os.getenv(env_key)
    if env_val and os.path.exists(env_val):
        return env_val
    user_home = Path.home()
    user_profile = Path(os.getenv("USERPROFILE", str(user_home)))
    candidatos = [
        Path(__file__).parent / "tor" / "data" / nombre,
        Path(__file__).parent / "tor" / "Data" / "Tor" / nombre,
        user_home / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Data" / "Tor" / nombre,
        user_profile / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Data" / "Tor" / nombre,
        Path(os.getenv("LOCALAPPDATA", "")) / "Tor Browser" / "Browser" / "TorBrowser" / "Data" / "Tor" / nombre,
    ]
    for c in candidatos:
        if c.exists():
            return str(c.resolve())
    return env_val or str(candidatos[0])

# Configuración de Tor Stream Isolation & Proxies
TOR_ENABLED = os.getenv("TOR_ENABLED", "True").strip().lower() in ("true", "1", "yes")
TOR_STREAM_ISOLATION = os.getenv("TOR_STREAM_ISOLATION", "True").strip().lower() in ("true", "1", "yes")
TOR_PATH = _detectar_tor_path()
TOR_SOCKS_PORT_BASE = int(os.getenv("TOR_SOCKS_PORT_BASE", os.getenv("TOR_SOCKS_PORT", "9050")))
TOR_CONTROL_PORT_BASE = int(os.getenv("TOR_CONTROL_PORT_BASE", os.getenv("TOR_CONTROL_PORT", "9051")))
TOR_CONTROL_PASSWORD = os.getenv("TOR_CONTROL_PASSWORD", "")
TOR_ROTATE_EVERY = int(os.getenv("TOR_ROTATE_EVERY", "19"))
TOR_GEOIP_PATH = _detectar_geoip_path("geoip")
TOR_GEOIPV6_PATH = _detectar_geoip_path("geoip6")

# Configuración de Pool de Proxies Públicos Rotativos (Alta Velocidad)
PROXY_POOL_ENABLED = os.getenv("PROXY_POOL_ENABLED", "False").strip().lower() in ("true", "1", "yes")
PROXY_POOL_TIMEOUT = float(os.getenv("PROXY_POOL_TIMEOUT", "2.5"))
PROXY_POOL_MAX_RETRIES = int(os.getenv("PROXY_POOL_MAX_RETRIES", "3"))
PROXY_POOL_VALIDATION_WORKERS = int(os.getenv("PROXY_POOL_VALIDATION_WORKERS", "35"))
PROXY_POOL_CACHE_FILE = os.getenv("PROXY_POOL_CACHE_FILE", os.path.join(os.getcwd(), "tor_data", "live_proxies.txt"))
PROXY_POOL_REFRESH_INTERVAL = int(os.getenv("PROXY_POOL_REFRESH_INTERVAL", "600"))  # 10 min


