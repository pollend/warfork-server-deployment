"""
config.py – load and validate server-config.json into Python dataclasses.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ServerEntry:
    host: str
    key_secret: str          # name of the env-var / secret (legacy field kept for reference)
    label: str
    username: str = "root"


@dataclass
class ServerType:
    label: str
    port: int
    cvars: dict[str, Any] = field(default_factory=dict)


@dataclass
class SteamBranch:
    label: str


@dataclass
class ServerConfig:
    servers: dict[str, ServerEntry]
    server_defaults: dict[str, Any]
    server_types: dict[str, ServerType]
    steam_branches: dict[str, SteamBranch]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def region_keys(self) -> list[str]:
        return list(self.servers.keys())

    def type_keys(self) -> list[str]:
        return list(self.server_types.keys())


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load(path: str | Path) -> ServerConfig:
    """Parse *path* and return a validated :class:`ServerConfig`."""
    raw = json.loads(Path(path).read_text())

    servers = {
        k: ServerEntry(
            host=v["host"],
            key_secret=v.get("key_secret", ""),
            label=v.get("label", k),
            username=v.get("username", "root"),
        )
        for k, v in raw.get("servers", {}).items()
    }

    server_types = {
        k: ServerType(
            label=v.get("label", k),
            port=int(v["port"]),
            cvars=v.get("cvars", {}),
        )
        for k, v in raw.get("server_types", {}).items()
    }

    steam_branches = {
        k: SteamBranch(label=v.get("label", k))
        for k, v in raw.get("steam_branches", {}).items()
    }

    return ServerConfig(
        servers=servers,
        server_defaults=raw.get("server_defaults", {}),
        server_types=server_types,
        steam_branches=steam_branches,
    )
