"""Escalação do Time do Bot: previsão de pontos + melhor time dentro do orçamento.

A previsão parte da média do atleta, reduz o peso de quem jogou pouco e ajusta
por mando de campo, diferença na tabela e forma recente dos clubes. Depois, para
cada esquema tático, escolhe o conjunto que maximiza a previsão sem estourar as
cartoletas. Arquivo sem Discord e sem rede: recebe dados, devolve o time.
"""

from __future__ import annotations

from collections import defaultdict

STATUS_PROVAVEL = 7
MINIMO_JOGOS = 3
PESO_MEDIA_POSICAO = 4          # equivale a 4 jogos com a média da posição
UNIDADE_PRECO = 0.25            # preços arredondados para cima nessa unidade
CANDIDATOS_POR_CRITERIO = 12

POSICOES = {1: "🧤 GOL", 2: "🔵 LAT", 3: "🛡️ ZAG", 4: "🎯 MEI", 5: "⚽ ATA", 6: "👔 TEC"}
ESQUEMAS = {
    "3-4-3": {1: 1, 2: 0, 3: 3, 4: 4, 5: 3, 6: 1},
    "3-5-2": {1: 1, 2: 0, 3: 3, 4: 5, 5: 2, 6: 1},
    "4-3-3": {1: 1, 2: 2, 3: 2, 4: 3, 5: 3, 6: 1},
    "4-4-2": {1: 1, 2: 2, 3: 2, 4: 4, 5: 2, 6: 1},
    "4-5-1": {1: 1, 2: 2, 3: 2, 4: 5, 5: 1, 6: 1},
    "5-3-2": {1: 1, 2: 2, 3: 3, 4: 3, 5: 2, 6: 1},
    "5-4-1": {1: 1, 2: 2, 3: 3, 4: 4, 5: 1, 6: 1},
}


def _forma(resultados) -> float:
    valores = {"v": 1.0, "e": 0.5, "d": 0.0}
    pontos = [valores[r] for r in (resultados or []) if r in valores]
    return sum(pontos) / len(pontos) if pontos else 0.5


def _contexto_clubes(partidas) -> dict[int, dict]:
    contexto = {}
    for partida in partidas:
        if partida.get("valida") is False:
            continue
        casa, fora = partida.get("clube_casa_id"), partida.get("clube_visitante_id")
        pos_casa = partida.get("clube_casa_posicao") or 10
        pos_fora = partida.get("clube_visitante_posicao") or 10
        forma_casa = _forma(partida.get("aproveitamento_mandante"))
        forma_fora = _forma(partida.get("aproveitamento_visitante"))
        contexto[casa] = {"mando": 1.08, "tabela": pos_fora - pos_casa, "forma": forma_casa - forma_fora}
        contexto[fora] = {"mando": 0.94, "tabela": pos_casa - pos_fora, "forma": forma_fora - forma_casa}
    return contexto


def prever_atletas(atletas, partidas) -> list[dict]:
    """Atletas prováveis com partida válida na rodada e sua previsão de pontos."""
    contexto = _contexto_clubes(partidas)
    aptos = [
        a for a in atletas
        if a.get("status_id") == STATUS_PROVAVEL
        and (a.get("jogos_num") or 0) >= MINIMO_JOGOS
        and (a.get("preco_num") or 0) > 0
        and a.get("clube_id") in contexto
        and a.get("posicao_id") in POSICOES
    ]

    medias = defaultdict(list)
    for atleta in aptos:
        medias[atleta["posicao_id"]].append(float(atleta.get("media_num") or 0))
    media_posicao = {pos: sum(v) / len(v) for pos, v in medias.items()}

    previstos = []
    for atleta in aptos:
        jogos = int(atleta["jogos_num"])
        media = float(atleta.get("media_num") or 0)
        base = (media * jogos + media_posicao[atleta["posicao_id"]] * PESO_MEDIA_POSICAO) / (jogos + PESO_MEDIA_POSICAO)
        clube = contexto[atleta["clube_id"]]
        fator = clube["mando"] * (1 + 0.008 * clube["tabela"]) * (1 + 0.10 * clube["forma"])
        previstos.append({
            "id": str(atleta["atleta_id"]),
            "nome": atleta.get("apelido", "?"),
            "posicao_id": atleta["posicao_id"],
            "preco": float(atleta["preco_num"]),
            "previsto": round(base * max(0.6, min(1.5, fator)), 2),
        })
    return previstos


def _candidatos(previstos, posicao) -> list[dict]:
    """Melhores por previsão e melhores por custo-benefício, sem repetição."""
    grupo = [p for p in previstos if p["posicao_id"] == posicao and p["previsto"] > 0]
    por_previsao = sorted(grupo, key=lambda p: -p["previsto"])[:CANDIDATOS_POR_CRITERIO]
    por_custo = sorted(grupo, key=lambda p: -p["previsto"] / p["preco"])[:CANDIDATOS_POR_CRITERIO]
    return list({p["id"]: p for p in por_previsao + por_custo}.values())


def _podar(estados: dict) -> dict:
    """Mantém só combinações em que gastar mais rende mais."""
    resultado, melhor = {}, float("-inf")
    for custo in sorted(estados):
        if estados[custo][0] > melhor:
            resultado[custo] = estados[custo]
            melhor = estados[custo][0]
    return resultado


def _melhor_para_esquema(previstos, esquema, limite) -> tuple[float, tuple] | None:
    estados = {0: (0.0, ())}
    for posicao, quantidade in esquema.items():
        if quantidade == 0:
            continue
        por_qtd = [estados] + [{} for _ in range(quantidade)]
        for candidato in _candidatos(previstos, posicao):
            custo_cand = int(-(-candidato["preco"] // UNIDADE_PRECO))
            for qtd in range(quantidade, 0, -1):
                for custo, (valor, ids) in por_qtd[qtd - 1].items():
                    novo_custo = custo + custo_cand
                    if novo_custo > limite:
                        continue
                    atual = por_qtd[qtd].get(novo_custo)
                    novo_valor = valor + candidato["previsto"]
                    if atual is None or novo_valor > atual[0]:
                        por_qtd[qtd][novo_custo] = (novo_valor, ids + (candidato["id"],))
                por_qtd[qtd] = _podar(por_qtd[qtd])
        estados = por_qtd[quantidade]
        if not estados:
            return None
    return max(estados.values(), key=lambda estado: estado[0])


def montar_time(atletas, partidas, orcamento: float) -> dict | None:
    """Melhor time possível com o orçamento; None se não der para montar."""
    previstos = prever_atletas(atletas, partidas)
    por_id = {p["id"]: p for p in previstos}
    limite = int(orcamento // UNIDADE_PRECO)

    melhor_esquema, melhor = None, None
    for nome, esquema in ESQUEMAS.items():
        resultado = _melhor_para_esquema(previstos, esquema, limite)
        if resultado and (melhor is None or resultado[0] > melhor[0]):
            melhor_esquema, melhor = nome, resultado
    if melhor is None:
        return None

    escolhidos = sorted((por_id[i] for i in melhor[1]), key=lambda p: (p["posicao_id"], -p["previsto"]))
    capitao = max((p for p in escolhidos if p["posicao_id"] != 6), key=lambda p: p["previsto"])
    return {
        "esquema": melhor_esquema,
        "atletas": escolhidos,
        "capitao": capitao["id"],
        "gasto": round(sum(p["preco"] for p in escolhidos), 2),
        "orcamento": round(float(orcamento), 2),
        "previsto": round(sum(p["previsto"] for p in escolhidos) + capitao["previsto"] * 0.5, 2),
    }


def pontuar_time(escalacao: dict, pontuacoes: dict) -> float:
    """Total do time com a regra do capitão (1,5x)."""
    total = 0.0
    for atleta in escalacao["atletas"]:
        pontos = float(pontuacoes.get(atleta["id"], 0) or 0)
        total += pontos * 1.5 if atleta["id"] == escalacao["capitao"] else pontos
    return round(total, 2)
