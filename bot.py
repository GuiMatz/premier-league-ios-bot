import discord
import json
import os
import requests # type: ignore
import asyncio
import subprocess
import base64
import shutil
import unicodedata
from pathlib import Path

from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import logging
from logging.handlers import RotatingFileHandler
import math
import random
from cartola_api import (
    HEADERS,
    MERCADO_ABERTO,
    MERCADO_ATUALIZACAO,
    MERCADO_MANUTENCAO,
    buscar_atletas_mercado,
    buscar_atletas_time as _buscar_atletas_time,
    buscar_mercado as _mercado,
    buscar_partidas,
    buscar_pontuados as _buscar_pontuados,
    buscar_time as _buscar_time,
)
from estatisticas_liga import atualizar_estatisticas
from time_do_bot import POSICOES, montar_time, pontuar_time
from escalacao_imagem import render_time_mais_escalado
from liga_dados import (
    ALIASES_PC, ARQUIVO_CAMPEONATOS, BASELINE_ROUND_SCORES, CLUBES_BIOGRAFIA,
    CONFRONTOS_PC, CONQUISTAS_BASE, COPA_IOS_2026, COPA_IOS_HISTORICO, DADOS_DE_EXEMPLO,
    EMOTE_CAPITAO, EMOTE_LUXO, EMOTES_PC, EMOTES_TIMES, ID_CLUBE_EXTINTO,
    IOS_WORLD_CUP_2026, MELHORES_PONTUACOES_2026_BASE, NOMES_PC, OFFICIAL_LAST_ROUND,
    OFFICIAL_STATS_BASELINE, PATROCINADORES, PC2_CONFRONTOS, PC2_RESULTADOS_BASE,
    RESULTADOS_PC, TABELAS_HISTORICAS, TEXTO_BIOGRAFIA_LIGA, TIMES, TIMES_INFO_BASE,
    TOP3_HISTORICO_BASE,
)

# ==========================================
# CONFIGURAÇÕES
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None, case_insensitive=True)

# ==========================================
# LOGGING
# ==========================================
# Log para arquivo (erros) e console (info+)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.WARNING)  # na bandeja, vira o "Logs de erros"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%d/%m/%Y %H:%M:%S',
    handlers=[
        RotatingFileHandler(
            Path(__file__).resolve().parent / 'bot.log',
            maxBytes=2_000_000, backupCount=3, encoding='utf-8',
        ),
        _console_handler,
    ],
    force=True,
)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

def _carregar_env_arquivo():
    """Carrega o .env (KEY=VALOR) da pasta do bot sem depender de python-dotenv.
    Variáveis já definidas no sistema têm prioridade."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        for linha in env_path.read_text(encoding="utf-8-sig").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            chave = chave.strip()
            valor = valor.strip().strip('"').strip("'")
            if chave and chave not in os.environ:
                os.environ[chave] = valor
    except OSError as e:
        print(f"[CONFIG] Não foi possível ler .env: {e}")


def _ler_token_txt(nome_arquivo="token.txt"):
    """Lê o token de um token.txt na pasta do bot.
    Aceita só o token na primeira linha OU uma linha DISCORD_TOKEN=..."""
    caminho = Path(__file__).resolve().parent / nome_arquivo
    if not caminho.is_file():
        return ""
    try:
        for linha in caminho.read_text(encoding="utf-8-sig").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            if "=" in linha and linha.upper().startswith("DISCORD_TOKEN"):
                linha = linha.split("=", 1)[1]
            return linha.strip().strip('"').strip("'")
    except OSError as e:
        print(f"[CONFIG] Não foi possível ler {nome_arquivo}: {e}")
    return ""


_carregar_env_arquivo()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip() or _ler_token_txt()
ID_DO_CANAL = int(os.getenv("DISCORD_CHANNEL_ID", "0") or 0)
ADMIN_DISCORD_ID = int(os.getenv("ADMIN_DISCORD_ID", "0") or 0)

# Publicação do state do site via GitHub Contents API.
# Ex.: GITHUB_REPO=usuario/premier-league-ios-site
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
GITHUB_STATE_PATH = os.getenv("GITHUB_STATE_PATH", "data/state_public.json").strip() or "data/state_public.json"
PUBLIC_STATE_FILE = os.getenv("PUBLIC_STATE_FILE", "state_public.json").strip() or "state_public.json"
LEAGUE_ENV = os.getenv("LEAGUE_ENV", "development").strip().lower() or "development"
LEAGUE_WRITER = os.getenv("LEAGUE_WRITER", "bot").strip().lower() or "bot"

VALID_LEAGUE_ENVS = {"development", "shadow", "production"}
VALID_LEAGUE_WRITERS = {"bot", "site"}


def validar_configuracao_runtime():
    erros = []
    if not DISCORD_TOKEN:
        erros.append("DISCORD_TOKEN não configurada (crie token.txt ou .env na pasta do bot)")
    if not ID_DO_CANAL:
        erros.append("DISCORD_CHANNEL_ID não configurado")
    if DADOS_DE_EXEMPLO and os.getenv("PERMITIR_DADOS_EXEMPLO", "").strip() != "1":
        erros.append(
            "liga_dados.py está com os dados FICTÍCIOS de exemplo; crie liga_dados_local.py "
            "com a sua liga (ou defina PERMITIR_DADOS_EXEMPLO=1 para testar)"
        )
    if LEAGUE_ENV not in VALID_LEAGUE_ENVS:
        erros.append("LEAGUE_ENV deve ser development, shadow ou production")
    if LEAGUE_WRITER not in VALID_LEAGUE_WRITERS:
        erros.append("LEAGUE_WRITER deve ser bot ou site")
    if erros:
        raise RuntimeError("Configuração inválida: " + "; ".join(erros) + ". Consulte .env.example.")

BOT_DIR = Path(__file__).resolve().parent
TEMPORADA_ATUAL = 2026
HISTORICO_RODADAS_FILE = BOT_DIR / "historico_rodadas.json"
BACKUPS_DIR = BOT_DIR / "backups"
IMPORTS_DIR = BOT_DIR / "imports"
IMPORT_PADRAO_FILE = IMPORTS_DIR / "campeonato.json"

# Times, IDs do Cartola e demais dados da liga ficam em liga_dados.py.
# O administrador é definido por ADMIN_DISCORD_ID no .env.

async def _exigir_admin(ctx):
    """Permite comandos técnicos apenas ao administrador (ADMIN_DISCORD_ID)."""
    if not ADMIN_DISCORD_ID or int(getattr(ctx.author, "id", 0)) != ADMIN_DISCORD_ID:
        await ctx.send("❌ Esse comando é restrito ao administrador da liga.")
        return False
    return True

# ==========================================
# ESTADO PERSISTENTE
# Salva o que já foi enviado para não repetir ao reiniciar
# ==========================================
STATE_FILE = str(BOT_DIR / os.getenv("STATE_FILE", "state.json"))
STATE_SCHEMA_VERSION = 2
STATS_BASELINE_ROUND = 22

# Base histórica até a rodada 22 (OFFICIAL_STATS_BASELINE, OFFICIAL_LAST_ROUND e
# BASELINE_ROUND_SCORES) vem de liga_dados.py. Os snapshots são usados para
# auditoria e para reconstruir confrontos caso o calendário seja adicionado depois.


def _official_aproveitamento():
    return {
        nome: {
            "v": int(d["v"]),
            "d": int(d["d"]),
            "e": max(0, int(d["j"]) - int(d["v"]) - int(d["d"])),
            "j": int(d["j"]),
        }
        for nome, d in OFFICIAL_STATS_BASELINE.items()
    }


def _official_counter(field):
    return {nome: int(d[field]) for nome, d in OFFICIAL_STATS_BASELINE.items()}


def _novo_state_v2():
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "rodada": 0,
        "aviso_24h": False,
        "aviso_dia": False,
        "aviso_1h": False,
        "aviso_10m": False,
        "aviso_fechado": False,
        "aviso_cartoleiro": False,
        "aviso_merda": False,
        "status_anterior": None,
        "lineup_round": 0,
        "lineup_por_atleta": {},
        "scouts_anteriores": {},
        "mural_historico": {},
        "mvp_historico": _official_counter("mvp"),
        "merda_historico": _official_counter("merda"),
        "zikado": _official_counter("zikado"),
        "aproveitamento": _official_aproveitamento(),
        "capitao_por_time": {},
        "reserva_luxo_por_time": {},
        "nomes_atletas": {},
        "stats_round": STATS_BASELINE_ROUND,
        "stats_last_round": OFFICIAL_LAST_ROUND.copy(),
        "last_processed_round": STATS_BASELINE_ROUND,
        "stats_processed_rounds": list(range(1, STATS_BASELINE_ROUND + 1)),
        "round_scores": {r: dict(v) for r, v in BASELINE_ROUND_SCORES.items()},
        "competitions": {
            "pc1_2026": {"processed_rounds": list(range(1, 19)), "results": {}},
            "pc2_2026": {"processed_rounds": [1, 2, 3, 4], "results": {}},
            "copa_ios_2026": {"processed_rounds": [19, 20, 21], "status": "finished"},
            "ios_world_cup_2026": {"processed_rounds": [], "status": "finished"},
        },
        "pending_competition_rounds": {},
        "recovery_required": None,
        "campeoes_anunciados": [],
        "temporada_atual": TEMPORADA_ATUAL,
        "migration": {"baseline_round": STATS_BASELINE_ROUND, "source": "Liga_Estatisticas"},
    }


def _merge_dict(base, incoming):
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def _backup_legacy_state():
    src = Path(STATE_FILE)
    if not src.exists():
        return None
    backup = src.with_name("state.pre-v2.json")
    if backup.exists():
        return str(backup)
    try:
        shutil.copy2(src, backup)
        log.info("[STATE] Backup legado criado em %s", backup)
        return str(backup)
    except Exception as e:
        log.error("[STATE] Falha ao criar backup legado: %s", e, exc_info=True)
        return None


def _ultimo_backup():
    """Backup de rodada válido mais recente: (arquivo, conteúdo) ou (None, None)."""
    arquivos = sorted(BACKUPS_DIR.rglob("state_R*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for arquivo in arquivos:
        try:
            payload = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("state"), dict):
            return arquivo, payload
    return None, None


def _restaurar_state(motivo):
    arquivo, payload = _ultimo_backup()
    if not payload:
        return None
    log.critical("[STATE] %s. Restaurando %s.", motivo, arquivo.name)
    restaurado = _merge_dict(_novo_state_v2(), payload["state"])
    restaurado["restaurado_de"] = arquivo.name
    return restaurado


def carregar_state():
    default = _novo_state_v2()
    if not os.path.exists(STATE_FILE):
        return _restaurar_state("state.json não encontrado") or default

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("a raiz do state não é um objeto")
    except Exception as e:
        log.error("[STATE] state.json inválido: %s", e, exc_info=True)
        destino = Path(STATE_FILE).with_name(f"state.corrompido.{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        try:
            shutil.copy2(STATE_FILE, destino)
        except OSError:
            pass
        restaurado = _restaurar_state("state.json inválido")
        if restaurado is None:
            raise RuntimeError("state.json inválido e sem backup para restaurar; o bot não vai sobrescrever os dados.") from e
        return restaurado

    if int(saved.get("schema_version", 0) or 0) < STATE_SCHEMA_VERSION:
        _backup_legacy_state()
        # O state antigo está congelado antes do PC/2. Só preservamos o mural,
        # porque MVP/Merda/Zikado/Aproveitamento foram substituídos pela planilha oficial.
        mural = saved.get("mural_historico") or {}
        if isinstance(mural, dict):
            default["mural_historico"] = mural
        default["migration"]["legacy_state_imported"] = True
        log.warning("[STATE] State legado detectado. Migrado para schema v2 com baseline oficial até R22.")
        return default

    return _merge_dict(default, saved)


def salvar_state():
    state["schema_version"] = STATE_SCHEMA_VERSION
    state.pop("fixture_cache", None)
    state.pop("events_cache", None)
    destino = Path(STATE_FILE)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temp = destino.with_suffix(destino.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, destino)


def _serializar_jogos(jogos):
    return [[int(j[0]), round(float(j[1]), 2), int(j[2]), round(float(j[3]), 2)] for j in jogos]


def _build_public_state():
    # RESULTADOS_PC / PC2_* são definidos mais abaixo no arquivo e já existem quando o bot conecta.
    resultados_pc_extra = {
        str(rod): _serializar_jogos(jogos)
        for rod, jogos in RESULTADOS_PC.items()
        if int(rod) >= 11
    }
    pc2_resultados = {
        str(rod): _serializar_jogos(jogos)
        for rod, jogos in _pc2_resultados_ativos().items()
    }
    pc2_confrontos = {
        str(rod): [[int(a), int(b)] for a, b in jogos]
        for rod, jogos in PC2_CONFRONTOS.items()
    }
    rodada_cartola = int(state.get("rodada", 0) or 0)
    pc2_round = rodada_cartola - 18 if 19 <= rodada_cartola <= 36 else 0

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "rodada": rodada_cartola,
        "status_anterior": state.get("status_anterior"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stats_round": int(state.get("stats_round", STATS_BASELINE_ROUND) or STATS_BASELINE_ROUND),
        "stats_last_round": state.get("stats_last_round", OFFICIAL_LAST_ROUND),
        "mvp_historico": state.get("mvp_historico", {}),
        "merda_historico": state.get("merda_historico", {}),
        "zikado": state.get("zikado", {}),
        "aproveitamento": state.get("aproveitamento", {}),
        "mural_historico": state.get("mural_historico", {}),
        "capitao_por_time": state.get("capitao_por_time", {}),
        "reserva_luxo_por_time": state.get("reserva_luxo_por_time", {}),
        "nomes_atletas": state.get("nomes_atletas", {}),
        "resultados_pc_extra": resultados_pc_extra,
        "pc2_round": pc2_round,
        "pc2_confrontos": pc2_confrontos,
        "pc2_resultados": pc2_resultados,
        "recovery_required": state.get("recovery_required"),
        "conquistas": _conquistas_publicas() if "_conquistas_publicas" in globals() else {},
        "round_scores": state.get("round_scores", {}),
        "temporada_atual": TEMPORADA_ATUAL,
        "historico_summary": _historico_stats() if "_historico_stats" in globals() else {},
    }


def publicar_site_estado():
    """Gera state_public.json e, quando configurado, publica direto no GitHub."""
    try:
        public_state = _build_public_state()
        payload_text = json.dumps(public_state, indent=2, ensure_ascii=False) + "\n"
        (BOT_DIR / PUBLIC_STATE_FILE).write_text(payload_text, encoding="utf-8")

        if not GITHUB_TOKEN or not GITHUB_REPO:
            log.debug("[SITE] state_public.json gerado localmente.")
            return True

        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATE_PATH}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        sha = None
        get_resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=20)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")
        elif get_resp.status_code != 404:
            raise RuntimeError(f"GitHub GET {get_resp.status_code}: {get_resp.text[:300]}")

        body = {
            "message": f"Atualiza state público — rodada {public_state.get('rodada', '?')}",
            "content": base64.b64encode(payload_text.encode("utf-8")).decode("ascii"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            body["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=body, timeout=30)
        if put_resp.status_code not in (200, 201):
            raise RuntimeError(f"GitHub PUT {put_resp.status_code}: {put_resp.text[:500]}")

        log.info("[SITE] state_public.json publicado no GitHub (%s/%s)", GITHUB_REPO, GITHUB_STATE_PATH)
        return True
    except Exception as e:
        log.error("[SITE] falha publicando state público: %s", e, exc_info=True)
        return False


state = carregar_state()

# ==========================================
# FUNÇÕES AUXILIARES
# ==========================================
async def run(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)

def _deduplicar_times(times):
    """Remove times repetidos sem alterar a ordem em que foram escalados."""
    if not times:
        return []
    unicos = []
    for nome_time in times:
        if nome_time not in unicos:
            unicos.append(nome_time)
    return unicos


# Mapa de atleta_id -> lista de nomes de times que o escalaram (persistido no state.json)
lineup_por_atleta = {
    aid: _deduplicar_times(times)
    for aid, times in state.get("lineup_por_atleta", {}).items()
}

# Scouts anteriores para detectar mudancas (persistido no state.json)
scouts_anteriores = state.get("scouts_anteriores", {})

# Capitão de cada time { "Nome do Time": "<atleta_id>" }
capitao_por_time = state.get("capitao_por_time", {})
reserva_luxo_por_time = state.get("reserva_luxo_por_time", {})


async def carregar_lineups(rodada):
    """Busca os atletas escalados de cada time e monta o mapa lineup_por_atleta."""
    global lineup_por_atleta, capitao_por_time, reserva_luxo_por_time
    novo_lineup = {}
    novo_capitao = {}
    novo_luxo = {}
    nomes_atletas = {}
    posicoes_atletas = {}
    fotos_atletas = {}
    clubes_atletas = {}
    nomes_completos = {}
    consultas = await asyncio.gather(*[run(_buscar_atletas_time, d["id"]) for d in TIMES.values()])
    falhas = [nome for nome, resultado in zip(TIMES, consultas) if resultado is None]
    for nome_time, resultado in zip(TIMES, consultas):
        if resultado is None:
            continue
        for aid, apelido in resultado["atletas"].items():
            times_atleta = novo_lineup.setdefault(aid, [])
            if nome_time not in times_atleta:
                times_atleta.append(nome_time)
            nomes_atletas[aid] = apelido
        posicoes_atletas.update(resultado["posicoes"])
        fotos_atletas.update(resultado.get("fotos", {}))
        nomes_completos.update(resultado.get("nomes_completos", {}))
        clubes_atletas.update(resultado.get("clubes", {}))
        if resultado["capitao_id"]:
            novo_capitao[nome_time] = resultado["capitao_id"]
        if resultado["reserva_luxo_id"]:
            novo_luxo[nome_time] = resultado["reserva_luxo_id"]
    # troca atômica (sem await entre as atribuições)
    lineup_por_atleta = novo_lineup
    capitao_por_time = novo_capitao
    reserva_luxo_por_time = novo_luxo
    state["lineup_round"] = int(rodada)
    state["lineup_pendente"] = int(rodada) if falhas else None
    state["lineup_por_atleta"] = lineup_por_atleta
    state["capitao_por_time"] = capitao_por_time
    state["reserva_luxo_por_time"] = reserva_luxo_por_time
    state["nomes_atletas"] = nomes_atletas
    state["posicoes_atletas"] = posicoes_atletas
    state["fotos_atletas"] = fotos_atletas
    state["clubes_atletas"] = clubes_atletas
    state["nomes_completos_atletas"] = nomes_completos
    salvar_state()
    if falhas:
        log.warning("[LINEUP] Falha em %s; nova tentativa no próximo minuto.", ", ".join(falhas))
    log.info("[LINEUP] %s atletas mapeados para a rodada %s", len(lineup_por_atleta), rodada)

# ==========================================
# EVENTOS
# ==========================================
_startup_sync_done = False


async def _coletar_pontuacoes_finais(rodada_finalizada):
    """Pontuação final dos times; quem falhar ou vier de outra rodada fica de fora."""
    rodada_finalizada = int(rodada_finalizada)
    consultas = await asyncio.gather(
        *[run(_buscar_time, dados["id"], rodada_finalizada + 1) for dados in TIMES.values()]
    )
    resultados = []
    for (nome, dados), t in zip(TIMES.items(), consultas):
        if not t:
            continue
        if t.get("rodada_pontos") not in (None, 0, rodada_finalizada):
            log.warning("[FECHAMENTO] %s ainda mostra a rodada %s", nome, t.get("rodada_pontos"))
            continue
        resultados.append((nome, dados["discord"], float(t["pontos_rodada"]), dados["id"]))
    resultados.sort(key=lambda x: x[2], reverse=True)
    return resultados


def _carregar_rodadas_manuais():
    """Lê rodadas digitadas a partir dos placares conferidos, sem alterar o state."""
    caminho = BOT_DIR / "rodadas_manuais.json"
    if not caminho.is_file():
        return {}
    try:
        payload = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.error("[RECOVERY] Não consegui ler %s: %s", caminho.name, error)
        return {}
    rodadas = {}
    nome_por_id = {dados["id"]: nome for nome, dados in TIMES.items()}
    nomes = {
        alias: nome_por_id[tid] for alias, tid in ALIASES_PC.items() if tid in nome_por_id
    }
    nomes.update({
        "".join(c for c in NOMES_PC[tid].casefold() if c.isalnum()): nome
        for tid, nome in nome_por_id.items() if tid in NOMES_PC
    })

    def normalizar(value):
        return "".join(
            character
            for character in unicodedata.normalize("NFKD", str(value).casefold())
            if not unicodedata.combining(character) and character.isalnum()
        )

    por_nome = {normalizar(name): name for name in TIMES}
    for rodada_texto, jogos in (payload.get("rodadas_pc2") or {}).items():
        try:
            pc2 = int(rodada_texto)
        except (TypeError, ValueError):
            continue
        resultados = []
        valido = True
        for jogo in jogos:
            if not isinstance(jogo, list) or len(jogo) != 4:
                valido = False
                break
            mandante, pontos_mandante, visitante, pontos_visitante = jogo
            mandante_nome = por_nome.get(normalizar(mandante))
            visitante_nome = por_nome.get(normalizar(visitante))
            if not mandante_nome:
                mandante_nome = nomes.get(normalizar(mandante))
            if not visitante_nome:
                visitante_nome = nomes.get(normalizar(visitante))
            if not mandante_nome or not visitante_nome:
                valido = False
                break
            try:
                resultados.extend(
                    [
                        (
                            mandante_nome,
                            TIMES[mandante_nome]["discord"],
                            round(float(pontos_mandante), 2),
                            TIMES[mandante_nome]["id"],
                        ),
                        (
                            visitante_nome,
                            TIMES[visitante_nome]["discord"],
                            round(float(pontos_visitante), 2),
                            TIMES[visitante_nome]["id"],
                        ),
                    ]
                )
            except (KeyError, TypeError, ValueError):
                valido = False
                break
        if valido and len(resultados) == len(TIMES):
            rodadas[pc2 + 18] = sorted(resultados, key=lambda item: item[2], reverse=True)
    return rodadas


async def _recuperar_rodadas_pendentes(canal, rodada_atual):
    """Fecha, em ordem, as rodadas manuais disponíveis depois de uma queda."""
    ultima = int(state.get("last_processed_round", STATS_BASELINE_ROUND) or STATS_BASELINE_ROUND)
    manuais = _carregar_rodadas_manuais()
    processadas = []
    for rodada in range(ultima + 1, int(rodada_atual)):
        resultados = manuais.get(rodada)
        if resultados is None:
            break
        resultado = await processar_rodada_finalizada(
            None, rodada, resultados, origem="startup-catchup"
        )
        if not resultado.get("processed"):
            break
        processadas.append(rodada)
    if processadas:
        state["recovery_required"] = None
        salvar_state()
        if canal:
            await canal.send(
                "✅ Recuperação concluída: "
                + ", ".join(f"Cartola R{rodada}" for rodada in processadas)
                + " processada(s) com os placares conferidos."
            )
    return processadas


async def _obter_canal():
    canal = bot.get_channel(ID_DO_CANAL)
    if not canal:
        try:
            canal = await bot.fetch_channel(ID_DO_CANAL)
        except Exception as e:
            log.warning("[CANAL] Não foi possível obter o canal %s: %s", ID_DO_CANAL, e)
    return canal


async def _sincronizar_inicio(canal):
    """Ancora o estado no mercado atual e recupera no máximo a rodada imediatamente anterior."""
    mercado = await run(_mercado)
    if not mercado or mercado.status.id in (MERCADO_ATUALIZACAO, MERCADO_MANUTENCAO):
        return False

    rodada_atual = int(mercado.rodada_atual)
    mercado_aberto = mercado.status.id == 1
    status_atual = "aberto" if mercado_aberto else "fechado"
    last_processed = int(state.get("last_processed_round", STATS_BASELINE_ROUND) or STATS_BASELINE_ROUND)

    # Se mudou de rodada no startup, reseta os avisos para não bloquear o ciclo novo
    rodada_anterior = state.get("rodada")
    # Os scouts ficam: a rodada anterior ainda pode precisar deles para fechar.
    if rodada_anterior != rodada_atual:
        log.info("[STARTUP] Nova rodada detectada no startup: %s → %s. Resetando avisos.", rodada_anterior, rodada_atual)
        state.update({
            "rodada": rodada_atual,
            "aviso_24h": False,
            "aviso_dia": False,
            "aviso_1h": False,
            "aviso_10m": False,
            "aviso_fechado": False,
            "aviso_cartoleiro": False,
            "aviso_merda": False,
        })

    # Se houver placares conferidos, recupera todas as rodadas perdidas em sequência.
    if mercado_aberto:
        recuperadas = await _recuperar_rodadas_pendentes(canal, rodada_atual)
        last_processed = int(state.get("last_processed_round", STATS_BASELINE_ROUND) or STATS_BASELINE_ROUND)
        rodada_finalizada = rodada_atual - 1
        if rodada_finalizada > last_processed:
            if rodada_finalizada == last_processed + 1:
                resultados = await _coletar_pontuacoes_finais(rodada_atual)
                if len(resultados) == len(TIMES):
                    log.warning("[RECOVERY] Recuperando rodada %s na inicialização.", rodada_finalizada)
                    await processar_rodada_finalizada(canal, rodada_finalizada, resultados, origem="startup")
                else:
                    state["recovery_required"] = {
                        "from_round": last_processed + 1,
                        "to_round": rodada_finalizada,
                        "reason": "pontuacoes_incompletas_no_startup",
                    }
            else:
                state["recovery_required"] = {
                    "from_round": last_processed + 1,
                    "to_round": rodada_finalizada,
                    "reason": "bot_offline_por_multiplas_rodadas",
                }
                if canal:
                    await canal.send(
                        f"⚠️ **RECUPERAÇÃO NECESSÁRIA:** o último fechamento processado foi a rodada **{last_processed}**, "
                        f"mas o Cartola já está na **{rodada_atual}**. Não vou inventar dados nem pular rodadas."
                    )

    state["rodada"] = rodada_atual
    state["status_anterior"] = status_atual
    salvar_state()
    await run(publicar_site_estado)
    return True


# "resumo" = 1 embed com os gols que rolaram offline | "silencioso" = só zera a base | "individual" = comportamento antigo
CATCHUP_AO_VIVO = os.getenv("CATCHUP_AO_VIVO", "resumo").lower()


async def _catchup_ao_vivo(canal):
    """Compara com o último scout conhecido, avisa UMA vez o que perdeu e atualiza a base."""
    pontuados = await run(_buscar_pontuados)
    if not pontuados:
        return
    perdidos, outros = [], 0
    for aid, info in pontuados.items():
        ant = scouts_anteriores.get(aid, {"gols": 0, "assistencias": 0})
        g = max(0, info["gols"] - ant["gols"])
        a = max(0, info["assistencias"] - ant["assistencias"])
        if g or a:
            times = lineup_por_atleta.get(aid) or []
            if times:
                perdidos.append((info["nome"], g, a, times))
            else:
                outros += 1
        scouts_anteriores[aid] = {
            "gols": info["gols"], "assistencias": info["assistencias"],
            "pontuacao": info.get("pontuacao", 0) or 0,
        }
    state["scouts_anteriores"] = scouts_anteriores
    salvar_state()

    if canal and CATCHUP_AO_VIVO == "resumo" and (perdidos or outros):
        linhas = []
        for nome, g, a, times in perdidos:
            partes = []
            if g: partes.append(f"⚽ x{g}")
            if a: partes.append(f"🎯 x{a}")
            linhas.append(f"**{nome}** {' '.join(partes)} → {', '.join(times)}")
        desc = "\n".join(linhas)[:3800]
        if outros:
            desc += f"\n\n*+{outros} jogador(es) com gol/assistência que ninguém da liga escalou.*"
        await canal.send(embed=discord.Embed(
            title="📼 O QUE ROLOU ENQUANTO O BOT ESTAVA OFFLINE",
            description=desc, color=discord.Color.from_rgb(30, 100, 200)))


_retomar_lock = asyncio.Lock()


async def _retomar_ao_vivo(canal):
    """Garante lineup da rodada atual + monitoramento ao vivo. Só chamar com o mercado FECHADO."""
    if _retomar_lock.locked():
        return  # outra retomada já em andamento (evita carregar lineup em paralelo)
    async with _retomar_lock:
        await _retomar_ao_vivo_interno(canal)


async def _retomar_ao_vivo_interno(canal):
    if monitorar_ao_vivo.is_running():
        return
    rodada = int(state.get("rodada", 0) or 0)
    if rodada <= 0:
        return

    if int(state.get("lineup_round", 0) or 0) != rodada or not state.get("lineup_por_atleta"):
        log.warning("[AO VIVO] Lineup da rodada %s ausente (bot estava offline no fechamento). Carregando agora.", rodada)
        await carregar_lineups(rodada)
        scouts_anteriores.clear()

    if not lineup_por_atleta:
        log.error("[AO VIVO] Sem lineup para a rodada %s; não consegui retomar.", rodada)
        return

    if CATCHUP_AO_VIVO != "individual":
        await _catchup_ao_vivo(canal)

    monitorar_ao_vivo.start()
    log.info("[AO VIVO] Monitoramento retomado (rodada %s, catch-up=%s)", rodada, CATCHUP_AO_VIVO)


@bot.event
async def on_ready():
    global _startup_sync_done
    log.info("✅ Bot online como %s", bot.user)
    log.info("📋 Comandos: %s", [c.name for c in bot.commands])
    log.info("📂 Estado carregado: schema=%s rodada=%s última_processada=%s", state.get("schema_version"), state.get("rodada"), state.get("last_processed_round"))
    await bot.change_presence(activity=discord.Game(name="!ajuda"))

    canal = await _obter_canal()
    if state.get("restaurado_de"):
        await _alertar_admin(f"⚠️ O state.json estava inválido e foi restaurado de `{state.pop('restaurado_de')}`. Confira a última rodada.")
        salvar_state()

    if not _startup_sync_done:
        _startup_sync_done = await _sincronizar_inicio(canal)

    if not monitorar_cartola.is_running():
        monitorar_cartola.start()

    if state.get("status_anterior") == "fechado":
        await _retomar_ao_vivo(canal)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument, commands.TooManyArguments)):
        await ctx.send("❌ Argumento inválido nesse comando. Confira em `!ajuda`.")
        return
    log.error("[COMANDO] Erro em '%s': %s", ctx.command, error, exc_info=error)
    await ctx.send("❌ Deu erro nesse comando. Tenta de novo daqui a pouco.")

# ==========================================
# LOOP AUTOMÁTICO
# ==========================================
AVISOS_MERCADO = (
    ("aviso_10m", 10,
     "⏳ **ÚLTIMOS 10 MINUTOS! ÚLTIMA CHAMADA!** O mercado está fechando!\n{mencoes}",
     "⏳ Últimos 10 minutos para o mercado fechar, mas todos os times já escalaram! GG!! ✅"),
    ("aviso_1h", 60,
     "🚨 **FALTA 1 HORA PRA FECHAR!** Corre pra escalar!\n{mencoes}",
     "🚨 Falta 1 hora para o mercado fechar, mas todos os times já escalaram! GG!! ✅"),
    ("aviso_dia", 720,
     "📢 **Atenção cartoleiros!** O mercado fecha {dia} às **{hora}**!! Bora ficar ligado! 😂\n{mencoes}",
     "📢 Faltam 12h para o mercado fechar, mas todos os times já escalaram! GG!! ✅"),
    ("aviso_24h", 1440,
     "📢 **Atenção cartoleiros!** O mercado fecha {dia} às **{hora}**!! Bora pensar em escalar 😬\n{mencoes}",
     "📢 Falta 24h para o mercado fechar, mas todos os times já escalaram! GG!! ✅"),
)


async def _mencoes_nao_escalados(rodada):
    """Menções de quem não escalou; None se algum time não pôde ser verificado."""
    consultas = await asyncio.gather(*[run(_buscar_time, d["id"], rodada) for d in TIMES.values()])
    if any(t is None for t in consultas):
        return None
    return [f"<@{d['discord']}>" for d, t in zip(TIMES.values(), consultas) if not t["escalou"]]


async def _enviar_aviso_mercado(canal, rodada, fechamento, agora, minutos):
    """Envia só o aviso mais próximo do fechamento e dá os mais antigos como vencidos."""
    vigente = next((aviso for aviso in AVISOS_MERCADO if minutos <= aviso[1]), None)
    if vigente is None:
        return
    chave, limite, com_pendentes, sem_pendentes = vigente

    mudou = False
    for outra_chave, outro_limite, _, _ in AVISOS_MERCADO:
        if outro_limite > limite and not state[outra_chave]:
            state[outra_chave] = True
            mudou = True

    if not state[chave]:
        mencoes = await _mencoes_nao_escalados(rodada)
        if mencoes is None:
            log.warning("[AVISO] Não consegui verificar todos os times; tento no próximo minuto.")
        else:
            dia = "HOJE" if fechamento.date() == agora.date() else "AMANHÃ"
            texto = (
                com_pendentes.format(dia=dia, hora=fechamento.strftime("%H:%M"), mencoes=" ".join(mencoes))
                if mencoes else sem_pendentes
            )
            await canal.send(texto)
            state[chave] = True
            mudou = True
    if mudou:
        salvar_state()


async def _alertar_admin(texto):
    try:
        usuario = await bot.fetch_user(ADMIN_DISCORD_ID)
        await usuario.send(texto)
    except Exception as e:
        log.warning("[ADMIN] Não consegui enviar o alerta: %s", e)


@tasks.loop(minutes=1)
async def monitorar_cartola():
    canal = await _obter_canal()
    if not canal:
        return
    try:
        mercado = await run(_mercado)
        if not mercado:
            return

        # Atualização ou manutenção não são fechamento: espera o mercado definir o status.
        if mercado.status.id in (MERCADO_ATUALIZACAO, MERCADO_MANUTENCAO):
            return

        await _processar_fila_anuncios(canal)

        rodada = mercado.rodada_atual
        agora = datetime.now(timezone(timedelta(hours=-3)))
        fechamento = mercado.fechamento
        if fechamento.tzinfo is None:
            fechamento = fechamento.replace(tzinfo=timezone(timedelta(hours=-3)))

        minutos_restantes = (fechamento - agora).total_seconds() / 60
        mercado_aberto = mercado.status.id == MERCADO_ABERTO
        encerrado = mercado.game_over
        status_atual = 'aberto' if mercado_aberto else 'fechado'

        # Os scouts continuam vivos aqui: só são zerados quando o próximo mercado fecha.
        if rodada != state["rodada"]:
            log.info("[LOOP] Nova rodada detectada: %s → %s", state["rodada"], rodada)
            state.update({
                "rodada": rodada,
                "aviso_24h": False,
                "aviso_dia": False,
                "aviso_1h": False,
                "aviso_10m": False,
                "aviso_fechado": False,
                "aviso_cartoleiro": False,
                "aviso_merda": False,
            })
            salvar_state()

        if mercado_aberto and 0 < minutos_restantes:
            await _enviar_aviso_mercado(canal, rodada, fechamento, agora, minutos_restantes)

        if mercado_aberto and 0 < minutos_restantes <= 60 and state.get("time_bot_rodada") != int(rodada):
            await _escalar_time_do_bot(canal, rodada)

        # Mercado ACABOU DE fechar (transição aberto → fechado)
        if not mercado_aberto and not encerrado and state["status_anterior"] == "aberto" and not state["aviso_fechado"]:
            await canal.send("🔒 **MERCADO FECHADO!** Boa sorte na rodada! 🍀")
            state["aviso_fechado"] = True
            scouts_anteriores.clear()
            state["scouts_anteriores"] = scouts_anteriores
            state["eventos_pendentes"] = {"gols": [], "assists": []}
            state["pontuacao_snapshot"] = {}
            salvar_state()

            await carregar_lineups(rodada)
            try:
                await _enviar_time_mais_escalado(canal)
            except Exception as e_sel:
                log.error("[MAIS ESCALADOS] %s", e_sel, exc_info=True)

            if not monitorar_ao_vivo.is_running():
                monitorar_ao_vivo.start()
                log.info("[AO VIVO] Monitoramento iniciado!")

            await run(publicar_site_estado)

        # Algum time falhou ao carregar no fechamento do mercado: completa agora.
        if not mercado_aberto and state.get("lineup_pendente") == int(rodada):
            await carregar_lineups(rodada)

        # Fim de rodada: vale também para rodada que ficou pendente por falha anterior.
        rodada_finalizada = int(rodada) if encerrado else int(rodada) - 1
        ultima_processada = int(state.get("last_processed_round", STATS_BASELINE_ROUND) or STATS_BASELINE_ROUND)
        transicao = state["status_anterior"] == "fechado" and not state["aviso_cartoleiro"]
        if (mercado_aberto or encerrado) and (rodada_finalizada == ultima_processada + 1 or transicao):
            if monitorar_ao_vivo.is_running():
                monitorar_ao_vivo.cancel()
                log.info("[AO VIVO] Monitoramento encerrado (rodada finalizada)")

            resultados = await _coletar_pontuacoes_finais(rodada_finalizada)
            resultado = await processar_rodada_finalizada(canal, rodada_finalizada, resultados, origem="transicao_mercado")
            if resultado.get("reason") in ("incomplete_scores", "suspicious_scores"):
                tentativas = int(state.get("fechamento_tentativas", 0)) + 1
                state["fechamento_tentativas"] = tentativas
                salvar_state()
                if tentativas == 30:
                    await _alertar_admin(
                        f"⚠️ A rodada {rodada_finalizada} ainda não fechou após 30 tentativas "
                        f"({resultado.get('reason')}). O bot continua tentando a cada minuto."
                    )
                return

            state["fechamento_tentativas"] = 0
            state["aviso_cartoleiro"] = True
            salvar_state()

        # Mercado fechado e ao vivo parado (ex.: reinício no meio da rodada) -> retoma
        if not mercado_aberto and not encerrado and not monitorar_ao_vivo.is_running():
            await _retomar_ao_vivo(canal)

        # Salva o status atual para comparar na próxima iteração
        if state["status_anterior"] != status_atual:
            state["status_anterior"] = status_atual
            salvar_state()

    except Exception as e:
        log.error("[LOOP] %s", e, exc_info=True)

@monitorar_cartola.error
async def _monitorar_cartola_error(error):
    log.error("[LOOP CRASH] Erro não tratado em monitorar_cartola: %s", error, exc_info=True)
    await asyncio.sleep(10)
    if not monitorar_cartola.is_running():
        monitorar_cartola.restart()

# ==========================================
# LOOP AO VIVO (a cada 30 segundos, apenas durante rodada)
# ==========================================
CICLOS_ESPERA_ASSIST = 1   # o gol sem garçom aguarda mais uma leitura (30 s)
CICLOS_ESPERA_ORFA = 4     # assistência sem gol é descartada ou publicada após 2 min


def _linha_time_evento(nome_time, aid):
    emote = EMOTES_TIMES.get(nome_time, "⚽")
    cap = f" {EMOTE_CAPITAO}" if capitao_por_time.get(nome_time) == str(aid) else ""
    luxo = f" {EMOTE_LUXO}" if reserva_luxo_por_time.get(nome_time) == str(aid) else ""
    return f"⤷ {emote} **{nome_time}**{cap}{luxo}"


def _bloco_evento(prefixo, evento):
    linhas = [f"{prefixo} **{evento['nome'].upper()}**"]
    linhas += [_linha_time_evento(t, evento["aid"]) for t in _deduplicar_times(evento["times"])]
    return "\n".join(linhas)


def _embed_evento(titulo, descricao):
    embed = discord.Embed(title=titulo, description=descricao, color=discord.Color.from_rgb(30, 100, 200))
    patrocinador = random.choice(PATROCINADORES)
    if patrocinador.get("logo"):
        embed.set_image(url=patrocinador["logo"])
    embed.set_footer(text=f"Oferecimento: {patrocinador['nome']} • {random.choice(patrocinador['slogans'])}")
    return embed


async def _postar_gol(canal, gol, assist):
    times_gol = set(gol["times"] or [])
    times_assist = set((assist or {}).get("times") or [])
    if times_gol & times_assist:
        titulo = "🚨 ALERTA DE GOL PERFEITO 🚨"
    elif not times_gol and not times_assist:
        titulo = "GOOOL NA RODADA!!"
    else:
        titulo = "BOLINHA NA R-E-D-E!!"

    descricao = _bloco_evento("⚽ GOL DO", gol)
    if assist:
        descricao += "\n\n" + _bloco_evento("🎯 ASSISTÊNCIA DO", assist)
    await canal.send(embed=_embed_evento(titulo, descricao))


async def _resolver_eventos_pendentes(canal):
    """Junta gols e assistências do mesmo clube, que podem chegar em leituras diferentes."""
    pendentes = state.setdefault("eventos_pendentes", {"gols": [], "assists": []})

    for gol in list(pendentes["gols"]):
        assist = next(
            (a for a in pendentes["assists"] if a["clube"] == gol["clube"] and a["aid"] != gol["aid"]),
            None,
        )
        if assist is None and gol["ciclos"] < CICLOS_ESPERA_ASSIST:
            gol["ciclos"] += 1
            continue
        await _postar_gol(canal, gol, assist)
        pendentes["gols"].remove(gol)
        if assist:
            pendentes["assists"].remove(assist)
        salvar_state()

    for assist in list(pendentes["assists"]):
        assist["ciclos"] += 1
        if assist["ciclos"] < CICLOS_ESPERA_ORFA:
            continue
        if assist["times"]:
            await canal.send(embed=_embed_evento("🎯 ASSISTÊNCIA NA RODADA!", _bloco_evento("🎯 ASSISTÊNCIA DO", assist)))
        pendentes["assists"].remove(assist)
        salvar_state()


@tasks.loop(seconds=30)
async def monitorar_ao_vivo():
    canal = await _obter_canal()
    if not canal or not lineup_por_atleta:
        return

    try:
        pontuados = await run(_buscar_pontuados)
        if not pontuados:
            return

        pendentes = state.setdefault("eventos_pendentes", {"gols": [], "assists": []})
        mudou = False
        for aid, info in pontuados.items():
            atual = {
                "gols": info["gols"],
                "assistencias": info["assistencias"],
                "pontuacao": info.get("pontuacao", 0) or 0,
            }
            anterior = scouts_anteriores.get(aid, {"gols": 0, "assistencias": 0})
            evento = {
                "nome": info["nome"],
                "aid": aid,
                "clube": info.get("clube"),
                "times": _deduplicar_times(lineup_por_atleta.get(aid)),
                "ciclos": 0,
            }
            for _ in range(max(0, atual["gols"] - anterior["gols"])):
                pendentes["gols"].append(dict(evento))
            for _ in range(max(0, atual["assistencias"] - anterior["assistencias"])):
                pendentes["assists"].append(dict(evento))
            if scouts_anteriores.get(aid) != atual:
                scouts_anteriores[aid] = atual
                mudou = True

        if mudou:
            state["scouts_anteriores"] = scouts_anteriores
            salvar_state()
        if pendentes["gols"] or pendentes["assists"]:
            await _resolver_eventos_pendentes(canal)

    except Exception as e:
        log.error("[AO VIVO] %s", e, exc_info=True)

@monitorar_ao_vivo.error
async def _monitorar_ao_vivo_error(error):
    log.error("[AO VIVO CRASH] Erro não tratado em monitorar_ao_vivo: %s", error, exc_info=True)
    await asyncio.sleep(10)
    if not monitorar_ao_vivo.is_running() and state.get("status_anterior") == "fechado":
        monitorar_ao_vivo.restart()




# ==========================================
# DADOS DA LIGA
# Patrocinadores, emotes, nomes, calendário e resultados do PC/1 e PC/2,
# copas, mundial e títulos ficam em liga_dados.py (versão pública fictícia).
# ==========================================

# PC/1 2026 é histórico fechado. O state antigo não é mais necessário para reconstruí-lo.
def _carregar_resultados_extras():
    return

_carregar_resultados_extras()


COMPETICOES = {
    "pc1_2026": {"name":"PC/1 2026","type":"league","cartola_start":1,"cartola_end":18,"status":"finished"},
    "pc2_2026": {"name":"PC/2 2026","type":"league","cartola_start":19,"cartola_end":36,"status":"active"},
    "copa_ios_2026": {"name":"Copa IOS 2026","type":"knockout","cartola_start":19,"cartola_end":21,"status":"finished"},
    "ios_world_cup_2026": {"name":"IOS World Cup 2026","type":"points","status":"finished"},
}



# Base única de títulos dos clubes: CONQUISTAS_BASE (liga_dados.py) + títulos do state.
def _conquistas_do_time(nome_time):
    itens = [dict(x) for x in CONQUISTAS_BASE.get(nome_time, [])]
    extras = state.get("conquistas", {}).get(nome_time, []) or []
    vistos = {x.get("key") for x in itens}
    for item in extras:
        if isinstance(item, str):
            item = {"key": item.lower().replace(" ", "-"), "texto": item}
        if item.get("key") not in vistos:
            itens.append(dict(item))
            vistos.add(item.get("key"))
    return itens

def _texto_titulos(nome_time):
    itens = _conquistas_do_time(nome_time)
    if not itens:
        return "Nenhum até o momento."
    return "\n".join(f"• {x.get('texto', x.get('key', 'Título'))}" for x in itens)

def _registrar_conquista(team_id, key, texto):
    id_nome = {dados["id"]: nome for nome, dados in TIMES.items()}
    nome = id_nome.get(int(team_id))
    if not nome:
        return False
    if any(x.get("key") == key for x in _conquistas_do_time(nome)):
        return False
    state.setdefault("conquistas", {}).setdefault(nome, []).append(
        {"key": key, "texto": texto}
    )
    log.info("[TÍTULO] %s conquistou %s", nome, texto)
    return True

def _conquistas_publicas():
    return {
        nome: [x.get("texto", x.get("key", "")) for x in _conquistas_do_time(nome)]
        for nome in TIMES
    }


def _history_default():
    return {
        "schema_version": 1,
        "updated_at": None,
        "seasons": {},
        "legacy_records": [],
    }


def _salvar_historico_rodadas():
    HISTORICO_RODADAS["schema_version"] = 1
    HISTORICO_RODADAS["updated_at"] = datetime.now(timezone.utc).isoformat()
    HISTORICO_RODADAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = HISTORICO_RODADAS_FILE.with_suffix(".json.tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(HISTORICO_RODADAS, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, HISTORICO_RODADAS_FILE)


def _carregar_historico_rodadas():
    if not HISTORICO_RODADAS_FILE.exists():
        return _history_default()
    try:
        with open(HISTORICO_RODADAS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("raiz do histórico não é um objeto")
        base = _history_default()
        base.update(raw)
        base.setdefault("seasons", {})
        base.setdefault("legacy_records", [])
        return base
    except Exception as e:
        log.error("[HISTÓRICO] Arquivo inválido: %s", e, exc_info=True)
        try:
            quebrado = HISTORICO_RODADAS_FILE.with_name(
                f"historico_rodadas.corrompido.{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            shutil.copy2(HISTORICO_RODADAS_FILE, quebrado)
        except Exception:
            pass
        _, backup = _ultimo_backup()
        historico = (backup or {}).get("historico_rodadas")
        if isinstance(historico, dict):
            log.critical("[HISTÓRICO] Restaurado a partir do último backup.")
            base = _history_default()
            base.update(historico)
            return base
        return _history_default()


HISTORICO_RODADAS = _carregar_historico_rodadas()


def _season_history(year):
    return HISTORICO_RODADAS.setdefault("seasons", {}).setdefault(
        str(int(year)),
        {
            "teams": {str(dados["id"]): nome for nome, dados in TIMES.items()},
            "cartola_rounds": {},
            "special_scores": [],
        },
    )


def _competicoes_da_rodada_2026(rodada):
    rodada = int(rodada)
    comps = ["Premier League IOS 2026"]
    if 1 <= rodada <= 18:
        comps.append("PC/1 2026")
    if 19 <= rodada <= 36:
        comps.append("PC/2 2026")
    if 19 <= rodada <= 21:
        comps.append("Copa IOS 2026")
    return comps


def _registrar_round_scores_historico(year, rodada, scores, source="bot", processed_at=None):
    season = _season_history(year)
    rodada = int(rodada)
    scores_norm = {}
    id_nome = {str(dados["id"]): nome for nome, dados in TIMES.items()}

    for tid, pts in (scores or {}).items():
        try:
            pontos = round(float(pts), 2)
        except (TypeError, ValueError):
            continue
        tid_txt = str(tid)
        scores_norm[tid_txt] = {
            "name": id_nome.get(tid_txt, tid_txt),
            "points": pontos,
        }

    if not scores_norm:
        return False

    ranking = sorted(
        scores_norm.items(),
        key=lambda kv: (-float(kv[1]["points"]), kv[1]["name"])
    )
    mvp_id, mvp_d = ranking[0]
    merda_id, merda_d = ranking[-1]

    competitions = (
        _competicoes_da_rodada_2026(rodada)
        if int(year) == 2026
        else [f"Premier League IOS {year}"]
    )

    novo = {
        "scores": scores_norm,
        "mvp_team_id": int(mvp_id),
        "mvp_points": float(mvp_d["points"]),
        "merda_team_id": int(merda_id),
        "merda_points": float(merda_d["points"]),
        "competitions": competitions,
        "source": source,
        "processed_at": processed_at or datetime.now(timezone.utc).isoformat(),
    }

    atual = season.setdefault("cartola_rounds", {}).get(str(rodada))
    if isinstance(atual, dict):
        # Compara apenas conteúdo oficial, ignorando timestamp antigo.
        comparavel = dict(atual)
        comparavel.update({k: novo[k] for k in (
            "scores", "mvp_team_id", "mvp_points", "merda_team_id",
            "merda_points", "competitions", "source"
        )})
        if all(comparavel.get(k) == novo.get(k) for k in (
            "scores", "mvp_team_id", "mvp_points", "merda_team_id",
            "merda_points", "competitions", "source"
        )):
            return False
        merged = dict(atual)
        merged.update(novo)
        novo = merged

    season["cartola_rounds"][str(rodada)] = novo
    return True


def _adicionar_special_score(year, team_id, points, round_label, competition, source="historico"):
    season = _season_history(year)
    item = {
        "team_id": int(team_id),
        "name": NOMES_PC.get(int(team_id), str(team_id)),
        "points": round(float(points), 2),
        "round": str(round_label),
        "competition": str(competition),
        "source": source,
    }
    chave = (item["team_id"], item["points"], item["round"], item["competition"])
    existentes = {
        (
            int(x.get("team_id", 0)),
            round(float(x.get("points", 0)), 2),
            str(x.get("round", "")),
            str(x.get("competition", "")),
        )
        for x in season.setdefault("special_scores", [])
        if isinstance(x, dict)
    }
    if chave in existentes:
        return False
    season["special_scores"].append(item)
    return True


def _migrar_historico_conhecido():
    """Cria/atualiza o arquivo global sem apagar dados anteriores."""
    mudou = False
    round_scores_state = state.setdefault("round_scores", {})

    # PC/1 2026 -> Cartola R1-R18.
    for rodada, jogos in RESULTADOS_PC.items():
        scores = {}
        for a, pa, b, pb in jogos:
            scores[str(a)] = round(float(pa), 2)
            scores[str(b)] = round(float(pb), 2)
        if round_scores_state.get(str(rodada)) != scores:
            round_scores_state[str(rodada)] = scores
            mudou = True
        if _registrar_round_scores_historico(
            2026, rodada, scores, source="migração PC/1 2026"
        ):
            mudou = True

    # PC/2 conhecido -> Cartola R19-R22.
    for rodada_pc2, jogos in PC2_RESULTADOS_BASE.items():
        rodada_cartola = int(rodada_pc2) + 18
        scores = {}
        for a, pa, b, pb in jogos:
            scores[str(a)] = round(float(pa), 2)
            scores[str(b)] = round(float(pb), 2)
        if round_scores_state.get(str(rodada_cartola)) != scores:
            round_scores_state[str(rodada_cartola)] = scores
            mudou = True
        if _registrar_round_scores_historico(
            2026, rodada_cartola, scores, source="migração PC/2 2026"
        ):
            mudou = True

    # Se o state local já tiver rodadas mais novas, elas também entram no arquivo global.
    for rodada_txt, scores in list(round_scores_state.items()):
        try:
            rodada_extra = int(rodada_txt)
        except (TypeError, ValueError):
            continue
        if not isinstance(scores, dict):
            continue
        if _registrar_round_scores_historico(
            2026, rodada_extra, scores, source="migração do state existente"
        ):
            mudou = True

    # IWC: estes registros já estavam cadastrados na V3, mas !recorde os ignorava.
    for item in MELHORES_PONTUACOES_2026_BASE:
        if str(item.get("competition", "")).upper() != "IWC":
            continue
        round_txt = str(item.get("round", ""))
        round_label = round_txt.split("/", 1)[0] if "/" in round_txt else round_txt
        if _adicionar_special_score(
            2026, item["team_id"], item["points"], round_label, "IWC",
            source="base IWC 2026"
        ):
            mudou = True

    # Top 3 histórico de 2025 conhecido.
    id_por_nome = {nome: dados["id"] for nome, dados in TIMES.items()}
    legacy = HISTORICO_RODADAS.setdefault("legacy_records", [])
    legacy_keys = {
        (
            int(x.get("team_id", 0)),
            round(float(x.get("points", 0)), 2),
            str(x.get("round", "")),
            int(x.get("year", 0)),
            str(x.get("competition", "")),
        )
        for x in legacy if isinstance(x, dict)
    }

    for item in TOP3_HISTORICO_BASE:
        tid = id_por_nome.get(item["nome"])
        if not tid:
            continue
        novo = {
            "team_id": int(tid),
            "name": item["nome"],
            "points": round(float(item["points"]), 2),
            "round": str(item["round"]),
            "year": int(item["year"]),
            "competition": "PC/2",
            "source": "recorde histórico conhecido",
        }
        chave = (
            novo["team_id"], novo["points"], novo["round"],
            novo["year"], novo["competition"]
        )
        if chave not in legacy_keys:
            legacy.append(novo)
            legacy_keys.add(chave)
            mudou = True

    if mudou:
        salvar_state()
        _salvar_historico_rodadas()
        log.info("[HISTÓRICO] Dados globais sincronizados.")
    elif not HISTORICO_RODADAS_FILE.exists():
        _salvar_historico_rodadas()


def _registrar_historico_rodada_finalizada(year, rodada, resultados):
    scores = {
        str(team_id): round(float(pts), 2)
        for _, _, pts, team_id in resultados
    }
    mudou = _registrar_round_scores_historico(
        year, rodada, scores, source="processamento automático"
    )
    if mudou:
        _salvar_historico_rodadas()
    return mudou


def _historico_pontuacoes_ordenado():
    """Ranking GLOBAL: Cartola + IWC + registros legados conhecidos."""
    itens = []
    vistos = set()

    def add_item(team_id, nome, points, round_label, year, competition):
        try:
            tid = int(team_id)
            pts = round(float(points), 2)
            ano = int(year)
        except (TypeError, ValueError):
            return
        comp = str(competition or "Cartola")
        rod = str(round_label)
        chave = (tid, pts, rod, ano, comp)
        if chave in vistos:
            return
        vistos.add(chave)
        itens.append({
            "team_id": tid,
            "nome": nome or NOMES_PC.get(tid, str(tid)),
            "points": pts,
            "round": rod,
            "year": ano,
            "competition": comp,
        })

    for year_txt, season in (HISTORICO_RODADAS.get("seasons", {}) or {}).items():
        try:
            year = int(year_txt)
        except (TypeError, ValueError):
            continue

        for rodada_txt, rd in (season.get("cartola_rounds", {}) or {}).items():
            if not isinstance(rd, dict):
                continue
            for tid, payload in (rd.get("scores", {}) or {}).items():
                if isinstance(payload, dict):
                    pts = payload.get("points")
                    nome = payload.get("name")
                else:
                    pts = payload
                    nome = NOMES_PC.get(int(tid), str(tid))
                add_item(tid, nome, pts, rodada_txt, year, "Cartola")

        for item in (season.get("special_scores", []) or []):
            if isinstance(item, dict):
                add_item(
                    item.get("team_id"), item.get("name"), item.get("points"),
                    item.get("round"), year, item.get("competition", "Especial")
                )

    for item in (HISTORICO_RODADAS.get("legacy_records", []) or []):
        if isinstance(item, dict):
            add_item(
                item.get("team_id"), item.get("name"), item.get("points"),
                item.get("round"), item.get("year"),
                item.get("competition", "Histórico")
            )

    itens.sort(
        key=lambda x: (-x["points"], x["year"], str(x["round"]), x["nome"])
    )
    return itens


def _historico_stats():
    rounds = cartola_scores = specials = 0
    seasons = HISTORICO_RODADAS.get("seasons", {}) or {}
    for season in seasons.values():
        rodadas = season.get("cartola_rounds", {}) or {}
        rounds += len(rodadas)
        for rd in rodadas.values():
            if isinstance(rd, dict):
                cartola_scores += len(rd.get("scores", {}) or {})
        specials += len(season.get("special_scores", []) or [])
    legacy = len(HISTORICO_RODADAS.get("legacy_records", []) or [])
    return {
        "seasons": len(seasons),
        "rounds": rounds,
        "cartola_scores": cartola_scores,
        "special_scores": specials,
        "legacy_records": legacy,
        "total_records": cartola_scores + specials + legacy,
    }


def _ranking_pl_por_historico(year=TEMPORADA_ATUAL):
    season = (HISTORICO_RODADAS.get("seasons", {}) or {}).get(str(int(year)), {})
    totais = {dados["id"]: 0.0 for dados in TIMES.values()}
    jogos = {dados["id"]: 0 for dados in TIMES.values()}

    for rd in (season.get("cartola_rounds", {}) or {}).values():
        if not isinstance(rd, dict):
            continue
        for tid_txt, payload in (rd.get("scores", {}) or {}).items():
            try:
                tid = int(tid_txt)
                pts = float(payload.get("points") if isinstance(payload, dict) else payload)
            except (TypeError, ValueError):
                continue
            if tid in totais:
                totais[tid] += pts
                jogos[tid] += 1

    rows = [
        {
            "id": tid,
            "nome": NOMES_PC.get(tid, str(tid)),
            "pontos": round(total, 2),
            "rodadas": jogos[tid],
        }
        for tid, total in totais.items()
        if jogos[tid] > 0
    ]
    rows.sort(key=lambda x: (-x["pontos"], x["nome"]))
    return rows



def _criar_backup_rodada(year, rodada, rotulo=None):
    try:
        pasta = BACKUPS_DIR / str(int(year))
        pasta.mkdir(parents=True, exist_ok=True)
        sufixo = f"_{rotulo}" if rotulo else ""
        destino = pasta / f"state_R{int(rodada):02d}{sufixo}.json"
        if destino.exists():
            return destino

        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "year": int(year),
            "round": int(rodada),
            "state": state,
            "historico_rodadas": HISTORICO_RODADAS,
        }
        temp = destino.with_suffix(".json.tmp")
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, destino)
        log.info("[BACKUP] Snapshot salvo em %s", destino)
        return destino
    except Exception as e:
        log.error("[BACKUP] Falha no snapshot R%s: %s", rodada, e, exc_info=True)
        return None


def _backup_info():
    if not BACKUPS_DIR.exists():
        return {"count": 0, "latest": None, "size": 0}
    arquivos = [p for p in BACKUPS_DIR.rglob("*.json") if p.is_file()]
    if not arquivos:
        return {"count": 0, "latest": None, "size": 0}
    arquivos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "count": len(arquivos),
        "latest": arquivos[0].name,
        "size": sum(p.stat().st_size for p in arquivos),
    }


def _format_bytes(n):
    n = float(n or 0)
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.1f} {unidade}"
        n /= 1024
    return f"{n:.1f} GB"


def _pc2_resultados_ativos():
    resultados = {int(r): [tuple(j) for j in jogos] for r, jogos in PC2_RESULTADOS_BASE.items()}
    salvos = state.get("competitions", {}).get("pc2_2026", {}).get("results", {})
    for rod, jogos in (salvos or {}).items():
        resultados[int(rod)] = [tuple(j) for j in jogos]
    return resultados


def _calcular_tabela_resultados(resultados):
    ids = {d["id"] for d in TIMES.values()}
    stats = {cid:{"id":cid,"nome":NOMES_PC[cid],"pts":0,"v":0,"e":0,"d":0,"j":0,"pc":0.0} for cid in ids}
    for rod in sorted(resultados):
        for mid, pm, vid, pv in resultados[rod]:
            if mid not in stats or vid not in stats:
                continue
            pm, pv = float(pm), float(pv)
            stats[mid]["j"] += 1; stats[vid]["j"] += 1
            stats[mid]["pc"] += pm; stats[vid]["pc"] += pv
            if pm > pv:
                stats[mid]["pts"] += 3; stats[mid]["v"] += 1; stats[vid]["d"] += 1
            elif pv > pm:
                stats[vid]["pts"] += 3; stats[vid]["v"] += 1; stats[mid]["d"] += 1
            else:
                stats[mid]["pts"] += 1; stats[vid]["pts"] += 1
                stats[mid]["e"] += 1; stats[vid]["e"] += 1
    return sorted(stats.values(), key=lambda x:(x["pts"],x["pc"]), reverse=True)


def _cartola_para_pc2(rodada_cartola):
    rodada_cartola = int(rodada_cartola)
    return rodada_cartola - 18 if 19 <= rodada_cartola <= 36 else None


def _pc2_para_cartola(rodada_pc2):
    rodada_pc2 = int(rodada_pc2)
    return rodada_pc2 + 18 if 1 <= rodada_pc2 <= 18 else None


def _registrar_resultado_no_aproveitamento(jogos):
    """Atualiza V/D/E/J históricos com os confrontos oficiais processados."""
    apr = state.setdefault("aproveitamento", _official_aproveitamento())
    id_nome = {dados["id"]: nome for nome, dados in TIMES.items()}

    def garantir(nome):
        atual = apr.setdefault(nome, {"v": 0, "d": 0, "e": 0, "j": 0})
        atual["v"] = int(atual.get("v", 0))
        atual["j"] = int(atual.get("j", 0))
        atual["d"] = int(atual.get("d", max(0, atual["j"] - atual["v"])))
        atual["e"] = int(atual.get("e", max(0, atual["j"] - atual["v"] - atual["d"])))
        return atual

    for mid, pm, vid, pv in jogos:
        nm, nv = id_nome.get(mid), id_nome.get(vid)
        if not nm or not nv:
            continue

        dm = garantir(nm)
        dv = garantir(nv)
        dm["j"] += 1
        dv["j"] += 1

        if float(pm) > float(pv):
            dm["v"] += 1
            dv["d"] += 1
        elif float(pv) > float(pm):
            dv["v"] += 1
            dm["d"] += 1
        else:
            dm["e"] += 1
            dv["e"] += 1

    normalizado = {
        nome: {
            "v": int(dados.get("v", 0)),
            "d": int(dados.get("d", 0)),
            "e": int(dados.get("e", 0)),
            "j": int(dados.get("j", 0)),
        }
        for nome, dados in apr.items()
    }
    state["aproveitamento"] = normalizado

    if "APROVEITAMENTO" in globals():
        APROVEITAMENTO.clear()
        APROVEITAMENTO.update({nome: dict(dados) for nome, dados in normalizado.items()})


def _processar_pc2_com_snapshot(rodada_pc2, rodada_cartola, scores):
    rodada_pc2 = int(rodada_pc2)
    comp = state.setdefault("competitions", {}).setdefault("pc2_2026", {"processed_rounds":[],"results":{}})
    processadas = [int(x) for x in comp.setdefault("processed_rounds", [])]
    if rodada_pc2 in processadas:
        return True

    confrontos = PC2_CONFRONTOS.get(rodada_pc2)
    if not confrontos:
        pend = state.setdefault("pending_competition_rounds", {}).setdefault("pc2_2026", [])
        if rodada_pc2 not in [int(x) for x in pend]:
            pend.append(rodada_pc2)
        log.warning("[PC2] R%s (Cartola %s) sem confrontos cadastrados. Snapshot foi preservado.", rodada_pc2, rodada_cartola)
        return False

    novos = []
    for mid, vid in confrontos:
        pm = scores.get(str(mid)); pv = scores.get(str(vid))
        if pm is None or pv is None:
            log.error("[PC2] Snapshot incompleto para R%s: %s x %s", rodada_pc2, mid, vid)
            return False
        novos.append((mid, round(float(pm),2), vid, round(float(pv),2)))

    comp.setdefault("results", {})[str(rodada_pc2)] = _serializar_jogos(novos)
    comp["processed_rounds"] = sorted(set(processadas + [rodada_pc2]))
    _registrar_resultado_no_aproveitamento(novos)
    pend = state.setdefault("pending_competition_rounds", {}).setdefault("pc2_2026", [])
    state["pending_competition_rounds"]["pc2_2026"] = [int(x) for x in pend if int(x) != rodada_pc2]
    if rodada_pc2 == 18:
        tabela_final = _calcular_tabela_resultados(_pc2_resultados_ativos())
        if tabela_final:
            campeao_id = tabela_final[0]["id"]
            if _registrar_conquista(
                campeao_id,
                "pc2-2026",
                "Pontos Corridos 2026/2"
            ):
                log.info(
                    "[PC2] Campeão 2026/2 registrado automaticamente: %s",
                    NOMES_PC.get(campeao_id, campeao_id)
                )

    log.info("[PC2] Rodada %s (Cartola %s) processada automaticamente.", rodada_pc2, rodada_cartola)
    return True


def reconciliar_pc2_pendente():
    """Se novos confrontos foram adicionados ao código, usa snapshots antigos para fechar as rodadas pendentes."""
    pendentes = list(state.get("pending_competition_rounds", {}).get("pc2_2026", []) or [])
    for rod_pc2 in pendentes:
        cartola_round = _pc2_para_cartola(int(rod_pc2))
        scores = state.get("round_scores", {}).get(str(cartola_round), {})
        if scores and PC2_CONFRONTOS.get(int(rod_pc2)):
            _processar_pc2_com_snapshot(int(rod_pc2), cartola_round, scores)
    salvar_state()



def _preparar_pasta_imports():
    try:
        IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
        readme = IMPORTS_DIR / "LEIA-ME.txt"
        if not readme.exists():
            readme.write_text(
                "IMPORTADOR PREMIER LEAGUE IOS\n\n"
                "1. Copie a resposta JSON bruta da liga de Pontos Corridos no navegador.\n"
                "2. Salve como imports/campeonato.json.\n"
                "3. No Discord use !admin importar.\n\n"
                "O comando apenas valida. Não altera campeonato, confrontos ou state.\n",
                encoding="utf-8",
            )
    except Exception as e:
        log.error("[IMPORT] Falha preparando imports/: %s", e)


def _arquivo_importacao():
    _preparar_pasta_imports()
    if IMPORT_PADRAO_FILE.exists():
        return IMPORT_PADRAO_FILE
    candidatos = sorted(
        [
            p for p in IMPORTS_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in (".json", ".txt")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidatos[0] if candidatos else None


def _validar_arquivo_campeonato(path):
    rel = {
        "ok": False, "errors": [], "warnings": [], "file": str(path) if path else None,
        "name": None, "league_id": None, "slug": None,
        "round_start": None, "round_end": None, "return_leg": None,
        "participants": 0, "known_participants": 0, "rounds": 0,
        "matches": 0, "completed_matches": 0, "ranking_rows": 0,
    }
    if not path or not Path(path).exists():
        rel["errors"].append("Nenhum arquivo .json/.txt encontrado em imports/.")
        return rel

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8").strip())
    except Exception as e:
        rel["errors"].append(f"JSON inválido: {e}")
        return rel

    if not isinstance(raw, dict):
        rel["errors"].append("A raiz do JSON precisa ser um objeto.")
        return rel

    participantes_raw = raw.get("participantes")
    confrontos_raw = raw.get("confrontos")
    ranking_raw = raw.get("ranking", [])

    rel["name"] = raw.get("nome") or "Sem nome"
    rel["league_id"] = raw.get("id")
    rel["slug"] = raw.get("slug")
    rel["round_start"] = raw.get("rodada_inicial")
    rel["round_end"] = raw.get("rodada_final")
    rel["return_leg"] = raw.get("tem_returno")

    participantes = set()
    if isinstance(participantes_raw, dict):
        for chave, dados in participantes_raw.items():
            tid = dados.get("time_id") if isinstance(dados, dict) else None
            if tid is None:
                tid = chave
            try:
                participantes.add(int(tid))
            except (TypeError, ValueError):
                rel["errors"].append(f"ID de participante inválido: {tid!r}")
    elif isinstance(participantes_raw, list):
        for dados in participantes_raw:
            try:
                tid = dados.get("time_id") if isinstance(dados, dict) else dados
                participantes.add(int(tid))
            except (TypeError, ValueError, AttributeError):
                rel["errors"].append(f"Participante inválido: {dados!r}")
    else:
        rel["errors"].append("Campo 'participantes' ausente ou inesperado.")

    rel["participants"] = len(participantes)
    conhecidos = {dados["id"] for dados in TIMES.values()}
    rel["known_participants"] = len(participantes & conhecidos)
    desconhecidos = sorted(participantes - conhecidos)
    if desconhecidos:
        rel["errors"].append(
            "Times ainda não cadastrados no bot: " + ", ".join(map(str, desconhecidos))
        )

    try:
        declarados = int(
            raw.get("quantidade_participantes", raw.get("quantidade_times", len(participantes))) or 0
        )
        if declarados and declarados != len(participantes):
            rel["errors"].append(
                f"Quantidade declarada ({declarados}) difere da encontrada ({len(participantes)})."
            )
    except (TypeError, ValueError):
        rel["warnings"].append("Quantidade declarada não pôde ser validada.")

    rodadas = []
    if not isinstance(confrontos_raw, dict):
        rel["errors"].append("Campo 'confrontos' ausente ou não separado por rodada.")
    else:
        for rodada_txt, jogos in confrontos_raw.items():
            try:
                rodada = int(rodada_txt)
                rodadas.append(rodada)
            except (TypeError, ValueError):
                rel["errors"].append(f"Rodada inválida: {rodada_txt!r}")
                continue

            if not isinstance(jogos, list):
                rel["errors"].append(f"Rodada {rodada}: jogos não são uma lista.")
                continue

            usados = []
            for idx, jogo in enumerate(jogos, 1):
                if not isinstance(jogo, dict):
                    rel["errors"].append(f"R{rodada} jogo {idx}: formato inválido.")
                    continue
                mand = jogo.get("mandante") or {}
                visi = jogo.get("visitante") or {}
                try:
                    mid = int(mand.get("id"))
                    vid = int(visi.get("id"))
                except (TypeError, ValueError, AttributeError):
                    rel["errors"].append(f"R{rodada} jogo {idx}: IDs inválidos.")
                    continue

                rel["matches"] += 1
                usados.extend([mid, vid])

                if mid == vid:
                    rel["errors"].append(f"R{rodada}: time {mid} enfrenta ele mesmo.")
                if participantes and (mid not in participantes or vid not in participantes):
                    rel["errors"].append(f"R{rodada}: time fora dos participantes ({mid} x {vid}).")

                pm = mand.get("pontuacao_rodada")
                pv = visi.get("pontuacao_rodada")
                if (pm is None) != (pv is None):
                    rel["errors"].append(f"R{rodada} {mid} x {vid}: só um lado tem pontuação.")
                elif pm is not None and pv is not None:
                    rel["completed_matches"] += 1
                    try:
                        pmf, pvf = float(pm), float(pv)
                        esperado = mid if pmf > pvf else vid if pvf > pmf else None
                        vencedor = jogo.get("vencedor_id")
                        if vencedor is not None and int(vencedor) != esperado:
                            rel["errors"].append(
                                f"R{rodada} {mid} x {vid}: vencedor_id não bate com o placar."
                            )
                    except (TypeError, ValueError):
                        rel["errors"].append(f"R{rodada} {mid} x {vid}: pontuação inválida.")

            if len(usados) != len(set(usados)):
                rel["errors"].append(f"R{rodada}: algum time aparece mais de uma vez.")

            if participantes:
                esperados = len(participantes) // 2
                if len(jogos) != esperados:
                    rel["errors"].append(
                        f"R{rodada}: {len(jogos)} jogos; esperado {esperados}."
                    )
                if len(participantes) % 2 == 0 and set(usados) != participantes:
                    faltam = sorted(participantes - set(usados))
                    if faltam:
                        rel["errors"].append(
                            f"R{rodada}: times sem jogo: {', '.join(map(str, faltam))}."
                        )

    rodadas = sorted(set(rodadas))
    rel["rounds"] = len(rodadas)

    try:
        ini, fim = int(rel["round_start"]), int(rel["round_end"])
        esperadas = list(range(ini, fim + 1))
        if rodadas and rodadas != esperadas:
            faltando = sorted(set(esperadas) - set(rodadas))
            rel["errors"].append(
                f"Rodadas não são contínuas de {ini} a {fim}. Faltando: {faltando}."
            )
    except (TypeError, ValueError):
        rel["warnings"].append("rodada_inicial/rodada_final não puderam ser validadas.")

    if isinstance(ranking_raw, list):
        rel["ranking_rows"] = len(ranking_raw)
        for row in ranking_raw:
            if not isinstance(row, dict):
                continue
            try:
                tid = int(row.get("time_id"))
            except (TypeError, ValueError):
                rel["warnings"].append("Ranking contém linha sem time_id válido.")
                continue
            if participantes and tid not in participantes:
                rel["errors"].append(f"Ranking contém time fora da liga: {tid}.")
    else:
        rel["warnings"].append("Campo 'ranking' não é lista.")

    rel["ok"] = not rel["errors"]
    return rel


_preparar_pasta_imports()


MARCOS_HISTORICOS = (50, 100, 150, 200)

# TOP3_HISTORICO_BASE (recordes antigos conhecidos) vem de liga_dados.py.

# Agora todas as bases necessárias já existem: migra o histórico uma vez e
# cria um ponto de restauração da situação atual.
_migrar_historico_conhecido()
_criar_backup_rodada(
    TEMPORADA_ATUAL,
    int(state.get("last_processed_round", STATS_BASELINE_ROUND) or STATS_BASELINE_ROUND),
    rotulo="inicial",
)

def _snapshot_aproveitamento():
    snap = {}
    for nome, dados in (state.get("aproveitamento", {}) or {}).items():
        v = int(dados.get("v", 0))
        j = int(dados.get("j", 0))
        d = int(dados.get("d", max(0, j - v)))
        e = int(dados.get("e", max(0, j - v - d)))
        snap[nome] = {"v": v, "d": d, "e": e, "j": j}
    return snap

def _detectar_marcos_atingidos(antes, depois):
    eventos = []
    metricas = (
        ("j", "jogos"),
        ("v", "vitórias"),
        ("d", "derrotas"),
    )

    for nome in TIMES:
        old = antes.get(nome, {})
        new = depois.get(nome, {})
        for campo, rotulo in metricas:
            anterior = int(old.get(campo, 0))
            atual = int(new.get(campo, 0))
            for marco in MARCOS_HISTORICOS:
                if anterior < marco <= atual:
                    chave = f"{nome}|{campo}|{marco}"
                    anunciados = state.setdefault("marcos_anunciados", [])
                    if chave not in anunciados:
                        eventos.append({
                            "nome": nome,
                            "campo": campo,
                            "rotulo": rotulo,
                            "marco": marco,
                            "chave": chave,
                        })
    return eventos

async def _enviar_marcos_historicos(canal, eventos):
    if not canal or not eventos:
        return

    for evento in eventos:
        nome = evento["nome"]
        marco = evento["marco"]
        campo = evento["campo"]
        info = TIMES_INFO.get(nome, {}) if "TIMES_INFO" in globals() else {}

        if campo == "d":
            titulo = "🩶 MARCA HISTÓRICA... MAS DESSAS QUE DOEM"
            descricao = (
                f"**{nome}** acaba de alcançar **{marco} derrotas oficiais** na história da liga.\n\n"
                "Nem toda marca histórica vem com champanhe. Essa veio com terapia e revisão de escalação. 😭"
            )
            cor = discord.Color.from_rgb(128, 128, 128)
        elif campo == "v":
            titulo = "🏆 CONQUISTA HISTÓRICA!"
            descricao = (
                f"**{nome}** chegou a **{marco} VITÓRIAS oficiais** na Premier League IOS!\n\n"
                "É vitória demais pra chamar de fase. O clube acaba de colocar mais uma placa na parede da história. ✨"
            )
            cor = discord.Color.gold()
        else:
            titulo = "⭐ CLUBE CENTENÁRIO DE GUERRA!" if marco >= 100 else "⭐ MARCA HISTÓRICA!"
            descricao = (
                f"**{nome}** completou **{marco} JOGOS oficiais** na história da liga!\n\n"
                "Rodada após rodada, escalação após escalação, o escudo segue em campo. 🫡"
            )
            cor = discord.Color.gold()

        embed = discord.Embed(
            title=titulo,
            description=descricao,
            color=cor,
        )
        if info.get("logo"):
            embed.set_thumbnail(url=info["logo"])
        embed.set_footer(text=f"Marco oficial • {marco} {evento['rotulo']}")
        await canal.send(embed=embed)

        state.setdefault("marcos_anunciados", []).append(evento["chave"])

    state["marcos_anunciados"] = sorted(set(state.get("marcos_anunciados", [])))
    salvar_state()

def _historico_2026_antes_da_rodada(rodada_excluir):
    itens = []
    for item in _historico_pontuacoes_ordenado():
        if int(item.get("year", 0)) != 2026:
            continue
        if (
            str(item.get("competition", "")).lower() == "cartola"
            and str(item.get("round")) == str(int(rodada_excluir))
        ):
            continue
        itens.append({
            "nome": item.get("nome"),
            "points": float(item.get("points", 0)),
            "round": item.get("round"),
            "year": 2026,
            "competition": item.get("competition", "Cartola"),
        })
    return itens

async def _enviar_alertas_recordes(canal, rodada_finalizada, resultados, historico_antes):
    if not canal:
        return

    anteriores_all_time = [dict(x) for x in TOP3_HISTORICO_BASE] + [dict(x) for x in historico_antes]
    melhor_temporada_antes = max(
        (float(x["points"]) for x in historico_antes),
        default=float("-inf"),
    )

    atuais = [
        {
            "nome": nome,
            "points": float(pts),
            "round": int(rodada_finalizada),
            "year": 2026,
            "team_id": int(team_id),
        }
        for nome, _, pts, team_id in resultados
    ]

    ranking_all_time = anteriores_all_time + atuais
    ranking_all_time.sort(key=lambda x: (-float(x["points"]), int(x.get("year", 0)), int(x.get("round", 0))))

    posicoes_atuais = {}
    for pos, item in enumerate(ranking_all_time, 1):
        if (
            int(item.get("year", 0)) == 2026
            and int(item.get("round", 0)) == int(rodada_finalizada)
            and "team_id" in item
        ):
            posicoes_atuais[int(item["team_id"])] = pos

    melhor_atual = max(atuais, key=lambda x: x["points"])
    novo_recorde_temporada_id = (
        melhor_atual["team_id"]
        if melhor_atual["points"] > melhor_temporada_antes
        else None
    )

    for item in sorted(atuais, key=lambda x: x["points"], reverse=True):
        pos_hist = posicoes_atuais.get(item["team_id"])
        entrou_top3 = pos_hist is not None and pos_hist <= 3
        recorde_temporada = item["team_id"] == novo_recorde_temporada_id

        if not entrou_top3 and not recorde_temporada:
            continue

        conquistas = []
        frases = []

        if entrou_top3:
            if pos_hist == 1:
                conquistas.append("🥇 **NOVO RECORDE ABSOLUTO DA HISTÓRIA**")
                frases.append("O teto da Premier League IOS acabou de ser quebrado.")
            elif pos_hist == 2:
                conquistas.append("🥈 **2ª MAIOR PONTUAÇÃO DA HISTÓRIA**")
                frases.append("Entrou na prateleira onde só ficam as atuações monstruosas.")
            else:
                conquistas.append("🥉 **TOP 3 DA HISTÓRIA**")
                frases.append("O nome do clube agora está oficialmente no pódio histórico.")

        if recorde_temporada:
            conquistas.append("👑 **MAIOR PONTUAÇÃO DA TEMPORADA 2026**")
            frases.append("Ninguém fez mais pontos nesta temporada.")

        info = TIMES_INFO.get(item["nome"], {}) if "TIMES_INFO" in globals() else {}
        embed = discord.Embed(
            title="✨ NOITE DE RECORDE! ✨",
            description=(
                f"**{item['nome']}** marcou **{item['points']:.2f} pontos** na rodada "
                f"**{rodada_finalizada}**.\n\n"
                + "\n".join(conquistas)
                + "\n\n"
                + " ".join(frases)
            ),
            color=discord.Color.gold(),
        )
        if info.get("logo"):
            embed.set_thumbnail(url=info["logo"])
        embed.set_footer(text="Uma atuação para entrar nos livros da Premier League IOS")
        await canal.send(embed=embed)


def _contar_titulos_categoria(nome_time, prefixo):
    return sum(
        1 for item in _conquistas_do_time(nome_time)
        if str(item.get("key", "")).startswith(prefixo)
    )


def _ordinal_feminino(n):
    return f"{int(n)}ª"


def _nome_faixa_titulo(n):
    nomes = {
        1: "PRIMEIRO TÍTULO",
        2: "BICAMPEÃO",
        3: "TRICAMPEÃO",
        4: "TETRACAMPEÃO",
        5: "PENTACAMPEÃO",
        6: "HEXACAMPEÃO",
    }
    return nomes.get(int(n), f"{int(n)} VEZES CAMPEÃO")


async def _anunciar_campeao(
    canal, team_id, titulo_key, titulo_texto,
    categoria_prefixo, campeonato_nome, ano
):
    if not canal:
        return False

    chave = f"{titulo_key}:{int(team_id)}"
    anunciados = state.setdefault("campeoes_anunciados", [])
    if chave in anunciados:
        return False

    _registrar_conquista(team_id, titulo_key, titulo_texto)
    nome = NOMES_PC.get(int(team_id), str(team_id))
    total = max(1, _contar_titulos_categoria(nome, categoria_prefixo))

    coroas = "👑" * min(total, 6)
    if total > 6:
        coroas += f" ×{total}"

    embed = discord.Embed(
        title="👑🏆 É CAMPEÃO! 🏆👑",
        description=(
            f"# {nome}\n\n"
            f"venceu o **{campeonato_nome} {int(ano)}** pela **{_ordinal_feminino(total)} vez**!\n\n"
            f"{coroas}\n"
            f"### {_nome_faixa_titulo(total)} DO {campeonato_nome.upper()}!"
        ),
        color=discord.Color.gold(),
    )

    info = TIMES_INFO.get(nome, {})
    if info.get("logo"):
        embed.set_thumbnail(url=info["logo"])

    embed.set_footer(
        text=f"🏆 {total} título(s) de {campeonato_nome} na história do clube"
    )

    await canal.send(embed=embed)
    anunciados.append(chave)
    state["campeoes_anunciados"] = sorted(set(anunciados))
    salvar_state()
    return True


def _eventos_campeao_apos_rodada(rodada_finalizada):
    eventos = []
    rodada_finalizada = int(rodada_finalizada)

    if rodada_finalizada == 36:
        tabela_pc2 = _calcular_tabela_resultados(_pc2_resultados_ativos())
        if tabela_pc2:
            eventos.append({
                "team_id": tabela_pc2[0]["id"],
                "titulo_key": "pc2-2026",
                "titulo_texto": "Pontos Corridos 2026/2",
                "categoria_prefixo": "pc2-",
                "campeonato_nome": "Pontos Corridos/2",
                "ano": 2026,
            })

        tabela_pl = _ranking_pl_por_historico(2026)
        if tabela_pl:
            eventos.append({
                "team_id": tabela_pl[0]["id"],
                "titulo_key": "pl-ios-2026",
                "titulo_texto": "Premier League IOS 2026",
                "categoria_prefixo": "pl-ios-",
                "campeonato_nome": "Premier League IOS",
                "ano": 2026,
            })

    return eventos


async def _enviar_mito_rodada(canal, rodada, nome, pontos):
    info = TIMES_INFO.get(nome, {})
    embed = discord.Embed(
        title="Parece que alguém não vai dar o burrote hoje!! GG!! 😎🔥",
        description=f"**{nome}** foi o Cartoleiro da rodada {rodada} com **{pontos:.2f} pontos!!!**\n\n<@{TIMES[nome]['discord']}>",
        color=info.get("cor", discord.Color.gold()),
    )
    if info.get("logo"):
        embed.set_thumbnail(url=info["logo"])
    await canal.send(embed=embed)


async def _enviar_merda_rodada(canal, rodada, nome, pontos):
    info = TIMES_INFO.get(nome, {})
    embed = discord.Embed(
        title="💩 MERDA DA RODADA 💩",
        description=f"**{nome}** terminou a rodada {rodada} em último com **{pontos:.2f} pontos**.\n\n<@{TIMES[nome]['discord']}>",
        color=discord.Color.from_rgb(90,30,0),
    )
    if info.get("logo"):
        embed.set_thumbnail(url=info["logo"])
    await canal.send(embed=embed)




FORMACAO_MAIS_ESCALADO = (
    (1, 1, "🧤 GOL"),
    (3, 2, "🛡️ ZG"),
    (2, 2, "🔵 LA"),
    (4, 3, "🎯 MEI"),
    (5, 3, "⚽ ATA"),
    (6, 1, "👔 TEC"),
)


def _time_mais_escalado():
    """Seleção mais escalada: [(rótulo, vezes, nome, atleta_id)] e o capitão mais usado."""
    posicoes = state.get("posicoes_atletas", {}) or {}
    nomes = state.get("nomes_atletas", {}) or {}
    por_pos = {pos_id: [] for pos_id, _, _ in FORMACAO_MAIS_ESCALADO}
    for aid, times_list in lineup_por_atleta.items():
        pos = posicoes.get(aid)
        if pos in por_pos:
            por_pos[pos].append((len(times_list), nomes.get(aid, aid), aid))

    linhas = []
    for pos_id, qtd, label in FORMACAO_MAIS_ESCALADO:
        por_pos[pos_id].sort(key=lambda x: (-x[0], x[1]))
        linhas.extend((label, count, nome, aid) for count, nome, aid in por_pos[pos_id][:qtd])

    cap_count = {}
    for cap_aid in capitao_por_time.values():
        cap_count[cap_aid] = cap_count.get(cap_aid, 0) + 1
    return linhas, (max(cap_count, key=cap_count.get) if cap_count else None)


async def _enviar_time_mais_escalado(canal):
    linhas, cap_mais = _time_mais_escalado()
    if not linhas:
        return
    texto = "".join(
        f"{label} - **{nome}**{f' {EMOTE_CAPITAO}' if aid == cap_mais else ''} ({count}/10)\n"
        for label, count, nome, aid in linhas
    )
    rodada = int(state.get("lineup_round", 0) or 0)
    rodape = "Empate desempatado por ordem alfabetica | 🇨 = capitao em mais times"
    jogadores = _jogadores_para_imagem(linhas, cap_mais)
    if await _enviar_imagem_mais_escalado(
        canal, "🏆 TIME MAIS ESCALADO DA RODADA", rodape, "time_mais_escalado.png",
        jogadores, "TIME MAIS ESCALADO", f"Rodada {rodada} • escalações da liga",
    ):
        return
    embed = discord.Embed(title="🏆 TIME MAIS ESCALADO DA RODADA", color=discord.Color.gold())
    embed.add_field(name="​", value=texto, inline=False)
    embed.set_footer(text=rodape)
    await canal.send(embed=embed)


async def _enviar_time_mais_escalado_final(canal):
    """Resumo final do time mais escalado, com a pontuação de cada atleta."""
    pontuacoes = state.get("pontuacao_snapshot", {}) or {}
    linhas, cap_mais = _time_mais_escalado()
    if not linhas or not pontuacoes:
        log.warning("[TIME ESCALADO FINAL] Sem lineup ou sem pontuação salva; embed não enviado.")
        return

    texto, total = [], 0.0
    for label, count, nome, aid in linhas:
        pontos = float(pontuacoes.get(aid, 0) or 0) * (1.5 if aid == cap_mais else 1)
        total += pontos
        capitao = f" {EMOTE_CAPITAO}" if aid == cap_mais else ""
        texto.append(f"{label} - **{nome}**{capitao} ({count}/10) — **{pontos:.1f} pts**")

    rodada = int(state.get("lineup_round", 0) or 0)
    rodape = f"Total: {total:.1f} pts | 🇨 = capitão"
    jogadores = _jogadores_para_imagem(linhas, cap_mais, pontuacoes)
    if await _enviar_imagem_mais_escalado(
        canal, "📊 TIME MAIS ESCALADO — PONTUAÇÃO FINAL", rodape, "time_mais_escalado_final.png",
        jogadores, "PONTUAÇÃO FINAL", f"Time mais escalado • Rodada {rodada}", total,
    ):
        return
    embed = discord.Embed(title="📊 TIME MAIS ESCALADO — PONTUAÇÃO FINAL", color=discord.Color.gold())
    embed.add_field(name="​", value="\n".join(texto), inline=False)
    embed.set_footer(text=rodape)
    await canal.send(embed=embed)


def _jogadores_para_imagem(linhas, cap_mais, pontuacoes=None):
    fotos = state.get("fotos_atletas", {}) or {}
    clubes = state.get("clubes_atletas", {}) or {}
    posicoes = state.get("posicoes_atletas", {}) or {}
    completos = state.get("nomes_completos_atletas", {}) or {}
    jogadores = []
    for _, count, nome, aid in linhas:
        jogador = {
            "id": aid, "nome": nome, "nome_completo": completos.get(aid),
            "posicao_id": posicoes.get(aid), "vezes": count,
            "capitao": aid == cap_mais, "foto": fotos.get(aid), "clube_id": clubes.get(aid),
        }
        if pontuacoes is not None:
            jogador["pontos"] = float(pontuacoes.get(aid, 0) or 0) * (1.5 if aid == cap_mais else 1)
        jogadores.append(jogador)
    return jogadores


async def _enviar_imagem_mais_escalado(canal, titulo_embed, rodape, arquivo, jogadores, titulo, subtitulo, total=None):
    """Envia a imagem; devolve False se ela não pôde ser gerada (aí vai o texto)."""
    try:
        imagem = await run(render_time_mais_escalado, jogadores, titulo, subtitulo, total)
    except Exception:
        log.error("[IMAGEM] Falha ao gerar %s; enviando em texto.", arquivo, exc_info=True)
        return False
    embed = discord.Embed(title=titulo_embed, color=discord.Color.gold())
    embed.set_image(url=f"attachment://{arquivo}")
    embed.set_footer(text=rodape)
    await canal.send(embed=embed, file=discord.File(imagem, filename=arquivo))
    return True


async def _escalar_time_do_bot(canal, rodada):
    """Monta o time da rodada com o patrimônio médio da liga e publica. Roda uma vez por rodada."""
    atletas, partidas = await asyncio.gather(run(buscar_atletas_mercado), run(buscar_partidas))
    if not atletas or not partidas:
        return

    consultas = await asyncio.gather(*[run(_buscar_time, d["id"], rodada) for d in TIMES.values()])
    patrimonios = [float(t["patrimonio"]) for t in consultas if t and t.get("patrimonio")]
    orcamento = sum(patrimonios) / len(patrimonios) if patrimonios else 100.0

    escalacao = await run(montar_time, atletas, partidas, orcamento)
    if not escalacao:
        log.warning("[TIME DO BOT] Não foi possível montar o time da rodada %s", rodada)
        return

    linhas = []
    for atleta in escalacao["atletas"]:
        capitao = f" {EMOTE_CAPITAO}" if atleta["id"] == escalacao["capitao"] else ""
        linhas.append(f"{POSICOES[atleta['posicao_id']]} **{atleta['nome']}**{capitao} — C$ {atleta['preco']:.2f}")

    embed = discord.Embed(
        title=f"🤖 TIME DO BOT — RODADA {int(rodada)}",
        description=(
            f"Esquema **{escalacao['esquema']}** • previsão de **{escalacao['previsto']:.1f} pts**\n"
            "Só prováveis, pesando média, mando, tabela e fase dos clubes."
        ),
        color=discord.Color.from_rgb(30, 100, 200),
    )
    embed.add_field(name="​", value="\n".join(linhas), inline=False)
    embed.set_footer(text=f"C$ {escalacao['gasto']:.2f} de C$ {escalacao['orcamento']:.2f} (patrimônio médio da liga)")
    await canal.send(embed=embed)

    state.setdefault("time_do_bot", {})[str(int(rodada))] = escalacao
    state["time_bot_rodada"] = int(rodada)
    salvar_state()


async def _enviar_resultado_time_bot(canal, rodada, resultados):
    escalacao = state.get("time_do_bot", {}).get(str(int(rodada)))
    pontuacoes = state.get("pontuacao_snapshot", {}) or {}
    if not escalacao or not pontuacoes:
        return

    pontos = pontuar_time(escalacao, pontuacoes)
    escalacao["pontos"] = pontos
    placar = sorted((float(r[2]) for r in resultados), reverse=True)
    posicao = 1 + sum(1 for valor in placar if valor > pontos)
    perderam = [r[0] for r in resultados if float(r[2]) < pontos]
    historico = [t["pontos"] for t in state["time_do_bot"].values() if "pontos" in t]

    descricao = (
        f"**{pontos:.2f} pts** (previsão: {escalacao['previsto']:.1f})\n"
        f"Terminaria a rodada em **{posicao}º** entre os times da liga."
    )
    if perderam:
        descricao += "\n\n🫵 **Perderam pro bot:** " + ", ".join(perderam)
    embed = discord.Embed(
        title=f"🤖 TIME DO BOT — RESULTADO DA RODADA {int(rodada)}",
        description=descricao,
        color=discord.Color.from_rgb(30, 100, 200),
    )
    embed.set_footer(text=f"Média do bot: {sum(historico) / len(historico):.2f} pts em {len(historico)} rodada(s)")
    await canal.send(embed=embed)
    salvar_state()



async def _enviar_nova_rodada_aberta(canal, rodada_nova):
    if not canal:
        return

    rodada_nova = int(rodada_nova)
    anunciadas = [
        int(x) for x in state.setdefault("rodadas_abertura_anunciadas", [])
    ]
    if rodada_nova in anunciadas:
        return

    mercado = await run(_mercado)
    fechamento_txt = "horário ainda não disponível"

    if mercado and int(mercado.rodada_atual) == rodada_nova:
        fechamento = mercado.fechamento
        if fechamento.tzinfo is None:
            fechamento = fechamento.replace(
                tzinfo=timezone(timedelta(hours=-3))
            )
        fechamento_txt = fechamento.astimezone(
            timezone(timedelta(hours=-3))
        ).strftime("%d/%m às %H:%M")

    linhas = []
    subtitulo = f"Cartola — Rodada {rodada_nova}"

    if 1 <= rodada_nova <= 18:
        rodada_comp = rodada_nova
        subtitulo += f" • PC/1 — Rodada {rodada_comp}"
        confrontos = CONFRONTOS_PC.get(rodada_comp, [])
    elif 19 <= rodada_nova <= 36:
        rodada_comp = rodada_nova - 18
        subtitulo += f" • PC/2 — Rodada {rodada_comp}"
        confrontos = PC2_CONFRONTOS.get(rodada_comp, [])
    else:
        confrontos = []

    for a, b in confrontos:
        linhas.append(
            f"{EMOTES_PC.get(a, '⚽')} **{NOMES_PC[a]}** "
            f"x **{NOMES_PC[b]}** {EMOTES_PC.get(b, '⚽')}"
        )

    descricao = (
        f"**{subtitulo}**\n"
        f"⏰ Mercado fecha: **{fechamento_txt}**"
    )
    if linhas:
        descricao += "\n\n⚔️ **CONFRONTOS**\n" + "\n".join(linhas)

    embed = discord.Embed(
        title="🟢 NOVA RODADA ABERTA!",
        description=descricao,
        color=discord.Color.green()
    )
    await canal.send(embed=embed)

    anunciadas.append(rodada_nova)
    state["rodadas_abertura_anunciadas"] = sorted(set(anunciadas))
    salvar_state()

_fila_anuncios_lock = asyncio.Lock()
MAX_TENTATIVAS_ANUNCIO = 60
_HANDLERS_ANUNCIO = {
    "mito": _enviar_mito_rodada,
    "merda": _enviar_merda_rodada,
    "time_final": _enviar_time_mais_escalado_final,
    "resultado_bot": _enviar_resultado_time_bot,
    "marcos": _enviar_marcos_historicos,
    "recordes": _enviar_alertas_recordes,
    "campeao": _anunciar_campeao,
    "nova_rodada": _enviar_nova_rodada_aberta,
}


def _enfileirar_anuncio(tipo, **dados):
    state.setdefault("fila_anuncios", []).append({"tipo": tipo, "dados": dados, "tentativas": 0})


async def _processar_fila_anuncios(canal):
    """Envia os anúncios pendentes em ordem; o que falhar é tentado no minuto seguinte."""
    if not canal or _fila_anuncios_lock.locked():
        return
    async with _fila_anuncios_lock:
        fila = state.setdefault("fila_anuncios", [])
        while fila:
            item = fila[0]
            try:
                await _HANDLERS_ANUNCIO[item["tipo"]](canal, **item["dados"])
            except Exception as e:
                item["tentativas"] = int(item.get("tentativas", 0)) + 1
                log.warning("[ANÚNCIOS] Falha em '%s' (%s/%s): %s", item["tipo"], item["tentativas"], MAX_TENTATIVAS_ANUNCIO, e)
                if item["tentativas"] < MAX_TENTATIVAS_ANUNCIO:
                    salvar_state()
                    return
                log.error("[ANÚNCIOS] Desistindo de '%s'.", item["tipo"], exc_info=True)
            fila.pop(0)
            salvar_state()


async def processar_rodada_finalizada(canal, rodada_finalizada, resultados, origem="loop"):
    """Único ponto de escrita para fechamento de rodada: snapshots, stats e competições."""
    rodada_finalizada = int(rodada_finalizada)
    if len(resultados) != len(TIMES):
        log.error("[FECHAMENTO] R%s incompleta: %s/%s times", rodada_finalizada, len(resultados), len(TIMES))
        return {"processed": False, "reason": "incomplete_scores"}

    last_processed = int(state.get("last_processed_round", STATS_BASELINE_ROUND) or STATS_BASELINE_ROUND)
    processadas = [int(x) for x in state.setdefault("stats_processed_rounds", [])]

    if rodada_finalizada in processadas or rodada_finalizada <= last_processed:
        log.info("[FECHAMENTO] Rodada %s já processada; ignorando (%s).", rodada_finalizada, origem)
        return {"processed": False, "reason": "already_processed"}

    if rodada_finalizada > last_processed + 1:
        state["recovery_required"] = {
            "from_round": last_processed + 1,
            "to_round": rodada_finalizada,
            "reason": "gap_detectado_no_fechamento",
        }
        salvar_state()
        if canal:
            await canal.send(
                f"⚠️ Não processei a rodada **{rodada_finalizada}** porque existe um buraco desde a rodada **{last_processed + 1}**. "
                "O state foi protegido contra atualização parcial."
            )
        return {"processed": False, "reason": "gap"}

    if all(float(r[2]) == 0 for r in resultados):
        log.warning("[FECHAMENTO] R%s veio toda zerada; aguardando a API consolidar.", rodada_finalizada)
        return {"processed": False, "reason": "suspicious_scores"}
    anterior = state.get("round_scores", {}).get(str(rodada_finalizada - 1))
    if anterior and all(
        abs(float(anterior.get(str(r[3]), float("inf"))) - float(r[2])) < 0.005 for r in resultados
    ):
        log.warning("[FECHAMENTO] R%s igual à rodada anterior; aguardando a API atualizar.", rodada_finalizada)
        return {"processed": False, "reason": "suspicious_scores"}

    historico_antes = _historico_2026_antes_da_rodada(rodada_finalizada)
    aproveitamento_antes = _snapshot_aproveitamento()

    scores = {str(r[3]): round(float(r[2]), 2) for r in resultados}
    state.setdefault("round_scores", {})[str(rodada_finalizada)] = scores

    # Ordem de TIMES é mantida como desempate determinístico em eventual igualdade.
    mito_nome, _, mito_pts, mito_id = max(resultados, key=lambda x: x[2])
    merda_nome, _, merda_pts, merda_id = min(resultados, key=lambda x: x[2])

    mvp = state.setdefault("mvp_historico", _official_counter("mvp"))
    mvp[mito_nome] = int(mvp.get(mito_nome, 0)) + 1

    merda = state.setdefault("merda_historico", _official_counter("merda"))
    merda[merda_nome] = int(merda.get(merda_nome, 0)) + 1

    zikado = state.setdefault("zikado", _official_counter("zikado"))
    for nome_time in TIMES:
        zikado[nome_time] = 0 if nome_time == mito_nome else int(zikado.get(nome_time, 0)) + 1

    state["stats_last_round"] = {
        "round": rodada_finalizada,
        "mvp_team_id": mito_id,
        "mvp_points": round(float(mito_pts), 2),
        "merda_team_id": merda_id,
        "merda_points": round(float(merda_pts), 2),
    }
    processadas.append(rodada_finalizada)
    state["stats_processed_rounds"] = sorted(set(processadas))
    state["stats_round"] = rodada_finalizada
    state["last_processed_round"] = rodada_finalizada
    state["recovery_required"] = None

    # Pontuação final dos atletas: os scouts ainda guardam a última leitura da rodada.
    state["pontuacao_snapshot"] = {
        aid: v.get("pontuacao", 0)
        for aid, v in scouts_anteriores.items()
        if v.get("pontuacao", 0)
    }

    # PC/2: rodada Cartola 19 → PC/2 R1 ... Cartola 36 → PC/2 R18.
    rodada_pc2 = _cartola_para_pc2(rodada_finalizada)
    pc2_ok = True
    if rodada_pc2 is not None:
        pc2_ok = _processar_pc2_com_snapshot(rodada_pc2, rodada_finalizada, scores)

    _registrar_historico_rodada_finalizada(
        TEMPORADA_ATUAL, rodada_finalizada, resultados
    )
    eventos_campeao = _eventos_campeao_apos_rodada(rodada_finalizada)
    for evento in eventos_campeao:
        _registrar_conquista(evento["team_id"], evento["titulo_key"], evento["titulo_texto"])

    aproveitamento_depois = _snapshot_aproveitamento()
    marcos_atingidos = _detectar_marcos_atingidos(aproveitamento_antes, aproveitamento_depois)

    # Primeiro garante os dados; os anúncios vão para uma fila que sobrevive a falhas.
    salvar_state()
    _salvar_historico_rodadas()
    _criar_backup_rodada(TEMPORADA_ATUAL, rodada_finalizada)

    if canal:
        resultados_json = [list(r) for r in resultados]
        _enfileirar_anuncio("mito", rodada=rodada_finalizada, nome=mito_nome, pontos=mito_pts)
        _enfileirar_anuncio("merda", rodada=rodada_finalizada, nome=merda_nome, pontos=merda_pts)
        _enfileirar_anuncio("time_final")
        _enfileirar_anuncio("resultado_bot", rodada=rodada_finalizada, resultados=resultados_json)
        for evento in marcos_atingidos:
            _enfileirar_anuncio("marcos", eventos=[evento])
        _enfileirar_anuncio(
            "recordes",
            rodada_finalizada=rodada_finalizada,
            resultados=resultados_json,
            historico_antes=historico_antes,
        )
        for evento in eventos_campeao:
            _enfileirar_anuncio("campeao", **evento)
        if rodada_finalizada < 36:
            _enfileirar_anuncio("nova_rodada", rodada_nova=rodada_finalizada + 1)
        salvar_state()

    try:
        nomes_estatisticas = {
            str(dados["id"]): nome for nome, dados in TIMES.items()
        }
        resumo_estatisticas = await run(atualizar_estatisticas, nomes_estatisticas)
        log.info("[ESTATISTICAS] tabelas atualizadas: %s", resumo_estatisticas)
    except Exception:
        log.error("[ESTATISTICAS] falha ao gerar tabelas; fechamento preservado", exc_info=True)
    await run(publicar_site_estado)
    await _processar_fila_anuncios(canal)
    return {"processed": True, "pc2_processed": pc2_ok, "mvp": mito_nome, "merda": merda_nome}


# Carrega resultados PC/2 persistidos e tenta fechar pendências usando snapshots salvos.
reconciliar_pc2_pendente()

# ==========================================
# COMANDOS
# ==========================================


def _buscar_dados_nacional_cartola():
    """Consulta a API do Cartola para os 10 clubes da liga e monta a classificação da Liga Nacional."""
    from concurrent.futures import ThreadPoolExecutor

    def fetch_time(item):
        nome_bot, info = item
        tid = info["id"]
        try:
            r = requests.get(f"https://api.cartola.globo.com/time/id/{tid}", headers=HEADERS, timeout=10)
            if r.status_code == 200:
                d = r.json()
                t_info = d.get("time", {})
                rk = d.get("ranking", {}).get("atual", {})
                pos_nacional_mes = rk.get("posicao")
                mes_ranking = rk.get("mes")
                return {
                    "nome_bot": nome_bot,
                    "nome_cartola": t_info.get("nome", nome_bot),
                    "cartoleiro": t_info.get("nome_cartola", ""),
                    "slug": t_info.get("slug", ""),
                    "pontos_campeonato": float(d.get("pontos_campeonato") or 0),
                    "pontos_rodada": float(d.get("pontos") or 0),
                    "patrimonio": float(d.get("patrimonio") or 0),
                    "pos_nacional_mes": pos_nacional_mes,
                    "mes_ranking": mes_ranking,
                    "rodada": d.get("rodada_atual") or 0,
                    "sucesso": True
                }
        except Exception as e:
            log.warning("[NACIONAL] Erro ao buscar time %s (id=%s): %s", nome_bot, tid, e)
        return {
            "nome_bot": nome_bot,
            "nome_cartola": nome_bot,
            "cartoleiro": "",
            "slug": "",
            "pontos_campeonato": 0.0,
            "pontos_rodada": 0.0,
            "patrimonio": 0.0,
            "pos_nacional_mes": None,
            "rodada": 0,
            "sucesso": False
        }

    with ThreadPoolExecutor(max_workers=min(10, len(TIMES))) as executor:
        return list(executor.map(fetch_time, TIMES.items()))


def _buscar_classificacao_cartola():
    slug = os.getenv("CARTOLA_LEAGUE_SLUG", "").strip()
    if not slug:
        raise RuntimeError("CARTOLA_LEAGUE_SLUG não configurada.")
    response = requests.get(
        f"https://api.cartola.globo.com/liga/{slug}",
        headers=HEADERS,
        timeout=15,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"API do Cartola respondeu HTTP {response.status_code}.")
    payload = response.json()
    entries = payload.get("times") or payload.get("times_liga") or payload.get("ranking")
    if not isinstance(entries, list):
        raise RuntimeError("A API do Cartola não retornou a classificação da liga.")
    rows = []
    for index, entry in enumerate(entries, start=1):
        team = entry.get("time") if isinstance(entry, dict) else None
        team = team if isinstance(team, dict) else entry
        if not isinstance(team, dict):
            continue
        rows.append(
            (
                int(entry.get("posicao") or entry.get("pos") or index),
                str(team.get("nome") or team.get("name") or "Time sem nome"),
                float(entry.get("pontos") or entry.get("pontuacao") or team.get("pontos") or 0),
            )
        )
    if not rows:
        raise RuntimeError("A classificação do Cartola veio vazia.")
    return sorted(rows, key=lambda row: (row[0], -row[2]))


@bot.command(name="nacional")
async def nacional(ctx, *, modo=None):
    """Mostra os 10 times do bot na Liga Nacional do Cartola."""
    modo_limpo = (modo or "").strip().casefold()

    # Padrão: Liga Nacional do Cartola FC (onde estão todos os times do fantasy)
    msg = await ctx.send("⏳ Consultando os 10 times na Liga Nacional do Cartola...")
    try:
        dados = await run(_buscar_dados_nacional_cartola)
        if not dados:
            await msg.edit(content="❌ Não foi possível carregar os times na Liga Nacional.")
            return

        if modo_limpo in {"rodada", "ultima", "última"}:
            dados.sort(key=lambda x: (x["pontos_rodada"], x["pontos_campeonato"]), reverse=True)
            subtitulo = "Ordenado por: **Pontuação da Última Rodada**"
        elif modo_limpo in {"cartoleta", "cartoletas", "patrimonio", "patrimônio"}:
            dados.sort(key=lambda x: (x["patrimonio"], x["pontos_campeonato"]), reverse=True)
            subtitulo = "Ordenado por: **Patrimônio (C$)**"
        elif modo_limpo in {"mes", "mês", "ranking"}:
            dados.sort(key=lambda x: (x["pos_nacional_mes"] if x["pos_nacional_mes"] else 99999999))
            subtitulo = "Ordenado por: **Ranking Nacional do Mês**"
        else:
            dados.sort(key=lambda x: x["pontos_campeonato"], reverse=True)
            subtitulo = "Ordenado por: **Pontos no Campeonato (Geral)**"

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        linhas = []
        for i, item in enumerate(dados, 1):
            pos_icon = medals.get(i, f"**{i}º**")
            nome = item["nome_bot"]
            pts_camp = item["pontos_campeonato"]
            pts_rod = item["pontos_rodada"]
            patr = item["patrimonio"]
            mes_n = item.get("mes_ranking") or 10
            mes_nomes = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
            nome_mes = mes_nomes.get(mes_n, f"Mês {mes_n}")
            pos_mes = f" | 📅 Mês ({nome_mes}): #{item['pos_nacional_mes']:,} nacional" if item.get("pos_nacional_mes") else ""

            linhas.append(
                f"{pos_icon} **{nome}** — **{pts_camp:.2f} pts**\n"
                f"   └ Rodada: `{pts_rod:.2f}` pts | C$ `{patr:.2f}`{pos_mes}"
            )

        embed = discord.Embed(
            title="🇧🇷 LIGA NACIONAL — CARTOLA FC",
            url="https://cartola.globo.com/#!/competicoes/classica/nacional",
            description=f"{subtitulo}\n\n" + "\n\n".join(linhas),
            color=discord.Color.green(),
        )
        embed.set_footer(
            text="Liga Nacional do Cartola • Use !nacional [rodada|mes|patrimonio]"
        )
        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as error:
        log.error("[NACIONAL] Falha ao consultar Liga Nacional: %s", error, exc_info=True)
        await msg.edit(content=f"❌ Falha ao consultar a Liga Nacional: {error}")


@bot.command(name='tabela')
async def tabela(ctx):
    msg = await ctx.send("⏳ Buscando a tabela... Pera aí!")
    try:
        mercado = await run(_mercado)
        rodada = mercado.rodada_atual if mercado else 0

        resultados = []
        for nome, dados in TIMES.items():
            t = await run(_buscar_time, dados["id"], rodada)
            if t:
                resultados.append((nome, t["pontos_rodada"], t["pontos_campeonato"]))

        if not resultados:
            await msg.edit(content="❌ Não consegui buscar os times. Tenta mais tarde.")
            return

        resultados.sort(key=lambda x: x[2] or 0, reverse=True)

        embed = discord.Embed(
            title="🏆 TABELA GERAL — PREMIER LEAGUE IOS",
            color=discord.Color.dark_blue()
        )
        embed.set_footer(text=f"Rodada {rodada - 1} • Ordenado por pontuação total")

        linhas = "```\n"
        linhas += f"{'POS':<4} {'TIME':<28} {'TOTAL':>8}  {'RODADA':>8}\n"
        linhas += "─" * 54 + "\n"
        pos_icons = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, (nome, pts_rodada, pts_camp) in enumerate(resultados, 1):
            pos_str = f"{i}º"
            pts_str = f"{pts_camp:.2f}" if pts_camp else "0.00"
            rod_str = f"{pts_rodada:.2f}" if pts_rodada else "—"
            nome_curto = nome[:26]
            linhas += f"{pos_str:<4} {nome_curto:<28} {pts_str:>8}  {rod_str:>8}\n"
        linhas += "```"

        embed.add_field(name="\u200b", value=linhas, inline=False)

        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"❌ Erro: {e}")
        log.error("[TABELA] %s", e, exc_info=True)


@bot.command(name='status')
async def status_escalacao(ctx):
    msg = await ctx.send("⏳ Verificando escalações... Pera aí!")
    try:
        mercado = await run(_mercado)
        rodada = mercado.rodada_atual if mercado else 0

        escalados = []
        nao_escalados = []
        for nome, dados in TIMES.items():
            t = await run(_buscar_time, dados["id"], rodada)
            escalou = t["escalou"] if t else False
            if escalou:
                escalados.append(f"✅ {nome}")
            else:
                nao_escalados.append(f"❌ {nome}")

        embed = discord.Embed(
            title=f"📊 STATUS DE ESCALAÇÃO — RODADA {rodada}",
            color=discord.Color.blue()
        )
        if escalados:
            embed.add_field(name="Escalados", value="\n".join(escalados), inline=True)
        if nao_escalados:
            embed.add_field(name="Não escalaram", value="\n".join(nao_escalados), inline=True)

        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"❌ Erro: {e}")
        log.error("[STATUS] %s", e, exc_info=True)


@bot.command(name='mercado')
async def info_mercado(ctx):
    msg = await ctx.send("⏳ Buscando info do mercado...")
    try:
        mercado = await run(_mercado)
        if not mercado:
            await msg.edit(content="❌ Não consegui acessar o mercado.")
            return

        agora = datetime.now(timezone(timedelta(hours=-3)))
        fechamento = mercado.fechamento
        if fechamento.tzinfo is None:
            fechamento = fechamento.replace(tzinfo=timezone(timedelta(hours=-3)))

        mercado_aberto = mercado.status.id == 1
        minutos = (fechamento - agora).total_seconds() / 60

        cor = discord.Color.green() if mercado_aberto else discord.Color.red()
        status_str = "🟢 ABERTO" if mercado_aberto else "🔴 FECHADO"

        if mercado_aberto and minutos > 0:
            horas = int(minutos // 60)
            mins = int(minutos % 60)
            tempo_str = f"{horas}h {mins}min"
        elif mercado_aberto:
            tempo_str = "Fechando agora!"
        else:
            tempo_str = "—"

        embed = discord.Embed(title="🏪 STATUS DO MERCADO", color=cor)
        embed.add_field(name="Status",     value=status_str,                                  inline=True)
        embed.add_field(name="Rodada",     value=str(mercado.rodada_atual),                   inline=True)
        embed.add_field(name="Fechamento", value=fechamento.strftime('%d/%m/%Y às %H:%M'),    inline=True)
        if mercado_aberto:
            embed.add_field(name="Tempo restante", value=tempo_str, inline=True)

        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"❌ Erro: {e}")
        log.error("[MERCADO CMD] %s", e, exc_info=True)


@bot.command(name='cartoleta', aliases=['cartoletas'])
async def cartoleta(ctx):
    msg = await ctx.send("⏳ Buscando cartoletas... Pera aí!")
    try:
        mercado = await run(_mercado)
        rodada = mercado.rodada_atual if mercado else 0

        resultados = []
        for nome, dados in TIMES.items():
            t = await run(_buscar_time, dados["id"], rodada)
            if t:
                resultados.append((nome, t["patrimonio"]))

        if not resultados:
            await msg.edit(content="❌ Não consegui buscar os dados. Tenta mais tarde.")
            return

        resultados.sort(key=lambda x: x[1], reverse=True)
        medalhas = {1: "🥇", 2: "🥈", 3: "🥉"}

        linhas = ""
        for i, (nome, patrimonio) in enumerate(resultados, 1):
            icone = medalhas.get(i, f"`{i}º`")
            linhas += f"{icone} **{nome}** — C$ {patrimonio:,.2f}\n"

        embed = discord.Embed(
            title="💰 CARTOLETAS — PREMIER LEAGUE IOS",
            description=linhas,
            color=discord.Color.blue()
        )
        await msg.delete()
        await ctx.send(embed=embed)
    except Exception as e:
        await msg.edit(content=f"❌ Erro: {e}")
        log.error("[CARTOLETA] %s", e, exc_info=True)



@bot.command(name='testerodada')
async def testerodada(ctx):
    if not await _exigir_admin(ctx):
        return
    mercado = await run(_mercado)
    rodada = mercado.rodada_atual if mercado else 0
    hora = "??"
    if mercado:
        f = mercado.fechamento
        if f.tzinfo is None:
            f = f.replace(tzinfo=timezone(timedelta(hours=-3)))
        hora = f.strftime('%d/%m às %H:%M')

    # Busca quem não escalou de verdade (sem pingar ninguém no teste)
    nao_escalaram = []
    for nome, dados in TIMES.items():
        t = await run(_buscar_time, dados["id"], rodada)
        if not (t and t["escalou"]):
            nao_escalaram.append(f"@{nome}")
    mencoes = "\n".join(nao_escalaram) if nao_escalaram else "Todos escalaram! 🎉"

    cor = discord.Color.blue()

    await ctx.send("🧪 **MODO TESTE — Simulando todos os avisos com dados reais:**")
    await asyncio.sleep(1)

    await ctx.send(embed=discord.Embed(
        title="📢 [TESTE] Faltam 24h!",
        description=f"O mercado fecha AMANHÃ às **{hora}**!! Bora pensar em escalar 😬\n\nAinda não escalaram:\n{mencoes}",
        color=cor
    ))
    await asyncio.sleep(2)

    await ctx.send(embed=discord.Embed(
        title="📢 [TESTE] Faltam 12h!",
        description=f"O mercado fecha HOJE às **{hora}**!! Bora ficar ligado! 😂\n\nAinda não escalaram:\n{mencoes}",
        color=cor
    ))
    await asyncio.sleep(2)

    await ctx.send(embed=discord.Embed(
        title="🚨 [TESTE] FALTA 1 HORA PRA FECHAR!",
        description=f"Corre pra escalar! 💀\n\nAinda não escalaram:\n{mencoes}",
        color=cor
    ))
    await asyncio.sleep(2)

    await ctx.send(embed=discord.Embed(
        title="⏳ [TESTE] ÚLTIMOS 10 MINUTOS! ÚLTIMA CHAMADA!",
        description=f"O mercado está fechando!! 🤣\n\nAinda não escalaram:\n{mencoes}",
        color=cor
    ))
    await asyncio.sleep(2)

    await ctx.send(embed=discord.Embed(
        title="🔒 [TESTE] MERCADO FECHADO! 🍀",
        description="Boa sorte na rodada! 🍀",
        color=cor
    ))
    await asyncio.sleep(2)

    # Simula cartoleiro e merda com os dois primeiros times cadastrados
    nome_mito, nome_merda = list(TIMES)[:2]
    info_mito = TIMES_INFO.get(nome_mito, {})
    embed_cartoleiro = discord.Embed(
        title="Parece que alguém não vai dar o burrote hoje!! GG!! 😎🔥",
        description=(
            f"**{nome_mito}** foi o Cartoleiro da rodada com **99.99 pontos!!!**\n"
            "Bora bora bora artilheiro! Bora Bora Bora Cartola o ano inteiro!! ⚽💥\n\n"
            "*(simulado)*"
        ),
        color=info_mito.get("cor", cor)
    )
    if info_mito.get("logo"):
        embed_cartoleiro.set_thumbnail(url=info_mito["logo"])
    await ctx.send(embed=embed_cartoleiro)
    await asyncio.sleep(1)

    info_merda = TIMES_INFO.get(nome_merda, {})
    embed_merda_teste = discord.Embed(
        title="💩 MERDA DA RODADA 💩",
        description=(
            f"Com escalação digna de museu, o **{nome_merda}** teve uma rodada para esquecer! 🤮\n\n"
            f"**{nome_merda}** terminou a rodada em ÚLTIMO com apenas **0.00 pontos!!!**\n"
            "Uma vergonha sem tamanho. Uma tragédia humana. Um crime contra o Cartola. 🤮\n\n"
            "*(simulado)*"
        ),
        color=discord.Color.from_rgb(90, 30, 0)
    )
    if info_merda.get("logo"):
        embed_merda_teste.set_thumbnail(url=info_merda["logo"])
    embed_merda_teste.set_footer(text="💩 Parabéns pelo feito histórico. Seu Cartola chora.")
    await ctx.send(embed=embed_merda_teste)
    await asyncio.sleep(1)

    await ctx.send("✅ **Teste finalizado!**")




@bot.command(name='testegol')
async def testegol(ctx):
    if not await _exigir_admin(ctx):
        return
    await ctx.send("🧪 **MODO TESTE — exemplos do novo embed de gol:**")
    await asyncio.sleep(1)

    # Usa os times cadastrados em liga_dados.py, na ordem em que aparecem.
    t = list(TIMES) * 2
    e = lambda nome: EMOTES_TIMES.get(nome, "⚽")  # noqa: E731

    # EX 1: Gol perfeito — vários times escalaram os dois
    embed1 = discord.Embed(
        title="🚨 ALERTA DE GOL PERFEITO 🚨",
        description=(
            "⚽ **GOL DO ARRASCAETA**\n"
            f"{e(t[0])} **{t[0]}** {EMOTE_CAPITAO}\n"
            f"{e(t[1])} **{t[1]}**\n"
            f"{e(t[2])} **{t[2]}** {EMOTE_LUXO}\n\n"
            "🎯 **ASSISTÊNCIA DO GARRO**\n"
            f"{e(t[0])} **{t[0]}**\n"
            f"{e(t[3])} **{t[3]}** {EMOTE_CAPITAO}\n"
        ),
        color=discord.Color.from_rgb(30, 100, 200)
    )
    await ctx.send(embed=embed1)
    await asyncio.sleep(1)

    # EX 2: Gol com times, assist sem times
    embed2 = discord.Embed(
        title="BOLINHA NA R-E-D-E!!",
        description=(
            "⚽ **GOL DO PEDRO**\n"
            f"{e(t[4])} **{t[4]}** {EMOTE_CAPITAO}\n"
            f"{e(t[5])} **{t[5]}**\n\n"
            "🎯 **ASSISTÊNCIA DO EVERTON CEBOLINHA**\n"
        ),
        color=discord.Color.from_rgb(30, 100, 200)
    )
    await ctx.send(embed=embed2)
    await asyncio.sleep(1)

    # EX 3: Ninguém escalou nenhum dos dois
    embed3 = discord.Embed(
        title="GOOOL NA RODADA!!",
        description=(
            "⚽ **GOL DO HULK**\n\n"
            "🎯 **ASSISTÊNCIA DO PAULINHO**\n"
        ),
        color=discord.Color.from_rgb(30, 100, 200)
    )
    await ctx.send(embed=embed3)
    await asyncio.sleep(1)

    # EX 4: Só gol tem times
    embed4 = discord.Embed(
        title="BOLINHA NA R-E-D-E!!",
        description=(
            "⚽ **GOL DO ALAN PATRICK**\n"
            f"{e(t[6])} **{t[6]}** {EMOTE_LUXO}\n"
            f"{e(t[7])} **{t[7]}**\n\n"
            "🎯 **ASSISTÊNCIA DO WANDERSON**\n"
        ),
        color=discord.Color.from_rgb(30, 100, 200)
    )
    await ctx.send(embed=embed4)
    await asyncio.sleep(1)

    # EX 5: Só assist tem times
    embed5 = discord.Embed(
        title="BOLINHA NA R-E-D-E!!",
        description=(
            "⚽ **GOL DO VEGETTI**\n\n"
            "🎯 **ASSISTÊNCIA DO CANO**\n"
            f"{e(t[8])} **{t[8]}** {EMOTE_CAPITAO}\n"
            f"{e(t[9])} **{t[9]}**\n"
        ),
        color=discord.Color.from_rgb(30, 100, 200)
    )
    await ctx.send(embed=embed5)


@bot.command(name='debuglineup')
async def debuglineup(ctx):
    if not await _exigir_admin(ctx):
        return
    if not lineup_por_atleta:
        await ctx.send("⚠️ Lineup ainda não carregado! O mercado precisa ter fechado ao menos uma vez.")
        return

    # Agrupa por time para facilitar leitura
    por_time = {}
    for aid, times in lineup_por_atleta.items():
        for t in times:
            por_time.setdefault(t, []).append(aid)

    embed = discord.Embed(
        title="🔍 DEBUG — LINEUP CARREGADO",
        description=f"**{len(lineup_por_atleta)} atletas** mapeados no total.",
        color=discord.Color.orange()
    )
    for nome_time, atletas in sorted(por_time.items()):
        embed.add_field(
            name=f"{nome_time} ({len(atletas)} atletas)",
            value="\n".join(atletas) or "—",
            inline=False
        )
    await ctx.send(embed=embed)


def _stats_time(nome):
    """Retorna MVP e zikado de um time."""
    mvp    = state.get("mvp_historico", {}).get(nome, 0)
    zikado = state.get("zikado", {}).get(nome, 0)
    mvp_str    = f"{mvp} vez" if mvp == 1 else f"{mvp} vezes"
    zikado_str = f"{zikado} Rodada" if zikado == 1 else f"{zikado} Rodadas"
    return mvp_str, zikado_str


def _clubes_por_status(status):
    return [c for c in CLUBES_BIOGRAFIA if c.get("status") == status]


@bot.command(name='biografia')
async def biografia(ctx):
    embed = discord.Embed(
        title="📚 BIOGRAFIAS DA PREMIER LEAGUE IOS",
        color=discord.Color.blue()
    )
    ativos = _clubes_por_status("ativo")
    outros = [c for c in CLUBES_BIOGRAFIA if c.get("status") != "ativo"]
    if ativos:
        embed.add_field(name="⚽ Times Ativos", value="\n".join(
            f"🔵 `!{c['comando']}`" for c in ativos
        ), inline=False)
    if outros:
        embed.add_field(name="⚫ Outros clubes", value=" • ".join(
            f"`!{c['comando']}`" for c in outros
        ), inline=False)
    embed.add_field(name="\u200b", value=TEXTO_BIOGRAFIA_LIGA, inline=False)
    await ctx.send(embed=embed)


def _embed_clube(clube):
    """Página de um clube. Clubes ativos mostram aproveitamento, títulos, MVP e zikado atuais."""
    embed = discord.Embed(
        title=f"📖 {clube['titulo']}",
        color=discord.Color.from_rgb(*clube.get("cor", (30, 100, 200))),
    )
    if clube.get("logo"):
        embed.set_thumbnail(url=clube["logo"])
    nome = clube.get("time")
    historia = clube.get("historia", "")
    if nome:
        d = APROVEITAMENTO.get(nome, {})
        v, j = d.get("v", 0), d.get("j", 0)
        historia = historia.format(v=v, j=j, apr=_calc_apr(v, j))
    embed.add_field(name="História", value=historia or "—", inline=False)
    if nome:
        embed.add_field(name="🏆 Títulos", value=_texto_titulos(nome), inline=False)
    for campo, valor in clube.get("extras", []):
        embed.add_field(name=campo, value=valor, inline=False)
    if nome:
        mvp_str, zikado_str = _stats_time(nome)
        embed.add_field(name="👑 MVPs de Rodada", value=mvp_str, inline=True)
        embed.add_field(name="😔 Zikado atual",   value=zikado_str, inline=True)
    else:
        embed.add_field(name="📌 Status", value=clube.get("status_texto", "⚫ Desativado"), inline=True)
    return embed


def _registrar_comandos_clubes():
    """Cria um comando `!<clube>` para cada entrada de CLUBES_BIOGRAFIA."""
    def fabricar(clube):
        async def comando(ctx):
            await ctx.send(embed=_embed_clube(clube))
        return comando

    for clube in CLUBES_BIOGRAFIA:
        nome = clube["comando"]
        if bot.get_command(nome):
            log.warning("[CLUBES] Comando !%s já existe; página do clube ignorada.", nome)
            continue
        bot.add_command(commands.Command(fabricar(clube), name=nome, aliases=list(clube.get("aliases", []))))



def _calcular_tabela_pc():
    return _calcular_tabela_resultados(RESULTADOS_PC)


def _calcular_tabela_pc2():
    return _calcular_tabela_resultados(_pc2_resultados_ativos())


def _embed_tabela_historica(chave, rodape=None):
    """Tabelas finais de temporadas antigas guardadas em liga_dados.TABELAS_HISTORICAS."""
    dados = TABELAS_HISTORICAS.get(chave)
    if not dados:
        return None
    tabela = "```\n"
    tabela += f"{'POS':<4} {'TIME':<24} {'PTS':>4} {'V':>3} {'E':>3} {'D':>3} {'PC':>8}\n"
    tabela += "─" * 56 + "\n"
    for i, (nome, pts, v, e, d, pc) in enumerate(dados["linhas"], 1):
        tabela += f"{f'{i}º':<4} {nome[:23]:<24} {pts:>4} {v:>3} {e:>3} {d:>3} {float(pc):>8.2f}\n"
    tabela += "```"
    embed = discord.Embed(title=dados["titulo"], color=discord.Color.orange())
    embed.add_field(name="\u200b", value=tabela, inline=False)
    if dados["linhas"]:
        embed.set_footer(text=rodape or f"🏆 Campeão: {dados['linhas'][0][0]}")
    return embed


PC_2026_FINALIZADO = True  # Pontos Corridos/1 2026 encerrado; aparece em !campeonatos

@bot.command(name='pc', aliases=['pontoscorridos'])
async def pc(ctx, ano: str = None):
    """
    !PC            -> abre automaticamente o PC ativo da temporada 2026.
    !PC 2024       -> PC/1 2024.
    !PC 2025       -> PC/1 2025.
    !PC 2026       -> PC/1 2026.
    """
    if ano is None:
        mercado = await run(_mercado)
        cartola_round = int(mercado.rodada_atual) if mercado else int(state.get("rodada", 0) or 0)

        if 1 <= cartola_round <= 18:
            return await pc1(ctx, 2026)

        if 19 <= cartola_round <= 36:
            return await pc2(ctx, 2026)

        # Fora do intervalo normal da temporada, abre a competição mais recente.
        return await pc2(ctx, 2026)

    ano_txt = str(ano).strip().lower()
    mapa_anos = {
        "24": 2024, "2024": 2024,
        "25": 2025, "2025": 2025,
        "26": 2026, "2026": 2026,
    }

    if ano_txt not in mapa_anos:
        await ctx.send("❌ Use `!PC`, `!PC 2024`, `!PC 2025` ou `!PC 2026`.")
        return

    ano_num = mapa_anos[ano_txt]

    if ano_num in (2024, 2025):
        embed = _embed_tabela_historica(f"pc1-{ano_num}")
        if embed is None:
            await ctx.send(f"❌ Não tenho a tabela do PC {ano_num}.")
            return
        await ctx.send(embed=embed)
        return

    return await pc1(ctx, 2026)


@bot.command(name='pc1')
async def pc1(ctx, ano: int = 2026):
    if ano != 2026:
        return await pc(ctx, str(ano))

    tabela = _calcular_tabela_pc()
    linhas = "```\n"
    linhas += f"{'POS':<4} {'TIME':<24} {'PTS':>4} {'V':>3} {'E':>3} {'D':>3} {'PC':>8}\n"
    linhas += "─" * 56 + "\n"
    for i, s in enumerate(tabela, 1):
        linhas += f"{i:<4} {s['nome'][:23]:<24} {s['pts']:>4} {s['v']:>3} {s['e']:>3} {s['d']:>3} {s['pc']:>8.2f}\n"
    linhas += "```"

    embed = discord.Embed(title="📊 PC/1 2026 — TABELA FINAL", color=discord.Color.orange())
    embed.add_field(name="Rodadas Cartola 1–18", value=linhas, inline=False)
    if tabela:
        embed.set_footer(text=f"🏆 Campeão: {tabela[0]['nome']} • competição encerrada")
    await ctx.send(embed=embed)


@bot.command(name='pc2', aliases=['pontoscorridos2'])
async def pc2(ctx, ano: int = 2026):
    """
    !PC2       -> PC/2 2026.
    !PC2 2025  -> PC/2 2025.
    !PC2 2026  -> PC/2 2026.
    """
    try:
        ano = int(ano)
    except (TypeError, ValueError):
        await ctx.send("❌ Use `!PC2 2025` ou `!PC2 2026`.")
        return

    if ano == 2025:
        embed = _embed_tabela_historica("pc2-2025")
        if embed is None:
            await ctx.send("❌ Não tenho a tabela do PC/2 2025.")
            return
        await ctx.send(embed=embed)
        return

    if ano != 2026:
        await ctx.send("❌ PC/2 disponível em `!PC2 2025` e `!PC2 2026`.")
        return

    tabela = _calcular_tabela_pc2()
    resultados = _pc2_resultados_ativos()
    rodadas_jogadas = len(resultados)

    mercado = await run(_mercado)
    cartola_round = int(mercado.rodada_atual) if mercado else int(state.get("rodada", 0) or 0)
    rodada_atual_pc2 = _cartola_para_pc2(cartola_round)

    linhas = "```\n"
    linhas += f"{'POS':<4} {'TIME':<24} {'PTS':>4} {'V':>3} {'E':>3} {'D':>3} {'PC':>8}\n"
    linhas += "─" * 56 + "\n"
    for i, s in enumerate(tabela, 1):
        linhas += f"{i:<4} {s['nome'][:23]:<24} {s['pts']:>4} {s['v']:>3} {s['e']:>3} {s['d']:>3} {s['pc']:>8.2f}\n"
    linhas += "```"

    embed = discord.Embed(title="📊 PC/2 2026 — TABELA ATUAL", color=discord.Color.orange())
    embed.add_field(name=f"Após {rodadas_jogadas} rodada(s)", value=linhas, inline=False)

    if 1 <= rodada_atual_pc2 <= 18:
        embed.set_footer(
            text=f"Cartola R{cartola_round} = PC/2 R{rodada_atual_pc2} • calendário completo até a Cartola R36"
        )
    else:
        embed.set_footer(text="PC/2 2026 • rodadas Cartola 19–36")

    await ctx.send(embed=embed)


@bot.command(name='confrontos')
async def confrontos(ctx, *, args=None):
    """
    O número sempre representa a rodada do Cartola.
    R1-R18  -> PC/1 R1-R18
    R19-R36 -> PC/2 R1-R18
    """
    resultados_pc2 = _pc2_resultados_ativos()

    if args and not args.strip().isdigit():
        cid = _resolver_time(args.strip())
        if not cid:
            await ctx.send(f"❌ Time `{args}` não encontrado.")
            return

        proximos = []
        for rod_pc2 in sorted(PC2_CONFRONTOS):
            if rod_pc2 in resultados_pc2:
                continue
            for a, b in PC2_CONFRONTOS[rod_pc2]:
                if cid in (a, b):
                    proximos.append((rod_pc2, b if a == cid else a))

        if not proximos:
            await ctx.send(
                f"✅ **{NOMES_PC[cid]}** não possui mais confrontos futuros no PC/2 2026."
            )
            return

        linhas = "\n".join(
            f"**Cartola R{_pc2_para_cartola(r)} • PC/2 R{r}** — "
            f"{EMOTES_PC.get(adv, '⚽')} **{NOMES_PC[adv]}**"
            for r, adv in proximos
        )
        embed = discord.Embed(
            title=f"📅 PRÓXIMOS — {NOMES_PC[cid]}",
            description=linhas,
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)
        return

    if args:
        rodada_cartola = int(args.strip())
    else:
        mercado = await run(_mercado)
        rodada_cartola = (
            int(mercado.rodada_atual)
            if mercado
            else int(state.get("rodada", 0) or 0)
        )

    if not 1 <= rodada_cartola <= 36:
        await ctx.send(
            "❌ Use a rodada do Cartola de `1` a `36`. "
            "Ex.: `!confrontos 1` = PC/1 R1; "
            "`!confrontos 19` = PC/2 R1."
        )
        return

    if rodada_cartola <= 18:
        nome_comp = "PC/1 2026"
        rodada_comp = rodada_cartola
        confrontos_rodada = CONFRONTOS_PC.get(rodada_cartola, [])
        resultados_rodada = RESULTADOS_PC.get(rodada_cartola, [])
    else:
        nome_comp = "PC/2 2026"
        rodada_comp = rodada_cartola - 18
        confrontos_rodada = PC2_CONFRONTOS.get(rodada_comp, [])
        resultados_rodada = resultados_pc2.get(rodada_comp, [])

    if not confrontos_rodada:
        await ctx.send(
            f"❌ Não encontrei confrontos para a rodada Cartola **{rodada_cartola}**."
        )
        return

    res_map = {
        (int(a), int(b)): (float(pa), float(pb))
        for a, pa, b, pb in resultados_rodada
    }

    linhas = []
    for a, b in confrontos_rodada:
        res = res_map.get((int(a), int(b)))

        if not res:
            linhas.append(
                f"{EMOTES_PC.get(a, '⚽')} **{NOMES_PC[a]}** "
                f"x **{NOMES_PC[b]}** {EMOTES_PC.get(b, '⚽')}"
            )
            continue

        pa, pb = res
        if pa > pb:
            sa, sb = f"**{pa:.2f}**", f"{pb:.2f}"
        elif pb > pa:
            sa, sb = f"{pa:.2f}", f"**{pb:.2f}**"
        else:
            sa = sb = f"**{pa:.2f}**"

        linhas.append(
            f"{EMOTES_PC.get(a, '⚽')} **{NOMES_PC[a]}** "
            f"{sa} x {sb} "
            f"**{NOMES_PC[b]}** {EMOTES_PC.get(b, '⚽')}"
        )

    embed = discord.Embed(
        title=f"⚔️ {nome_comp} — RODADA {rodada_comp}",
        description="\n".join(linhas),
        color=discord.Color.orange()
    )
    embed.set_footer(text=f"Rodada Cartola {rodada_cartola}")
    await ctx.send(embed=embed)


def _resolver_time(texto):
    import unicodedata
    def norm(s):
        s = unicodedata.normalize('NFKD', s.lower().strip())
        return ''.join(c for c in s if not unicodedata.combining(c)).replace(' ','')
    aliases = dict(ALIASES_PC)
    aliases.update({norm(NOMES_PC[cid]):cid for cid in NOMES_PC if cid != ID_CLUBE_EXTINTO})
    aliases.update({norm(nome):dados['id'] for nome,dados in TIMES.items()})
    return aliases.get(norm(texto))


# Compatibilidade com funções antigas que ainda chamem o resolver antigo.
def _resolver_time_h2h(texto):
    return _resolver_time(texto)


@bot.command(name='forma')
async def forma(ctx):
    resultados = _pc2_resultados_ativos()
    historico = {cid:[] for cid in {d['id'] for d in TIMES.values()}}
    for rod in sorted(resultados):
        for a,pa,b,pb in resultados[rod]:
            if pa > pb: ra,rb='V','D'
            elif pb > pa: ra,rb='D','V'
            else: ra=rb='E'
            historico[a].append(ra); historico[b].append(rb)
    emoji={'V':'🟢','E':'🟡','D':'🔴'}; pontos={'V':3,'E':1,'D':0}
    rows=[]
    for cid,h in historico.items():
        ult=h[-5:]; rows.append((cid,''.join(emoji[x] for x in ult) or '—',sum(pontos[x] for x in ult)))
    rows.sort(key=lambda x:x[2], reverse=True)
    desc='\n'.join(f"{EMOTES_PC.get(cid,'⚽')} **{NOMES_PC[cid]}** — {form}" for cid,form,_ in rows)
    embed=discord.Embed(title="📈 FORMA RECENTE — PC/2 2026", description=desc, color=discord.Color.green())
    await ctx.send(embed=embed)


@bot.command(name='copa')
async def copa(ctx, ano: int = 2026):
    if ano == 2026:
        embed = discord.Embed(title="🏆 COPA IOS 2026 — ENCERRADA", color=discord.Color.green())
        for fase in COPA_IOS_2026["rounds"]:
            linhas=[]
            for a,pa,b,pb in fase["matches"]:
                wa = pa > pb; wb = pb > pa
                linhas.append(f"{EMOTES_PC.get(a,'⚽')} **{NOMES_PC[a]}** {'**' if wa else ''}{pa:.2f}{'**' if wa else ''} x {'**' if wb else ''}{pb:.2f}{'**' if wb else ''} **{NOMES_PC[b]}** {EMOTES_PC.get(b,'⚽')}")
            embed.add_field(name=f"{fase['phase']} • Cartola {fase['cartola_round']}", value='\n'.join(linhas), inline=False)
        embed.set_footer(
            text=f"🏆 Campeão: {NOMES_PC.get(COPA_IOS_2026['champion'], '?')} • "
                 f"🥉 3º lugar: {NOMES_PC.get(COPA_IOS_2026['third_place'], '?')}"
        )
        await ctx.send(embed=embed); return

    edicao=f"Copa IOS {ano}"
    jogos=[j for j in COPA_IOS_HISTORICO if j["edicao"]==edicao and j["perdedor"] is not None]
    if not jogos:
        await ctx.send(f"❌ Não tenho dados da Copa IOS {ano}."); return
    embed=discord.Embed(title=f"🏆 COPA IOS {ano}", color=discord.Color.green())
    for fase in ["Quartas de Final","Semifinal","3º Lugar","Final"]:
        jf=[j for j in jogos if j['fase']==fase]
        if jf:
            embed.add_field(name=fase,value='\n'.join(f"{EMOTES_PC.get(j['vencedor'],'⚽')} **{NOMES_PC.get(j['vencedor'],'?')}** ✅ x ❌ **{NOMES_PC.get(j['perdedor'],'?')}** {EMOTES_PC.get(j['perdedor'],'⚽')}" for j in jf),inline=False)
    final=next((j for j in jogos if j['fase']=='Final'),None)
    if final: embed.set_footer(text=f"🏆 Campeão: {NOMES_PC.get(final['vencedor'],'?')}")
    await ctx.send(embed=embed)


# Dados de aproveitamento (editáveis via !setaproveitamento)
# Cores reais dos times da Série A 2026 para embeds de gol
# Mapa clube_id (Cartola) → cor do time
CORES_CLUBE_ID = {
    262: discord.Color.from_rgb(0,   107,  60),   # Palmeiras
    265: discord.Color.from_rgb(230,  30,  30),   # Flamengo
    266: discord.Color.from_rgb(130,  20,  20),   # Fluminense
    263: discord.Color.from_rgb(0,     0,   0),   # Botafogo
    267: discord.Color.from_rgb(0,     0,   0),   # Vasco
    276: discord.Color.from_rgb(220,  30,  30),   # São Paulo
    264: discord.Color.from_rgb(0,     0,   0),   # Corinthians
    275: discord.Color.from_rgb(0,     0,   0),   # Santos
    285: discord.Color.from_rgb(200,  20,  20),   # Internacional
    284: discord.Color.from_rgb(0,    70, 160),   # Grêmio
    293: discord.Color.from_rgb(200,  20,  20),   # Athletico-PR
    282: discord.Color.from_rgb(0,     0,   0),   # Atlético-MG
    273: discord.Color.from_rgb(0,    50, 160),   # Cruzeiro
    265: discord.Color.from_rgb(0,    70, 170),   # Bahia (id real 327)
    327: discord.Color.from_rgb(0,    70, 170),   # Bahia
    356: discord.Color.from_rgb(0,    70, 170),   # Fortaleza
    354: discord.Color.from_rgb(0,     0,   0),   # Ceará
    290: discord.Color.from_rgb(0,   100,  50),   # Goiás
    373: discord.Color.from_rgb(0,   130,  50),   # Coritiba
    315: discord.Color.from_rgb(220, 180,   0),   # Mirassol
}

CORES_TIMES_BRASILEIRAO = {
    # Times da Série A 2026
    "Palmeiras":              discord.Color.from_rgb(0,   107,  60),   # Verde
    "Flamengo":               discord.Color.from_rgb(230,  30,  30),   # Vermelho
    "Fluminense":             discord.Color.from_rgb(130,  20,  20),   # Grená
    "Botafogo":               discord.Color.from_rgb(0,     0,   0),   # Preto
    "Vasco":                  discord.Color.from_rgb(0,     0,   0),   # Preto
    "São Paulo":              discord.Color.from_rgb(220,  30,  30),   # Vermelho
    "Corinthians":            discord.Color.from_rgb(0,     0,   0),   # Preto
    "Santos":                 discord.Color.from_rgb(0,     0,   0),   # Preto
    "Internacional":          discord.Color.from_rgb(200,  20,  20),   # Vermelho
    "Grêmio":                 discord.Color.from_rgb(0,    70, 160),   # Azul
    "Athletico":              discord.Color.from_rgb(200,  20,  20),   # Vermelho
    "Atletico":               discord.Color.from_rgb(200,  20,  20),   # Vermelho (variação)
    "Athletico-PR":           discord.Color.from_rgb(200,  20,  20),
    "Atlético-MG":            discord.Color.from_rgb(0,     0,   0),   # Preto
    "Atlético Mineiro":       discord.Color.from_rgb(0,     0,   0),   # Preto
    "Cruzeiro":               discord.Color.from_rgb(0,    50, 160),   # Azul royal
    "Bahia":                  discord.Color.from_rgb(0,    70, 170),   # Azul
    "Bragantino":             discord.Color.from_rgb(200,  20,  20),   # Vermelho
    "Red Bull Bragantino":    discord.Color.from_rgb(200,  20,  20),
    "Mirassol":               discord.Color.from_rgb(220, 180,   0),   # Amarelo
    "Coritiba":               discord.Color.from_rgb(0,   130,  50),   # Verde
    "Chapecoense":            discord.Color.from_rgb(0,   120,  50),   # Verde
    "Remo":                   discord.Color.from_rgb(0,    50, 160),   # Azul
    "Fortaleza":              discord.Color.from_rgb(0,    70, 170),   # Azul (rebaixado mas pode ter jogadores escalados)
    "Ceará":                  discord.Color.from_rgb(0,     0,   0),   # Preto
}

def _cor_jogador(nome_atleta, pontuados):
    """Tenta descobrir o clube do atleta pelo nome e retorna a cor correspondente."""
    for aid, info in pontuados.items():
        if info.get("nome") == nome_atleta and info.get("clube"):
            clube = info["clube"]
            for chave, cor in CORES_TIMES_BRASILEIRAO.items():
                if chave.lower() in clube.lower() or clube.lower() in chave.lower():
                    return cor
    return discord.Color.from_rgb(70, 130, 180)  # Azul padrão se não encontrar

# Logo e cor de cada time da liga para embeds (liga_dados.TIMES_INFO_BASE)
TIMES_INFO = {
    nome: {"logo": info.get("logo"), "cor": discord.Color.from_rgb(*info["cor"])}
    for nome, info in TIMES_INFO_BASE.items()
}

APROVEITAMENTO = {
    nome: {"v": int(d["v"]), "j": int(d["j"])} for nome, d in OFFICIAL_STATS_BASELINE.items()
}

APROVEITAMENTO_PADRAO_ATUAL = {
    nome: dados.copy() for nome, dados in APROVEITAMENTO.items()
}

def carregar_aproveitamento_persistente():
    """Carrega aproveitamento do state.json e evita resetar para bases antigas."""
    apr_state = state.setdefault("aproveitamento", {})

    if not apr_state:
        apr_state.update({nome: dados.copy() for nome, dados in APROVEITAMENTO_PADRAO_ATUAL.items()})

    for nome, dados in APROVEITAMENTO_PADRAO_ATUAL.items():
        apr_state.setdefault(nome, dados.copy())

    for nome in list(apr_state.keys()):
        if nome not in APROVEITAMENTO_PADRAO_ATUAL:
            del apr_state[nome]

    APROVEITAMENTO.clear()
    for nome, dados in apr_state.items():
        v = int(dados.get("v", 0))
        j = int(dados.get("j", 0))
        d = int(dados.get("d", max(0, j - v)))
        e = int(dados.get("e", max(0, j - v - d)))
        APROVEITAMENTO[nome] = {
            "v": v,
            "d": d,
            "e": e,
            "j": j,
        }

    state["aproveitamento"] = APROVEITAMENTO
    salvar_state()

carregar_aproveitamento_persistente()


def _calc_apr(v, j):
    return round((v / j) * 100, 1) if j > 0 else 0.0

@bot.command(name='aproveitamento')
async def aproveitamento(ctx, modo: str = "aproveitamento"):
    modo = modo.lower()

    dados = [
        (nome, d["v"], d["j"], _calc_apr(d["v"], d["j"]))
        for nome, d in APROVEITAMENTO.items()
    ]

    if modo in ("jogos", "j"):
        dados.sort(key=lambda x: x[2], reverse=True)
        titulo = "⚽ APROVEITAMENTO — ORDENADO POR JOGOS"
        cor = discord.Color.blue()
        def linha(v, j, apr): return f"{v}V — **{j}J** — {apr}%"
    elif modo in ("vitorias", "vitórias", "v"):
        dados.sort(key=lambda x: x[1], reverse=True)
        titulo = "🏆 APROVEITAMENTO — ORDENADO POR VITÓRIAS"
        cor = discord.Color.green()
        def linha(v, j, apr): return f"**{v}V** — {j}J — {apr}%"
    else:
        dados.sort(key=lambda x: x[3], reverse=True)
        titulo = "📊 APROVEITAMENTO — ORDENADO POR %"
        cor = discord.Color.gold()
        def linha(v, j, apr): return f"{v}V — {j}J — **{apr}%**"

    medalhas = {1: "🥇", 2: "🥈", 3: "🥉"}
    linhas = ""
    for i, (nome, v, j, apr) in enumerate(dados, 1):
        icone = medalhas.get(i, f"`{i}º`")
        linhas += f"{icone} **{nome}**\n┗ {linha(v, j, apr)}\n\n"

    embed = discord.Embed(title=titulo, description=linhas, color=cor)
    embed.set_footer(text="Use !aproveitamento | !aproveitamento jogos | !aproveitamento vitorias")
    await ctx.send(embed=embed)


@bot.command(name='setaproveitamento')
async def setaproveitamento(ctx, *, args: str):
    if not await _exigir_admin(ctx):
        return
    """Atualiza manualmente: !setaproveitamento NomeTime V J"""
    partes = args.rsplit(" ", 2)
    if len(partes) != 3:
        await ctx.send("❌ Uso correto: `!setaproveitamento Nome do Time VITORIAS JOGOS`\nEx: `!setaproveitamento Nome do Time 55 76`")
        return
    nome, v_str, j_str = partes
    nome = nome.strip()
    if nome not in APROVEITAMENTO:
        await ctx.send(f"❌ Time **{nome}** não encontrado. Verifique o nome exato.")
        return
    try:
        v, j = int(v_str), int(j_str)
    except ValueError:
        await ctx.send("❌ Vitórias e jogos precisam ser números inteiros.")
        return
    d = max(0, j - v)
    APROVEITAMENTO[nome] = {"v": v, "d": d, "e": 0, "j": j}
    state.setdefault("aproveitamento", {})[nome] = {"v": v, "d": d, "e": 0, "j": j}
    salvar_state()
    apr = _calc_apr(v, j)
    embed = discord.Embed(
        title="✅ Aproveitamento atualizado!",
        description=f"**{nome}**\n┗ {v}V — {j}J — **{apr}%**",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)


@bot.command(name='capitao', aliases=['c'])
async def capitao(ctx):
    if not capitao_por_time:
        await ctx.send("⚠️ Capitães ainda não carregados! O mercado precisa ter fechado ao menos uma vez.")
        return

    # Usa nomes salvos no lineup
    nomes_atletas = state.get("nomes_atletas", {})

    # Complementa com pontuados
    pontuados = await run(_buscar_pontuados) or {}
    for aid, info in pontuados.items():
        if str(aid) not in nomes_atletas:
            nomes_atletas[str(aid)] = info["nome"]

    # Para os que ainda faltam, busca direto no time via API
    caps_faltando = [cap_id for cap_id in capitao_por_time.values() if nomes_atletas.get(str(cap_id)) is None]
    if caps_faltando:
        mercado_info = await run(_mercado)
        rodada_atual = mercado_info.rodada_atual if mercado_info else state.get("rodada", 1)
        for nome_time, dados in TIMES.items():
            resultado = await run(_buscar_atletas_time, dados["id"])
            for aid, apelido in resultado["atletas"].items():
                nomes_atletas[str(aid)] = apelido
        # Salva para próximas vezes
        state["nomes_atletas"] = nomes_atletas
        salvar_state()

    embed = discord.Embed(title="🅲 CAPITÃES DA RODADA", color=discord.Color.blue())
    linhas = ""
    for nome_time, cap_id in sorted(capitao_por_time.items()):
        nome_cap = nomes_atletas.get(str(cap_id), "Não encontrado")
        linhas += f"**{nome_time}**\n┗ 🅲 {nome_cap}\n\n"
    embed.description = linhas
    await ctx.send(embed=embed)


@bot.command(name='reservadeluxo', aliases=['rl'])
async def reservadeluxo(ctx):
    if not reserva_luxo_por_time:
        await ctx.send("⚠️ Reservas de luxo ainda não carregadas! O mercado precisa ter fechado ao menos uma vez.")
        return

    nomes_atletas = state.get("nomes_atletas", {})

    # Complementa com pontuados
    pontuados = await run(_buscar_pontuados) or {}
    for aid, info in pontuados.items():
        if str(aid) not in nomes_atletas:
            nomes_atletas[str(aid)] = info["nome"]

    # IDs que ainda faltam — busca no mercado
    ids_faltando = [lid for lid in reserva_luxo_por_time.values() if nomes_atletas.get(str(lid)) is None]
    if ids_faltando:
        for atl in await run(buscar_atletas_mercado):
            aid = str(atl.get("atleta_id", ""))
            if aid and aid not in nomes_atletas:
                nomes_atletas[aid] = atl.get("apelido", "Desconhecido")

    embed = discord.Embed(title="⭐ RESERVAS DE LUXO DA RODADA", color=discord.Color.blue())
    linhas = ""
    for nome_time in sorted(TIMES.keys()):
        luxo_id = reserva_luxo_por_time.get(nome_time)
        if luxo_id:
            nome_luxo = nomes_atletas.get(str(luxo_id), "Não encontrado")
            linhas += f"**{nome_time}**\n┗ {EMOTE_LUXO} {nome_luxo}\n\n"
        else:
            linhas += f"**{nome_time}**\n┗ ❌ Não definido\n\n"
    embed.description = linhas
    await ctx.send(embed=embed)


@bot.command(name='cartoleiro', aliases=['mvp'])
async def cartoleiro(ctx):
    mvp_hist = state.get("mvp_historico", {})
    if not mvp_hist:
        embed = discord.Embed(title="👑 CARTOLEIRO DA RODADA — 2025 em diante", description="Nenhuma rodada finalizada ainda.", color=discord.Color.blue())
        await ctx.send(embed=embed)
        return

    ranking = sorted(mvp_hist.items(), key=lambda x: x[1], reverse=True)
    linhas = "\n".join(f"⭐ **{nome}** : {total}" for nome, total in ranking)

    embed = discord.Embed(title="👑 CARTOLEIRO DA RODADA — 2025 em diante", description=linhas, color=discord.Color.blue())
    await ctx.send(embed=embed)





class RecordesView(discord.ui.View):
    def __init__(self, entries, author_id, page_size=10):
        super().__init__(timeout=180)
        self.entries = entries
        self.author_id = int(author_id)
        self.page_size = int(page_size)
        self.page = 0
        self.message = None
        self._atualizar_botoes()

    @property
    def total_pages(self):
        return max(1, math.ceil(len(self.entries) / self.page_size))

    def _atualizar_botoes(self):
        self.anterior.disabled = self.page <= 0
        self.proxima.disabled = self.page >= self.total_pages - 1

    def build_embed(self):
        inicio = self.page * self.page_size
        fim = min(inicio + self.page_size, len(self.entries))
        linhas = []

        for idx in range(inicio, fim):
            item = self.entries[idx]
            cid = int(item["team_id"])
            fonte = str(item.get("competition", "Cartola"))
            origem = (
                f"Cartola R{item['round']}/{item['year']}"
                if fonte.lower() == "cartola"
                else f"{fonte} R{item['round']}/{item['year']}"
            )
            linhas.append(
                f"**#{idx + 1}** {EMOTES_PC.get(cid, '⚽')} "
                f"**{NOMES_PC.get(cid, item['nome'])}** — "
                f"**{item['points']:.2f} pts** • {origem}"
            )

        embed = discord.Embed(
            title="🏆 RANKING HISTÓRICO DE PONTUAÇÕES",
            description="\n".join(linhas) if linhas else "Nenhuma pontuação final registrada.",
            color=discord.Color.gold()
        )
        embed.set_footer(
            text=f"Página {self.page + 1}/{self.total_pages} • "
                 f"{len(self.entries)} atuações globais • da maior até a menor"
        )
        return embed

    async def interaction_check(self, interaction):
        if int(interaction.user.id) != self.author_id:
            await interaction.response.send_message(
                "❌ Use `!recorde` para abrir o seu próprio ranking.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def anterior(self, interaction, button):
        if self.page > 0:
            self.page -= 1
        self._atualizar_botoes()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self
        )

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def proxima(self, interaction, button):
        if self.page < self.total_pages - 1:
            self.page += 1
        self._atualizar_botoes()
        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


@bot.command(name='recorde')
async def recorde(ctx):
    ranking = _historico_pontuacoes_ordenado()
    if not ranking:
        await ctx.send("⚠️ Ainda não tenho pontuações finais salvas.")
        return

    view = RecordesView(ranking, ctx.author.id, page_size=10)
    msg = await ctx.send(embed=view.build_embed(), view=view)
    view.message = msg


@bot.command(name='zikado')
async def zikado(ctx):
    zikado_hist = state.get("zikado", {})
    if not zikado_hist:
        await ctx.send("⚠️ Nenhum dado de zikado ainda.")
        return

    ranking = sorted(zikado_hist.items(), key=lambda x: x[1], reverse=True)
    linhas = ""
    for nome, rodadas in ranking:
        vezes = "Rodada" if rodadas == 1 else "Rodadas"
        linhas += f"😔 **{nome}** : {rodadas} {vezes}\n"

    embed = discord.Embed(
        title="😔 ZIKADO — JEJUM DE CARTOLEIRO DA RODADA",
        description=linhas,
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)


@bot.command(name='merda', aliases=['piortime', 'lanterna'])
async def merda(ctx):
    merda_hist = state.get("merda_historico", {})
    if not merda_hist:
        embed = discord.Embed(
            title="💩 MERDA DA RODADA — RANKING HISTÓRICO",
            description="Nenhuma rodada finalizada ainda. Mas já tá todo mundo se preparando pra ser o próximo. 💩",
            color=discord.Color.from_rgb(90, 30, 0)
        )
        await ctx.send(embed=embed)
        return

    ranking = sorted(merda_hist.items(), key=lambda x: x[1], reverse=True)
    linhas = ""
    medalhas_merda = {1: "💩", 2: "🤮", 3: "🚽"}
    for i, (nome, total) in enumerate(ranking, 1):
        icone = medalhas_merda.get(i, f"`{i}º`")
        vezes = "vez" if total == 1 else "vezes"
        linhas += f"{icone} **{nome}** — {total} {vezes}\n"

    embed = discord.Embed(
        title="💩 MERDA DA RODADA — HALL DA VERGONHA",
        description=linhas,
        color=discord.Color.from_rgb(90, 30, 0)
    )
    embed.set_footer(text="Quem lidera esse ranking é o orgulho invertido da liga 🤣")
    await ctx.send(embed=embed)





@bot.command(name='temporada')
async def temporada(ctx):
    ano = TEMPORADA_ATUAL
    ultima_finalizada = int(state.get("last_processed_round", 0) or 0)

    ranking_pl = _ranking_pl_por_historico(ano)
    if ranking_pl:
        lider_pl = ranking_pl[0]
        if ultima_finalizada >= 36:
            pl_txt = (
                f"✅ **Encerrado**\n"
                f"🏆 Campeão: **{lider_pl['nome']}** — {lider_pl['pontos']:.2f} pts"
            )
        else:
            pl_txt = (
                f"🟢 **Em andamento**\n"
                f"👑 Líder: **{lider_pl['nome']}** — {lider_pl['pontos']:.2f} pts "
                f"({lider_pl['rodadas']} rodadas fechadas)"
            )
    else:
        pl_txt = "⏳ Aguardando dados."

    tabela_pc1 = _calcular_tabela_pc()
    pc1_txt = (
        f"✅ **Encerrado**\n🏆 Campeão: **{tabela_pc1[0]['nome']}**"
        if tabela_pc1 else "⏳ Aguardando resultados."
    )

    resultados_pc2 = _pc2_resultados_ativos()
    tabela_pc2 = _calcular_tabela_resultados(resultados_pc2)
    if tabela_pc2:
        lider_pc2 = tabela_pc2[0]
        nome_pc2 = NOMES_PC[lider_pc2["id"]]
        if len(resultados_pc2) >= 18:
            pc2_txt = f"✅ **Encerrado**\n🏆 Campeão: **{nome_pc2}** — {lider_pc2['pts']} pts"
        else:
            pc2_txt = (
                f"🟢 **Em andamento**\n"
                f"👑 Líder: **{nome_pc2}** — {lider_pc2['pts']} pts "
                f"• {len(resultados_pc2)}/18 rodadas"
            )
    else:
        pc2_txt = "⏳ Aguardando resultados."

    try:
        mercado = await run(_mercado)
        rodada_cartola = int(mercado.rodada_atual) if mercado else int(state.get("rodada", 0) or 0)
    except Exception:
        rodada_cartola = int(state.get("rodada", 0) or 0)

    ranking_ano = [
        x for x in _historico_pontuacoes_ordenado()
        if int(x.get("year", 0)) == ano
    ]
    melhor = ranking_ano[0] if ranking_ano else None

    embed = discord.Embed(
        title=f"🏟️ TEMPORADA {ano} — PREMIER LEAGUE IOS",
        description=(
            f"📅 **Cartola:** R{rodada_cartola or '?'}\n"
            f"✅ **Última rodada processada:** R{ultima_finalizada or '?'}"
        ),
        color=discord.Color.from_rgb(212, 175, 55),
    )
    embed.add_field(name="👑 PREMIER LEAGUE IOS", value=pl_txt, inline=False)
    embed.add_field(name="📊 PONTOS CORRIDOS/1", value=pc1_txt, inline=True)
    embed.add_field(name="📊 PONTOS CORRIDOS/2", value=pc2_txt, inline=True)
    embed.add_field(
        name="🏆 COPA IOS",
        value=f"✅ **Encerrada**\n🏆 Campeão: **{NOMES_PC.get(COPA_IOS_2026['champion'], '?')}**",
        inline=True,
    )
    embed.add_field(
        name="🌍 IOS WORLD CUP",
        value=f"✅ **Encerrado**\n🌍 🏆 Campeão: **{NOMES_PC.get(IOS_WORLD_CUP_2026[0][0], '?')}**",
        inline=True,
    )

    if melhor:
        fonte = str(melhor.get("competition", "Cartola"))
        origem = (
            f"Cartola R{melhor['round']}"
            if fonte.lower() == "cartola"
            else f"{fonte} R{melhor['round']}"
        )
        embed.add_field(
            name="🔥 MAIOR PONTUAÇÃO DA TEMPORADA",
            value=(
                f"{EMOTES_PC.get(int(melhor['team_id']), '⚽')} "
                f"**{melhor['nome']}** — **{melhor['points']:.2f} pts** • {origem}"
            ),
            inline=False,
        )

    embed.set_footer(text="!campeonatos = arquivo histórico das competições")
    await ctx.send(embed=embed)


@bot.command(name='cartoloucos')
async def cartoloucos(ctx):
    presidentes = sorted(
        (c["presidente"].upper(), c["titulo"]) for c in CLUBES_BIOGRAFIA if c.get("presidente")
    )

    linhas = []
    for presidente, clube in presidentes:
        linhas.append(f"👤 **{presidente}**\n┗ ⚽ {clube}")

    embed = discord.Embed(
        title="🤪 CARTOLOUCOS — PRESIDENTES DA LIGA",
        description="\n\n".join(linhas),
        color=discord.Color.from_rgb(255, 170, 0),
    )
    await ctx.send(embed=embed)

@bot.command(name='ajuda', aliases=['help', 'comandos'])
async def ajuda(ctx):
    embed = discord.Embed(
        title="📚 PREMIER LEAGUE IOS — COMANDOS",
        description=(
            "Tudo separado por categoria para não virar uma parede de comandos.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.from_rgb(37, 99, 235)
    )

    embed.add_field(
        name="⚽ CARTOLA",
        value=(
            "`!tabela` • classificação da liga atual\n"
            "`!status` • quem escalou na rodada\n"
            "`!mercado` • status e fechamento do mercado\n"
            "`!cartoleta` • patrimônio dos times\n"
            "`!nacional` • 10 clubes na Liga Nacional do Cartola\n"
            "`!nacional rodada` • ordenações por rodada/mês/patrimônio\n"
            "`!capitao` • capitães da rodada\n"
            "`!reservadeluxo` • reservas de luxo\n"
        ),
        inline=False
    )

    embed.add_field(
        name="🏆 NAVEGAÇÃO",
        value=(
            "`!campeonatos` • todas as competições e temporadas\n"
            "`!times` • todos os clubes e seus comandos"
        ),
        inline=False
    )

    embed.add_field(
        name="📈 ESTATÍSTICAS",
        value=(
            "`!mvp` • ranking de MVPs\n"
            "`!merda` • Merda da Rodada\n"
            "`!zikado` • jejum atual sem MVP\n"
            "`!aproveitamento` • aproveitamento histórico\n"
            "`!recorde` • recordes de pontuação\n"
        ),
        inline=False
    )

    embed.add_field(
        name="📺 OUTROS",
        value=(
            "`!biografia` • história da Premier League IOS\n"
            "`!temporada` • panorama da temporada atual\n"
            "`!cartoloucos` • presidentes dos clubes"
        ),
        inline=False
    )

    embed.set_footer(text="Competições ficam em !campeonatos • Clubes ficam em !times")
    await ctx.send(embed=embed)

@bot.command(name='times', aliases=['clubes'])
async def times(ctx):
    embed = discord.Embed(
        title="🛡️ CLUBES — PREMIER LEAGUE IOS",
        description=(
            "Escolha um clube para abrir a página dele.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.from_rgb(30, 100, 200)
    )

    ativos = _clubes_por_status("ativo")
    outros = [c for c in CLUBES_BIOGRAFIA if c.get("status") != "ativo"]
    if ativos:
        embed.add_field(
            name="⚽ CLUBES ATUAIS",
            value="\n".join(f"`!{c['comando']}` • {c.get('time') or c['titulo']}" for c in ativos),
            inline=False
        )
    if outros:
        embed.add_field(
            name="🗃️ OUTROS CLUBES REGISTRADOS",
            value="\n".join(f"`!{c['comando']}` • {c['titulo'].title()}" for c in outros),
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name='campeonatos')
async def campeonatos(ctx):
    resultados_pc2 = _pc2_resultados_ativos()
    if len(resultados_pc2) >= 18:
        tabela_pc2 = _calcular_tabela_resultados(resultados_pc2)
        campeao_pc2 = NOMES_PC.get(tabela_pc2[0]["id"], "?") if tabela_pc2 else "?"
        pc2_2026_linha = f"✅ **PC/2 2026**  • `!pc2 2026`  • 🏆 {campeao_pc2}\n"
    else:
        pc2_2026_linha = "🟢 **PC/2 2026**  • `!pc2 2026`  • em andamento\n"
    """Catálogo de campeonatos espelhando as edições registradas no site."""

    embed = discord.Embed(
        title="🏆 ARQUIVO DE CAMPEONATOS",
        description=(
            "**PREMIER LEAGUE IOS • HISTÓRICO OFICIAL**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Todas as edições registradas no site, com o comando para abrir cada uma quando disponível.\n\n"
            "🟢 **Em andamento**  •  ✅ **Encerrado**"
        ),
        color=discord.Color.from_rgb(30, 64, 175)
    )

    for secao in ARQUIVO_CAMPEONATOS:
        linhas = list(secao["linhas"])
        if secao.get("pc2_atual"):
            linhas.insert(0, pc2_2026_linha.rstrip("\n"))
        embed.add_field(name=secao["nome"], value="\n".join(linhas), inline=False)

    embed.add_field(
        name="🧭 COMANDOS DE CAMPEONATOS",
        value=(
            "`!pc` • abre automaticamente o PC ativo\n"
            "`!confrontos [rodada Cartola ou time]` • R1–18 = PC/1; R19–36 = PC/2\n"
            "`!forma` • forma recente no PC/2\n"
            "`!copa` • Copa IOS 2026\n"
            "`!mundial` • IOS World Cup 2026"
        ),
        inline=False
    )

    embed.set_footer(
        text="Histórico completo das competições da liga"
    )

    await ctx.send(embed=embed)

@bot.command(name='mundial')
async def mundial(ctx, ano: int = 2026):
    if ano == 2026:
        linhas="```\nPOS  TIME                       PONTOS\n" + "─"*43 + "\n"
        for i,(cid,pts) in enumerate(IOS_WORLD_CUP_2026,1):
            linhas += f"{i:<4} {NOMES_PC[cid][:26]:<27} {pts:>6}\n"
        linhas += "```"
        embed=discord.Embed(title="🌍 IOS WORLD CUP 2026 — TABELA FINAL", color=discord.Color.from_rgb(212,175,55))
        embed.add_field(name=f"🏆 Campeão: {NOMES_PC.get(IOS_WORLD_CUP_2026[0][0], '?')}", value=linhas, inline=False)
        await ctx.send(embed=embed); return
    if ano == 2025:
        embed = _embed_tabela_historica("mundial-2025")
        if embed is not None:
            await ctx.send(embed=embed); return
    await ctx.send("❌ Tenho Mundial 2025 e IOS World Cup 2026.")


@bot.command(name='admin')
async def admin(ctx, *, acao=None):
    if not await _exigir_admin(ctx):
        return

    acao_norm = (acao or "").strip().lower()

    if acao_norm in ("importar", "import", "validarimport", "validar", "importacao", "importação"):
        arquivo = _arquivo_importacao()
        rel = _validar_arquivo_campeonato(arquivo)

        embed = discord.Embed(
            title="📥 VALIDADOR DE CAMPEONATO",
            description=(
                "**MODO SEGURO:** apenas leitura e validação. "
                "**Nenhum confronto, resultado ou state é alterado.**"
            ),
            color=discord.Color.green() if rel["ok"] else discord.Color.red(),
        )

        if not arquivo:
            embed.add_field(
                name="📄 Arquivo",
                value="❌ Nenhum `.json` ou `.txt` em `imports/`.",
                inline=False,
            )
        else:
            embed.add_field(name="📄 Arquivo", value=f"`{Path(arquivo).name}`", inline=False)
            embed.add_field(
                name="🔎 Leitura",
                value=(
                    f"**Status:** {'✅ VÁLIDO' if rel['ok'] else '❌ REPROVADO'}\n"
                    f"**Nome:** {rel['name']}\n"
                    f"**ID:** {rel['league_id']}\n"
                    f"**Slug:** {rel['slug'] or '—'}\n"
                    f"**Rodadas Cartola:** {rel['round_start']} → {rel['round_end']}\n"
                    f"**Turno e returno:** {'Sim' if rel['return_leg'] else 'Não'}"
                ),
                inline=False,
            )
            embed.add_field(
                name="📊 Estrutura",
                value=(
                    f"Times: **{rel['participants']}** ({rel['known_participants']} reconhecidos)\n"
                    f"Rodadas: **{rel['rounds']}**\n"
                    f"Confrontos: **{rel['matches']}**\n"
                    f"Resultados preenchidos: **{rel['completed_matches']}**\n"
                    f"Ranking: **{rel['ranking_rows']}** linhas"
                ),
                inline=False,
            )

        if rel["errors"]:
            embed.add_field(
                name="❌ Erros",
                value="\n".join(f"• {x}" for x in rel["errors"][:12]),
                inline=False,
            )
        if rel["warnings"]:
            embed.add_field(
                name="⚠️ Avisos",
                value="\n".join(f"• {x}" for x in rel["warnings"][:8]),
                inline=False,
            )

        embed.set_footer(
            text="Salve a resposta bruta como imports/campeonato.json • nada é importado ainda"
        )
        await ctx.send(embed=embed)
        return

    try:
        mercado = await run(_mercado)
        cartola_ok = mercado is not None
        rodada_cartola = int(mercado.rodada_atual) if mercado else int(state.get("rodada", 0) or 0)
    except Exception:
        cartola_ok = False
        rodada_cartola = int(state.get("rodada", 0) or 0)

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            json.load(f)
        state_ok = True
    except Exception:
        state_ok = False

    try:
        json.loads(HISTORICO_RODADAS_FILE.read_text(encoding="utf-8"))
        hist_ok = True
    except Exception:
        hist_ok = False

    hist_stats = _historico_stats()
    bkp = _backup_info()
    import_file = _arquivo_importacao()

    github_status = "⚪ Não configurado"
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            def _check_github():
                url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_STATE_PATH}"
                headers = {
                    "Authorization": f"Bearer {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
                return requests.get(
                    url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10
                ).status_code
            gh_code = await run(_check_github)
            github_status = "✅ Conectado" if gh_code == 200 else f"⚠️ HTTP {gh_code}"
        except Exception:
            github_status = "❌ Falha"

    public_path = Path(PUBLIC_STATE_FILE)
    if public_path.exists():
        public_txt = datetime.fromtimestamp(public_path.stat().st_mtime).strftime("%d/%m/%Y %H:%M:%S")
    else:
        public_txt = "Nunca gerado"

    pc2_round = rodada_cartola - 18 if 19 <= rodada_cartola <= 36 else None
    last_processed = int(state.get("last_processed_round", 0) or 0)

    embed = discord.Embed(
        title="🔧 PAINEL ADMINISTRATIVO — SAÚDE DO BOT",
        description="Acesso restrito ao administrador da liga.",
        color=discord.Color.dark_red(),
    )
    embed.add_field(
        name="🩺 SERVIÇOS",
        value=(
            f"🤖 Discord: ✅ Online\n"
            f"⚽ Cartola: {'✅ Respondendo' if cartola_ok else '❌ Falha'}\n"
            f"🌐 GitHub: {github_status}\n"
            f"📄 State: {'✅ Válido' if state_ok else '❌ Inválido'}\n"
            f"📚 Histórico: {'✅ Válido' if hist_ok else '❌ Ausente/Inválido'}"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏟️ TEMPORADA",
        value=(
            f"Ano: **{TEMPORADA_ATUAL}**\n"
            f"Cartola: **R{rodada_cartola or '?'}**\n"
            f"PC/2: **{'R' + str(pc2_round) if pc2_round else 'fora da janela'}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="⚙️ PROCESSAMENTO",
        value=(
            f"Última rodada processada: **R{last_processed}**\n"
            f"Schema: **v{state.get('schema_version', '?')}**\n"
            f"State público: **{public_txt}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="📚 BANCO HISTÓRICO",
        value=(
            f"Temporadas: **{hist_stats['seasons']}**\n"
            f"Rodadas salvas: **{hist_stats['rounds']}**\n"
            f"Pontuações Cartola: **{hist_stats['cartola_scores']}**\n"
            f"Pontuações especiais/IWC: **{hist_stats['special_scores']}**\n"
            f"Registros globais: **{hist_stats['total_records']}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="💾 BACKUPS",
        value=(
            f"Arquivos: **{bkp['count']}**\n"
            f"Espaço: **{_format_bytes(bkp['size'])}**\n"
            f"Último: **{bkp['latest'] or '—'}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="📥 IMPORTAÇÃO",
        value=(
            f"Arquivo: **{import_file.name}**\n`!admin importar` para validar."
            if import_file
            else "Nenhum arquivo.\nUse `imports/campeonato.json`."
        ),
        inline=True,
    )
    embed.add_field(
        name="🧪 FERRAMENTAS",
        value=(
            "`!testerodada` • teste de rodada\n"
            "`!testegol` • teste de gol\n"
            "`!debuglineup` • lineup\n"
            "`!admin importar` • valida JSON do campeonato"
        ),
        inline=False,
    )
    embed.set_footer(
        text="MVP, Merda, Zikado, vitórias e recordes são históricos globais e não resetam."
    )
    await ctx.send(embed=embed)


# ==========================================
# ==========================================

# ==========================================
# ==========================================
# INICIA O BOT
# ==========================================

# Segurança: remove comandos desativados caso tenham sido registrados por código legado.
for _cmd_removido in ("h2h", "top10", "campeoes"):
    try:
        bot.remove_command(_cmd_removido)
    except Exception:
        pass

# Páginas dos clubes (!<clube>) por último, para não colidir com comandos fixos.
_registrar_comandos_clubes()

if __name__ == "__main__":
    validar_configuracao_runtime()
    bot.run(DISCORD_TOKEN, log_handler=None)
