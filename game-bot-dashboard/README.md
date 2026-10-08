# Painel do Veneno do Pokémon

Janela para ligar e desligar o bot sem usar o terminal.

## O que tem

**Módulos**
- **Batalha**: ataca (`e`, `q`) quando aparece inimigo
- **Captura**: procura os pokémons marcados e joga a pokébola
- **Cavebot**: segue a rota do minimapa, luta e captura
- **Cura**: aperta a tecla de cura quando um pixel da barra de vida do seu pokémon muda de cor
- **Parar tudo**: botão no topo ou **F12** (configurável); cada módulo também pode ter um atalho próprio

**Segurança**
- Os módulos **pausam sozinhos quando o jogo não está em primeiro plano** (não clicam em outras janelas)
- Se a janela do jogo fechar, tudo para
- O cavebot desiste de uma luta que passar do **tempo máximo** e **para sozinho** se ficar várias voltas sem achar ponto no minimapa
- A vida (do inimigo e a sua) é lida com uma **tolerância de cor** ajustável, e se a leitura do pixel falhar a Cura não aperta nada
- Ligar o **Cavebot** desliga a Batalha e a Captura, que ele já faz sozinho; enquanto ele estiver ligado, as duas não ligam
- **Alertas** com som e o painel piscando na barra de tarefas (erro, travamento, jogo fechado, timer, pokémon com 🔔)
- Só abre **um painel por vez**

**Coordenadas que acompanham a janela**: o painel guarda onde a janela do jogo estava quando as coordenadas foram marcadas e ajusta tudo se ela for movida.

**Topo, lateral e rodapé**
- Módulos em uma linha cada; o ligado ganha borda verde e mostra há quanto tempo está rodando
- **Barra de status** no rodapé: **Jogo** (verde = em foco, amarelo = aberto atrás de outra janela), **Driver**, módulos rodando, timer e a última mensagem do console
- Troca rápida de **perfil**, **modo compacto** (janelinha por cima do jogo) e **timer** (desliga tudo em X tempo ou num horário)
- Aviso quando sai uma **versão nova** no GitHub (precisa publicar um Release com tag tipo `v1.2.0` e mudar `VERSION` em `src/main.py`)

**Abas**
- **Console**: log ao vivo, filtro por módulo e botão para abrir a pasta `logs/` (um arquivo por dia, bom para mandar quando der problema)
- **Captura**: escolha os pokémons clicando nas fotos, 🔔 para só avisar quando aparecer; **+ Adicionar pokémon** (de um arquivo ou recortando direto da tela); **Testar detecção** mostra um print com o que foi achado e a nota de cada imagem
- **Rota**: arraste para mudar a ordem, ligue/desligue pontos, tempo de caminhada por ponto, **+ Adicionar ponto** recortando do minimapa; **Testar minimapa**
- **Estatísticas**: números da sessão, gráficos dos últimos 14 dias e pokébolas e capturas por pokémon, com a taxa de acerto (salvo em `estatisticas.db`)
- **Confirmar captura** (aba Captura): recorte da tela a mensagem que o jogo mostra quando a captura dá certo. Depois de cada pokébola, o bot procura essa mensagem por alguns segundos e conta a captura. Ela fica em `imags/captura_ok/sucesso.png`; sem ela, o bot conta só as pokébolas
- **Parar sem pokébola** (aba Captura): recorte a mensagem que o jogo mostra quando você tenta jogar sem pokébola. Se ela aparecer depois de uma pokébola, a Captura ou o Cavebot desliga e o painel avisa. Fica em `imags/captura_ok/sem_pokebola.png`
- **Pokémon desmaiado** (Ajustes > Cura): pegue um pixel no começo da barra de vida do seu pokémon. Se ele perder a cor por 3 segundos seguidos com o jogo em foco, o bot para tudo e avisa
- **Ajustes**: grupos que abrem e fecham, com um resumo no título (perfis, coordenadas com **Marcar/Pegar** — posicione o mouse no jogo e aperte **F8** —, cura, segurança, atalhos, batalha e geral); em **Geral** dá para trocar a **cor do painel** (roxo, verde veneno ou azul)
- **Mascote**: uma garota no canto do painel que balança enquanto o bot roda e comenta capturas, alertas e erros num balão. Em **Ajustes > Geral** dá para escolher a da cor do painel (automática), uma das três prontas, nenhuma, ou uma imagem sua (PNG, JPG, GIF ou WEBP), que fica em `imags/mascotes`. Em **Tamanho da mascote** dá para deixá-la pequena no canto, grande no fundo do painel ou esticada no painel inteiro, com a transparência ajustável

## Arquivos que o painel cria

Ficam na raiz do repositório (ou ao lado do `.exe`):

| Arquivo | O que é |
| --- | --- |
| `config.json` | configurações atuais |
| `perfis/*.json` | perfis salvos |
| `estatisticas.db` | histórico de batalhas e pokébolas |
| `logs/*.txt` | log de cada dia |
| `imags/captura/removidos/`, `imags/map/removidos/` | pokémons e pontos removidos pelo painel (dá pra devolver movendo de volta) |

## Rodar pelo código

```
pip install -r requirements.txt
python src/main.py
```

O driver do [Interception](https://github.com/oblitum/Interception) precisa estar instalado (veja o passo 2 do README principal).

## Gerar o .exe

Dê dois cliques em `build.bat`. Ele cria `dist\VenenoBot\VenenoBot.exe` e copia a pasta `imags` pra junto.
Para mandar pra alguém, compacte a pasta `dist\VenenoBot` inteira.

## Estrutura

```
src/
  main.py     janela (pywebview) e API que o JavaScript chama
  engine.py   módulos do bot, detecção de imagem, perfis, estatísticas
  ui/         index.html, style.css, app.js
```
