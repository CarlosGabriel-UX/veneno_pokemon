#import para controlar o pc
import pyautogui as pg
import interception as ic

import config_bot


#O FAILSAFE do PyAutoGUI não interrompe os cliques enviados pelo Interception.
pg.FAILSAFE = True

#função para evitar crash do programa caso o ícone não seja encontrado
pg.useImageNotFoundException(False)

#teclas e tempos vêm do config.json (o mesmo do painel)
cfg_batalha = config_bot.carregar()["battle"]
batalha_vazia = config_bot.imagem("battle", "batalha_vazia.png")

def batalha():
    while True:
        battle_vazia = pg.locateCenterOnScreen(batalha_vazia, confidence=cfg_batalha["confidence"])
        print("Procurando inimigos na batalha...")

        if battle_vazia:
            print("Sem inimigos na batalha.")
            pg.sleep(cfg_batalha["interval"])  # Aguarda antes de verificar novamente

        else:
            print("Inimigos detectados na batalha. Iniciando ataque...")
            for tecla in cfg_batalha["attack_keys"]:
                ic.press(tecla)

            pg.sleep(cfg_batalha["interval"])

ic.auto_capture_devices(keyboard=True, mouse=True, verbose=True)            


batalha()