"""
BOT CARTOLA na bandeja do Windows (icone perto do relogio).

Coloque este arquivo na MESMA pasta do bot.py e do token.txt.

Instalar as dependencias (uma vez):
    pip install pystray pillow

Comandos:
    python cartola_tray.py                -> roda agora, preso ao terminal (fecha junto com ele)
    python cartola_tray.py --iniciar      -> roda agora, INDEPENDENTE (nao fecha com o VS Code)
    python cartola_tray.py --instalar     -> passa a abrir sozinho quando o Windows ligar
    python cartola_tray.py --desinstalar  -> deixa de abrir sozinho

Sem janela de console: os erros vao para o arquivo cartola_tray.log
(menu do icone -> "Ver log de erros"). O log normal do bot continua em bot.log.
"""
import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

PASTA = Path(__file__).resolve().parent
os.chdir(PASTA)                  # state.json, backups/, token.txt etc. sao relativos a esta pasta
sys.path.insert(0, str(PASTA))

ARQ_LOG = PASTA / "cartola_tray.log"
ARQ_BOT_LOG = PASTA / "bot.log"
ARQ_LOGO = PASTA / "assets" / "logo-premier.png"
PORTA_UNICA = 47654              # trava de instancia unica (troque se outro app usar a porta)
NOME_ATALHO = "CartolaBOT.vbs"
ATALHO_ANTIGO = "Bot Cartola.lnk"   # criado pela primeira versao (bandeja antiga)


def pasta_inicializacao() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"


def _linha_vbs() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    return f'CreateObject("Wscript.Shell").Run """{pythonw}"" ""{Path(__file__).resolve()}""", 0, False'


def iniciar():
    """Abre a bandeja de forma independente do terminal/VS Code (processo destacado)."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    DETACHED_PROCESS = 0x00000008
    NEW_GROUP = 0x00000200
    NO_WINDOW = 0x08000000
    BREAKAWAY = 0x01000000
    base = DETACHED_PROCESS | NEW_GROUP | NO_WINDOW
    args = [str(pythonw), str(Path(__file__).resolve())]
    kw = dict(cwd=str(PASTA), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, close_fds=True)
    try:
        subprocess.Popen(args, creationflags=base | BREAKAWAY, **kw)
    except OSError:                      # o job do terminal nao permite breakaway
        subprocess.Popen(args, creationflags=base, **kw)
    print("Bot iniciado em segundo plano. Procure o icone perto do relogio (seta ^).\n"
          "Pode fechar o VS Code e este terminal.")


def instalar():
    """Cria um .vbs na pasta 'Inicializar' do Windows que abre este script sem janela."""
    linha = _linha_vbs()
    destino = pasta_inicializacao() / NOME_ATALHO
    destino.write_text(linha + "\r\n", encoding="utf-16")   # UTF-16: o Windows le acentos (Area) certo
    antigo = pasta_inicializacao() / ATALHO_ANTIGO
    if antigo.exists():
        antigo.unlink()
    print(f"Instalado! O bot vai abrir sozinho quando o Windows ligar.\n   ({destino})")


def desinstalar():
    removeu = False
    for nome in (NOME_ATALHO, ATALHO_ANTIGO):
        destino = pasta_inicializacao() / nome
        if destino.exists():
            destino.unlink()
            removeu = True
    print("Removido. O bot nao abre mais sozinho." if removeu else "Nao estava instalado.")


if len(sys.argv) > 1:
    if sys.argv[1] in ("--instalar", "--install"):
        instalar()
    elif sys.argv[1] in ("--iniciar", "--start"):
        iniciar()
    elif sys.argv[1] in ("--desinstalar", "--uninstall"):
        desinstalar()
    else:
        print(__doc__)
    sys.exit(0)

# --- a partir daqui: rodar de verdade --------------------------------------
# sem console (pythonw) nao existe stdout/stderr: manda tudo pro cartola_tray.log
if ARQ_LOG.exists() and ARQ_LOG.stat().st_size > 5_000_000:      # guarda so o log anterior
    os.replace(ARQ_LOG, ARQ_LOG.with_suffix(".old.log"))
_log = open(ARQ_LOG, "a", encoding="utf-8", buffering=1)
if sys.stdout is None or sys.stderr is None or Path(sys.executable).name.lower() == "pythonw.exe":
    sys.stdout = sys.stderr = _log
print(f"\n===== Iniciando {time.strftime('%d/%m/%Y %H:%M:%S')} =====")

def aviso(msg):
    """Mostra uma janelinha (sem console nao ha outro jeito de avisar) e grava no log."""
    print(msg)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Bot Cartola", 0x40)
    except Exception:  # noqa: BLE001
        pass


try:
    import pystray                                   # noqa: E402
    from PIL import Image, ImageDraw, ImageFont      # noqa: E402
except Exception:  # noqa: BLE001
    traceback.print_exc()
    aviso("Faltam dependencias. No PowerShell rode:\n\npy -m pip install pystray pillow\n\n"
          "(detalhes em cartola_tray.log)")
    sys.exit(1)

estado = {"loop": None, "icone": None, "bot": None, "ok": False}


def _fonte(tam):
    for nome in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
        try:
            return ImageFont.truetype(nome, tam)
        except OSError:
            continue
    return ImageFont.load_default()


def criar_imagem(cor):
    if ARQ_LOGO.is_file():
        imagem = Image.open(ARQ_LOGO).convert("RGBA")
        imagem.thumbnail((60, 60), Image.Resampling.LANCZOS)
        tela = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        tela.paste(imagem, ((64 - imagem.width) // 2, (64 - imagem.height) // 2), imagem)
        return tela
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=cor)
    d.text((32, 33), "C", fill=(255, 255, 255, 255), font=_fonte(38), anchor="mm")
    return img


IMG_OK = criar_imagem((34, 160, 80, 255))      # verde
IMG_ERRO = criar_imagem((200, 50, 50, 255))    # vermelho


def marcar(ok, msg):
    estado["ok"] = ok
    ic = estado["icone"]
    if ic:
        ic.icon = IMG_OK if ok else IMG_ERRO
        ic.title = "Bot Cartola - " + msg
        try:
            ic.notify(msg, "Bot Cartola")
        except Exception:  # noqa: BLE001
            pass


def esperar_internet(tentativas=40, pausa=3):
    """Logo apos ligar o PC a rede pode demorar; espera ate ~2 min antes de conectar."""
    for _ in range(tentativas):
        try:
            socket.create_connection(("discord.com", 443), timeout=5).close()
            return True
        except OSError:
            time.sleep(pausa)
    return False


def rodar_bot():
    try:
        import bot as cartola_bot            # importa; o bot nao roda sozinho
        estado["bot"] = cartola_bot
        cartola_bot.validar_configuracao_runtime()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        marcar(False, "Erro ao carregar o bot. Veja o log.")
        return

    if not esperar_internet():
        print("Sem internet: nao foi possivel alcancar discord.com")
        marcar(False, "Sem internet. Reinicie o bot quando a rede voltar.")
        return

    async def principal():
        estado["loop"] = asyncio.get_running_loop()
        try:
            async with cartola_bot.bot:
                marcar(True, "Bot iniciado")
                await cartola_bot.bot.start(cartola_bot.DISCORD_TOKEN)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            marcar(False, "O bot parou por erro. Veja o log.")

    asyncio.run(principal())
    if estado["ok"]:
        marcar(False, "Bot parado")


_trava_socket = None


def reiniciar(icone=None, item=None):
    global _trava_socket
    print(f"\n===== Reiniciando Bot {time.strftime('%d/%m/%Y %H:%M:%S')} =====")
    loop, bot = estado.get("loop"), estado.get("bot")
    if loop and loop.is_running() and bot:
        try:
            asyncio.run_coroutine_threadsafe(bot.bot.close(), loop).result(timeout=5)
        except Exception:  # noqa: BLE001
            pass

    if _trava_socket:
        try:
            _trava_socket.close()
            _trava_socket = None
        except Exception:  # noqa: BLE001
            pass

    time.sleep(0.5)
    iniciar()

    if icone:
        try:
            icone.stop()
        except Exception:  # noqa: BLE001
            pass
    sys.exit(0)


def sair(icone, item):
    global _trava_socket
    loop, bot = estado["loop"], estado["bot"]
    if loop and loop.is_running() and bot:
        try:
            asyncio.run_coroutine_threadsafe(bot.bot.close(), loop).result(timeout=10)
        except Exception:  # noqa: BLE001
            pass
    if _trava_socket:
        try:
            _trava_socket.close()
            _trava_socket = None
        except Exception:  # noqa: BLE001
            pass
    icone.stop()
    sys.exit(0)


def abrir_pasta(icone, item):
    os.startfile(PASTA)


def ver_log(icone, item):
    os.startfile(ARQ_LOG)


def ver_log_bot(icone, item):
    os.startfile(ARQ_BOT_LOG if ARQ_BOT_LOG.exists() else ARQ_LOG)


def main():
    global _trava_socket
    _trava_socket = socket.socket()
    try:
        _trava_socket.bind(("127.0.0.1", PORTA_UNICA))
    except OSError:
        aviso("O Bot Cartola ja esta rodando (procure o icone na seta ^ perto do relogio).\n\n"
              "Se nao houver icone, ha uma copia escondida. Feche-a no PowerShell com:\n"
              "Get-Process pythonw | Stop-Process -Force")
        return

    menu = pystray.Menu(
        pystray.MenuItem("Reiniciar", reiniciar),
        pystray.MenuItem("Abrir pasta do bot", abrir_pasta),
        pystray.MenuItem("Logs", ver_log_bot),
        pystray.MenuItem("Logs de erros", ver_log),
        pystray.MenuItem("Desligar", sair),
    )
    estado["icone"] = pystray.Icon("cartolabot", IMG_ERRO, "Bot Cartola - iniciando...", menu)
    # o icone aparece SEMPRE, mesmo que o bot falhe ao carregar (o erro vai pro log)
    threading.Thread(target=rodar_bot, daemon=True).start()
    estado["icone"].run()          # fica aqui ate clicar em "Desligar"


if __name__ == "__main__":
    main()
