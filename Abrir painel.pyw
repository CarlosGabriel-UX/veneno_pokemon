# Abre o painel sem a janela do terminal (dois cliques neste arquivo).
import runpy
import sys
from pathlib import Path

main = Path(__file__).resolve().parent / "game-bot-dashboard" / "src" / "main.py"
sys.path.insert(0, str(main.parent))
sys.argv[0] = str(main)  # o pywebview acha a pasta ui/ a partir daqui
runpy.run_path(str(main), run_name="__main__")
