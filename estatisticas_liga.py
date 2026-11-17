"""Histórico e tabelas dos Pontos Corridos, sem depender de Excel.

O SQLite é a base histórica. A cada rodada, os resultados das competições
registradas no state.json são gravados nele e somente as campanhas dessas
competições são recalculadas; temporadas antigas ficam intactas.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
BOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = Path(
    os.getenv(
        "ESTATISTICAS_LIGA_DIR",
        str(BOT_DIR / "estatisticas"),
    )
)
DB_PATH = OUTPUT_DIR / "estatisticas_liga.sqlite"
PUBLIC_JSON = OUTPUT_DIR / "estatisticas_publicas.json"
RODADAS_POR_TURNO = 18

SCHEMA = """
CREATE TABLE IF NOT EXISTS round_scores (
    season INTEGER NOT NULL, competition TEXT NOT NULL, round INTEGER NOT NULL,
    team_id INTEGER NOT NULL, team_name TEXT NOT NULL, points REAL NOT NULL,
    source TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (season, competition, round, team_id)
);
CREATE TABLE IF NOT EXISTS match_results (
    season INTEGER NOT NULL, competition TEXT NOT NULL, round INTEGER NOT NULL,
    team_id INTEGER NOT NULL, team_name TEXT NOT NULL, opponent_id INTEGER NOT NULL,
    opponent_name TEXT NOT NULL, points REAL NOT NULL, result TEXT NOT NULL,
    PRIMARY KEY (season, competition, round, team_id)
);
CREATE TABLE IF NOT EXISTS campaign_results (
    season INTEGER NOT NULL, competition TEXT NOT NULL, team_id INTEGER NOT NULL,
    team_name TEXT NOT NULL, nome_exibicao TEXT NOT NULL, rounds INTEGER NOT NULL,
    total_rounds INTEGER NOT NULL, finalized INTEGER NOT NULL DEFAULT 0,
    total_points REAL NOT NULL, league_points INTEGER NOT NULL, wins INTEGER NOT NULL,
    draws INTEGER NOT NULL, losses INTEGER NOT NULL, position INTEGER NOT NULL,
    aproveitamento REAL NOT NULL, aproveitamento_real REAL NOT NULL,
    is_champion INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
    PRIMARY KEY (season, competition, team_id)
);
CREATE TABLE IF NOT EXISTS source_rounds (
    season INTEGER NOT NULL, competition TEXT NOT NULL, round INTEGER NOT NULL,
    source TEXT NOT NULL, processed_at TEXT,
    PRIMARY KEY (season, competition, round)
);
"""

CAMPOS_CAMPANHA = [
    "season", "competition", "team_id", "team_name", "nome_exibicao", "rounds",
    "total_rounds", "finalized", "total_points", "league_points", "wins", "draws",
    "losses", "position", "aproveitamento", "aproveitamento_real", "is_champion",
]
CAMPOS_RODADA = [
    "season", "competition", "round", "team_id", "team_name",
    "opponent_id", "opponent_name", "points", "result",
]


def _preparar_banco(connection: sqlite3.Connection) -> None:
    colunas = {row[1] for row in connection.execute("PRAGMA table_info(campaign_results)")}
    if colunas and "league_points" not in colunas:
        raise RuntimeError(f"Esquema inesperado em {DB_PATH}: campaign_results sem league_points")
    connection.executescript(SCHEMA)


def _competicoes_do_state(state: dict[str, Any]) -> list[tuple[int, str, dict[str, Any]]]:
    """Encontra pc1_2026, pc2_2026 etc. com resultados salvos."""
    encontradas = []
    for chave, dados in (state.get("competitions") or {}).items():
        match = re.fullmatch(r"pc([12])_(\d{4})", chave)
        resultados = (dados or {}).get("results") if isinstance(dados, dict) else None
        if match and resultados:
            encontradas.append((int(match.group(2)), f"PC/{match.group(1)}", resultados))
    return encontradas


def _resultado(pontos: float, adversario: float) -> str:
    if pontos > adversario:
        return "V"
    return "D" if pontos < adversario else "E"


def _nomes_conhecidos(connection: sqlite3.Connection) -> tuple[dict[int, str], dict[int, str]]:
    nomes = dict(connection.execute("SELECT team_id, team_name FROM match_results"))
    exibicao = dict(connection.execute("SELECT team_id, nome_exibicao FROM campaign_results"))
    return nomes, exibicao


def _gravar_rodadas(connection, season, competition, resultados, nomes, agora) -> None:
    def nome(team_id: int) -> str:
        return nomes.get(team_id, str(team_id))

    for rodada, jogos in resultados.items():
        connection.execute(
            "INSERT INTO source_rounds VALUES (?, ?, ?, 'bot', NULL) "
            "ON CONFLICT(season, competition, round) DO NOTHING",
            (season, competition, int(rodada)),
        )
        for mandante, pontos_m, visitante, pontos_v in jogos:
            for time, pontos, adversario, pontos_adv in (
                (int(mandante), float(pontos_m), int(visitante), float(pontos_v)),
                (int(visitante), float(pontos_v), int(mandante), float(pontos_m)),
            ):
                connection.execute(
                    """
                    INSERT INTO match_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(season, competition, round, team_id) DO UPDATE SET
                        team_name=excluded.team_name, opponent_id=excluded.opponent_id,
                        opponent_name=excluded.opponent_name, points=excluded.points,
                        result=excluded.result
                    """,
                    (season, competition, int(rodada), time, nome(time), adversario,
                     nome(adversario), round(pontos, 2), _resultado(pontos, pontos_adv)),
                )
                connection.execute(
                    """
                    INSERT INTO round_scores VALUES (?, ?, ?, ?, ?, ?, 'bot', ?)
                    ON CONFLICT(season, competition, round, team_id) DO UPDATE SET
                        team_name=excluded.team_name, points=excluded.points,
                        updated_at=excluded.updated_at
                    """,
                    (season, competition, int(rodada), time, nome(time), round(pontos, 2), agora),
                )


def _recalcular_campanha(connection, season, competition, exibicao, agora) -> None:
    linhas = connection.execute(
        """
        SELECT team_id, MAX(team_name), COUNT(*), SUM(points),
               SUM(result='V'), SUM(result='E'), SUM(result='D')
        FROM match_results WHERE season=? AND competition=? GROUP BY team_id
        """,
        (season, competition),
    ).fetchall()

    campanhas = []
    for team_id, nome, rodadas, total, vitorias, empates, derrotas in linhas:
        campanhas.append({
            "team_id": team_id, "team_name": nome, "rounds": rodadas,
            "total_points": round(total, 2), "league_points": vitorias * 3 + empates,
            "wins": vitorias, "draws": empates, "losses": derrotas,
        })
    campanhas.sort(key=lambda c: (-c["league_points"], -c["total_points"]))

    for posicao, campanha in enumerate(campanhas, start=1):
        rodadas = campanha["rounds"]
        finalizada = int(rodadas >= RODADAS_POR_TURNO)
        pontos = campanha["league_points"]
        connection.execute(
            """
            INSERT OR REPLACE INTO campaign_results VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                season, competition, campanha["team_id"], campanha["team_name"],
                exibicao.get(campanha["team_id"], campanha["team_name"]),
                rodadas, RODADAS_POR_TURNO, finalizada, campanha["total_points"], pontos,
                campanha["wins"], campanha["draws"], campanha["losses"], posicao,
                round(pontos / (3 * RODADAS_POR_TURNO) * 100, 2),
                round(pontos / (3 * rodadas) * 100, 2),
                int(finalizada and posicao == 1), agora,
            ),
        )


def _dicionarios(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    nomes = [coluna[0] for coluna in cursor.description]
    return [dict(zip(nomes, linha)) for linha in cursor.fetchall()]


def _escrever_arquivo(destino: Path, escrever) -> None:
    """Grava num temporário e troca, para nunca deixar arquivo pela metade."""
    temporario = destino.with_name(destino.name + ".tmp")
    escrever(temporario)
    os.replace(temporario, destino)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    def escrever(destino: Path) -> None:
        with destino.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    _escrever_arquivo(path, escrever)


def _exportar(connection: sqlite3.Connection, agora: str) -> tuple[int, int]:
    campanhas = _dicionarios(connection.execute(
        "SELECT * FROM campaign_results ORDER BY season DESC, competition, position"
    ))
    rodadas = _dicionarios(connection.execute(
        """
        SELECT m.*, COALESCE(c.nome_exibicao, m.team_name) AS nome_exibicao
        FROM match_results m
        LEFT JOIN campaign_results c
          ON c.season=m.season AND c.competition=m.competition AND c.team_id=m.team_id
        ORDER BY m.season DESC, m.competition, m.round, m.team_id
        """
    ))
    ranking = sorted(campanhas, key=lambda c: (-c["league_points"], -c["total_points"]))
    melhores = sorted(campanhas, key=lambda c: (-c["aproveitamento_real"], -c["total_points"]))
    piores = sorted(campanhas, key=lambda c: (c["aproveitamento_real"], c["total_points"]))

    _write_csv(OUTPUT_DIR / "campanhas_finais.csv", campanhas, CAMPOS_CAMPANHA)
    _write_csv(OUTPUT_DIR / "ranking_pontuacao_final.csv", ranking, CAMPOS_CAMPANHA)
    _write_csv(OUTPUT_DIR / "melhores_campanhas.csv", melhores, CAMPOS_CAMPANHA)
    _write_csv(OUTPUT_DIR / "piores_campanhas.csv", piores, CAMPOS_CAMPANHA)
    _write_csv(OUTPUT_DIR / "rodadas_detalhadas.csv", rodadas, CAMPOS_RODADA)

    publico = {
        "updated_at": agora,
        "campaigns": campanhas,
        "champions": [c for c in campanhas if c["is_champion"]],
        "best_campaigns": melhores,
        "worst_campaigns": piores,
        "ranking": ranking,
        "rounds": rodadas,
    }
    texto = json.dumps(publico, ensure_ascii=False, indent=2) + "\n"
    _escrever_arquivo(PUBLIC_JSON, lambda destino: destino.write_text(texto, encoding="utf-8"))
    return len(rodadas), len(campanhas)


def atualizar_estatisticas(team_names: dict[str, str] | None = None) -> dict[str, int]:
    """Atualiza o banco com o state.json e regenera as tabelas; pode repetir sem duplicar."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state = json.loads((BOT_DIR / "state.json").read_text(encoding="utf-8"))
    agora = datetime.now(timezone.utc).isoformat()

    connection = sqlite3.connect(DB_PATH)
    try:
        _preparar_banco(connection)
        nomes, exibicao = _nomes_conhecidos(connection)
        nomes.update({int(k): v for k, v in (team_names or {}).items()})
        for season, competition, resultados in _competicoes_do_state(state):
            _gravar_rodadas(connection, season, competition, resultados, nomes, agora)
            _recalcular_campanha(connection, season, competition, exibicao, agora)
        connection.commit()
        total_rodadas, total_campanhas = _exportar(connection, agora)
    finally:
        connection.close()
    return {"rounds": total_rodadas, "campaigns": total_campanhas}
