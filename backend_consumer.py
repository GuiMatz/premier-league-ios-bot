"""Contratos offline para a futura leitura do backend pelo bot.

Este módulo não é importado pelo bot canônico nesta fase e nunca envia Discord.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable
from urllib.parse import urlencode, urljoin

VALID_DATA_SOURCE_MODES = {"legacy", "backend_readonly"}
UNCERTAIN_STATUSES = {"unavailable", "partial", "ambiguous"}

COMMAND_ENDPOINT_MAP = {
    "tabela": ("/api/league/standings",),
    "status": ("/api/league/lineups", "/api/league/status"),
    "mercado": ("/api/league/status",),
    "cartoleta": (),
    "pc": ("/api/league/standings",),
    "pc1": ("/api/league/standings",),
    "pc2": ("/api/league/standings",),
    "confrontos": ("/api/league/rounds",),
    "forma": ("/api/league/rounds",),
    "copa": ("/api/league/history", "/api/league/rounds"),
    "aproveitamento": ("/api/league/state", "/api/league/history"),
    "capitao": ("/api/league/ownership",),
    "reservadeluxo": ("/api/league/ownership",),
    "cartoleiro": ("/api/league/history",),
    "mvp": ("/api/league/history",),
    "recorde": ("/api/league/records",),
    "zikado": ("/api/league/history",),
    "merda": ("/api/league/history",),
    "temporada": ("/api/league/state", "/api/league/history"),
    "times": ("/api/league/clubs",),
    "campeonatos": ("/api/league/history",),
    "mundial": ("/api/league/history", "/api/league/standings"),
    "debuglineup": ("/api/league/lineups", "/api/league/ownership"),
    "admin": ("/api/league/health", "/api/league/status"),
}

BACKEND_GAPS = {
    "CURRENT_MARKET_CLOSE": "status não expõe horário/contagem regressiva do mercado",
    "CURRENT_SCALING_STATUS": "não há contrato consolidado de quem escalou antes do lock",
    "TEAM_BUDGET": "não há endpoint read-only de patrimônio/cartoletas",
    "LIVE_EVENT_FEED": "não há feed de eventos idempotentes para notificações Discord",
    "RECENT_FORM": "rounds não oferece uma projeção dedicada de forma recente",
    "CUP_BRACKET": "history/rounds ainda não garantem bracket completo da Copa",
    "ADMIN_DIAGNOSTICS": "health público não substitui todas as ferramentas administrativas locais",
    "STATIC_EDITORIAL": "biografias, transmissão e páginas de clubes ainda contêm conteúdo editorial local",
}


@dataclass(frozen=True)
class BotDataSourceConfig:
    mode: str = "legacy"
    backend_url: str | None = None
    timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "BotDataSourceConfig":
        values = os.environ if env is None else env
        mode = (values.get("BOT_DATA_SOURCE") or "legacy").strip().lower()
        if mode not in VALID_DATA_SOURCE_MODES:
            raise ValueError("BOT_DATA_SOURCE deve ser legacy ou backend_readonly")
        url = (values.get("LEAGUE_API_BASE_URL") or "").strip().rstrip("/") or None
        if mode == "backend_readonly" and not url:
            raise ValueError("LEAGUE_API_BASE_URL é obrigatória em backend_readonly")
        return cls(mode=mode, backend_url=url, timeout_seconds=float(values.get("LEAGUE_API_TIMEOUT", "8")))


class BackendUnavailable(RuntimeError):
    pass


class BackendReadOnlySource:
    def __init__(self, config: BotDataSourceConfig, transport: Callable[..., dict[str, Any]]):
        if config.mode != "backend_readonly":
            raise ValueError("BackendReadOnlySource exige mode=backend_readonly")
        self.config = config
        self.transport = transport

    def get(self, endpoint: str, **query: Any) -> dict[str, Any]:
        if not endpoint.startswith("/api/league/"):
            raise ValueError("endpoint fora de /api/league")
        suffix = endpoint.lstrip("/")
        url = urljoin(f"{self.config.backend_url}/", suffix)
        if query:
            url = f"{url}?{urlencode(query)}"
        try:
            response = self.transport(method="GET", url=url, timeout=self.config.timeout_seconds)
        except Exception as error:
            raise BackendUnavailable("backend read-only indisponível") from error
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise BackendUnavailable("resposta read-only inválida")
        return response


class LegacySource:
    mode = "legacy"

    def __init__(self, readers: dict[str, Callable[..., Any]] | None = None):
        self.readers = readers or {}

    def read(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self.readers:
            raise KeyError(f"legacy reader ausente: {name}")
        return self.readers[name](*args, **kwargs)


class NotificationDeduplicator:
    """Contrato em memória; persistência oficial ficará para o cutover."""

    def __init__(self) -> None:
        self._sent: set[tuple[str, str, str]] = set()

    @staticmethod
    def key(event_id: str, notification_type: str, discord_channel: str | int) -> tuple[str, str, str]:
        if not event_id or not notification_type or discord_channel in (None, ""):
            raise ValueError("notification identity incompleta")
        return str(event_id), str(notification_type), str(discord_channel)

    def should_send(self, event_id: str, notification_type: str, discord_channel: str | int) -> bool:
        return self.key(event_id, notification_type, discord_channel) not in self._sent

    def mark_sent(self, event_id: str, notification_type: str, discord_channel: str | int) -> None:
        self._sent.add(self.key(event_id, notification_type, discord_channel))


def display_api_value(response: dict[str, Any]) -> Any:
    """Não converte ausência semântica em zero, lista vazia ou 'nenhum'."""
    status = response.get("status")
    data = response.get("data")
    if status in UNCERTAIN_STATUSES or data is None:
        return {"status": status or "unavailable", "value": None}
    return {"status": status or "available", "value": data}


def build_data_source(config: BotDataSourceConfig, *, legacy_readers=None, transport=None):
    if config.mode == "legacy":
        return LegacySource(legacy_readers)
    if transport is None:
        raise ValueError("transport read-only obrigatório")
    return BackendReadOnlySource(config, transport)
