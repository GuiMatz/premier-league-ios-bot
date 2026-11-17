"""Acesso à API do Cartola, com novas tentativas para falhas passageiras de rede.

Funções síncronas: dentro do bot, chame sempre por `await run(funcao, ...)`.
Retornar None significa "não consegui consultar", nunca "o time não escalou".
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from types import SimpleNamespace

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.cartola.globo.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://cartola.globo.com",
    "Referer": "https://cartola.globo.com/",
}

MERCADO_ABERTO = 1
MERCADO_FECHADO = 2
MERCADO_ATUALIZACAO = 3
MERCADO_MANUTENCAO = 4
MERCADO_FIM_TEMPORADA = 6


def _get_json(caminho: str, timeout: int = 10, tentativas: int = 3):
    """Faz GET e devolve o JSON; None se a API não entregou dados."""
    url = f"{BASE_URL}{caminho}"
    for tentativa in range(1, tentativas + 1):
        try:
            resposta = requests.get(url, headers=HEADERS, timeout=timeout)
            if resposta.status_code == 200:
                return resposta.json()
            if resposta.status_code == 204:
                return None
            if resposta.status_code < 500:
                log.warning("[CARTOLA] %s respondeu HTTP %s", caminho, resposta.status_code)
                return None
            motivo = f"HTTP {resposta.status_code}"
        except (requests.RequestException, ValueError) as erro:
            motivo = type(erro).__name__
        if tentativa < tentativas:
            time.sleep(2 * tentativa)
    log.warning("[CARTOLA] %s indisponível após %s tentativas (%s)", caminho, tentativas, motivo)
    return None


def buscar_mercado():
    dados = _get_json("/mercado/status")
    if not dados:
        return None
    try:
        status_id = int(dados["status_mercado"])
        rodada_atual = int(dados["rodada_atual"])
    except (KeyError, TypeError, ValueError):
        log.warning("[CARTOLA] /mercado/status veio em formato inesperado")
        return None

    bruto = dados.get("fechamento") or {}
    try:
        fechamento = datetime(bruto["ano"], bruto["mes"], bruto["dia"], bruto["hora"], bruto["minuto"])
    except (KeyError, TypeError, ValueError):
        fechamento = datetime.now()

    return SimpleNamespace(
        rodada_atual=rodada_atual,
        status=SimpleNamespace(id=status_id),
        fechamento=fechamento,
        game_over=bool(dados.get("game_over")) or status_id == MERCADO_FIM_TEMPORADA,
    )


def buscar_time(cartola_id, rodada_atual):
    dados = _get_json(f"/time/id/{cartola_id}")
    if not dados:
        return None
    info = dados.get("time", {}) or {}
    rodada_time_id = info.get("rodada_time_id") or 0
    return {
        "pontos_campeonato": dados.get("pontos_campeonato") or 0,
        "pontos_rodada": dados.get("pontos") or 0,
        "rodada_pontos": dados.get("rodada_atual"),
        "escalou": rodada_time_id == rodada_atual and rodada_atual > 0,
        "patrimonio": info.get("patrimonio") or dados.get("patrimonio") or 0,
    }


def buscar_atletas_time(cartola_id):
    """Escalação do time; None quando a consulta falha."""
    dados = _get_json(f"/time/id/{cartola_id}")
    if not dados:
        return None
    atletas = dados.get("atletas", []) or []
    return {
        "atletas": {str(a["atleta_id"]): a.get("apelido", "Desconhecido") for a in atletas},
        "posicoes": {str(a["atleta_id"]): a.get("posicao_id", 0) for a in atletas},
        "fotos": {str(a["atleta_id"]): a.get("foto") for a in atletas if a.get("foto")},
        "nomes_completos": {str(a["atleta_id"]): a.get("nome") for a in atletas if a.get("nome")},
        "clubes": {str(a["atleta_id"]): a.get("clube_id") for a in atletas if a.get("clube_id")},
        "capitao_id": str(dados.get("capitao_id") or ""),
        "reserva_luxo_id": str(dados.get("reserva_luxo_id") or ""),
    }


def buscar_pontuados():
    """{atleta_id: {nome, clube, nome_clube, gols, assistencias, pontuacao}}; {} sem dados."""
    dados = _get_json("/atletas/pontuados", timeout=15, tentativas=2)
    if not dados:
        return {}
    clubes = dados.get("clubes", {}) or {}
    resultado = {}
    for atleta_id, info in (dados.get("atletas", {}) or {}).items():
        scouts = info.get("scout", {}) or {}
        clube_id = info.get("clube_id", "")
        clube = clubes.get(str(clube_id)) or clubes.get(clube_id) or {}
        resultado[str(atleta_id)] = {
            "nome": info.get("apelido", "Desconhecido"),
            "clube": clube_id,
            "nome_clube": clube.get("nome", "") or clube.get("apelido", ""),
            "gols": scouts.get("G", 0) or 0,
            "assistencias": scouts.get("A", 0) or 0,
            "pontuacao": info.get("pontuacao", 0) or 0,
        }
    return resultado


def buscar_atletas_mercado():
    """Lista de atletas do mercado; [] sem dados."""
    dados = _get_json("/atletas/mercado", timeout=15)
    if not dados:
        return []
    atletas = dados.get("atletas", []) or []
    return list(atletas.values()) if isinstance(atletas, dict) else list(atletas)


def buscar_partidas():
    """Partidas da rodada aberta; [] sem dados."""
    dados = _get_json("/partidas", timeout=15)
    return (dados or {}).get("partidas", []) or []
