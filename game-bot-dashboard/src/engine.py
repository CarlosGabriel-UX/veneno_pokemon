"""Motor do bot: os mesmos módulos dos scripts da raiz (battle.py, captura.py,
cave bot.py), mas rodando em threads que podem ser ligadas/desligadas pelo painel."""

import base64
import copy
import ctypes
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from collections import deque
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np

try:
    import pyautogui as pg
    pg.FAILSAFE = True
    pg.useImageNotFoundException(False)
except Exception as e:  # pragma: no cover
    pg = None
    PG_ERROR = str(e)

try:
    import interception as ic
    from interception import inputs as ic_inputs
except Exception as e:  # pragma: no cover
    ic = None
    ic_inputs = None
    IC_ERROR = str(e)

# O pacote "interception" (bindings cffi, outro projeto) instala na mesma pasta do
# "interception-python" e o _utils.pyd dele sombreia o _utils.py: teclado funciona,
# mas qualquer clique com coordenada quebra com "has no attribute 'to_interception_coordinate'".
IC_CONFLICT = bool(ic_inputs and not hasattr(getattr(ic_inputs, "_utils", None), "to_interception_coordinate"))
IC_FIX_CMD = "pip uninstall -y interception interception-python && pip install interception-python==1.13.6"


PANEL_TITLE = "Veneno do Pokémon"

DEFAULT_CONFIG = {
    "battle": {
        "attack_keys": ["e", "q"],
        "interval": 0.5,
        "confidence": 0.9,
    },
    "capture": {
        "key": "1",
        "interval": 0.5,
        "confidence": 0.75,
        "region": [3, 28, 1910, 993],
        "targets": ["croa.png", "croa_2.png"],
        "alert_on": [],  # avisa com som quando aparecer, mesmo sem capturar
        "confirm_wait": 4,  # segundos procurando a mensagem de sucesso depois de cada pokébola
    },
    "cavebot": {
        "walk_time": 9,
        "confidence": 0.8,
        "map_region": [1730, 57, 182, 270],
        "hp_pixel": [1748, 286],
        "hp_color": [255, 0, 0],
        "route": [],  # [{"name": "1.png", "time": 9}, ...]; vazio = todos de imags/map em ordem
    },
    "heal": {
        "key": "f1",
        "pixel": [],  # ponto da barra de vida do SEU pokémon; vazio = não configurado
        "color": [],
        "cooldown": 2,
        "interval": 0.3,
        "faint_pixel": [],  # ponto no começo da barra de vida do SEU pokémon; vazio = não vigia desmaio
        "faint_color": [],
    },
    "safety": {
        "pause_unfocused": True,
        "fight_timeout": 60,
        "max_misses": 5,
        "color_tolerance": 30,  # quanto cada canal (R, G, B) pode variar e ainda contar como a mesma cor
    },
    "alerts": {
        "sound": True,
    },
    "hotkeys": {"battle": "", "capture": "", "cavebot": "", "heal": ""},
    "window_title": "otPokemon | Lisalon | South America",
    "window_ref": None,  # canto da janela do jogo quando as coordenadas foram marcadas
    "stop_hotkey": "F12",
    "theme": "roxo",  # cor principal do painel: roxo, verde ou azul
    "mascot": "auto",  # auto = a garota da cor do painel; "" = nenhuma; ou um arquivo de imags/mascotes
    "mascot_mode": "canto",  # canto = pequena na coluna da esquerda; fundo = grande e transparente atrás do painel; tela = esticada no painel inteiro
    "mascot_opacity": 50,  # % de opacidade no modo fundo
}

MASCOT_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# recortes de mensagens do jogo: captura que deu certo e falta de pokébola
SUCCESS_IMAGE = "captura_ok/sucesso.png"
NO_BALL_IMAGE = "captura_ok/sem_pokebola.png"
MESSAGE_IMAGES = {"success": SUCCESS_IMAGE, "noball": NO_BALL_IMAGE}
FAINT_READS = 3  # leituras seguidas (1 por segundo) com a barra vazia antes de parar tudo

PICK_HOTKEY = "F8"
VK_CODES = {f"F{i}": 0x6F + i for i in range(1, 13)}
VK_ESCAPE = 0x1B

MODULES = {
    "battle": "Batalha",
    "capture": "Captura",
    "cavebot": "Cavebot",
    "heal": "Cura",
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate(cfg):
    """Configs antigas guardavam a rota como lista de nomes em cavebot.waypoints."""
    cave = cfg.get("cavebot", {})
    old = cave.pop("waypoints", None)
    if old and not cave.get("route"):
        cave["route"] = [{"name": n, "time": cave.get("walk_time", 9)} for n in old]
    return cfg


def _color_close(rgb, target, tolerance):
    """True se cada canal de rgb está a no máximo `tolerance` do canal de target."""
    if rgb is None or not target or len(rgb) != len(target):
        return False
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(rgb, target))


# Batalha e Captura fazem o mesmo que o Cavebot já faz sozinho; rodando juntos, atacam e clicam por cima.
CONFLICTS = {"cavebot": ("battle", "capture")}


def _safe_name(name):
    name = re.sub(r"[^\w\- ]", "", str(name).strip(), flags=re.UNICODE).strip().replace(" ", "_")
    return name.lower()[:40] or "pokemon"


def _read_image(path):
    """cv2.imread não abre caminhos com acento no Windows; isso abre."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _write_png(path, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(str(path))
    return ok


def _jpeg_data_uri(img, max_w):
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


# Instância própria da user32 com tipos declarados (HWND é 64 bits); não mexe na do pygetwindow
_user32 = ctypes.WinDLL("user32") if hasattr(ctypes, "WinDLL") else None
if _user32:
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def _window_text(hwnd):
    if not hwnd:
        return ""
    n = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


class Stats:
    """Histórico em SQLite (estatisticas.db) + contadores da sessão atual."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS eventos (quando TEXT DEFAULT (datetime('now','localtime')), tipo TEXT, nome TEXT, modulo TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS tempo (quando TEXT DEFAULT (datetime('now','localtime')), modulo TEXT, segundos REAL)")
        self.session_start = time.time()
        self.kills = 0
        self.heals = 0
        self.balls = {}
        self.caught = {}
        self.run_seconds = {name: 0.0 for name in MODULES}
        self.started = {}

    def _db(self):
        return sqlite3.connect(str(self.path))

    def _exec(self, sql, args):
        with self.lock:
            try:
                with self._db() as db:
                    db.execute(sql, args)
            except sqlite3.Error:
                pass

    def kill(self, module):
        self.kills += 1
        self._exec("INSERT INTO eventos (tipo, nome, modulo) VALUES ('batalha', NULL, ?)", (module,))

    def ball(self, pokemon, module):
        self.balls[pokemon] = self.balls.get(pokemon, 0) + 1
        self._exec("INSERT INTO eventos (tipo, nome, modulo) VALUES ('pokebola', ?, ?)", (pokemon, module))

    def capture(self, pokemon, module):
        self.caught[pokemon] = self.caught.get(pokemon, 0) + 1
        self._exec("INSERT INTO eventos (tipo, nome, modulo) VALUES ('captura', ?, ?)", (pokemon, module))

    def heal(self):
        self.heals += 1
        self._exec("INSERT INTO eventos (tipo, nome, modulo) VALUES ('cura', NULL, 'heal')", ())

    def module_on(self, name):
        self.started[name] = time.time()

    def module_off(self, name):
        t = self.started.pop(name, None)
        if t:
            secs = time.time() - t
            self.run_seconds[name] += secs
            self._exec("INSERT INTO tempo (modulo, segundos) VALUES (?, ?)", (name, secs))

    def snapshot(self, days=14):
        now = time.time()
        run = {n: s + (now - self.started[n] if n in self.started else 0) for n, s in self.run_seconds.items()}
        since = f"-{days - 1} days"
        with self.lock, self._db() as db:
            total_balls = dict(db.execute("SELECT nome, COUNT(*) FROM eventos WHERE tipo='pokebola' GROUP BY nome").fetchall())
            total_caught = dict(db.execute("SELECT nome, COUNT(*) FROM eventos WHERE tipo='captura' GROUP BY nome").fetchall())
            total_kills = db.execute("SELECT COUNT(*) FROM eventos WHERE tipo='batalha'").fetchone()[0]
            day_events = db.execute(
                "SELECT date(quando), tipo, COUNT(*) FROM eventos WHERE date(quando) >= date('now','localtime',?) GROUP BY 1, 2",
                (since,)).fetchall()
            day_time = dict(db.execute(
                "SELECT date(quando), SUM(segundos) FROM tempo WHERE date(quando) >= date('now','localtime',?) GROUP BY 1",
                (since,)).fetchall())

        today = dt.date.today()
        daily = []
        for i in range(days - 1, -1, -1):
            d = (today - dt.timedelta(days=i)).isoformat()
            ev = {t: c for day, t, c in day_events if day == d}
            secs = day_time.get(d, 0) or 0
            if i == 0:  # inclui o que está rodando agora
                secs += sum(now - t for t in self.started.values())
            daily.append({"day": d, "balls": ev.get("pokebola", 0), "kills": ev.get("batalha", 0), "seconds": secs})
        return {
            "session": {"seconds_open": now - self.session_start, "run_seconds": run, "kills": self.kills,
                        "heals": self.heals, "balls": self.balls, "caught": self.caught},
            "total": {"kills": total_kills, "balls": total_balls, "caught": total_caught},
            "daily": daily,
        }

    def reset_history(self):
        with self.lock, self._db() as db:
            db.execute("DELETE FROM eventos")
            db.execute("DELETE FROM tempo")
        self.kills = 0
        self.heals = 0
        self.balls = {}
        self.caught = {}
        self.run_seconds = {name: 0.0 for name in MODULES}
        self.session_start = time.time()
        self.started = {n: time.time() for n in self.started}


class BotEngine:
    def __init__(self, root: Path, config_path: Path):
        self.root = Path(root)
        self.img = self.root / "imags"
        self.config_path = Path(config_path)
        self.profiles_dir = self.root / "perfis"
        self.logs_dir = self.root / "logs"
        self.config = self._load_config()
        self.stats = Stats(self.root / "estatisticas.db")

        self._logs = deque(maxlen=800)
        self._seq = 0
        self._log_lock = threading.Lock()

        self._threads = {}
        self._stops = {}
        self._status = {name: "Parado" for name in MODULES}
        self._ic_ready = False
        self._ic_lock = threading.Lock()
        self._templates = {}
        self._health = {"game": False, "focused": False, "driver": False}
        self._game_rect = None  # (left, top, width, height) da janela do jogo
        self._pick = None
        self._deadline = None
        self._alerted = {}
        self._confirm_lock = threading.Lock()
        self._confirm = None  # (pokémon, módulo, até quando procurar) da última pokébola
        self._faint_reads = 0

        threading.Thread(target=self._hotkey_watcher, daemon=True).start()
        threading.Thread(target=self._health_watcher, daemon=True).start()

        if pg is None:
            self.log(f"pyautogui não carregou: {PG_ERROR}", "error")
        if ic is None:
            self.log(f"interception não carregou: {IC_ERROR}", "error")
        elif IC_CONFLICT:
            self.log("Tem outro pacote 'interception' instalado por cima do interception-python. "
                     f"Os cliques vão funcionar, mas para corrigir de vez rode: {IC_FIX_CMD}", "warn")
        self.log("Painel pronto.", "success")

    # ---------- config ----------
    def _load_config(self):
        try:
            with open(self.config_path, encoding="utf-8") as f:
                return _merge(DEFAULT_CONFIG, _migrate(json.load(f)))
        except Exception:
            return copy.deepcopy(DEFAULT_CONFIG)

    def save_config(self, new_config, quiet=False):
        new_config = _migrate(dict(new_config))
        # a referência da janela é gerenciada pelo motor; o painel pode estar com uma cópia velha
        if not new_config.get("window_ref") and self.config.get("window_ref"):
            new_config["window_ref"] = self.config["window_ref"]
        self.config = _merge(DEFAULT_CONFIG, new_config)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        if not quiet:
            self.log("Configurações salvas.", "success")
        return self.config

    # ---------- perfis ----------
    def list_profiles(self):
        if not self.profiles_dir.exists():
            return []
        return sorted(p.stem for p in self.profiles_dir.glob("*.json"))

    def save_profile(self, name, config):
        name = str(name).strip()
        if not name or re.search(r'[\\/:*?"<>|]', name):
            raise ValueError("Nome de perfil inválido.")
        config = dict(config, profile=name)
        self.save_config(config, quiet=True)
        self.profiles_dir.mkdir(exist_ok=True)
        with open(self.profiles_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        self.log(f"Perfil '{name}' salvo.", "success")
        return self.config

    def load_profile(self, name):
        with open(self.profiles_dir / f"{name}.json", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["profile"] = name
        self.config["window_ref"] = None  # o perfil traz a própria referência
        self.save_config(cfg, quiet=True)
        self.log(f"Perfil '{name}' carregado.", "success")
        return self.config

    def delete_profile(self, name):
        (self.profiles_dir / f"{name}.json").unlink(missing_ok=True)
        if self.config.get("profile") == name:
            self.config.pop("profile", None)
            self.save_config(self.config, quiet=True)
        self.log(f"Perfil '{name}' excluído.", "info")
        return self.config

    # ---------- logs / estado ----------
    def log(self, msg, level="info", module=None):
        entry = {"time": time.strftime("%H:%M:%S"), "level": level, "module": module, "msg": msg}
        with self._log_lock:
            self._seq += 1
            entry["seq"] = self._seq
            self._logs.append(entry)
            try:
                self.logs_dir.mkdir(exist_ok=True)
                tag = f"[{MODULES[module]}] " if module else ""
                with open(self.logs_dir / f"{time.strftime('%Y-%m-%d')}.txt", "a", encoding="utf-8") as f:
                    f.write(f"{entry['time']} {level.upper():7} {tag}{msg}\n")
            except OSError:
                pass
        if module:
            self._status[module] = msg

    def open_logs_folder(self):
        self.logs_dir.mkdir(exist_ok=True)
        os.startfile(self.logs_dir)

    def state(self, since=0):
        with self._log_lock:
            logs = [l for l in self._logs if l["seq"] > since]
        out = {
            "running": {name: self.is_running(name) for name in MODULES},
            "status": dict(self._status),
            "health": dict(self._health),
            "logs": logs,
            "pick": None,
            "timer": max(0, self._deadline - time.time()) if self._deadline else None,
            # há quanto tempo cada módulo ligado está rodando
            "uptime": {n: time.time() - t for n, t in dict(self.stats.started).items()},
        }
        pick = self._pick
        if pick:
            out["pick"] = {k: pick[k] for k in ("id", "kind", "target", "points", "result", "cancelled")}
            if pg is not None and pick["result"] is None and not pick["cancelled"]:
                x, y = pg.position()
                out["pick"]["mouse"] = {"x": x, "y": y, "rgb": list(self._pixel(x, y) or (0, 0, 0))}
        return out

    def is_running(self, name):
        t = self._threads.get(name)
        return bool(t and t.is_alive())

    def any_running(self):
        return any(self.is_running(n) for n in MODULES)

    # ---------- alertas ----------
    def alert(self, msg, level="error", module=None):
        self.log(msg, level, module)
        if not self.config["alerts"].get("sound"):
            return
        try:
            import winsound
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            pass
        self._flash_panel()

    @staticmethod
    def _flash_panel():
        """Pisca o painel na barra de tarefas até alguém olhar."""
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]
        try:
            hwnd = _user32.FindWindowW(None, PANEL_TITLE)
            if hwnd:
                info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)  # FLASHW_ALL | FLASHW_TIMERNOFG
                _user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    # ---------- timer ----------
    def set_timer(self, minutes=None, at=None):
        if at:
            h, m = (int(v) for v in str(at).split(":"))
            now = dt.datetime.now()
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += dt.timedelta(days=1)
            self._deadline = target.timestamp()
            self.log(f"Timer: desliga tudo às {target:%H:%M}.", "info")
        elif minutes:
            self._deadline = time.time() + float(minutes) * 60
            self.log(f"Timer: desliga tudo em {int(float(minutes))} min.", "info")
        else:
            if self._deadline:
                self.log("Timer cancelado.", "info")
            self._deadline = None
        return self._deadline

    # ---------- janela do jogo ----------
    def _find_game_windows(self):
        try:
            import pygetwindow as gw
        except Exception:
            return []
        title = self.config["window_title"].strip().casefold()
        return [w for w in gw.getAllWindows() if w.title and w.title.strip().casefold() == title]

    def _game_focused(self):
        try:
            title = _window_text(_user32.GetForegroundWindow())
        except Exception:
            return True
        return title.strip().casefold() == self.config["window_title"].strip().casefold()

    def _offset(self):
        """Quanto a janela do jogo andou desde que as coordenadas foram marcadas."""
        ref, rect = self.config.get("window_ref"), self._game_rect
        if not ref or not rect:
            return 0, 0
        return rect[0] - ref[0], rect[1] - ref[1]

    def _abs_region(self, region):
        if not region:
            return None
        dx, dy = self._offset()
        return [region[0] + dx, region[1] + dy, region[2], region[3]]

    def _abs_point(self, point):
        dx, dy = self._offset()
        return point[0] + dx, point[1] + dy

    def set_window_ref(self):
        """As coordenadas atuais estão certas para a janela onde ela está agora."""
        if not self._game_rect:
            raise RuntimeError("Abra o jogo primeiro: não achei a janela.")
        self.config["window_ref"] = list(self._game_rect[:2])
        self.save_config(self.config, quiet=True)
        self.log(f"Referência da janela: canto em {self._game_rect[0]}, {self._game_rect[1]}.", "success")
        return self.config

    def _health_watcher(self):
        was_found = False
        while True:
            try:
                wins = self._find_game_windows()
                w = next((w for w in wins if not w.isMinimized), wins[0] if wins else None)
                self._game_rect = (w.left, w.top, w.width, w.height) if w and not w.isMinimized else None
            except Exception:
                wins, self._game_rect = [], None
            found = bool(wins)
            self._health["game"] = found
            self._health["focused"] = found and self._game_focused()
            ctx = getattr(ic_inputs, "_g_context", None)
            self._health["driver"] = bool(ctx is not None and getattr(ctx, "valid", False))

            if found and self._game_rect and not self.config.get("window_ref"):
                self.config["window_ref"] = list(self._game_rect[:2])
                self.save_config(self.config, quiet=True)
            if was_found and not found and self.any_running():
                self.stop_all()
                self.alert("A janela do jogo sumiu: parei tudo.")
            was_found = found
            self._check_faint()

            if self._deadline and time.time() >= self._deadline:
                self._deadline = None
                if self.any_running():
                    self.stop_all()
                    self.alert("Timer acabou: parei tudo.", "warn")
                else:
                    self.log("Timer acabou.", "info")
            time.sleep(1)

    def _check_faint(self):
        """Para tudo quando o começo da barra de vida do seu pokémon perde a cor (desmaiou)."""
        c = self.config["heal"]
        point, color = c.get("faint_pixel") or [], c.get("faint_color") or []
        if len(point) != 2 or len(color) != 3 or not self.any_running() or not self._health["focused"]:
            self._faint_reads = 0
            return
        rgb = self._pixel(*self._abs_point(point))
        if rgb is None or _color_close(rgb, color, self._tolerance()):
            self._faint_reads = 0
            return
        self._faint_reads += 1
        if self._faint_reads >= FAINT_READS:
            self._faint_reads = 0
            self.stop_all()
            self.alert("Seu pokémon desmaiou: parei tudo.")

    def _wait_ready(self, stop, module):
        """Segura o módulo enquanto o jogo não estiver em primeiro plano. False se mandaram parar."""
        if not self.config["safety"].get("pause_unfocused", True):
            return not stop.is_set()
        paused = False
        while not stop.is_set() and not self._game_focused():
            if not paused:
                self.log("Pausado: o jogo não está em primeiro plano.", "warn", module)
                paused = True
            stop.wait(0.4)
        if paused and not stop.is_set():
            self.log("Jogo em foco, continuando.", "info", module)
        return not stop.is_set()

    # ---------- imagens ----------
    def capture_images(self):
        return sorted(p.name for p in (self.img / "captura").glob("*.png") if p.name != "tela.png")

    def map_waypoints(self):
        files = [p for p in (self.img / "map").glob("*.png") if p.stem.isdigit()]
        return [p.name for p in sorted(files, key=lambda p: int(p.stem))]

    def mascot_images(self):
        folder = self.img / "mascotes"
        return sorted(p.name for p in folder.glob("*") if p.suffix.lower() in MASCOT_EXTS) if folder.exists() else []

    def add_mascot(self, src_path):
        """Copia uma imagem para imags/mascotes e devolve o nome dela."""
        src = Path(src_path)
        if src.suffix.lower() not in MASCOT_EXTS:
            raise ValueError("Use uma imagem PNG, JPG, GIF ou WEBP.")
        folder = self.img / "mascotes"
        folder.mkdir(exist_ok=True)
        base = _safe_name(src.stem)
        dest = folder / f"{base}{src.suffix.lower()}"
        n = 2
        while dest.exists():
            dest = folder / f"{base}_{n}{src.suffix.lower()}"
            n += 1
        shutil.copyfile(src, dest)
        self.log(f"Mascote '{dest.stem}' adicionada.", "success")
        return dest.name

    def open_mascot_folder(self):
        folder = self.img / "mascotes"
        folder.mkdir(exist_ok=True)
        os.startfile(folder)

    def route(self):
        c = self.config["cavebot"]
        if c["route"]:
            return [(wp["name"], float(wp.get("time", c["walk_time"]))) for wp in c["route"]]
        return [(name, float(c["walk_time"])) for name in self.map_waypoints()]

    def add_capture_image(self, name, src_path=None, region=None):
        """Adiciona um pokémon à pasta de captura, de um arquivo ou recortando a tela."""
        if src_path:
            img = _read_image(src_path)
            if img is None:
                raise ValueError("Não consegui abrir essa imagem.")
        else:
            img, _ = self._grab(region)
        base = _safe_name(name)
        dest = self.img / "captura" / f"{base}.png"
        n = 2
        while dest.exists():
            dest = self.img / "captura" / f"{base}_{n}.png"
            n += 1
        _write_png(dest, img)
        self.log(f"Pokémon '{dest.stem}' adicionado.", "success")
        return dest.name

    def add_waypoint(self, region):
        """Recorta um marcador do minimapa e salva como o próximo número da rota."""
        img, _ = self._grab(region)
        existing = [int(p.stem) for p in (self.img / "map").glob("*.png") if p.stem.isdigit()]
        removed = self.img / "map" / "removidos"
        if removed.exists():
            existing += [int(p.stem) for p in removed.glob("*.png") if p.stem.isdigit()]
        dest = self.img / "map" / f"{max(existing, default=0) + 1}.png"
        _write_png(dest, img)
        route = self.config["cavebot"]["route"]
        if route:
            route.append({"name": dest.name, "time": self.config["cavebot"]["walk_time"]})
            self.save_config(self.config, quiet=True)
        self.log(f"Ponto {dest.stem} adicionado à rota.", "success")
        return dest.name

    def _move_to_trash(self, folder, name):
        path = self.img / folder / Path(name).name
        trash = self.img / folder / "removidos"
        trash.mkdir(exist_ok=True)
        if path.exists():
            shutil.move(str(path), str(trash / path.name))
        return path

    def remove_capture_image(self, name):
        path = self._move_to_trash("captura", name)
        c = self.config["capture"]
        for key in ("targets", "alert_on"):
            if name in c[key]:
                c[key].remove(name)
        self.save_config(self.config, quiet=True)
        self.log(f"'{path.stem}' movido para imags/captura/removidos.", "info")

    def remove_waypoint(self, name):
        self._move_to_trash("map", name)
        c = self.config["cavebot"]
        c["route"] = [r for r in c["route"] if r["name"] != name]
        self.save_config(self.config, quiet=True)
        self.log(f"Ponto {Path(name).stem} movido para imags/map/removidos.", "info")

    # ---------- detecção ----------
    def _template(self, rel_path):
        path = self.img / rel_path
        mtime = path.stat().st_mtime if path.exists() else None
        cached = self._templates.get(rel_path)
        if cached and cached[0] == mtime:
            return cached[1]
        tpl = _read_image(path) if mtime else None
        self._templates[rel_path] = (mtime, tpl)
        return tpl

    def _grab(self, region=None):
        region = tuple(int(v) for v in region) if region else None
        shot = pg.screenshot(region=region)
        img = cv2.cvtColor(np.array(shot), cv2.COLOR_RGB2BGR)
        return img, (region[0], region[1]) if region else (0, 0)

    @staticmethod
    def _match(tpl, img):
        if tpl is None or tpl.shape[0] > img.shape[0] or tpl.shape[1] > img.shape[1]:
            return 0.0, (0, 0)
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        return float(score), loc

    def _locate(self, rel_path, confidence, region=None, img=None, offset=(0, 0)):
        """Centro da imagem na tela, ou None. Mesmo critério do pyautogui (TM_CCOEFF_NORMED),
        mas pega a melhor correspondência em vez da primeira."""
        tpl = self._template(rel_path)
        if tpl is None:
            return None
        if img is None:
            img, offset = self._grab(region)
        score, (x, y) = self._match(tpl, img)
        if score < confidence:
            return None
        th, tw = tpl.shape[:2]
        return pg.Point(offset[0] + x + tw // 2, offset[1] + y + th // 2)

    def _pixel(self, x, y):
        """Cor do pixel, ou None se não deu para ler (aí ninguém deve agir pela cor)."""
        try:
            return tuple(pg.pixel(int(x), int(y)))
        except Exception:
            return None

    def _tolerance(self):
        try:
            return max(0, int(self.config["safety"].get("color_tolerance", 30)))
        except (TypeError, ValueError):
            return 30

    def test_detection(self, kind):
        """Uma busca só, com print marcando o que achou — para ajustar precisão e áreas."""
        if pg is None:
            raise RuntimeError("pyautogui não está disponível.")
        cfg = self.config
        if kind == "capture":
            region = self._abs_region(cfg["capture"]["region"])
            conf = cfg["capture"]["confidence"]
            names = [f"captura/{n}" for n in (cfg["capture"]["targets"] or self.capture_images())]
            max_w = 1000
        elif kind == "map":
            region = self._abs_region(cfg["cavebot"]["map_region"])
            conf = cfg["cavebot"]["confidence"]
            names = [f"map/{n}" for n, _ in self.route()]
            max_w = 600
        elif kind == "battle":
            region = None
            conf = cfg["battle"]["confidence"]
            names = ["battle/batalha_vazia.png"]
            max_w = 1000
        else:
            raise ValueError(kind)

        img, offset = self._grab(region)
        canvas = img.copy()
        scale = 2 if kind == "map" else 1
        if scale != 1:
            canvas = cv2.resize(canvas, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
        thick = max(2, int(img.shape[1] / max_w * 2))

        results = []
        for rel in names:
            tpl = self._template(rel)
            if tpl is None:
                results.append({"name": Path(rel).stem, "score": 0, "found": False, "missing": True})
                continue
            score, (x, y) = self._match(tpl, img)
            found = score >= conf
            th, tw = tpl.shape[:2]
            if found:
                p1 = (x * scale, y * scale)
                p2 = ((x + tw) * scale, (y + th) * scale)
                cv2.rectangle(canvas, p1, p2, (90, 224, 143), thick)
                cv2.putText(canvas, Path(rel).stem, (p1[0], max(12, p1[1] - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                            0.45 * thick, (90, 224, 143), max(1, thick // 2), cv2.LINE_AA)
            results.append({
                "name": Path(rel).stem,
                "score": round(score, 3),
                "found": found,
                "x": offset[0] + x + tw // 2,
                "y": offset[1] + y + th // 2,
            })

        extra = None
        if kind == "battle":
            x, y = self._abs_point(cfg["cavebot"]["hp_pixel"])
            rgb = self._pixel(x, y)
            extra = {"hp_pixel": [x, y], "rgb": list(rgb or (0, 0, 0)),
                     "matches": _color_close(rgb, cfg["cavebot"]["hp_color"], self._tolerance())}
            cv2.circle(canvas, (int(x), int(y)), 10 * thick, (255, 112, 169), thick)

        self.log(f"Teste de detecção ({kind}): {sum(r['found'] for r in results)}/{len(results)} encontrados.", "info")
        return {
            "kind": kind,
            "confidence": conf,
            "image": _jpeg_data_uri(canvas, max_w),
            "results": sorted(results, key=lambda r: -r["score"]),
            "extra": extra,
            "offset": list(self._offset()),
        }

    # ---------- calibração com o mouse ----------
    def start_pick(self, kind, target):
        """kind: 'point' (1 F8) ou 'region' (canto superior esquerdo + inferior direito)."""
        self._pick = {"id": time.time(), "kind": kind, "target": target, "points": [], "result": None, "cancelled": False}
        return PICK_HOTKEY

    def cancel_pick(self):
        if self._pick:
            self._pick["cancelled"] = True

    def clear_pick(self):
        self._pick = None

    def _pick_press(self):
        pick = self._pick
        if not pick or pick["result"] is not None or pick["cancelled"]:
            return
        x, y = pg.position()
        rgb = list(self._pixel(x, y) or (0, 0, 0))
        pick["points"].append([x, y])
        self._beep()
        # coordenadas salvas na config ficam no "referencial" da janela; recortes usam a tela real
        if pick["target"] in ("new_pokemon", "new_waypoint", "msg_success", "msg_noball"):
            dx = dy = 0
        else:
            if not self.config.get("window_ref") and self._game_rect:
                self.config["window_ref"] = list(self._game_rect[:2])
                self.save_config(self.config, quiet=True)
            dx, dy = self._offset()
        if pick["kind"] == "point":
            pick["result"] = {"x": x - dx, "y": y - dy, "rgb": rgb}
        elif len(pick["points"]) == 2:
            (x1, y1), (x2, y2) = pick["points"]
            left, top = min(x1, x2), min(y1, y2)
            w, h = abs(x2 - x1), abs(y2 - y1)
            if w < 4 or h < 4:
                pick["points"] = []
                self.log("Área muito pequena, marque de novo os dois cantos.", "warn")
                return
            pick["result"] = {"region": [left - dx, top - dy, w, h]}

    @staticmethod
    def _beep():
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass

    # ---------- start / stop ----------
    def start(self, name):
        if name not in MODULES:
            return False
        if self.is_running(name):
            return True
        if pg is None or ic is None:
            self.log("Dependências faltando (veja os erros acima). Módulo não iniciado.", "error", name)
            return False
        for other in CONFLICTS.get(name, ()):
            if self.is_running(other):
                self.stop(other)
                self.log(f"{MODULES[other]} desligada: o {MODULES[name]} já faz isso sozinho.", "warn", other)
        blocker = next((m for m, others in CONFLICTS.items() if name in others and self.is_running(m)), None)
        if blocker:
            self.log(f"O {MODULES[blocker]} já faz isso sozinho. Desligue o {MODULES[blocker]} para usar a {MODULES[name]}.",
                     "warn", name)
            return False
        if name == "heal" and len(self.config["heal"]["pixel"]) != 2:
            self.log("Configure o pixel da sua vida em Ajustes > Cura antes de ligar.", "error", name)
            return False
        if not self._ensure_interception():
            return False

        stop = threading.Event()
        target = getattr(self, f"_run_{name}")

        def runner():
            self.stats.module_on(name)
            self.log("Ligado.", "success", name)
            try:
                target(stop)
            except Exception as e:
                self.alert(f"Erro: {e}", "error", name)
            finally:
                self.stats.module_off(name)
                self.log("Desligado.", "info", name)
                self._status[name] = "Parado"

        self._stops[name] = stop
        t = threading.Thread(target=runner, daemon=True, name=name)
        self._threads[name] = t
        t.start()
        return True

    def stop(self, name):
        ev = self._stops.get(name)
        if ev:
            ev.set()

    def stop_all(self):
        for name in MODULES:
            self.stop(name)

    def toggle(self, name):
        if self.is_running(name):
            self.stop(name)
        else:
            self.start(name)

    def _ensure_interception(self):
        with self._ic_lock:
            if self._ic_ready:
                return True
            try:
                ic.auto_capture_devices(keyboard=True, mouse=True, verbose=False)
                self._ic_ready = True
                return True
            except Exception as e:
                self.log(f"Falha ao iniciar o Interception (driver instalado?): {e}", "error")
                return False

    def _hotkey_watcher(self):
        user32 = getattr(ctypes, "windll", None) and ctypes.windll.user32
        if not user32:
            return
        down_before = set()
        while True:
            stop_vk = VK_CODES.get(str(self.config.get("stop_hotkey", "F12")).upper(), 0x7B)
            pick_vk = VK_CODES[PICK_HOTKEY]
            module_vks = {VK_CODES[k.upper()]: m for m, k in self.config["hotkeys"].items()
                          if k and k.upper() in VK_CODES and VK_CODES[k.upper()] not in (stop_vk, pick_vk)}
            watched = (stop_vk, pick_vk, VK_ESCAPE, *module_vks)
            down = {vk for vk in watched if user32.GetAsyncKeyState(vk) & 0x8000}
            pressed = down - down_before
            down_before = down
            if stop_vk in pressed and self.any_running():
                self.stop_all()
                self.log(f"{self.config.get('stop_hotkey')} pressionado: parando tudo.", "error")
            if pick_vk in pressed and self._pick:
                self._pick_press()
            if VK_ESCAPE in pressed and self._pick and self._pick["result"] is None:
                self.cancel_pick()
            for vk, module in module_vks.items():
                if vk in pressed:
                    threading.Thread(target=self.toggle, args=(module,), daemon=True).start()
            time.sleep(0.05)

    # ---------- ações básicas ----------
    def _battle_empty(self):
        return bool(self._locate("battle/batalha_vazia.png", self.config["battle"]["confidence"]))

    def _click(self, x, y):
        """Move o mouse e clica pelo driver. Não usa ic.click(x, y), que depende do
        _utils interno do interception e quebra quando há outro pacote por cima."""
        pg.moveTo(x, y)
        time.sleep(0.15)
        ic.click()

    def _ball_key(self):
        key = str(self.config["capture"].get("key") or "").strip().lower()
        return key or "1"

    def _attack(self):
        for key in self.config["battle"]["attack_keys"]:
            ic.press(key)

    def _try_capture_once(self, module):
        """Uma passada pelos alvos com um único print da tela. Retorna True se clicou em algum."""
        c = self.config["capture"]
        img, offset = self._grab(self._abs_region(c["region"]))
        caught = False
        for name in dict.fromkeys(c["targets"] + c["alert_on"]):
            pos = self._locate(f"captura/{name}", c["confidence"], img=img, offset=offset)
            if not pos:
                continue
            poke = Path(name).stem
            if name in c["alert_on"] and time.time() - self._alerted.get(name, 0) > 60:
                self._alerted[name] = time.time()
                self.alert(f"{poke} apareceu na tela!", "success", module)
            if name in c["targets"]:
                self.log(f"{poke} encontrado, jogando pokébola.", "success", module)
                ic.press(self._ball_key())
                time.sleep(0.1)
                self._click(pos.x, pos.y)
                self.stats.ball(poke, module)
                if self._out_of_balls(module):
                    return False
                self._watch_success(poke, module)
                caught = True
        return caught

    def _out_of_balls(self, module):
        """Depois de jogar, vê se o jogo avisou que acabaram as pokébolas. Se sim, desliga o módulo."""
        if not self.has_message_image("noball"):
            return False
        time.sleep(0.5)
        if not self._locate(NO_BALL_IMAGE, self.config["capture"]["confidence"]):
            return False
        self.stop(module)
        artigo = "a" if MODULES[module].endswith("a") else "o"
        self.alert(f"Acabaram as pokébolas: desliguei {artigo} {MODULES[module]}.", "error", module)
        return True

    # ---------- mensagens do jogo (captura confirmada, sem pokébola) ----------
    MESSAGE_LOGS = {
        "success": ("Mensagem de captura salva: agora o bot conta as capturas confirmadas.",
                    "Mensagem de captura removida: o bot volta a contar só as pokébolas."),
        "noball": ("Mensagem de sem pokébola salva: o bot para quando elas acabarem.",
                   "Mensagem de sem pokébola removida: o bot não vigia mais as pokébolas."),
    }

    def has_message_image(self, kind):
        return (self.img / MESSAGE_IMAGES[kind]).exists()

    def has_success_image(self):
        return self.has_message_image("success")

    def set_message_image(self, kind, region):
        """Recorta da tela uma mensagem do jogo (kind: success ou noball)."""
        img, _ = self._grab(region)
        dest = self.img / MESSAGE_IMAGES[kind]
        dest.parent.mkdir(exist_ok=True)
        _write_png(dest, img)
        self.log(self.MESSAGE_LOGS[kind][0], "success")
        return True

    def remove_message_image(self, kind):
        path = self.img / MESSAGE_IMAGES[kind]
        if path.exists():
            path.unlink()
        self.log(self.MESSAGE_LOGS[kind][1], "info")

    def _watch_success(self, poke, module):
        """Depois de uma pokébola, procura a mensagem de sucesso por alguns segundos numa thread à parte."""
        if not self.has_success_image():
            return
        until = time.time() + float(self.config["capture"].get("confirm_wait", 4) or 4)
        with self._confirm_lock:
            running = self._confirm is not None
            self._confirm = (poke, module, until)  # outra pokébola só troca o alvo e estende o prazo
        if not running:
            threading.Thread(target=self._confirm_loop, daemon=True).start()

    def _confirm_loop(self):
        conf = self.config["capture"]["confidence"]
        # a mensagem de uma captura anterior pode continuar na tela: só conta quando ela aparece
        # (não estava visível e passou a estar), nunca porque já estava lá
        visible = bool(self._locate(SUCCESS_IMAGE, conf))
        while True:
            with self._confirm_lock:
                poke, module, until = self._confirm
                if time.time() > until:
                    self._confirm = None
                    return
            time.sleep(0.3)
            seen = bool(self._locate(SUCCESS_IMAGE, conf))
            if seen and not visible:
                self.stats.capture(poke, module)
                self.log(f"{poke} capturado!", "success", module)
                with self._confirm_lock:
                    self._confirm = None
                return
            visible = seen

    # ---------- módulos ----------
    def _run_battle(self, stop):
        last = None
        while self._wait_ready(stop, "battle"):
            empty = self._battle_empty()
            if not empty:
                self._attack()
            if empty != last:
                self.log("Sem inimigos na batalha." if empty else "Inimigo detectado, atacando.", "info", "battle")
                # o cavebot já conta as próprias batalhas
                if empty and last is False and not self.is_running("cavebot"):
                    self.stats.kill("battle")
                last = empty
            stop.wait(self.config["battle"]["interval"])

    def _run_capture(self, stop):
        self.log("Procurando pokémons para capturar...", "info", "capture")
        while self._wait_ready(stop, "capture"):
            self._try_capture_once("capture")
            stop.wait(self.config["capture"]["interval"])

    def _run_heal(self, stop):
        last = 0.0
        self.log("Vigiando a vida.", "info", "heal")
        while self._wait_ready(stop, "heal"):
            c = self.config["heal"]
            x, y = self._abs_point(c["pixel"])
            rgb = self._pixel(x, y)
            low = rgb is not None and not _color_close(rgb, c["color"], self._tolerance())
            if low and time.time() - last >= c["cooldown"]:
                ic.press(c["key"])
                last = time.time()
                self.stats.heal()
                self.log(f"Vida baixa, apertei {c['key'].upper()}.", "success", "heal")
            stop.wait(c["interval"])

    def _run_cavebot(self, stop):
        misses = 0
        while not stop.is_set():
            c = self.config["cavebot"]
            route = self.route()
            if not route:
                self.log("Nenhum waypoint na rota.", "error", "cavebot")
                return
            found_any = False
            for wp, walk_time in route:
                if not self._wait_ready(stop, "cavebot"):
                    return
                pos = self._locate(f"map/{wp}", c["confidence"], self._abs_region(c["map_region"]))
                if not pos:
                    continue
                found_any = True
                self.log(f"Indo para o ponto {Path(wp).stem}.", "info", "cavebot")
                prev = pg.position()
                self._click(pos.x, pos.y)
                if stop.wait(walk_time):
                    return
                pg.moveTo(prev)
                if not self._wait_ready(stop, "cavebot"):
                    return
                self._fight_and_capture(stop)
            if found_any:
                misses = 0
                continue
            misses += 1
            limit = int(self.config["safety"]["max_misses"])
            if limit and misses >= limit:
                self.alert(f"Cavebot travado: {misses} voltas sem achar nenhum ponto no minimapa. Parei.", "error", "cavebot")
                return
            self.log(f"Nenhum ponto visível no minimapa ({misses}/{limit}).", "warn", "cavebot")
            stop.wait(2)

    def _fight_and_capture(self, stop):
        if self._battle_empty():
            return
        c = self.config["cavebot"]
        timeout = float(self.config["safety"]["fight_timeout"] or 0)
        self.log("Inimigo na batalha, atacando até morrer.", "info", "cavebot")
        self._attack()
        x, y = self._abs_point(c["hp_pixel"])
        started = time.time()
        while not stop.is_set():
            rgb = self._pixel(x, y)
            # leitura falhou (None): na dúvida, continua lutando até o tempo limite
            if rgb is not None and not _color_close(rgb, c["hp_color"], self._tolerance()):
                break
            if timeout and time.time() - started > timeout:
                self.alert(f"Luta passou de {int(timeout)}s, desisti e segui a rota.", "warn", "cavebot")
                return
            if not self._wait_ready(stop, "cavebot"):
                return
            self._attack()
            stop.wait(1)
        if stop.is_set():
            return
        self.stats.kill("cavebot")
        self.log("Monstro derrotado, tentando captura.", "info", "cavebot")
        for _ in range(5):
            if stop.is_set() or not self._try_capture_once("cavebot"):
                break
            stop.wait(self.config["capture"]["interval"])

    # ---------- opacidade (mesma lógica de opacity.py) ----------
    def set_opacity(self, value):
        windows = self._find_game_windows()
        if not windows:
            self.log(f"Janela '{self.config['window_title']}' não encontrada.", "error")
            return False

        user32 = ctypes.windll.user32
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]

        value = max(1, min(255, int(value)))
        for w in windows:
            hwnd = w._hWnd
            set_long(hwnd, -20, get_long(hwnd, -20) | 0x00080000)  # WS_EX_LAYERED
            user32.SetLayeredWindowAttributes(hwnd, 0, value, 0x2)  # LWA_ALPHA
        self.log(f"Opacidade do jogo: {round(value / 255 * 100)}%.", "info")
        return True
