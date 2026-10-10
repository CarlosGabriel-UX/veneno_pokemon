"""Controle pelo celular: uma página servida pelo próprio painel na rede de casa.

Fica desligado até ser ativado em Ajustes > Celular. Só aceita aparelhos da rede local
(ou da Tailscale) e toda chamada precisa do PIN de 6 dígitos mostrado no painel.
A calibração (F8) e o recorte de imagens continuam só no PC.
"""
import hmac
import ipaddress
import json
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import MODULES, PANEL_TITLE

if getattr(sys, "frozen", False):
    UI_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "ui"
else:
    UI_DIR = Path(__file__).resolve().parent / "ui"

DEFAULT_PORT = 8765
MAX_TRIES = 5  # PIN errado seguidos antes de bloquear o aparelho
LOCK_SECONDS = 60
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
VIRTUAL = ipaddress.ip_network("192.168.56.0/24")  # placa do VirtualBox, o celular não alcança
STATIC = {
    "/": ("mobile.html", "text/html; charset=utf-8"),
    "/mobile.html": ("mobile.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("mobile.webmanifest", "application/manifest+json"),
    "/icon.svg": ("mobile-icon.svg", "image/svg+xml"),
    "/fonts/Poppins-Regular.ttf": ("fonts/Poppins-Regular.ttf", "font/ttf"),
    "/fonts/Poppins-Medium.ttf": ("fonts/Poppins-Medium.ttf", "font/ttf"),
    "/fonts/Poppins-SemiBold.ttf": ("fonts/Poppins-SemiBold.ttf", "font/ttf"),
}


def _allowed_client(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return addr.is_private or addr.is_loopback or addr in TAILSCALE


def lan_addresses():
    """IPs deste PC que o celular consegue usar: Wi-Fi/cabo primeiro, Tailscale depois."""
    found = []
    try:
        # não envia nada: só pergunta ao Windows qual placa sai para a internet
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    lan, tail = [], []
    for ip in found:
        addr = ipaddress.ip_address(ip)
        if addr in TAILSCALE:
            tail.append(ip)
        elif addr.is_private and not addr.is_loopback and addr not in VIRTUAL:
            lan.append(ip)
    return list(dict.fromkeys(lan)), list(dict.fromkeys(tail))


class RemoteServer:
    def __init__(self, api, engine, root):
        self.api = api
        self.engine = engine
        self.path = Path(root) / "celular.json"
        self.settings = self._load()
        self.config_rev = 0  # muda quando o celular troca o perfil; o painel do PC recarrega
        self._server = None
        self._error = None
        self._fails = {}  # ip -> [erros seguidos, bloqueado até]
        self._lock = threading.Lock()
        if self.settings["enabled"]:
            self._start()

    # ---------- configurações (arquivo próprio: perfis não trocam o PIN) ----------
    def _load(self):
        data = {"enabled": False, "pin": "", "port": DEFAULT_PORT}
        try:
            data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        if not re.fullmatch(r"\d{6}", str(data["pin"])):
            data["pin"] = self._new_pin()
        return data

    def _save(self):
        try:
            self.path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        except OSError as e:
            self.engine.log(f"Celular: não consegui salvar {self.path.name} ({e}).", "error")

    @staticmethod
    def _new_pin():
        return f"{secrets.randbelow(10 ** 6):06d}"

    def info(self):
        lan, tail = lan_addresses()
        port = self.settings["port"]
        return {
            "enabled": self.settings["enabled"],
            "running": self._server is not None,
            "pin": self.settings["pin"],
            "port": port,
            "urls": [f"http://{ip}:{port}" for ip in lan],
            "tailscale": [f"http://{ip}:{port}" for ip in tail],
            "error": self._error,
        }

    def set_enabled(self, enabled):
        self.settings["enabled"] = bool(enabled)
        self._save()
        if enabled:
            self._start()
        else:
            self.stop()
            self.engine.log("Controle pelo celular desligado.", "info")
        return self.info()

    def new_pin(self):
        self.settings["pin"] = self._new_pin()
        self._save()
        self._fails.clear()
        self.engine.log("Novo PIN do celular gerado; o aparelho antigo precisa do PIN novo.", "info")
        return self.info()

    # ---------- servidor ----------
    def _start(self):
        if self._server:
            return
        self._error = None
        try:
            server = ThreadingHTTPServer(("0.0.0.0", int(self.settings["port"])), self._handler())
        except OSError as e:
            self._error = f"A porta {self.settings['port']} está em uso ou bloqueada ({e.strerror or e})."
            self.engine.log(f"Celular: {self._error}", "error")
            return
        server.daemon_threads = True
        self._server = server
        threading.Thread(target=server.serve_forever, name="celular", daemon=True).start()
        lan, _ = lan_addresses()
        where = f"http://{lan[0]}:{self.settings['port']}" if lan else f"porta {self.settings['port']}"
        self.engine.log(f"Controle pelo celular ligado em {where}.", "success")

    def stop(self):
        server, self._server = self._server, None
        if server:
            server.shutdown()
            server.server_close()

    # ---------- PIN e bloqueio ----------
    def check_pin(self, ip, pin):
        """None se ok; senão a mensagem de erro (e o status HTTP)."""
        now = time.time()
        with self._lock:
            fails, until = self._fails.get(ip, (0, 0))
            if until > now:
                return 429, f"Muitas tentativas. Tente de novo em {int(until - now) + 1} s."
            if hmac.compare_digest(str(pin or "").encode(), self.settings["pin"].encode()):
                self._fails.pop(ip, None)
                return None
            fails += 1
            if fails >= MAX_TRIES:
                self._fails[ip] = (0, now + LOCK_SECONDS)
                self.engine.log(f"Celular: {MAX_TRIES} PINs errados vindos de {ip}; bloqueado por {LOCK_SECONDS} s.", "warn")
            else:
                self._fails[ip] = (fails, 0)
        return 401, "PIN incorreto."

    # ---------- ações ----------
    def _modules(self):
        order = [m for m in self.engine.config.get("module_order", []) if m in MODULES]
        order += [m for m in MODULES if m not in order]
        return [{"id": m, "name": MODULES[m]} for m in order]

    def handle(self, method, path, query, body):
        api = self.api
        if method == "GET" and path == "/api/info":
            cfg = self.engine.config
            return {
                "title": PANEL_TITLE,
                "modules": self._modules(),
                "profiles": api.list_profiles(),
                "profile": cfg.get("profile", ""),
                "macro": cfg.get("macro", {}).get("name", ""),
            }
        if method == "GET" and path == "/api/state":
            try:
                since = int(query.get("since", ["0"])[0])
            except ValueError:
                since = 0
            s = api.get_state(since)
            s.pop("pick", None)
            s["logs"] = s["logs"][-60:]
            st = api.get_stats()["session"]
            s["session"] = {"seconds_open": st["seconds_open"], "kills": st["kills"],
                            "balls": sum(st["balls"].values()), "caught": sum(st["caught"].values())}
            s["profile"] = self.engine.config.get("profile", "")
            return s
        if method != "POST":
            return None
        if path == "/api/toggle":
            name = str(body.get("name", ""))
            if name not in {m["id"] for m in self._modules()}:
                return 400, "Módulo desconhecido."
            on = bool(body.get("on"))
            macro = self.engine.config.get("macro", {})
            ok = api.toggle_module(name, on, macro.get("name") or None, bool(macro.get("loop")))
            return {"ok": True, "running": bool(ok) if on else False}
        if path == "/api/stop_all":
            api.stop_all()
            self.engine.log("Tudo parado pelo celular.", "info")
            return {"ok": True}
        if path == "/api/profile":
            name = str(body.get("name", ""))
            if name not in api.list_profiles():
                return 400, "Perfil não encontrado."
            api.stop_all()
            api.load_profile(name)
            self.config_rev += 1
            return {"ok": True, "profile": name}
        if path == "/api/timer":
            minutes = body.get("minutes")
            try:
                minutes = float(minutes) if minutes else None
            except (TypeError, ValueError):
                return 400, "Minutos inválidos."
            if minutes is not None and not 0 < minutes <= 24 * 60:
                return 400, "Use entre 1 minuto e 24 horas."
            api.set_timer(minutes)
            return {"ok": True}
        return None

    def _handler(self):
        remote = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "VenenoCelular"
            sys_version = ""

            def log_message(self, *args):  # sem poluir logs/saida.txt a cada consulta
                pass

            def _send(self, status, payload, ctype="application/json; charset=utf-8"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(data)

            def _route(self, method):
                ip = self.client_address[0]
                if not _allowed_client(ip):
                    return self._send(403, {"error": "Só aparelhos da rede de casa."})
                url = urlparse(self.path)
                if method == "GET" and url.path in STATIC:
                    name, ctype = STATIC[url.path]
                    try:
                        return self._send(200, (UI_DIR / name).read_bytes(), ctype)
                    except OSError:
                        return self._send(404, {"error": "Arquivo não encontrado."})
                if not url.path.startswith("/api/"):
                    return self._send(404, {"error": "Não encontrado."})
                denied = remote.check_pin(ip, self.headers.get("X-Pin"))
                if denied:
                    return self._send(denied[0], {"error": denied[1]})
                body = {}
                if method == "POST":
                    try:
                        size = min(int(self.headers.get("Content-Length") or 0), 4096)
                        body = json.loads(self.rfile.read(size) or b"{}")
                        if not isinstance(body, dict):
                            raise ValueError
                    except (ValueError, UnicodeDecodeError):
                        return self._send(400, {"error": "Pedido inválido."})
                try:
                    out = remote.handle(method, url.path, parse_qs(url.query), body)
                except Exception as e:  # erro do motor vira mensagem no celular, não derruba o servidor
                    return self._send(500, {"error": str(e) or e.__class__.__name__})
                if out is None:
                    return self._send(404, {"error": "Não encontrado."})
                if isinstance(out, tuple):
                    return self._send(out[0], {"error": out[1]})
                return self._send(200, out)

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

        return Handler
