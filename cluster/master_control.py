#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CONTROLADOR MAESTRO DE CLUSTER LAN (PC MADRE / MASTER)
Arquitectura Hexagonal - Subsistema de Orquestación Distribuida

Permite a la PC Principal:
1. 'push-and-update': Hacer git push a main y ordenar a todas las PCs hijas actualizarse al instante.
2. 'status': Monitorear en tiempo real el estado, recursos y scraper activo de cada máquina.
3. 'start' / 'start-all': Mandar a raspar a una o todas las máquinas con configuraciones personalizadas.
4. 'stop' / 'stop-all': Detener el scraping en una o todas las máquinas.
5. 'scan': Descubrir automáticamente PCs en la red local ejecutando el agente.
6. 'ui': Servir un Panel Web interactivo en tiempo real para operar con clics.
"""

import os
import sys
import json
import time
import socket
import logging
import argparse
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# Ajuste de codificación en Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(__file__).resolve().parent / "nodes.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MasterControl]: %(message)s"
)
logger = logging.getLogger("MasterControl")


class ClusterManager:
    """Gestor del inventario de nodos y despacho de peticiones RPC por red local."""

    @staticmethod
    def load_nodes() -> list[dict]:
        if not CONFIG_FILE.exists():
            return []
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("nodes", [])
        except Exception as e:
            logger.error(f"Error al leer {CONFIG_FILE}: {e}")
            return []

    @staticmethod
    def save_nodes(nodes: list[dict]):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"nodes": nodes}, f, indent=2, ensure_ascii=False)

    @classmethod
    def get_node(cls, name_or_ip: str) -> dict | None:
        nodes = cls.load_nodes()
        for n in nodes:
            if n.get("name", "").lower() == name_or_ip.lower() or n.get("ip") == name_or_ip:
                return n
        return None

    @classmethod
    def query_node_status(cls, node: dict, timeout: float = 3.0) -> dict:
        url = f"http://{node['ip']}:{node.get('port', 5555)}/status"
        res_info = {
            "name": node.get("name", "Desconocido"),
            "ip": node["ip"],
            "port": node.get("port", 5555),
            "online": False,
            "status": "OFFLINE",
            "scraper": "-",
            "workers": "-",
            "commit": "-",
            "uptime": "-",
            "cpu": "-",
            "ram": "-",
            "error": None
        }
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                res_info["online"] = True
                res_info["status"] = data.get("status", "idle").upper()
                res_info["pc_name"] = data.get("pc_name", "")
                params = data.get("params") or {}
                res_info["scraper"] = params.get("scraper", "-")
                res_info["workers"] = str(params.get("workers", "-"))
                res_info["commit"] = data.get("git", {}).get("commit", "-")
                res_info["branch"] = data.get("git", {}).get("branch", "main")
                uptime_sec = data.get("uptime_seconds", 0)
                if uptime_sec > 0:
                    mins = int(uptime_sec // 60)
                    secs = int(uptime_sec % 60)
                    res_info["uptime"] = f"{mins}m {secs}s"
                sys_m = data.get("system") or {}
                if sys_m.get("cpu_percent") is not None:
                    res_info["cpu"] = f"{sys_m['cpu_percent']}%"
                if sys_m.get("ram_percent") is not None:
                    res_info["ram"] = f"{sys_m['ram_percent']}%"
            else:
                res_info["error"] = f"HTTP {resp.status_code}"
        except Exception as e:
            res_info["error"] = str(e)
        return res_info

    @classmethod
    def query_all_status(cls) -> list[dict]:
        nodes = cls.load_nodes()
        results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_node = {executor.submit(cls.query_node_status, node): node for node in nodes}
            for future in as_completed(future_to_node):
                results.append(future.result())
        # Ordenar por nombre
        results.sort(key=lambda x: x["name"])
        return results

    @classmethod
    def send_command(cls, node: dict, endpoint: str, payload: dict = None, timeout: float = 40.0) -> dict:
        url = f"http://{node['ip']}:{node.get('port', 5555)}/{endpoint.lstrip('/')}"
        try:
            if payload is not None:
                resp = requests.post(url, json=payload, timeout=timeout)
            else:
                resp = requests.get(url, timeout=timeout)
            return {"node": node.get("name"), "ip": node["ip"], "status_code": resp.status_code, "data": resp.json()}
        except Exception as e:
            return {"node": node.get("name"), "ip": node["ip"], "status_code": 0, "error": str(e)}

    @classmethod
    def broadcast_command(cls, endpoint: str, payload: dict = None, timeout: float = 40.0) -> list[dict]:
        nodes = cls.load_nodes()
        results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(cls.send_command, node, endpoint, payload, timeout) for node in nodes]
            for f in as_completed(futures):
                results.append(f.result())
        return results


# ============================================================================
# COMANDOS DE LÍNEA DE COMANDOS (CLI)
# ============================================================================

def cmd_status(args):
    print("\n" + "=" * 80)
    print(" ESTADO DEL CLUSTER DE SCRAPING EN RED LOCAL (LAN)")
    print("=" * 80)
    results = ClusterManager.query_all_status()

    if not results:
        print("[!] No hay nodos registrados en cluster/nodes.json.")
        print("    Usa: python master_control.py add-node --name PC-01 --ip 192.168.1.X")
        print("    O bien: python master_control.py scan")
        return

    header = f"{'NODO':<12} | {'IP:PUERTO':<18} | {'ESTADO':<10} | {'SCRAPER':<12} | {'W':<3} | {'COMMIT':<8} | {'UPTIME':<10} | {'RAM':<6}"
    print(header)
    print("-" * 80)

    for r in results:
        status_str = r["status"]
        if status_str == "RUNNING":
            status_display = f"\033[92m{status_str}\033[0m" if sys.stdout.isatty() else status_str
        elif status_str == "IDLE":
            status_display = f"\033[94m{status_str}\033[0m" if sys.stdout.isatty() else status_str
        else:
            status_display = f"\033[91m{status_str}\033[0m" if sys.stdout.isatty() else status_str

        line = f"{r['name']:<12} | {r['ip'] + ':' + str(r['port']):<18} | {status_display:<10} | {r['scraper']:<12} | {r['workers']:<3} | {r['commit']:<8} | {r['uptime']:<10} | {r['ram']:<6}"
        print(line)
    print("-" * 80 + "\n")


def cmd_push_and_update(args):
    print("\n" + "=" * 70)
    print(" PUSH & ACTUALIZACIÓN SIMULTÁNEA DEL CLUSTER")
    print("=" * 70)

    # 1. Ejecutar git push origin main desde esta máquina
    print("[1/2] Enviando commits locales a GitHub (git push origin main)...")
    try:
        push_res = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True
        )
        if push_res.returncode != 0:
            print(f"[!] Error al hacer git push:\n{push_res.stderr}")
            choice = input("¿Deseas intentar actualizar los nodos de todas formas? (s/n): ").strip().lower()
            if choice != "s":
                return
        else:
            print("[OK] Push completado exitosamente en GitHub.")
    except Exception as e:
        print(f"[!] Excepción al ejecutar git push: {e}")
        return

    # 2. Ordenar actualización a todos los nodos
    print("\n[2/2] Ordenando actualización inmediata a todos los nodos del cluster...")
    results = ClusterManager.broadcast_command("update", payload={"restart_if_running": True})

    for r in results:
        node_name = r.get("node")
        if r.get("status_code") == 200:
            data = r.get("data", {})
            commit = data.get("data", {}).get("git", {}).get("commit", "-")
            restarted = data.get("data", {}).get("restarted", False)
            restart_str = " (Scraper reiniciado)" if restarted else ""
            print(f"  [OK] {node_name} ({r['ip']}): Actualizado a commit {commit}{restart_str}")
        else:
            print(f"  [FALLO] {node_name} ({r['ip']}): Error o nodo inalcanzable ({r.get('error', 'HTTP ' + str(r.get('status_code')))})")
    print("\nCluster sincronizado.\n")


def cmd_update(args):
    target_nodes = []
    if args.all:
        target_nodes = ClusterManager.load_nodes()
    elif args.pc:
        n = ClusterManager.get_node(args.pc)
        if not n:
            print(f"[!] No se encontró el nodo '{args.pc}'.")
            return
        target_nodes = [n]
    else:
        print("[!] Debes especificar --pc <nombre> o --all.")
        return

    print(f"Actualizando código en {len(target_nodes)} nodo(s)...")
    for n in target_nodes:
        res = ClusterManager.send_command(n, "update", payload={"restart_if_running": not args.no_restart})
        if res.get("status_code") == 200:
            commit = res.get("data", {}).get("data", {}).get("git", {}).get("commit", "-")
            print(f"  [OK] {n['name']}: Actualizado exitosamente a {commit}.")
        else:
            print(f"  [FALLO] {n['name']}: {res.get('error', 'HTTP ' + str(res.get('status_code')))}")


def cmd_start(args):
    target_nodes = []
    if args.all:
        target_nodes = ClusterManager.load_nodes()
    elif args.pc:
        n = ClusterManager.get_node(args.pc)
        if not n:
            print(f"[!] No se encontró el nodo '{args.pc}'.")
            return
        target_nodes = [n]
    else:
        print("[!] Debes especificar --pc <nombre> o --all.")
        return

    payload = {
        "scraper": args.scraper,
        "workers": args.workers,
        "batch_size": args.batch_size,
        "prioridad": args.prioridad,
        "tor": args.tor,
        "proxy_pool": args.proxy_pool,
        "solo_sin_coincidencia": args.solo_sin_coincidencia,
        "forzar_horario": args.forzar_horario
    }

    print(f"Iniciando scraper '{args.scraper}' (workers={args.workers}) en {len(target_nodes)} nodo(s)...")
    for n in target_nodes:
        res = ClusterManager.send_command(n, "start", payload=payload)
        if res.get("status_code") == 200:
            msg = res.get("data", {}).get("message", "Iniciado")
            print(f"  [OK] {n['name']}: {msg}")
        else:
            err = res.get("data", {}).get("message") or res.get("error") or f"HTTP {res.get('status_code')}"
            print(f"  [!] {n['name']}: {err}")


def cmd_stop(args):
    target_nodes = []
    if args.all:
        target_nodes = ClusterManager.load_nodes()
    elif args.pc:
        n = ClusterManager.get_node(args.pc)
        if not n:
            print(f"[!] No se encontró el nodo '{args.pc}'.")
            return
        target_nodes = [n]
    else:
        print("[!] Debes especificar --pc <nombre> o --all.")
        return

    print(f"Deteniendo scraping en {len(target_nodes)} nodo(s)...")
    for n in target_nodes:
        res = ClusterManager.send_command(n, "stop")
        if res.get("status_code") == 200:
            print(f"  [OK] {n['name']}: {res.get('data', {}).get('message', 'Detenido')}")
        else:
            print(f"  [FALLO] {n['name']}: {res.get('error', 'HTTP ' + str(res.get('status_code')))}")


def cmd_logs(args):
    node = ClusterManager.get_node(args.pc)
    if not node:
        print(f"[!] Nodo no encontrado: {args.pc}")
        return
    res = ClusterManager.send_command(node, f"logs?lines={args.lines}")
    if res.get("status_code") == 200:
        lines = res.get("data", {}).get("lines", [])
        print(f"\n--- ÚLTIMOS LOGS DE {node['name']} ({node['ip']}) ---")
        for line in lines:
            print(line, end="")
        print("-" * 50 + "\n")
    else:
        print(f"[!] Error al obtener logs de {node['name']}: {res.get('error')}")


def cmd_scan(args):
    print("\nEscaneando red local en busca de agentes de scraping en puerto 5555...")
    subnet_prefix = args.subnet
    if not subnet_prefix:
        # Intentar deducir la IP local
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            parts = local_ip.split(".")
            subnet_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}"
            print(f"Subred detectada automáticamente: {subnet_prefix}.0/24")
        except Exception:
            subnet_prefix = "192.168.1"
            print(f"Usando subred por defecto: {subnet_prefix}.0/24")

    active_agents = []

    def check_ip(ip):
        url = f"http://{ip}:5555/health"
        try:
            r = requests.get(url, timeout=0.8)
            if r.status_code == 200:
                data = r.json()
                return ip, data.get("pc_name", "Desconocido")
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=50) as executor:
        ips_to_check = [f"{subnet_prefix}.{i}" for i in range(1, 255)]
        futures = [executor.submit(check_ip, ip) for ip in ips_to_check]
        for f in as_completed(futures):
            res = f.result()
            if res:
                active_agents.append(res)

    print(f"\nEscaneo finalizado. Agentes encontrados: {len(active_agents)}")
    current_nodes = ClusterManager.load_nodes()
    existing_ips = {n["ip"] for n in current_nodes}

    for ip, pc_name in active_agents:
        is_new = ip not in existing_ips
        tag = "[NUEVO]" if is_new else "[REGISTRADO]"
        print(f"  {tag} {pc_name} ({ip}:5555)")
        if is_new and args.auto_add:
            current_nodes.append({"name": pc_name, "ip": ip, "port": 5555, "description": "Auto-descubierto"})
            print(f"    -> Agregado a {CONFIG_FILE.name}")

    if args.auto_add and len(active_agents) > 0:
        ClusterManager.save_nodes(current_nodes)
    print()


def cmd_add_node(args):
    nodes = ClusterManager.load_nodes()
    for n in nodes:
        if n["name"].lower() == args.name.lower():
            print(f"[!] Ya existe un nodo con el nombre '{args.name}'.")
            return
        if n["ip"] == args.ip:
            print(f"[!] Ya existe un nodo con la IP {args.ip} ('{n['name']}').")
            return

    nodes.append({
        "name": args.name,
        "ip": args.ip,
        "port": args.port,
        "description": args.description or "Nodo de scraping"
    })
    ClusterManager.save_nodes(nodes)
    print(f"[OK] Nodo '{args.name}' ({args.ip}:{args.port}) agregado con éxito a {CONFIG_FILE.name}.")


def cmd_remove_node(args):
    nodes = ClusterManager.load_nodes()
    new_nodes = [n for n in nodes if n["name"].lower() != args.name.lower() and n["ip"] != args.name]
    if len(new_nodes) == len(nodes):
        print(f"[!] No se encontró ningún nodo que coincida con '{args.name}'.")
        return
    ClusterManager.save_nodes(new_nodes)
    print(f"[OK] Nodo '{args.name}' eliminado de la lista.")


# ============================================================================
# PANEL WEB DE CONTROL INTEGRADO (DASHBOARD)
# ============================================================================

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Master Control - Cluster de Scraping LAN</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --accent: #58a6ff;
      --green: #238636;
      --red: #da3633;
      --yellow: #d29922;
      --text: #c9d1d9;
      --text-muted: #8b949e;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 20px;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
      margin-bottom: 24px;
    }
    h1 { margin: 0; font-size: 1.5rem; display: flex; align-items: center; gap: 10px; }
    .badge { font-size: 0.8rem; padding: 4px 8px; border-radius: 12px; background: var(--border); }
    .btn {
      background-color: #21262d;
      color: var(--text);
      border: 1px solid var(--border);
      padding: 8px 14px;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 600;
      transition: all 0.2s;
    }
    .btn:hover { background-color: #30363d; }
    .btn-primary { background-color: var(--green); border-color: #2ea043; color: #fff; }
    .btn-primary:hover { background-color: #2c974b; }
    .btn-danger { background-color: var(--red); border-color: #b62324; color: #fff; }
    .btn-danger:hover { background-color: #b62324; }
    .actions-bar {
      display: flex;
      gap: 12px;
      margin-bottom: 24px;
      background: var(--card-bg);
      padding: 16px;
      border-radius: 8px;
      border: 1px solid var(--border);
      align-items: center;
      flex-wrap: wrap;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 20px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
      position: relative;
      transition: transform 0.1s;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }
    .node-name { font-size: 1.2rem; font-weight: bold; margin: 0; }
    .node-ip { color: var(--text-muted); font-size: 0.85rem; }
    .status-pill {
      font-size: 0.75rem;
      font-weight: bold;
      padding: 4px 10px;
      border-radius: 20px;
      text-transform: uppercase;
    }
    .status-running { background: rgba(35, 134, 54, 0.2); color: #3fb950; border: 1px solid #238636; }
    .status-idle { background: rgba(88, 166, 255, 0.2); color: #58a6ff; border: 1px solid #1f6feb; }
    .status-offline { background: rgba(218, 54, 51, 0.2); color: #f85149; border: 1px solid #da3633; }
    .metric-row { display: flex; justify-content: space-between; margin: 8px 0; font-size: 0.9rem; }
    .metric-label { color: var(--text-muted); }
    .card-actions {
      display: flex;
      gap: 8px;
      margin-top: 16px;
      border-top: 1px solid var(--border);
      padding-top: 14px;
    }
    select, input {
      background: #0d1117;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 10px;
      border-radius: 6px;
    }
    #log-modal {
      display: none;
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.7);
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }
    .modal-content {
      background: var(--card-bg);
      border: 1px solid var(--border);
      width: 80%;
      max-width: 900px;
      height: 70vh;
      border-radius: 8px;
      display: flex;
      flex-direction: column;
      padding: 20px;
    }
    .terminal-box {
      flex: 1;
      background: #000;
      color: #0f0;
      font-family: monospace;
      padding: 12px;
      border-radius: 4px;
      overflow-y: auto;
      font-size: 0.85rem;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>📡 Master Control <span class="badge">Cluster LAN</span></h1>
      <div>
        <button class="btn" onclick="fetchStatus()">🔄 Refrescar</button>
      </div>
    </header>

    <div class="actions-bar">
      <strong>Acciones Globales:</strong>
      <button class="btn btn-primary" onclick="pushAndUpdate()">🚀 Git Push & Actualizar Cluster</button>
      <select id="global-scraper">
        <option value="claro">Claro</option>
        <option value="movistar">Movistar</option>
        <option value="personal">Personal</option>
        <option value="iris_http">Iris HTTP</option>
        <option value="datuar">Datuar</option>
      </select>
      <input type="number" id="global-workers" value="8" min="1" max="20" style="width: 50px;">
      <label><input type="checkbox" id="global-tor"> Tor</label>
      <label><input type="checkbox" id="global-proxy"> Proxy Pool</label>
      <button class="btn" onclick="startAll()">▶️ Iniciar en Todos</button>
      <button class="btn btn-danger" onclick="stopAll()">⏹️ Detener Todos</button>
    </div>

    <div class="grid" id="nodes-grid">
      <!-- Tarjetas inyectadas dinámicamente -->
    </div>
  </div>

  <div id="log-modal">
    <div class="modal-content">
      <div style="display:flex; justify-content:space-between; margin-bottom:12px;">
        <h3 id="log-title" style="margin:0;">Logs</h3>
        <button class="btn" onclick="closeLogs()">Cerrar</button>
      </div>
      <div class="terminal-box" id="log-box">Cargando...</div>
    </div>
  </div>

  <script>
    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        renderNodes(data);
      } catch (err) {
        console.error("Error al cargar estado:", err);
      }
    }

    function renderNodes(nodes) {
      const grid = document.getElementById('nodes-grid');
      grid.innerHTML = '';
      if (!nodes || nodes.length === 0) {
        grid.innerHTML = '<p>No hay nodos registrados. Usa la CLI para escanear o agregar PCs.</p>';
        return;
      }

      nodes.forEach(n => {
        let statusClass = 'status-offline';
        if (n.status === 'RUNNING') statusClass = 'status-running';
        if (n.status === 'IDLE') statusClass = 'status-idle';

        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div class="card-header">
            <div>
              <h3 class="node-name">${n.name}</h3>
              <div class="node-ip">${n.ip}:${n.port}</div>
            </div>
            <span class="status-pill ${statusClass}">${n.status}</span>
          </div>

          <div class="metric-row"><span class="metric-label">Scraper:</span> <strong>${n.scraper}</strong></div>
          <div class="metric-row"><span class="metric-label">Workers:</span> <span>${n.workers}</span></div>
          <div class="metric-row"><span class="metric-label">Git Commit:</span> <code>${n.commit}</code></div>
          <div class="metric-row"><span class="metric-label">Uptime:</span> <span>${n.uptime}</span></div>
          <div class="metric-row"><span class="metric-label">RAM / CPU:</span> <span>${n.ram} / ${n.cpu}</span></div>

          <div class="card-actions">
            <button class="btn btn-primary" onclick="startSingle('${n.name}')" ${n.status === 'RUNNING' || !n.online ? 'disabled' : ''}>Iniciar</button>
            <button class="btn btn-danger" onclick="stopSingle('${n.name}')" ${n.status !== 'RUNNING' ? 'disabled' : ''}>Detener</button>
            <button class="btn" onclick="updateSingle('${n.name}')" ${!n.online ? 'disabled' : ''}>Actualizar Git</button>
            <button class="btn" onclick="viewLogs('${n.name}')" ${!n.online ? 'disabled' : ''}>Ver Log</button>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    async function pushAndUpdate() {
      if (!confirm("¿Deseas hacer git push y actualizar todas las PCs del cluster?")) return;
      alert("Comenzando sincronización global... revisa la consola o espera 5 segundos.");
      await fetch('/api/push-and-update', { method: 'POST' });
      fetchStatus();
    }

    async function startAll() {
      const scraper = document.getElementById('global-scraper').value;
      const workers = parseInt(document.getElementById('global-workers').value);
      const tor = document.getElementById('global-tor').checked;
      const proxy_pool = document.getElementById('global-proxy').checked;

      await fetch('/api/start-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scraper, workers, tor, proxy_pool })
      });
      setTimeout(fetchStatus, 1500);
    }

    async function stopAll() {
      if (!confirm("¿Detener el scraping en todo el cluster?")) return;
      await fetch('/api/stop-all', { method: 'POST' });
      setTimeout(fetchStatus, 1000);
    }

    async function startSingle(nodeName) {
      const scraper = prompt("Scraper a ejecutar (claro, movistar, personal, iris_http, datuar):", "claro");
      if (!scraper) return;
      const workers = parseInt(prompt("Cantidad de workers:", "8")) || 8;
      await fetch('/api/start-node', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node: nodeName, scraper, workers })
      });
      setTimeout(fetchStatus, 1500);
    }

    async function stopSingle(nodeName) {
      await fetch('/api/stop-node', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node: nodeName })
      });
      setTimeout(fetchStatus, 1000);
    }

    async function updateSingle(nodeName) {
      await fetch('/api/update-node', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node: nodeName })
      });
      alert(`Orden de actualización enviada a ${nodeName}`);
      setTimeout(fetchStatus, 2000);
    }

    async function viewLogs(nodeName) {
      document.getElementById('log-title').innerText = `Logs de ${nodeName}`;
      document.getElementById('log-box').innerText = 'Cargando logs remotos...';
      document.getElementById('log-modal').style.display = 'flex';
      const res = await fetch(`/api/logs?node=${nodeName}`);
      const data = await res.json();
      document.getElementById('log-box').innerText = (data.lines || []).join('');
    }

    function closeLogs() {
      document.getElementById('log-modal').style.display = 'none';
    }

    // Auto-refresco cada 4 segundos
    fetchStatus();
    setInterval(fetchStatus, 4000);
  </script>
</body>
</html>
"""

from http.server import HTTPServer, BaseHTTPRequestHandler

class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path in ("", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode("utf-8"))

        elif path == "/api/status":
            nodes = ClusterManager.query_all_status()
            self._send_json(nodes)

        elif path == "/api/logs":
            query = parse_qs(parsed.query)
            node_name = query.get("node", [""])[0]
            node = ClusterManager.get_node(node_name)
            if not node:
                self._send_json({"lines": [f"Nodo '{node_name}' no encontrado."]}, 404)
            else:
                res = ClusterManager.send_command(node, "logs?lines=60")
                lines = res.get("data", {}).get("lines", ["No se pudieron cargar logs."])
                self._send_json({"lines": lines})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except Exception:
                pass

        path = self.path.rstrip("/")
        if path == "/api/push-and-update":
            # Ejecutar push y update en hilo secundario para no congelar la UI
            def _task():
                cmd_push_and_update(None)
            ThreadPoolExecutor(max_workers=1).submit(_task)
            self._send_json({"status": "iniciado"})

        elif path == "/api/start-all":
            ClusterManager.broadcast_command("start", payload=body)
            self._send_json({"status": "ok"})

        elif path == "/api/stop-all":
            ClusterManager.broadcast_command("stop")
            self._send_json({"status": "ok"})

        elif path == "/api/start-node":
            node = ClusterManager.get_node(body.get("node"))
            if node:
                res = ClusterManager.send_command(node, "start", payload=body)
                self._send_json(res)
            else:
                self._send_json({"error": "Nodo no encontrado"}, 404)

        elif path == "/api/stop-node":
            node = ClusterManager.get_node(body.get("node"))
            if node:
                res = ClusterManager.send_command(node, "stop")
                self._send_json(res)
            else:
                self._send_json({"error": "Nodo no encontrado"}, 404)

        elif path == "/api/update-node":
            node = ClusterManager.get_node(body.get("node"))
            if node:
                res = ClusterManager.send_command(node, "update", payload={"restart_if_running": True})
                self._send_json(res)
            else:
                self._send_json({"error": "Nodo no encontrado"}, 404)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def cmd_ui(args):
    port = args.port
    server = HTTPServer(("0.0.0.0", port), DashboardHTTPHandler)
    print("\n" + "=" * 65)
    print(f"🚀 PANEL WEB DE CONTROL DEL CLUSTER LAN INICIADO")
    print(f"   Abre en tu navegador: http://localhost:{port}")
    print(f"   (Accesible desde cualquier PC de la red en: http://<tu-ip>:{port})")
    print("   Presiona Ctrl+C para detener el servidor web.")
    print("=" * 65 + "\n")

    # Abrir navegador si se solicita
    if not args.no_browser:
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo servidor web...")
        server.server_close()


# ============================================================================
# PARSER PRINCIPAL
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Control Maestro de Cluster LAN (PC Madre)")
    subparsers = parser.add_subparsers(dest="command", help="Comando a ejecutar")

    # status
    p_status = subparsers.add_parser("status", help="Ver estado de todos los nodos del cluster")
    p_status.set_defaults(func=cmd_status)

    # push-and-update
    p_pau = subparsers.add_parser("push-and-update", help="Hacer git push y sincronizar todas las PCs hijas")
    p_pau.set_defaults(func=cmd_push_and_update)

    # update
    p_update = subparsers.add_parser("update", help="Actualizar código vía Git en uno o todos los nodos")
    p_update.add_argument("--pc", help="Nombre o IP de la PC objetivo")
    p_update.add_argument("--all", action="store_true", help="Actualizar en todo el cluster")
    p_update.add_argument("--no-restart", action="store_true", help="No reiniciar el scraper si estaba activo")
    p_update.set_defaults(func=cmd_update)

    # start
    p_start = subparsers.add_parser("start", help="Iniciar scraping en uno o todos los nodos")
    p_start.add_argument("--pc", help="Nombre o IP de la PC objetivo")
    p_start.add_argument("--all", action="store_true", help="Iniciar en todo el cluster")
    p_start.add_argument("--scraper", default="iris_http", help="Scraper (claro, movistar, personal, iris_http, datuar)")
    p_start.add_argument("--workers", type=int, default=8, help="Cantidad de workers concurrentes (default: 8)")
    p_start.add_argument("--batch-size", type=int, default=50, help="Lote de reclamo (default: 50)")
    p_start.add_argument("--prioridad", choices=["auto", "1", "2", "3"], default="auto")
    p_start.add_argument("--tor", action="store_true", help="Activar Tor")
    p_start.add_argument("--proxy-pool", action="store_true", help="Activar Proxy Pool rotativo")
    p_start.add_argument("--solo-sin-coincidencia", action="store_true", help="Solo sin coincidencia previa")
    p_start.add_argument("--forzar-horario", action="store_true", help="Ignorar horario comercial")
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser("stop", help="Detener scraping en uno o todos los nodos")
    p_stop.add_argument("--pc", help="Nombre o IP de la PC objetivo")
    p_stop.add_argument("--all", action="store_true", help="Detener en todo el cluster")
    p_stop.set_defaults(func=cmd_stop)

    # logs
    p_logs = subparsers.add_parser("logs", help="Ver logs en vivo de un nodo")
    p_logs.add_argument("--pc", required=True, help="Nombre o IP de la PC")
    p_logs.add_argument("--lines", type=int, default=50, help="Líneas a ver (default: 50)")
    p_logs.set_defaults(func=cmd_logs)

    # scan
    p_scan = subparsers.add_parser("scan", help="Escanear la red local para descubrir agentes")
    p_scan.add_argument("--subnet", help="Prefijo de subred (ej: 192.168.1)")
    p_scan.add_argument("--auto-add", action="store_true", help="Agregar agentes encontrados a nodes.json")
    p_scan.set_defaults(func=cmd_scan)

    # add-node
    p_add = subparsers.add_parser("add-node", help="Registrar una nueva PC en el cluster")
    p_add.add_argument("--name", required=True, help="Nombre identificador (ej: PC-Oficina-1)")
    p_add.add_argument("--ip", required=True, help="Dirección IP local")
    p_add.add_argument("--port", type=int, default=5555, help="Puerto (default: 5555)")
    p_add.add_argument("--description", help="Descripción opcional")
    p_add.set_defaults(func=cmd_add_node)

    # remove-node
    p_rem = subparsers.add_parser("remove-node", help="Eliminar un nodo del registro")
    p_rem.add_argument("--name", required=True, help="Nombre o IP del nodo a eliminar")
    p_rem.set_defaults(func=cmd_remove_node)

    # ui
    p_ui = subparsers.add_parser("ui", help="Levantar panel web de control en navegador")
    p_ui.add_argument("--port", type=int, default=5000, help="Puerto web (default: 5000)")
    p_ui.add_argument("--no-browser", action="store_true", help="No abrir automáticamente el navegador")
    p_ui.set_defaults(func=cmd_ui)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
