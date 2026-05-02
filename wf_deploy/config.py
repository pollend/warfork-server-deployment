"""
config.py – load and validate server-config.json into Python dataclasses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ServerType:
    label: str
    port: int
    cvars: dict[str, Any] = field(default_factory=dict)


@dataclass
class ServerEntry:
    host: str
    key_secret: str          # name of the env-var / secret (legacy field kept for reference)
    label: str
    username: str = "root"
    steam_branch: str | None = None  # per-host branch; falls back to CLI --branch
    configuration: dict[str, ServerType] = field(default_factory=dict)


@dataclass
class SteamBranch:
    label: str


@dataclass
class ServerConfig:
    servers: dict[str, ServerEntry]
    server_defaults: dict[str, Any]
    steam_branches: dict[str, SteamBranch]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def region_keys(self) -> list[str]:
        return list(self.servers.keys())

    def type_keys(self) -> list[str]:
        """Union of all type keys defined across every server (preserving order)."""
        seen: list[str] = []
        for srv in self.servers.values():
            for k in srv.configuration:
                if k not in seen:
                    seen.append(k)
        return seen


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_configuration(raw: dict[str, Any]) -> dict[str, ServerType]:
    return {
        k: ServerType(
            label=v.get("label", k),
            port=int(v["port"]),
            cvars=v.get("cvars", {}),
        )
        for k, v in raw.items()
    }


def load(path: str | Path) -> ServerConfig:
    """Parse *path* and return a validated :class:`ServerConfig`."""
    raw = json.loads(Path(path).read_text())

    servers = {
        k: ServerEntry(
            host=v["host"],
            key_secret=v.get("key_secret", ""),
            label=v.get("label", k),
            username=v.get("username", "root"),
            steam_branch=v.get("steam_branch"),
            configuration=_load_configuration(v.get("configuration", {})),
        )
        for k, v in raw.get("servers", {}).items()
    }

    steam_branches = {
        k: SteamBranch(label=v.get("label", k))
        for k, v in raw.get("steam_branches", {}).items()
    }

    return ServerConfig(
        servers=servers,
        server_defaults=raw.get("server_defaults", {}),
        steam_branches=steam_branches,
    )
