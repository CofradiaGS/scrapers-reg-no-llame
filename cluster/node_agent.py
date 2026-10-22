#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AGENTE DE NODO LAN (WORKER / PC HIJA)
Arquitectura Hexagonal - Subsistema de Orquestación Distribuida

Se ejecuta en segundo plano en cada PC esclava/worker de la red local.
Escucha peticiones HTTP JSON de la PC Madre en el puerto 5555 para:
1. Sincronizar automáticamente el código fuente desde Git (git reset --hard origin/main).
2. Iniciar, supervisar y detener el motor de scraping (supervisor_vps.py).
3. Reportar métricas de salud, uso de recursos y logs en tiempo real.
"""

import os
import sys
import json
import time
import socket
import logging
import platform
import argparse
import subprocess
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# Ajuste de codificación en Windows (seguro para procesos sin consola / servicios)
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LOG_FILE = PROJECT_ROOT / "supervisor_247.log"
AGENT_LOG_FILE = PROJECT_ROOT / "node_agent.log"
LOCK_FILE = PROJECT_ROOT / "node_agent.lock"
_lock_handle = None


def acquire_singleton_lock() -> bool:
    """
    Garantiza a nivel de kernel de Windows/SO que solo pueda ejecutarse 1 única
    instancia del agente en esta máquina física, impidiendo procesos duplicados.
    """
    global _lock_handle
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _lock_handle = open(LOCK_FILE, "a+")
        if os.name == "nt":
            import msvcrt
            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_handle.seek(0)
        _lock_handle.truncate()
        _lock_handle.write(str(os.getpid()))
        _lock_handle.flush()
        return True
    except (IOError, OSError):
        return False

log_handlers = [logging.FileHandler(AGENT_LOG_FILE, encoding="utf-8")]
if sys.stdout is not None:
    log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [NodeAgent]: %(message)s",
    handlers=log_handlers
)
logger = logging.getLogger("NodeAgent")



class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Servidor HTTP multihilo para atender peticiones concurrentes sin bloqueo."""
    daemon_threads = True


class ProcessManager:
    """Administrador thread-safe del subproceso supervisor_vps.py y operaciones de Git."""
    _lock = threading.Lock()
    _process: subprocess.Popen = None
    _active_params: dict = None
    _start_time: float = None

    @classmethod
    def get_status(cls) -> dict:
        with cls._lock:
            is_running = False
            pid = None
            uptime = 0.0

            if cls._process is not None:
                ret = cls._process.poll()
                if ret is None:
                    is_running = True
                    pid = cls._process.pid
                    if cls._start_time:
                        uptime = round(time.time() - cls._start_time, 1)
                else:
                    # El proceso terminó por su cuenta
                    cls._process = None
                    cls._active_params = None
                    cls._start_time = None

            # Información de Git local
            git_info = cls._get_git_info()

            # Métricas de sistema
            sys_metrics = cls._get_system_metrics()

            return {
                "pc_name": socket.gethostname(),
                "platform": platform.platform(),
                "status": "running" if is_running else "idle",
                "pid": pid,
                "uptime_seconds": uptime,
                "params": cls._active_params if is_running else None,
                "git": git_info,
                "system": sys_metrics,
                "log_available": LOG_FILE.exists()
            }

    @classmethod
    def start_scraper(cls, params: dict) -> tuple[bool, str, dict]:
        with cls._lock:
            # Si ya hay un proceso corriendo, rechazar o reportar
            if cls._process is not None and cls._process.poll() is None:
                return False, f"Ya hay un scraper en ejecución (PID: {cls._process.pid})", cls.get_status()

            scraper_name = params.get("scraper", "iris_http")
            workers = int(params.get("workers", 9))
            batch_size = int(params.get("batch_size", 50))
            max_queries = int(params.get("max_queries_worker", 350))
            prioridad = params.get("prioridad", "auto")
            use_tor = bool(params.get("tor", False))
            use_proxy_pool = bool(params.get("proxy_pool", False))
            solo_sin_coincidencia = bool(params.get("solo_sin_coincidencia", False))
            forzar_horario = bool(params.get("forzar_horario", False))

            cmd = [
                sys.executable,
                str(PROJECT_ROOT / "supervisor_vps.py"),
                "--scraper", scraper_name,
                "--workers", str(workers),
                "--batch-size", str(batch_size),
                "--max-queries-worker", str(max_queries),
                "--prioridad", str(prioridad)
            ]
            if use_tor:
                cmd.append("--tor")
            if use_proxy_pool:
                cmd.append("--proxy-pool")
            if solo_sin_coincidencia:
                cmd.append("--solo-sin-coincidencia")
            if forzar_horario:
                cmd.append("--forzar-horario")
            queue_type = params.get("queue")
            if queue_type:
                cmd.extend(["--queue", str(queue_type)])
            auto_id = params.get("auto_id")
            if auto_id:
                cmd.extend(["--auto-id", str(auto_id)])

            try:
                creationflags = 0
                if platform.system() == "Windows":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags
                )
                cls._process = proc
                cls._active_params = params
                cls._start_time = time.time()
                logger.info(f"Scraper iniciado con éxito: {scraper_name} | PID: {proc.pid} | Workers: {workers}")
                return True, f"Scraper '{scraper_name}' iniciado correctamente.", cls.get_status()
            except Exception as e:
                logger.error(f"Error al iniciar scraper: {e}")
                return False, f"Excepción al arrancar proceso: {str(e)}", {}

    @classmethod
    def stop_scraper(cls) -> tuple[bool, str]:
        with cls._lock:
            if cls._process is None or cls._process.poll() is not None:
                cls._process = None
                cls._active_params = None
                cls._start_time = None
                return True, "No había ningún proceso de scraping activo."

            pid = cls._process.pid
            logger.info(f"Deteniendo supervisor y árbol de procesos (PID: {pid})...")

            try:
                if platform.system() == "Windows":
                    # taskkill con /T y /F mata el árbol completo de procesos hijos
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False
                    )
                else:
                    cls._process.terminate()
                    cls._process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Aviso durante la detención de PID {pid}: {e}")

            cls._process = None
            cls._active_params = None
            cls._start_time = None
            return True, f"Proceso {pid} y sus workers hijos han sido detenidos."

    @classmethod
    def update_code(cls, restart_if_running: bool = True) -> tuple[bool, str, dict]:
        logger.info("Recibida orden de sincronización Git...")
        was_running = False
        prev_params = None

        with cls._lock:
            if cls._process is not None and cls._process.poll() is None:
                was_running = True
                prev_params = cls._active_params

        # Detener temporalmente si estaba corriendo
        if was_running and restart_if_running:
            cls.stop_scraper()

        # Ejecutar git fetch y git reset --hard origin/main
        output_log = []
        try:
            # 1. Fetch
            fetch_res = subprocess.run(
                ["git", "fetch", "origin", "main"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=45
            )
            output_log.append(f"[git fetch]\n{fetch_res.stdout}\n{fetch_res.stderr}".strip())

            # 2. Reset Hard
            reset_res = subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=30
            )
            output_log.append(f"[git reset --hard]\n{reset_res.stdout}\n{reset_res.stderr}".strip())
            success = reset_res.returncode == 0
        except Exception as e:
            output_log.append(f"[ERROR EXCEPCIÓN] {str(e)}")
            success = False

        # Reiniciar si estaba corriendo y la actualización fue exitosa
        restarted = False
        if success and was_running and restart_if_running and prev_params:
            logger.info("Reiniciando scraper tras actualización exitosa de código...")
            cls.start_scraper(prev_params)
            restarted = True

        status_info = cls.get_status()
        status_info["restarted"] = restarted
        status_info["git_output"] = "\n".join(output_log)
        return success, "Código sincronizado con éxito." if success else "Error al sincronizar con Git.", status_info

    @classmethod
    def get_last_logs(cls, lines: int = 50) -> list[str]:
        if not LOG_FILE.exists():
            return ["[INFO] Aún no se ha generado el archivo de log (supervisor_247.log)."]
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                return all_lines[-lines:]
        except Exception as e:
            return [f"[ERROR] No se pudo leer el archivo de log: {e}"]

    @staticmethod
    def _get_git_info() -> dict:
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=5
            ).stdout.strip()
            msg = subprocess.run(
                ["git", "log", "-1", "--pretty=%s"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=5
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=5
            ).stdout.strip()
            return {"commit": commit, "branch": branch, "message": msg}
        except Exception:
            return {"commit": "desconocido", "branch": "main", "message": "N/A"}

    @staticmethod
    def _get_system_metrics() -> dict:
        metrics = {}
        try:
            import psutil
            metrics["cpu_percent"] = psutil.cpu_percent(interval=None)
            metrics["ram_percent"] = psutil.virtual_memory().percent
        except ImportError:
            metrics["cpu_percent"] = None
            metrics["ram_percent"] = None
        return metrics


class NodeAgentHTTPHandler(BaseHTTPRequestHandler):
    """Manejador de endpoints REST HTTP para el agente de nodo."""

    def _set_headers(self, status_code: int = 200, content_type: str = "application/json"):
        self.send_response(status_code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/status", "/health"):
            status_data = ProcessManager.get_status()
            self._set_headers(200)
            self.wfile.write(json.dumps(status_data, ensure_ascii=False, indent=2).encode("utf-8"))

        elif path == "/logs":
            query = parse_qs(parsed.query)
            n_lines = int(query.get("lines", [50])[0])
            logs = ProcessManager.get_last_logs(n_lines)
            self._set_headers(200)
            self.wfile.write(json.dumps({"lines": logs}, ensure_ascii=False).encode("utf-8"))

        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": f"Ruta GET no encontrada: {path}"}).encode("utf-8"))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body_data = {}
        if content_length > 0:
            try:
                body_bytes = self.rfile.read(content_length)
                body_data = json.loads(body_bytes.decode("utf-8"))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": f"JSON inválido: {e}"}).encode("utf-8"))
                return

        if path == "/start":
            ok, msg, data = ProcessManager.start_scraper(body_data)
            status_code = 200 if ok else 400
            self._set_headers(status_code)
            self.wfile.write(json.dumps({"success": ok, "message": msg, "data": data}, ensure_ascii=False).encode("utf-8"))

        elif path == "/stop":
            ok, msg = ProcessManager.stop_scraper()
            self._set_headers(200 if ok else 500)
            self.wfile.write(json.dumps({"success": ok, "message": msg, "status": ProcessManager.get_status()}, ensure_ascii=False).encode("utf-8"))

        elif path == "/update":
            restart = body_data.get("restart_if_running", True)
            ok, msg, data = ProcessManager.update_code(restart_if_running=restart)
            self._set_headers(200 if ok else 500)
            self.wfile.write(json.dumps({"success": ok, "message": msg, "data": data}, ensure_ascii=False).encode("utf-8"))

        else:
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": f"Ruta POST no encontrada: {path}"}).encode("utf-8"))

    def log_message(self, format, *args):
        # Redirigir logs del servidor HTTP a logger interno
        logger.debug(f"{self.address_string()} - {format % args}")


def run_agent(host: str = "0.0.0.0", port: int = 5555):
    if not acquire_singleton_lock():
        logger.warning("=" * 60)
        logger.warning("[SINGLETON LOCK] Ya existe una instancia activa de NodeAgent en esta máquina.")
        logger.warning("Finalizando este nuevo proceso para garantizar estrictamente 1 sola instancia.")
        logger.warning("=" * 60)
        sys.exit(0)

    server = ThreadedHTTPServer((host, port), NodeAgentHTTPHandler)
    logger.info("=" * 60)
    logger.info(f"AGENTE DE NODO LAN ACTIVO EN http://{host}:{port}")
    logger.info(f"Nombre de la máquina: {socket.gethostname()}")
    logger.info(f"Directorio de trabajo: {PROJECT_ROOT}")
    logger.info("Esperando comandos de la PC Madre...")
    logger.info("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Deteniendo Agente de Nodo...")
        server.server_close()
        ProcessManager.stop_scraper()
        logger.info("Agente detenido correctamente.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agente de Nodo LAN para Scrapers (Worker PC)")
    parser.add_argument("--host", default="0.0.0.0", help="Dirección IP de escucha (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5555, help="Puerto de escucha (default: 5555)")
    args = parser.parse_args()

    run_agent(host=args.host, port=args.port)
