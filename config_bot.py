"""Configuração dos scripts da raiz (battle.py, captura.py, cave bot.py, opacity.py).

Lê o mesmo config.json que o painel (game-bot-dashboard) salva na raiz do projeto.
Assim, as coordenadas marcadas no painel com F8 valem também para os scripts.
Se o arquivo não existir, usa os valores padrão abaixo (monitor 1920x1080).
"""

import copy
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CAMINHO_CONFIG = RAIZ / "config.json"
PASTA_IMAGENS = RAIZ / "imags"

# Mesmas chaves e padrões do DEFAULT_CONFIG do painel (game-bot-dashboard/src/engine.py)
PADRAO = {
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
    },
    "cavebot": {
        "walk_time": 9,
        "confidence": 0.8,
        "map_region": [1730, 57, 182, 270],
        "hp_pixel": [1748, 286],
        "hp_color": [255, 0, 0],
        "route": [],  # [{"name": "1.png", "time": 9}, ...]; vazio = todos de imags/map em ordem
    },
    "safety": {
        "fight_timeout": 60,
    },
    "window_title": "otPokemon | Lisalon | South America",
}


def _juntar(base, novo):
    saida = copy.deepcopy(base)
    for chave, valor in (novo or {}).items():
        if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
            saida[chave] = _juntar(saida[chave], valor)
        else:
            saida[chave] = valor
    return saida


def carregar():
    """Devolve a configuração do config.json misturada com os padrões."""
    try:
        with open(CAMINHO_CONFIG, encoding="utf-8") as f:
            return _juntar(PADRAO, json.load(f))
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(PADRAO)


def imagem(pasta, nome):
    """Caminho de uma imagem dentro de imags/, funcionando de qualquer pasta."""
    return str(PASTA_IMAGENS / pasta / nome)


def rota(config):
    """Lista de (caminho do ícone, segundos de caminhada) do cavebot."""
    cave = config["cavebot"]
    if cave.get("route"):
        return [(imagem("map", p["name"]), p.get("time", cave["walk_time"])) for p in cave["route"]]

    # Sem rota salva: usa os ícones numerados de imags/map (1.png, 2.png, ...) em ordem
    numerados = [p for p in (PASTA_IMAGENS / "map").glob("*.png") if p.stem.isdigit()]
    return [(str(p), cave["walk_time"]) for p in sorted(numerados, key=lambda p: int(p.stem))]
