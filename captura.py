#import para controlar o pc
import pyautogui as pg
import interception as ic

import config_bot


#O FAILSAFE do PyAutoGUI não interrompe os cliques enviados pelo Interception.
pg.FAILSAFE = True

#função para evitar crash do programa caso o ícone não seja encontrado
pg.useImageNotFoundException(False)

#região, tecla e Pokémon-alvo vêm do config.json (o mesmo do painel)
cfg_captura = config_bot.carregar()["capture"]

tela = tuple(cfg_captura["region"])

pokemon = [config_bot.imagem("captura", nome) for nome in cfg_captura["targets"]]

def captura():

    while True:

        for caminho_pokemon in pokemon:

            print("Procurando Pokémon para capturar...")

            captura = pg.locateCenterOnScreen(caminho_pokemon,region=tela, confidence=cfg_captura["confidence"])

            if captura:

                print("Pokemon encontrado, iniciando captura...")

                ic.press(cfg_captura["key"])

                ic.click(x=captura.x, y=captura.y)

                pg.sleep(cfg_captura["interval"]) # Aguarda antes de verificar novamente


            else:

                print("Nenhum Pokémon encontrado para capturar.")

                pg.sleep(cfg_captura["interval"]) # Aguarda antes de verificar novamente

ic.auto_capture_devices(keyboard=True, mouse=True, verbose=True)


captura ()

