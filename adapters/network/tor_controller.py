# -*- coding: utf-8 -*-
"""
Adaptador de Infraestructura: Controlador de Tor (TorController)
Administra instancias locales del kernel de Tor (Multi-Instance) para rotación de IPs,
enrutamiento SOCKS5h y evasión de límites de tasa (HTTP 429 Too Many Requests).
Inspirado en la arquitectura probada de gs-scrap-cobro-express.
"""
import os
import time
import uuid
import socket
import logging
import subprocess
from typing import Optional

import requests

import config

logger = logging.getLogger("TorController")


class TorController:
    """
    Controlador de instancia individual de Tor para rotación de IP y proxy SOCKS5h.
    Soporta operación mono-instancia y multi-instancia particionada por worker_slot.
    """

    def __init__(
        self,
        socks_port: Optional[int] = None,
        control_port: Optional[int] = None,
        password: Optional[str] = None,
        tor_path: Optional[str] = None,
        data_dir: Optional[str] = None,
        is_owner: bool = True
    ):
        self.socks_port = int(socks_port or config.TOR_SOCKS_PORT_BASE)
        self.control_port = int(control_port or config.TOR_CONTROL_PORT_BASE)
        self.password = password or config.TOR_CONTROL_PASSWORD
        self.tor_path = tor_path or config.TOR_PATH
        self.is_owner = is_owner
        # Usar tor_data/ raíz cuando hay torrc (el DataDirectory del torrc apunta ahí)
        # Usar tor_data/port_NNNN solo si no hay torrc (multi-instancia sin torrc)
        torrc_path = os.path.join(os.getcwd(), "torrc")
        default_dir = os.path.join(os.getcwd(), "tor_data") if os.path.exists(torrc_path) else os.path.join(os.getcwd(), "tor_data", f"port_{self.socks_port}")
        self.data_dir = os.path.abspath(data_dir or default_dir)
        self._process: Optional[subprocess.Popen] = None

    def get_proxy_url(self) -> str:
        """Devuelve la URL del proxy SOCKS5h de Tor con resolución remota de DNS."""
        return f"socks5h://127.0.0.1:{self.socks_port}"

    def get_isolated_proxy_url(self, session_id: Optional[str] = None) -> str:
        """
        Devuelve la URL del proxy SOCKS5h con credenciales aleatorias o personalizadas.
        Bajo la directiva 'IsolateSOCKSAuth' de Tor, cada credencial fuerza la asignación
        de un circuito de salida y una IP pública totalmente independiente.
        """
        ident = session_id or uuid.uuid4().hex[:12]
        return f"socks5h://{ident}:tor@127.0.0.1:{self.socks_port}"

    def is_port_open(self, port: int) -> bool:
        """Comprueba si un socket local responde rápidamente."""
        s = socket.socket()
        s.settimeout(0.8)
        try:
            res = s.connect_ex(("127.0.0.1", port))
            return res == 0
        except Exception:
            return False
        finally:
            s.close()

    def is_running(self) -> bool:
        """Verifica si Tor está respondiendo en el puerto de control y autentica."""
        if not self.is_port_open(self.control_port):
            return False
        try:
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as c:
                c.authenticate(password=self.password)
                return True
        except Exception:
            return False

    def is_alive(self) -> bool:
        """Alias retrocompatible para is_running()."""
        return self.is_running()

    def get_bootstrap_status(self) -> tuple[bool, str]:
        """Comprueba conectividad con el ControlPort y devuelve (is_ready_100, phase_string)."""
        if not self.is_port_open(self.control_port):
            return False, "ControlPort cerrado"
        try:
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as c:
                c.authenticate(password=self.password)
                info = c.get_info("status/bootstrap-phase", default="")
                is_100 = "PROGRESS=100" in info
                return is_100, info
        except Exception as e:
            return False, str(e)

    def is_bootstrapped(self) -> bool:
        """Comprueba si el circuito de Tor completó el bootstrap."""
        ready, _ = self.get_bootstrap_status()
        return ready

    def ensure_running(self, timeout_sec: int = 60) -> bool:
        """
        Garantiza que Tor esté 100% operativo en los puertos SOCKS y Control.
        Si ya está activo, retorna True de inmediato.
        Si no está activo y es propietario, lo inicia. Si no es propietario, espera a que esté listo.
        """
        ready, _ = self.get_bootstrap_status()
        if ready and self.is_port_open(self.socks_port):
            return True

        if self.is_owner:
            return self.start(timeout_sec=timeout_sec)

        # Si no es propietario, espera a que el proceso maestro lo deje listo
        t0 = time.time()
        logger.info(f"Esperando a que el daemon Tor externo en SOCKS {self.socks_port} esté listo...")
        while time.time() - t0 < timeout_sec:
            if self.is_port_open(self.socks_port):
                ready, _ = self.get_bootstrap_status()
                if ready:
                    return True
            time.sleep(1.0)
        logger.warning(f"Daemon Tor externo no respondió en {timeout_sec}s.")
        return False

    def start(self, timeout_sec: int = 600) -> bool:
        """
        Inicia la instancia de Tor o reutiliza una existente en los puertos configurados.
        Retorna True si el puerto SOCKS y de control quedan operativos y con bootstrap 100%.
        Usa una conexión stem persistente para evitar el ruido de SocketClosed en el loop.
        """
        # Si ya está corriendo y bootstrapped, reutilizar
        ready, phase = self.get_bootstrap_status()
        if ready:
            logger.info(f"Tor ya se encuentra activo y listo en SOCKS {self.socks_port} y Control {self.control_port}.")
            return True

        if not os.path.exists(self.tor_path):
            logger.warning(
                f"No se encontró el ejecutable de Tor en '{self.tor_path}'. "
                "Verifique la ruta en TOR_PATH o instale Tor Browser / Tor Expert Bundle."
            )
            return False

        if not self.is_port_open(self.control_port):
            logger.info(
                f"Iniciando instancia Tor (SOCKS: {self.socks_port}, Control: {self.control_port}, "
                f"DataDir: '{self.data_dir}')..."
            )
            try:
                os.makedirs(self.data_dir, exist_ok=True)
                runtime_torrc = os.path.join(self.data_dir, "torrc_runtime")
                with open(runtime_torrc, "w", encoding="utf-8") as f:
                    f.write(f"DataDirectory {self.data_dir}\n")
                    if config.TOR_GEOIP_PATH and os.path.exists(config.TOR_GEOIP_PATH):
                        f.write(f"GeoIPFile {config.TOR_GEOIP_PATH}\n")
                    if config.TOR_GEOIPV6_PATH and os.path.exists(config.TOR_GEOIPV6_PATH):
                        f.write(f"GeoIPv6File {config.TOR_GEOIPV6_PATH}\n")
                    f.write(f"SocksPort {self.socks_port} IsolateSOCKSAuth\n")
                    f.write(f"ControlPort {self.control_port}\n")
                    f.write("CookieAuthentication 0\n")
                    f.write("DormantCanceledByStartup 1\n")
                    f.write("ExitNodes {ar},{cl},{uy},{br}\n")
                    f.write("StrictNodes 0\n")
                    f.write("CircuitBuildTimeout 10\n")
                    f.write("KeepalivePeriod 60\n")
                    f.write("MaxCircuitDirtiness 300\n")

                cmd = [self.tor_path, "-f", runtime_torrc]
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                logger.error(f"Error al iniciar subproceso Tor: {e}")
                return False

        # Esperar a que el ControlPort esté disponible antes de abrir la conexión persistente
        t0 = time.time()
        logger.info("Esperando que ControlPort esté disponible...")
        while time.time() - t0 < 15:
            if self.is_port_open(self.control_port):
                break
            time.sleep(0.8)
        else:
            logger.warning("ControlPort no respondió en 15s.")
            return False

        # Loop de bootstrap con conexión stem persistente (evita SocketClosed spam)
        time.sleep(1.0)  # Pequeña pausa extra para que Tor inicialice el handler
        t0 = time.time()
        try:
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as ctrl:
                ctrl.authenticate(password=self.password)
                try:
                    ctrl.reset_conf("__OwningControllerProcess")
                except Exception:
                    pass
                logger.info("Conexión al ControlPort establecida. Esperando bootstrap 100%...")
                while time.time() - t0 < timeout_sec:
                    if not ctrl.is_alive():
                        logger.warning("La conexión con el puerto de control de Tor se interrumpió.")
                        break
                    try:
                        info = ctrl.get_info("status/bootstrap-phase", default="")
                        progress_str = ""
                        for part in info.split():
                            if part.startswith("PROGRESS="):
                                progress_str = part.split("=")[1]
                                break
                        progress = int(progress_str) if progress_str.isdigit() else 0
                        logger.info(f"Bootstrap: {progress}% — {info[:80]}")
                        if progress >= 100 and self.is_port_open(self.socks_port):
                            logger.info(f"Tor listo y bootstrapped al 100% en {self.get_proxy_url()}.")
                            return True
                    except Exception as poll_err:
                        logger.debug(f"Poll error (ignorado): {poll_err}")
                        if not ctrl.is_alive():
                            break
                    time.sleep(2.0)
        except Exception as conn_err:
            logger.error(f"No se pudo conectar al ControlPort persistente: {conn_err}")

        logger.warning(f"Tor no completó bootstrap en {timeout_sec}s.")
        return False

    def rotate_ip(self) -> bool:
        """
        Envía la señal NEWNYM a través del puerto de control para rotar el circuito.
        Equivalente a cambiar de IP pública de salida en milisegundos.
        """
        try:
            from stem import Signal
            from stem.control import Controller
            with Controller.from_port(port=self.control_port) as c:
                c.authenticate(password=self.password)
                c.signal(Signal.NEWNYM)
            time.sleep(1.5)  # Pausa breve para establecimiento del nuevo circuito
            logger.info(f"Circuito Tor rotado exitosamente (Puerto {self.control_port}).")
            return True
        except Exception as e:
            logger.warning(f"No se pudo rotar el circuito Tor en puerto {self.control_port}: {e}")
            return False

    def get_current_ip(self) -> str:
        """Obtiene la IP pública actual a través del proxy SOCKS5."""
        try:
            proxies = {
                "http": self.get_proxy_url(),
                "https": self.get_proxy_url()
            }
            resp = requests.get("https://api.ipify.org", proxies=proxies, timeout=12)
            return resp.text.strip()
        except Exception as e:
            return f"Desconocida ({e})"

    def stop(self) -> None:
        """Detiene el proceso local de Tor si fue instanciado por este controlador como propietario."""
        if not self.is_owner:
            logger.debug(f"TorController (puerto {self.socks_port}) no es propietario del subproceso. Omitiendo stop.")
            return

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            logger.info(f"Proceso Tor (puerto {self.socks_port}) detenido.")
