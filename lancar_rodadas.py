#!/usr/bin/env python3
"""
Lança resultados do PC/2 (rodadas ja jogadas) no state do bot.

Usa a MESMA rotina de fechamento que o bot usa ao vivo
(bot.processar_rodada_finalizada), entao atualiza de uma vez:
  MVP, Merda, Zikado, Aproveitamento, tabela do PC/2 e o historico do !recorde.
Nada e enviado ao Discord (canal=None).

USO (com o BOT PARADO, na mesma pasta do bot.py):
    python3 lancar_rodadas.py --dry-run     # so valida e mostra o que faria
    python3 lancar_rodadas.py               # grava de verdade

Para lancar mais rodadas depois, acrescente-as em rodadas_manuais.json
(rodadas ja processadas sao puladas, entao pode manter as antigas no arquivo).
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
os.chdir(AQUI)
sys.path.insert(0, str(AQUI))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt).lower())
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return "".join(c for c in txt if c.isalnum())


def erro(msg):
    print(f"\n❌ {msg}\n   Nada foi gravado.")
    sys.exit(1)


def carregar_rodadas(caminho, bot):
    """Le o JSON e devolve {rodada_pc2: (scores_por_time_id, partidas)} ja validado."""
    try:
        bruto = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception as e:
        erro(f"Não consegui ler {caminho}: {e}")

    nome_para_id = {}
    for tid, nome in bot.NOMES_PC.items():
        if tid != bot.ID_CLUBE_EXTINTO:
            nome_para_id[norm(nome)] = tid
    for nome_completo, d in bot.TIMES.items():
        nome_para_id[norm(nome_completo)] = d["id"]

    ids_liga = {d["id"] for d in bot.TIMES.values()}
    rodadas = {}

    for rod_txt, jogos in (bruto.get("rodadas_pc2") or {}).items():
        try:
            rod = int(rod_txt)
        except ValueError:
            erro(f"Rodada inválida no JSON: {rod_txt!r}")

        esperado = bot.PC2_CONFRONTOS.get(rod)
        if not esperado:
            erro(f"PC/2 R{rod}: não há confrontos cadastrados em PC2_CONFRONTOS.")
        if len(jogos) != len(esperado):
            erro(f"PC/2 R{rod}: esperava {len(esperado)} confrontos, o JSON tem {len(jogos)}.")

        scores, partidas = {}, []
        for item in jogos:
            if len(item) != 4:
                erro(f"PC/2 R{rod}: confronto malformado: {item}")
            mand, pm, vis, pv = item
            mid, vid = nome_para_id.get(norm(mand)), nome_para_id.get(norm(vis))
            if mid is None or vid is None:
                erro(f"PC/2 R{rod}: time não reconhecido em {item}")
            try:
                pm, pv = round(float(pm), 2), round(float(pv), 2)
            except (TypeError, ValueError):
                erro(f"PC/2 R{rod}: pontuação inválida em {item}")
            for tid, pts in ((mid, pm), (vid, pv)):
                if tid in scores:
                    erro(f"PC/2 R{rod}: time {bot.NOMES_PC.get(tid, tid)} aparece duas vezes.")
                scores[tid] = pts
            partidas.append((mid, pm, vid, pv))

        if set(scores) != ids_liga:
            faltam = [bot.NOMES_PC.get(t, t) for t in ids_liga - set(scores)]
            erro(f"PC/2 R{rod}: faltam times no JSON: {faltam}")

        pares_json = {frozenset((m, v)) for m, _, v, _ in partidas}
        pares_codigo = {frozenset(p) for p in esperado}
        if pares_json != pares_codigo:
            erro(
                f"PC/2 R{rod}: os confrontos do JSON não batem com PC2_CONFRONTOS do bot. "
                "Confira os nomes/lados digitados."
            )
        rodadas[rod] = (scores, partidas)

    if not rodadas:
        erro("O JSON não tem nenhuma rodada em 'rodadas_pc2'.")
    return dict(sorted(rodadas.items()))


def resumo_rodada(bot, rod, scores):
    nomes = bot.NOMES_PC
    ordem = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    mvp, merda = ordem[0], ordem[-1]
    return (
        f"PC/2 R{rod} (Cartola {rod + 18}) — "
        f"MVP: {nomes[mvp[0]]} {mvp[1]:.2f} | Merda: {nomes[merda[0]]} {merda[1]:.2f}"
    )


def main():
    ap = argparse.ArgumentParser(description="Lança rodadas do PC/2 no state do bot.")
    ap.add_argument("--arquivo", default="rodadas_manuais.json")
    ap.add_argument("--dry-run", action="store_true", help="só valida e mostra o plano")
    ap.add_argument("--sim", action="store_true", help="não pergunta se o bot está parado")
    ap.add_argument("--sem-publicar", action="store_true", help="não publica o site no fim")
    args = ap.parse_args()

    if not (AQUI / "bot.py").exists():
        erro("bot.py não está na mesma pasta deste script.")

    print("Carregando o bot (sem conectar ao Discord)...")
    import bot  # noqa: E402  — o bot só conecta dentro de `if __name__ == "__main__"`

    rodadas = carregar_rodadas(args.arquivo, bot)
    state = bot.state
    ultima = int(state.get("last_processed_round", bot.STATS_BASELINE_ROUND) or bot.STATS_BASELINE_ROUND)
    print(f"Última rodada Cartola já processada no state: {ultima}\n")

    plano, esperada = [], ultima + 1
    for rod, (scores, partidas) in rodadas.items():
        cartola = rod + 18
        if cartola <= ultima:
            print(f"⏭️  PC/2 R{rod} (Cartola {cartola}): já processada, pulando.")
            continue
        if cartola != esperada:
            erro(
                f"Buraco na sequência: falta a Cartola {esperada} (PC/2 R{esperada - 18}) "
                f"antes da PC/2 R{rod}. Adicione as rodadas em ordem, sem pular."
            )
        plano.append((rod, cartola, scores))
        esperada += 1
        print("🆕 " + resumo_rodada(bot, rod, scores))

    if not plano:
        print("\nNada a fazer: todas as rodadas do arquivo já estavam processadas.")
        return

    if args.dry_run:
        print("\n✅ Validação ok. Modo --dry-run: nada foi gravado.")
        return

    if not args.sim:
        resp = input("\nO bot está PARADO? Rodar com ele ligado pode sobrescrever o state. (s/n) ")
        if resp.strip().lower() not in ("s", "sim", "y", "yes"):
            erro("Pare o bot e rode de novo.")

    # backup de segurança antes de gravar
    pasta = Path(bot.BACKUPS_DIR) / f"pre-lancamento-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    pasta.mkdir(parents=True, exist_ok=True)
    for arq in (Path(bot.STATE_FILE), Path(bot.HISTORICO_RODADAS_FILE)):
        if arq.exists():
            shutil.copy2(arq, pasta / arq.name)
    print(f"\n💾 Backup salvo em {pasta}")

    id_nome = {d["id"]: nome for nome, d in bot.TIMES.items()}
    id_discord = {d["id"]: d["discord"] for d in bot.TIMES.values()}

    async def processar():
        feitas = []
        for rod, cartola, scores in plano:
            resultados = sorted(
                [(id_nome[t], id_discord[t], float(p), t) for t, p in scores.items()],
                key=lambda x: x[2], reverse=True,
            )
            r = await bot.processar_rodada_finalizada(None, cartola, resultados, origem="lancamento_manual")
            ok = bool(r and r.get("processed"))
            print(f"{'✅' if ok else '❌'} Cartola {cartola} (PC/2 R{rod}): {r}")
            if not ok:
                break
            feitas.append(cartola)
        return feitas

    token = bot.GITHUB_TOKEN
    bot.GITHUB_TOKEN = ""  # evita 1 commit no site por rodada; publica uma vez no fim
    try:
        feitas = asyncio.run(processar())
    finally:
        bot.GITHUB_TOKEN = token

    if not feitas:
        erro("Nenhuma rodada foi processada. Veja as mensagens acima e o bot.log.")

    if not args.sem_publicar and token:
        print("🌐 Publicando o state no site...")
        print("   " + ("ok" if bot.publicar_site_estado() else "FALHOU (veja o bot.log)"))
    elif not token:
        print("ℹ️  GITHUB_TOKEN não configurado: state_public.json foi gerado só localmente.")

    print("\n===== RESULTADO =====")
    print(f"Última rodada processada: {state.get('last_processed_round')}")
    for titulo, campo in (("MVP", "mvp_historico"), ("Merda", "merda_historico"), ("Zikado", "zikado")):
        ranking = sorted(state.get(campo, {}).items(), key=lambda kv: kv[1], reverse=True)
        print(f"{titulo}: " + " | ".join(f"{n} {v}" for n, v in ranking))
    print("\nPode ligar o bot de novo.")


if __name__ == "__main__":
    main()
