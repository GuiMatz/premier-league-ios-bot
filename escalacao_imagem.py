"""Imagem do time mais escalado: campo, fotos dos atletas, escudos e selos.

Mesma lógica do renderizador de escalação do FUT.AE BOT: Pillow, foto circular
com fallback de camisa, nomes que não encostam no vizinho. Fotos do TheSportsDB
(rosto) ou do Cartola (camisa) e escudos ficam em cache em `cache/`, pelo ID.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger(__name__)

BOT_DIR = Path(__file__).resolve().parent
CACHE_DIR = BOT_DIR / "cache"
LOGO_LIGA = BOT_DIR / "assets" / "logo-premier.png"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
TIMEOUT = (5, 20)
MAX_BYTES = 5 * 1024 * 1024
VALIDADE_FOTO = 7 * 24 * 3600          # fotos trocam pouco; renova uma vez por semana
VALIDADE_CLUBES = 24 * 3600

WIDTH, HEIGHT = 900, 1240
PITCH = (40, 170, WIDTH - 40, HEIGHT - 40)
GRASS = ((34, 118, 70), (39, 130, 77))
LINE = (226, 242, 230)
BACKGROUND = (17, 22, 30)
GOLD = (240, 196, 60)
AVATAR = 104
FONTES = (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf")
FONTES_BOLD = (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf")
SIGLAS = {1: "GOL", 2: "LAT", 3: "ZAG", 4: "MEI", 5: "ATA", 6: "TEC"}


# --------------------------------------------------------------------------- cache

def _baixar(url: str | None, destino: Path) -> Path | None:
    """Baixa uma imagem para o cache; devolve o arquivo ou None se não houver imagem."""
    if destino.is_file() and time.time() - destino.stat().st_mtime < VALIDADE_FOTO:
        return destino
    if not url or not url.startswith("https://"):
        return destino if destino.is_file() else None
    try:
        with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as resposta:
            tipo = resposta.headers.get("Content-Type", "").split(";")[0].lower()
            if resposta.status_code != 200 or not tipo.startswith("image/"):
                return destino if destino.is_file() else None
            dados = bytearray()
            for parte in resposta.iter_content(64 * 1024):
                dados.extend(parte)
                if len(dados) > MAX_BYTES:
                    return destino if destino.is_file() else None
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_suffix(".tmp")
        temporario.write_bytes(bytes(dados))
        temporario.replace(destino)
        return destino
    except (requests.RequestException, OSError) as erro:
        log.warning("[IMAGEM] Falha ao baixar %s: %s", url, type(erro).__name__)
        return destino if destino.is_file() else None


def _clubes() -> dict[str, dict[str, str]]:
    """{clube_id: {escudo, slug}}, com cache de um dia."""
    arquivo = CACHE_DIR / "clubes.json"
    if arquivo.is_file() and time.time() - arquivo.stat().st_mtime < VALIDADE_CLUBES:
        try:
            return json.loads(arquivo.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        resposta = requests.get("https://api.cartola.globo.com/clubes", headers=HEADERS, timeout=TIMEOUT)
        bruto = resposta.json() if resposta.status_code == 200 else {}
    except (requests.RequestException, ValueError):
        bruto = {}
    clubes = {
        str(cid): {"escudo": (dados.get("escudos") or {}).get("60x60"), "slug": dados.get("slug") or ""}
        for cid, dados in (bruto or {}).items()
        if isinstance(dados, dict)
    }
    if clubes:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(clubes), encoding="utf-8")
        return clubes
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- fotos reais

# O Cartola 2026 só fornece a silhueta da camisa. Como no FUT.AE BOT, a foto do rosto
# vem do TheSportsDB pelo nome, aceita só quando o clube do resultado bate com o do
# Cartola (evita trocar um "Pedro" por outro). Sem foto confirmada, fica a camisa.
SPORTSDB_URL = "https://www.thesportsdb.com/api/v1/json/{chave}/searchplayers.php"
SPORTSDB_INTERVALO = 2.2              # a chave gratuita aceita ~30 buscas por minuto
NOVA_BUSCA_SEM_FOTO = 7 * 24 * 3600
APELIDOS_CLUBE = {"atletico-mg": "mineiro", "atletico-pr": "paranaense", "athletico-pr": "paranaense"}
SPORTSDB_PAUSA = 120
_sportsdb_lock = threading.Lock()
_sportsdb_ultima = 0.0
_sportsdb_pausa_ate = 0.0


def _normalizar(valor: str | None) -> str:
    texto = unicodedata.normalize("NFKD", valor or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9]+", " ", texto).lower().strip()


def _buscar_sportsdb(nome: str) -> list[dict[str, Any]] | None:
    """Resultados da busca; None se a API recusou (limite) ou falhou."""
    global _sportsdb_ultima, _sportsdb_pausa_ate
    chave = os.getenv("THESPORTSDB_API_KEY", "").strip() or "123"
    with _sportsdb_lock:
        if time.time() < _sportsdb_pausa_ate:
            return None
        espera = SPORTSDB_INTERVALO - (time.time() - _sportsdb_ultima)
        if espera > 0:
            time.sleep(espera)
        _sportsdb_ultima = time.time()
        try:
            resposta = requests.get(SPORTSDB_URL.format(chave=quote(chave, safe="")), params={"p": nome}, timeout=TIMEOUT)
            if resposta.status_code == 200:
                return (resposta.json() or {}).get("player") or []
            motivo = f"HTTP {resposta.status_code}"
        except (requests.RequestException, ValueError) as erro:
            motivo = type(erro).__name__
        # Limite ou falha: pausa as buscas e a imagem sai com a camisa, sem esperar.
        _sportsdb_pausa_ate = time.time() + SPORTSDB_PAUSA
        log.warning("[IMAGEM] TheSportsDB recusou a busca (%s); pausando por %ss.", motivo, SPORTSDB_PAUSA)
        return None


def _url_foto_real(jogador: dict[str, Any], clube_slug: str) -> tuple[str | None, bool]:
    """(url, consultou_tudo). consultou_tudo=False quando a API falhou: não grava a ausência."""
    clube = APELIDOS_CLUBE.get(clube_slug, clube_slug.replace("-", " "))
    if not clube:
        return None, True
    for nome in dict.fromkeys(n for n in (jogador.get("nome"), jogador.get("nome_completo")) if n):
        resultados = _buscar_sportsdb(nome)
        if resultados is None:
            return None, False
        for resultado in resultados:
            if resultado.get("strSport") not in (None, "Soccer"):
                continue
            if clube in _normalizar(resultado.get("strTeam")):
                url = resultado.get("strCutout") or resultado.get("strThumb") or resultado.get("strRender")
                if url:
                    return url, True
    return None, True


def _foto_real(jogador: dict[str, Any], clube_slug: str) -> Path | None:
    destino = CACHE_DIR / "fotos_reais" / f"{jogador['id']}.png"
    if destino.is_file():
        return destino
    sem_foto = CACHE_DIR / "fotos_reais" / f"{jogador['id']}.sem_foto"
    if sem_foto.is_file() and time.time() - sem_foto.stat().st_mtime < NOVA_BUSCA_SEM_FOTO:
        return None
    url, consultou = _url_foto_real(jogador, clube_slug)
    arquivo = _baixar(url, destino) if url else None
    if arquivo is None and consultou:
        sem_foto.parent.mkdir(parents=True, exist_ok=True)
        sem_foto.touch()
    return arquivo


def preparar_imagens(jogadores: list[dict[str, Any]]) -> None:
    """Preenche `foto_arquivo` e `escudo_arquivo` de cada jogador, baixando em paralelo."""
    clubes = _clubes()
    ids_clube = sorted({str(j["clube_id"]) for j in jogadores if j.get("clube_id")})

    def foto(jogador):
        slug = (clubes.get(str(jogador.get("clube_id") or "")) or {}).get("slug", "")
        real = _foto_real(jogador, slug)
        if real:
            return real
        silhueta = (jogador.get("foto") or "").replace("FORMATO", "220x220")
        return _baixar(silhueta, CACHE_DIR / "fotos" / f"{jogador['id']}.png")

    def escudo(clube):
        return _baixar((clubes.get(clube) or {}).get("escudo"), CACHE_DIR / "escudos" / f"{clube}.png")

    # Um download por arquivo: dois atletas do mesmo clube não disputam o mesmo escudo.
    with ThreadPoolExecutor(max_workers=8) as executor:
        fotos = list(executor.map(foto, jogadores))
        arquivos_escudo = dict(zip(ids_clube, executor.map(escudo, ids_clube)))
    for jogador, arquivo in zip(jogadores, fotos):
        jogador["foto_arquivo"] = arquivo
        jogador["escudo_arquivo"] = arquivos_escudo.get(str(jogador.get("clube_id") or ""))


# --------------------------------------------------------------------------- desenho

@lru_cache(maxsize=32)
def _fonte(tamanho: int, negrito: bool = False):
    for caminho in FONTES_BOLD if negrito else FONTES:
        try:
            return ImageFont.truetype(caminho, tamanho)
        except OSError:
            continue
    return ImageFont.load_default(size=tamanho)


def _centralizado(draw, xy, texto, fonte, cor="white") -> None:
    caixa = draw.textbbox((0, 0), texto, font=fonte)
    draw.text((xy[0] - (caixa[2] - caixa[0]) / 2, xy[1]), texto, font=fonte, fill=cor)


def _caber(draw, texto: str, fonte, largura: float) -> str:
    if draw.textlength(texto, font=fonte) <= largura:
        return texto
    while len(texto) > 1 and draw.textlength(texto + "…", font=fonte) > largura:
        texto = texto[:-1]
    return texto.rstrip() + "…"


def _desenhar_campo(imagem: Image.Image) -> None:
    draw = ImageDraw.Draw(imagem)
    esquerda, topo, direita, baixo = PITCH
    faixa = (baixo - topo) / 12
    for i in range(12):
        draw.rectangle((esquerda, int(topo + i * faixa), direita, int(topo + (i + 1) * faixa)), fill=GRASS[i % 2])
    draw.rectangle(PITCH, outline=LINE, width=4)
    meio = (topo + baixo) // 2
    draw.line((esquerda, meio, direita, meio), fill=LINE, width=3)
    draw.ellipse((WIDTH // 2 - 90, meio - 90, WIDTH // 2 + 90, meio + 90), outline=LINE, width=3)
    for y0, y1 in ((topo, topo + 150), (baixo - 150, baixo)):
        draw.rectangle((WIDTH // 2 - 190, y0, WIDTH // 2 + 190, y1), outline=LINE, width=3)
    for y0, y1 in ((topo, topo + 55), (baixo - 55, baixo)):
        draw.rectangle((WIDTH // 2 - 85, y0, WIDTH // 2 + 85, y1), outline=LINE, width=3)


def _avatar(jogador: dict[str, Any]) -> Image.Image:
    """Foto circular; sem foto, uma camisa. Capitão ganha aro dourado."""
    tamanho = AVATAR
    mascara = Image.new("L", (tamanho, tamanho), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, tamanho - 1, tamanho - 1), fill=255)
    fundo = Image.new("RGBA", (tamanho, tamanho), (28, 34, 44, 255))
    foto = jogador.get("foto_arquivo")
    if foto:
        try:
            with Image.open(foto) as aberta:
                fundo.alpha_composite(ImageOps.fit(aberta.convert("RGBA"), (tamanho, tamanho), centering=(0.5, 0.2)))
        except OSError:
            foto = None
    if not foto:
        c = tamanho // 2
        ImageDraw.Draw(fundo).polygon(
            [(c - 22, 26), (c - 44, 42), (c - 34, 58), (c - 26, 52), (c - 24, 88), (c + 24, 88),
             (c + 26, 52), (c + 34, 58), (c + 44, 42), (c + 22, 26), (c + 10, 32), (c - 10, 32)],
            fill=(46, 139, 87), outline=(255, 255, 255), width=2,
        )
    resultado = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    resultado.paste(fundo, (0, 0), mascara)
    aro = GOLD if jogador.get("capitao") else (255, 255, 255)
    ImageDraw.Draw(resultado).ellipse((1, 1, tamanho - 2, tamanho - 2), outline=aro, width=5 if jogador.get("capitao") else 4)
    return resultado


def _escudo(jogador: dict[str, Any], tamanho: int = 38) -> Image.Image | None:
    arquivo = jogador.get("escudo_arquivo")
    if not arquivo:
        return None
    try:
        with Image.open(arquivo) as aberto:
            escudo = aberto.convert("RGBA")
    except OSError:
        return None
    escudo.thumbnail((tamanho, tamanho))
    return escudo


def _cor_pontos(pontos: float) -> tuple[int, int, int]:
    if pontos >= 8:
        return (40, 170, 90)
    if pontos < 0:
        return (205, 55, 55)
    if pontos < 3:
        return (110, 116, 128)
    return (60, 120, 210)


def _posicoes(jogadores: list[dict[str, Any]]) -> list[tuple[dict[str, Any], int, int]]:
    """Coordenadas no campo: ataque em cima, goleiro embaixo; técnico no canto."""
    por_pos = {p: [j for j in jogadores if j.get("posicao_id") == p] for p in SIGLAS}
    laterais = por_pos[2]
    defesa = laterais[:1] + por_pos[3] + laterais[1:]
    linhas = [(por_pos[5], 300), (por_pos[4], 525), (defesa, 750), (por_pos[1], 975)]
    esquerda, _, direita, baixo = PITCH
    coordenadas = []
    for membros, y in linhas:
        espaco = (direita - esquerda) / (len(membros) + 1)
        for i, jogador in enumerate(membros, start=1):
            coordenadas.append((jogador, int(esquerda + espaco * i), y))
    for jogador in por_pos[6][:1]:
        coordenadas.append((jogador, direita - 90, baixo - 115))
    return coordenadas


def _largura_nome(x: int, y: int, coordenadas) -> float:
    vizinhos = [abs(x - ox) for _, ox, oy in coordenadas if (ox, oy) != (x, y) and abs(oy - y) < 60]
    return min([*vizinhos, 230]) - 20


def render_time_mais_escalado(
    jogadores: list[dict[str, Any]],
    titulo: str,
    subtitulo: str,
    total: float | None = None,
) -> BytesIO:
    """PNG do time. Com `pontos` em cada jogador, o selo mostra a pontuação; senão, as escalações."""
    preparar_imagens(jogadores)
    imagem = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
    _desenhar_campo(imagem)
    draw = ImageDraw.Draw(imagem)

    draw.rectangle((0, 0, WIDTH, 150), fill=(12, 16, 22))
    draw.rectangle((0, 144, WIDTH, 150), fill=GOLD)
    texto_x = 40
    if LOGO_LIGA.is_file():
        try:
            with Image.open(LOGO_LIGA) as aberto:
                logo = aberto.convert("RGBA")
            logo.thumbnail((110, 110))
            imagem.alpha_composite(logo, (30, (144 - logo.height) // 2))
            texto_x = 30 + logo.width + 22
        except OSError:
            pass
    draw.text((texto_x, 26), titulo, font=_fonte(42, True), fill="white")
    draw.text((texto_x, 86), subtitulo, font=_fonte(26), fill=(190, 200, 210))
    if total is not None:
        _centralizado(draw, (WIDTH - 95, 22), f"{total:.1f}", _fonte(54, True), GOLD)
        _centralizado(draw, (WIDTH - 95, 92), "PONTOS", _fonte(18, True), (190, 200, 210))

    coordenadas = _posicoes(jogadores)
    raio = AVATAR // 2
    for jogador, x, y in coordenadas:
        imagem.alpha_composite(_avatar(jogador), (x - raio, y - raio))

        escudo = _escudo(jogador)
        if escudo:
            imagem.alpha_composite(escudo, (x - raio - 8, y + raio - escudo.height + 4))

        if "pontos" in jogador:
            selo, cor = f"{jogador['pontos']:.1f}", _cor_pontos(jogador["pontos"])
        else:
            selo, cor = f"{jogador.get('vezes', 0)}/10", (60, 120, 210)
        fonte_selo = _fonte(24, True)
        largura = draw.textlength(selo, font=fonte_selo) + 18
        draw.rounded_rectangle((x + 16, y - raio - 8, x + 16 + largura, y - raio + 28), 10, fill=cor, outline="white", width=2)
        draw.text((x + 16 + largura / 2, y - raio + 10), selo, font=fonte_selo, fill="white", anchor="mm")

        if jogador.get("capitao"):
            draw.ellipse((x - raio - 6, y - raio - 6, x - raio + 30, y - raio + 30), fill=GOLD, outline="white", width=2)
            draw.text((x - raio + 12, y - raio + 12), "C", font=_fonte(22, True), fill=(20, 20, 20), anchor="mm")

        fonte = _fonte(20, True)
        nome = _caber(draw, str(jogador.get("nome") or "?"), fonte, _largura_nome(x, y, coordenadas))
        caixa = draw.textbbox((0, 0), nome, font=fonte)
        metade = (caixa[2] - caixa[0]) / 2 + 10
        draw.rounded_rectangle((x - metade, y + raio + 4, x + metade, y + raio + 36), 8, fill=(10, 14, 20, 230))
        _centralizado(draw, (x, y + raio + 7), nome, fonte)
        sigla = SIGLAS.get(jogador.get("posicao_id"), "")
        if "pontos" in jogador:
            sigla += f"  ·  {jogador.get('vezes', 0)}/10"
        _centralizado(draw, (x, y + raio + 39), sigla, _fonte(17, True), LINE)

    saida = BytesIO()
    imagem.convert("RGB").save(saida, format="PNG", compress_level=6)
    saida.seek(0)
    return saida
