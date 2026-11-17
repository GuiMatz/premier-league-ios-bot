from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from contextlib import redirect_stderr, redirect_stdout

from backend_consumer import (
    BACKEND_GAPS,
    BackendReadOnlySource,
    BackendUnavailable,
    BotDataSourceConfig,
    COMMAND_ENDPOINT_MAP,
    LegacySource,
    NotificationDeduplicator,
    build_data_source,
    display_api_value,
)

ROOT = Path(__file__).resolve().parent
BOT = ROOT / "bot.py"
PROTECTED = [ROOT / "state.json", ROOT / "state_public.json", ROOT / "historico_rodadas.json"]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


before = {path: digest(path) for path in PROTECTED if path.exists()}
source = BOT.read_text(encoding="utf-8")
compile(source, str(BOT), "exec")
tree = ast.parse(source)
commands = {}
for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute) or decorator.func.attr != "command":
            continue
        name = next((kw.value.value for kw in decorator.keywords if kw.arg == "name" and isinstance(kw.value, ast.Constant)), node.name)
        commands[name] = node.name
assert {"tabela", "status", "mercado", "capitao", "reservadeluxo", "cartoleiro", "recorde", "admin"}.issubset(commands)

legacy_config = BotDataSourceConfig.from_env({})
assert legacy_config.mode == "legacy"
legacy = build_data_source(legacy_config, legacy_readers={"status": lambda: {"ok": True}})
assert isinstance(legacy, LegacySource) and legacy.read("status") == {"ok": True}
try:
    BotDataSourceConfig.from_env({"BOT_DATA_SOURCE": "backend_readonly"})
    raise AssertionError("backend URL deveria ser obrigatória")
except ValueError as error:
    assert "LEAGUE_API_BASE_URL" in str(error)

calls = []
def transport(**request):
    calls.append(request)
    return {"ok": True, "status": "available", "data": {"writer": "bot"}, "warnings": [], "meta": {}}
backend_config = BotDataSourceConfig.from_env({"BOT_DATA_SOURCE": "backend_readonly", "LEAGUE_API_BASE_URL": "https://example.invalid"})
backend = build_data_source(backend_config, transport=transport)
assert isinstance(backend, BackendReadOnlySource)
assert backend.get("/api/league/status")["data"]["writer"] == "bot"
assert calls == [{"method": "GET", "url": "https://example.invalid/api/league/status", "timeout": 8.0}]
assert display_api_value({"ok": True, "status": "unavailable", "data": None}) == {"status": "unavailable", "value": None}
assert display_api_value({"ok": True, "status": "ambiguous", "data": []}) == {"status": "ambiguous", "value": None}
try:
    BackendReadOnlySource(backend_config, lambda **_: (_ for _ in ()).throw(OSError("token=secret"))).get("/api/league/health")
    raise AssertionError("falha de rede deveria ser normalizada")
except BackendUnavailable as error:
    assert "secret" not in str(error)

dedupe = NotificationDeduplicator()
assert dedupe.should_send("event-1", "round-final", 123)
dedupe.mark_sent("event-1", "round-final", 123)
assert not dedupe.should_send("event-1", "round-final", 123)
assert dedupe.should_send("event-1", "mvp", 123)
assert COMMAND_ENDPOINT_MAP["capitao"] == ("/api/league/ownership",)
assert "LIVE_EVENT_FEED" in BACKEND_GAPS

# Importa uma cópia isolada: nunca conecta ao Discord e nunca toca os JSON canônicos.
with tempfile.TemporaryDirectory(prefix="pl-ios-bot-readiness-") as raw_temp:
    temp = Path(raw_temp)
    shutil.copy2(BOT, temp / "bot.py")
    for nome in ("state.json", "historico_rodadas.json"):
        if (ROOT / nome).exists():  # opcionais: sem eles o bot parte do estado padrão
            shutil.copy2(ROOT / nome, temp / nome)
    old_cwd = Path.cwd()
    old_state = os.environ.get("STATE_FILE")
    old_exemplo = os.environ.get("PERMITIR_DADOS_EXEMPLO")
    os.environ["STATE_FILE"] = str(temp / "state.json")
    os.environ["PERMITIR_DADOS_EXEMPLO"] = "1"
    try:
        os.chdir(temp)
        spec = importlib.util.spec_from_file_location("bot_readiness_isolated", temp / "bot.py")
        module = importlib.util.module_from_spec(spec)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            spec.loader.exec_module(module)
        assert module._deduplicar_times(["A", "A", "B", "A"]) == ["A", "B"]
        assert module.LEAGUE_WRITER == "bot"
        assert len(module._calcular_tabela_pc()) == len(module.TIMES)
        assert all(module.bot.get_command(c["comando"]) for c in module.CLUBES_BIOGRAFIA)
        assert not module.monitorar_cartola.is_running()
        assert not module.monitorar_ao_vivo.is_running()
        assert output.getvalue().find(os.environ.get("DISCORD_TOKEN", "__missing_token__")) == -1
    finally:
        import logging
        logging.shutdown()
        logging.getLogger().handlers.clear()
        os.chdir(old_cwd)
        if old_state is None:
            os.environ.pop("STATE_FILE", None)
        else:
            os.environ["STATE_FILE"] = old_state
        if old_exemplo is None:
            os.environ.pop("PERMITIR_DADOS_EXEMPLO", None)
        else:
            os.environ["PERMITIR_DADOS_EXEMPLO"] = old_exemplo

after = {path: digest(path) for path in PROTECTED if path.exists()}
assert before == after
serialized = json.dumps({"commands": commands, "gaps": BACKEND_GAPS}, ensure_ascii=False)
for forbidden in ("DISCORD_TOKEN=", "GITHUB_TOKEN=", "Authorization: Bearer"):
    assert forbidden not in serialized
print(json.dumps({"ok": True, "status": "BOT_READINESS_OFFLINE_OK", "commands": len(commands), "backend_gaps": sorted(BACKEND_GAPS)}, ensure_ascii=False))
