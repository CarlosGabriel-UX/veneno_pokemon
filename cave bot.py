#import para controlar o pc
import time

import pyautogui as pg
import interception as ic

import config_bot


#O FAILSAFE do PyAutoGUI não interrompe os cliques enviados pelo Interception.
pg.FAILSAFE = True

#função para evitar crash do programa caso o ícone não seja encontrado
pg.useImageNotFoundException(False)

#move o mouse e clica pelo driver; ic.click(x, y) quebra quando o pacote "interception"
#(outro projeto) está instalado por cima do interception-python
def clicar(x, y):
    pg.moveTo(x, y)
    pg.sleep(0.15)
    ic.click()

#coordenadas vêm do config.json (o mesmo do painel); sem ele, valem os padrões de 1920x1080
config = config_bot.carregar()
cfg_batalha = config["battle"]
cfg_captura = config["capture"]
cfg_cave = config["cavebot"]

regiao_mapa = tuple(cfg_cave["map_region"]) #região do mapa na tela
tela = tuple(cfg_captura["region"])
pixel_vida = tuple(cfg_cave["hp_pixel"]) #pixel da barra de vida do inimigo na battle
cor_vida = tuple(cfg_cave["hp_color"])
tempo_max_luta = config["safety"]["fight_timeout"]

#lista de (ícone do mapa, segundos para chegar até ele)
icones = config_bot.rota(config)

pokemon = [config_bot.imagem("captura", nome) for nome in cfg_captura["targets"]]

batalha_vazia = config_bot.imagem("battle", "batalha_vazia.png")


def atacar():
    for tecla in cfg_batalha["attack_keys"]:
        ic.press(tecla)


def aguardar_monstro_morrer():
    inicio = time.time()
    while pg.pixel(*pixel_vida) == cor_vida:
        if time.time() - inicio > tempo_max_luta:
            print("luta passou do tempo limite, seguindo a rota...")
            return False

        print("monstro vivo, aguardando morte...")
        atacar()
        pg.sleep(1)  # Aguarda 1 segundo antes de verificar novamente

    print("monstro morto.")
    return True


def batalha():
    #luta até a battle ficar vazia; devolve True se matou algum monstro
    matou = False
    while not pg.locateCenterOnScreen(batalha_vazia, confidence=cfg_batalha["confidence"]):
        print("Inimigos detectados na batalha. Iniciando ataque...")
        atacar()
        pg.sleep(cfg_batalha["interval"])

        if not aguardar_monstro_morrer():
            break
        matou = True

    print("Sem inimigos na batalha.")
    return matou


def captura():
    #clica nos corpos até não sobrar nenhum Pokémon da lista na tela
    for _ in range(10):  # limite para não travar se a imagem continuar aparecendo
        encontrou = False
        for caminho_pokemon in pokemon:
            posicao = pg.locateCenterOnScreen(caminho_pokemon, region=tela, confidence=cfg_captura["confidence"])
            if posicao:
                print("Pokemon encontrado, iniciando captura...")
                ic.press(cfg_captura["key"])
                clicar(posicao.x, posicao.y)
                pg.sleep(cfg_captura["interval"])
                encontrou = True

        if not encontrou:
            print("Nenhum Pokémon encontrado para capturar.")
            return


def movimentação():
#repete a rota indefinidamente: anda até o ícone, luta, captura e vai para o próximo
    if not icones:
        print("Nenhum ícone de rota encontrado em imags/map.")
        return

    while True:
        for caminho_icone, segundos in icones:
            print("Procurando o ícone:", caminho_icone)

            posicao = pg.locateCenterOnScreen(caminho_icone, region=regiao_mapa, confidence=cfg_cave["confidence"]) #localize o centro desta imagem na tela

            if not posicao:
                print("Ícone não encontrado")
                continue

            print("indo para o icone")
            posicao_atual = pg.position()
            clicar(posicao.x, posicao.y)
            pg.sleep(segundos)
            pg.moveTo(posicao_atual)

            if batalha():
                captura()


ic.auto_capture_devices(keyboard=True, mouse=True, verbose=True)

movimentação()
