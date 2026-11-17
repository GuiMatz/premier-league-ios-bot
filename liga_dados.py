"""Dados da liga: times, calendário, histórico e textos editoriais.

Esta é a versão PÚBLICA, com dados 100% FICTÍCIOS e gerados de forma
determinística, só para o bot importar e funcionar como demonstração.

Para usar com uma liga real, crie `liga_dados_local.py` (ignorado pelo git)
redefinindo TODAS as constantes abaixo com os dados verdadeiros e
`DADOS_DE_EXEMPLO = False`. Ele é carregado automaticamente no fim deste arquivo.

Formatos principais:
- TIMES: {nome_do_time: {"discord": "<id do usuário no Discord>", "id": <id do time no Cartola>}}
- CONFRONTOS_PC / PC2_CONFRONTOS: {rodada: [(mandante_id, visitante_id), ...]}
- RESULTADOS_PC / PC2_RESULTADOS_BASE: {rodada: [(mandante_id, pts, visitante_id, pts), ...]}
"""

from __future__ import annotations

import random

DADOS_DE_EXEMPLO = True
NOME_LIGA = "Premier League IOS"

_rng = random.Random(2026)  # semente fixa: os dados de exemplo são sempre os mesmos

# --------------------------------------------------------------------------- times

# (nome completo, nome curto, id Cartola fictício, alias, cor RGB, emoji)
_CLUBES = [
    ("Leões do Norte FC",   "Leões do Norte",   101, "leoes",     (230, 140, 20),  "🟠"),
    ("Unidos da Várzea EC", "Unidos da Várzea", 102, "varzea",    (20, 60, 160),   "🔵"),
    ("Real Pixel",          "Real Pixel",       103, "pixel",     (200, 30, 30),   "🔴"),
    ("Sporting Bytes",      "Sporting Bytes",   104, "bytes",     (20, 140, 70),   "🟢"),
    ("Atlético Cachoeira",  "Cachoeira",        105, "cachoeira", (30, 30, 30),    "⚫"),
    ("Inter do Bairro",     "Inter do Bairro",  106, "bairro",    (200, 20, 60),   "🟥"),
    ("Estrela do Sul EC",   "Estrela do Sul",   107, "estrela",   (240, 200, 40),  "🟡"),
    ("Galáticos FC",        "Galáticos",        108, "galaticos", (240, 240, 240), "⚪"),
    ("Trovão United",       "Trovão",           109, "trovao",    (120, 60, 180),  "🟣"),
    ("Dínamo Café",         "Dínamo Café",      110, "dinamo",    (120, 80, 40),   "🟤"),
]

# Discord "0" = sem usuário vinculado (as menções viram @desconhecido).
TIMES = {nome: {"discord": "0", "id": tid} for nome, _, tid, _, _, _ in _CLUBES}
IDS = [tid for _, _, tid, _, _, _ in _CLUBES]
ID_CLUBE_EXTINTO = 99999999  # time que só existe no histórico (ex.: saiu da liga)

NOMES_PC = {tid: curto for _, curto, tid, _, _, _ in _CLUBES}
NOMES_PC[ID_CLUBE_EXTINTO] = "Clube Extinto"

EMOTES_TIMES = {nome: emoji for nome, _, _, _, _, emoji in _CLUBES}
EMOTES_PC = {tid: emoji for _, _, tid, _, _, emoji in _CLUBES}
EMOTES_PC[ID_CLUBE_EXTINTO] = "⚪"
EMOTE_CAPITAO = "🅲"
EMOTE_LUXO = "⭐"

ALIASES_PC = {alias: tid for _, _, tid, alias, _, _ in _CLUBES}
CLASSICOS = {frozenset([101, 102]): "⚔️ CLÁSSICO DE EXEMPLO"}

# Cor (RGB) e escudo (URL https opcional) de cada time nos embeds.
TIMES_INFO_BASE = {nome: {"logo": None, "cor": cor} for nome, _, _, _, cor, _ in _CLUBES}

PATROCINADORES = [
    {"nome": "Patrocinador Exemplo", "logo": None, "slogans": [
        "A RODADA SÓ TERMINA DEPOIS DA RESENHA",
        "ONDE 0,10 PONTO VIRA RIVALIDADE",
    ]},
    {"nome": NOME_LIGA, "logo": None, "slogans": [
        "DESDE 2023 TRANSFORMANDO AMIZADE EM TABELA",
        "A CASA OFICIAL DO MVP E DO ZIKADO",
    ]},
]

# --------------------------------------------------------------------------- calendário


def _turno(ids, deslocamento=0):
    """Rodízio pelo método do círculo: cada time enfrenta todos uma vez."""
    ids = ids[deslocamento:] + ids[:deslocamento]
    fixo, giro = ids[0], ids[1:]
    rodadas = []
    for r in range(len(ids) - 1):
        roda = [fixo] + giro
        jogos = [(roda[i], roda[-1 - i]) for i in range(len(ids) // 2)]
        rodadas.append([(b, a) if r % 2 else (a, b) for a, b in jogos])
        giro = giro[-1:] + giro[:-1]
    return rodadas


def _campeonato(deslocamento):
    ida = _turno(IDS, deslocamento)
    volta = [[(b, a) for a, b in jogos] for jogos in ida]
    return {i: jogos for i, jogos in enumerate(ida + volta, start=1)}


def _pontos():
    return round(_rng.uniform(30, 130), 2)


def _jogar(jogos):
    return [(a, _pontos(), b, _pontos()) for a, b in jogos]


CONFRONTOS_PC = _campeonato(0)                       # PC/1: rodadas Cartola 1–18
RESULTADOS_PC = {r: _jogar(j) for r, j in CONFRONTOS_PC.items()}
PC2_CONFRONTOS = _campeonato(3)                      # PC/2: rodadas Cartola 19–36
PC2_RESULTADOS_BASE = {r: _jogar(PC2_CONFRONTOS[r]) for r in range(1, 5)}

# Rodadas Cartola 19–22 já conhecidas quando o bot assumiu o histórico.
BASELINE_ROUND_SCORES = {
    str(r + 18): {str(t): p for a, pa, b, pb in jogos for t, p in ((a, pa), (b, pb))}
    for r, jogos in PC2_RESULTADOS_BASE.items()
}

_r22 = BASELINE_ROUND_SCORES["22"]
_mvp22 = max(_r22, key=_r22.get)
_merda22 = min(_r22, key=_r22.get)
OFFICIAL_LAST_ROUND = {
    "round": 22,
    "mvp_team_id": int(_mvp22), "mvp_points": _r22[_mvp22],
    "merda_team_id": int(_merda22), "merda_points": _r22[_merda22],
}


def _baseline_time():
    j = _rng.randint(40, 95)
    v = _rng.randint(j // 4, j * 3 // 4)
    d = _rng.randint(0, j - v)
    return {"v": v, "d": d, "j": j, "mvp": _rng.randint(0, 12),
            "zikado": _rng.randint(0, 20), "merda": _rng.randint(0, 6)}


# Números acumulados até a rodada 22 (o bot continua contando a partir daí).
OFFICIAL_STATS_BASELINE = {nome: _baseline_time() for nome in TIMES}


def _tabela(resultados):
    pts = {t: [0, 0.0] for t in IDS}
    for jogos in resultados.values():
        for a, pa, b, pb in jogos:
            pts[a][1] += pa
            pts[b][1] += pb
            if pa > pb:
                pts[a][0] += 3
            elif pb > pa:
                pts[b][0] += 3
            else:
                pts[a][0] += 1
                pts[b][0] += 1
    return sorted(pts, key=lambda t: (pts[t][0], pts[t][1]), reverse=True)


CAMPEAO_PC1_2026 = _tabela(RESULTADOS_PC)[0]

# --------------------------------------------------------------------------- copas e mundial


def _mata_mata(rodadas_cartola, quartas):
    """Monta quartas, semi, 3º lugar e final usando as pontuações das rodadas."""
    def jogo(a, b, rodada):
        sa = int(BASELINE_ROUND_SCORES[str(rodada)][str(a)])
        sb = int(BASELINE_ROUND_SCORES[str(rodada)][str(b)])
        if sa == sb:
            sa += 1
        return (a, sa, b, sb)

    def vence(j):
        return (j[0], j[2]) if j[1] > j[3] else (j[2], j[0])

    r_q, r_s, r_f = rodadas_cartola
    q = [jogo(a, b, r_q) for a, b in quartas]
    vq = [vence(j)[0] for j in q]
    s = [jogo(vq[0], vq[1], r_s), jogo(vq[2], vq[3], r_s)]
    (f1, p1), (f2, p2) = vence(s[0]), vence(s[1])
    terceiro, final = jogo(p1, p2, r_f), jogo(f1, f2, r_f)
    return {
        "champion": vence(final)[0],
        "third_place": vence(terceiro)[0],
        "rounds": [
            {"phase": "Quartas de Final", "cartola_round": r_q, "matches": q},
            {"phase": "Semifinal", "cartola_round": r_s, "matches": s},
            {"phase": "3º Lugar", "cartola_round": r_f, "matches": [terceiro]},
            {"phase": "Final", "cartola_round": r_f, "matches": [final]},
        ],
    }


COPA_IOS_2026 = _mata_mata((19, 20, 21), [(101, 108), (102, 107), (103, 106), (104, 105)])


def _copa_historica(edicao, participantes):
    _rng.shuffle(participantes)
    jogos, vivos = [], participantes
    for fase in ("Quartas de Final", "Semifinal"):
        proximos, perdedores = [], []
        for a, b in zip(vivos[::2], vivos[1::2]):
            v, p = (a, b) if _rng.random() < 0.5 else (b, a)
            jogos.append({"edicao": edicao, "fase": fase, "vencedor": v, "perdedor": p})
            proximos.append(v)
            perdedores.append(p)
        vivos = proximos
    jogos.append({"edicao": edicao, "fase": "3º Lugar", "vencedor": perdedores[0], "perdedor": perdedores[1]})
    jogos.append({"edicao": edicao, "fase": "Final", "vencedor": vivos[0], "perdedor": vivos[1]})
    return jogos


COPA_IOS_HISTORICO = (
    _copa_historica("Copa IOS 2024", [101, 102, 103, 104, 105, 106, 107, ID_CLUBE_EXTINTO])
    + _copa_historica("Copa IOS 2025", [101, 102, 103, 104, 105, 106, 107, 108])
)

IOS_WORLD_CUP_2026 = sorted(
    ((tid, _rng.randint(550, 950)) for tid in IDS[:9]), key=lambda x: x[1], reverse=True
)
PL_IOS_CHAMPIONS = {2023: 104, 2024: 101, 2025: 107}

MELHORES_PONTUACOES_2026_BASE = [
    {"team_id": tid, "points": float(_rng.randint(110, 145)), "round": f"{_rng.randint(1, 6)}/2026", "competition": "IWC"}
    for tid in IDS[:6]
]

TOP3_HISTORICO_BASE = [
    {"nome": nome, "points": pts, "round": 24, "year": 2025}
    for nome, pts in zip(list(TIMES)[:3], (171.5, 165.2, 158.9))
]

# --------------------------------------------------------------------------- títulos

_NOME_POR_ID = {d["id"]: nome for nome, d in TIMES.items()}
CONQUISTAS_BASE = {nome: [] for nome in TIMES}


def _titulo(tid, key, texto):
    if tid in _NOME_POR_ID:
        CONQUISTAS_BASE[_NOME_POR_ID[tid]].append({"key": key, "texto": texto})


for _ano, _tid in PL_IOS_CHAMPIONS.items():
    _titulo(_tid, f"pl-ios-{_ano}", f"{NOME_LIGA} {_ano}")
for _jogo in COPA_IOS_HISTORICO:
    if _jogo["fase"] == "Final":
        _ano = _jogo["edicao"].split()[-1]
        _titulo(_jogo["vencedor"], f"copa-ios-{_ano}", _jogo["edicao"])
_titulo(CAMPEAO_PC1_2026, "pc1-2026", "Pontos Corridos 2026/1")
_titulo(COPA_IOS_2026["champion"], "copa-ios-2026", "Copa IOS 2026")
_titulo(IOS_WORLD_CUP_2026[0][0], "ios-world-cup-2026", "IOS World Cup 2026")

# --------------------------------------------------------------------------- tabelas antigas


def _tabela_antiga(times, jogos=14):
    linhas = []
    for nome in times:
        v = _rng.randint(2, 12)
        linhas.append((nome, 3 * v, v, 0, jogos - v, round(_rng.uniform(500, 1300), 2)))
    return sorted(linhas, key=lambda x: (x[1], x[5]), reverse=True)


_OITO = [NOMES_PC[t] for t in IDS[:8]]
TABELAS_HISTORICAS = {
    "pc1-2024": {"titulo": "📊 PONTOS CORRIDOS/1 — TABELA FINAL 2024", "linhas": _tabela_antiga(_OITO)},
    "pc1-2025": {"titulo": "📊 PONTOS CORRIDOS/1 — TABELA FINAL 2025", "linhas": _tabela_antiga(_OITO)},
    "pc2-2025": {"titulo": "📊 PONTOS CORRIDOS/2 — TABELA FINAL 2025", "linhas": _tabela_antiga(_OITO)},
    "mundial-2025": {"titulo": "🌍 IOS CLUB WORLD CUP 2025", "linhas": _tabela_antiga(_OITO, jogos=7)},
}

ARQUIVO_CAMPEONATOS = [
    {"nome": "👑 PL IOS • LIGA CLÁSSICA", "linhas": ["🟢 **PL IOS 2026**  • `!tabela`  • em andamento"] + [
        f"✅ **PL IOS {ano}**  • 🏆 {NOMES_PC[tid]}" for ano, tid in sorted(PL_IOS_CHAMPIONS.items(), reverse=True)
    ]},
    {"nome": "📊 PONTOS CORRIDOS", "pc2_atual": True, "linhas": [
        f"✅ **PC/1 2026**  • `!pc 2026`  • 🏆 {NOMES_PC[CAMPEAO_PC1_2026]}",
        f"✅ **PC/2 2025**  • `!pc2 2025`  • 🏆 {TABELAS_HISTORICAS['pc2-2025']['linhas'][0][0]}",
        f"✅ **PC/1 2025**  • `!pc 2025`  • 🏆 {TABELAS_HISTORICAS['pc1-2025']['linhas'][0][0]}",
        f"✅ **PC 2024**  • `!pc 2024`  • 🏆 {TABELAS_HISTORICAS['pc1-2024']['linhas'][0][0]}",
        "",
        "⚡ `!pc` abre sozinho o PC que estiver acontecendo no momento.",
    ]},
    {"nome": "🏆 COPA IOS", "linhas": [
        f"✅ **Copa IOS 2026**  • `!copa 2026`  • 🏆 {NOMES_PC[COPA_IOS_2026['champion']]}",
    ] + [
        f"✅ **{j['edicao']}**  • `!copa {j['edicao'].split()[-1]}`  • 🏆 {NOMES_PC[j['vencedor']]}"
        for j in reversed(COPA_IOS_HISTORICO) if j["fase"] == "Final"
    ]},
    {"nome": "🌍 MUNDIAL", "linhas": [
        f"✅ **IOS World Cup 2026**  • `!mundial 2026`  • 🏆 {NOMES_PC[IOS_WORLD_CUP_2026[0][0]]}",
        f"✅ **IOS Club World Cup 2025**  • `!mundial 2025`  • 🏆 {TABELAS_HISTORICAS['mundial-2025']['linhas'][0][0]}",
    ]},
]

# --------------------------------------------------------------------------- textos

TEXTO_BIOGRAFIA_LIGA = (
    f"🏆 **{NOME_LIGA}** — liga de Cartola FC entre amigos, fundada em **2023**.\n\n"
    "Competições:\n"
    f"• 🏆 **{NOME_LIGA}** — rodadas 1-38\n"
    "• 📊 **Pontos Corridos/1** — rodadas 1-18\n"
    "• 🏆 **Copa IOS** — mata-mata, rodadas 19-21\n"
    "• 📊 **Pontos Corridos/2** — rodadas 19-36\n\n"
    "Bot próprio com gols em tempo real, estatísticas históricas e site da liga."
)

# Páginas dos clubes (`!<comando>`). Em clubes ativos, {v}, {j} e {apr} são
# preenchidos com o aproveitamento atual. "extras" vira campos extras do embed.
CLUBES_BIOGRAFIA = [
    {
        "comando": alias, "time": nome, "titulo": nome.upper(), "cor": cor, "logo": None,
        "presidente": f"Técnico {i}", "status": "ativo",
        "historia": (
            f"Clube fictício de exemplo, presidido e treinado pelo **Técnico {i}**.\n\n"
            "Soma **{v} vitórias em {j} partidas** — aproveitamento de **{apr}%**."
        ),
        "extras": [],
    }
    for i, (nome, _, _, alias, cor, _) in enumerate(_CLUBES, start=1)
] + [
    {
        "comando": "clubeextinto", "time": None, "titulo": "CLUBE EXTINTO", "cor": (90, 90, 90),
        "logo": None, "presidente": "Técnico 11", "status": "desativado",
        "historia": "Disputou a Copa IOS 2024 e deixou a liga no fim daquela temporada.",
        "extras": [("🏆 Títulos", "Nenhum.")],
        "status_texto": "⚫ Desativado",
    },
]

# --------------------------------------------------------------------------- dados reais

try:
    from liga_dados_local import *  # noqa: F401,F403  (arquivo privado, fora do git)
except ModuleNotFoundError as erro:
    if erro.name != "liga_dados_local":
        raise
