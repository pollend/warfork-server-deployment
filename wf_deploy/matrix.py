"""
matrix.py – build the deployment matrix from a ServerConfig.

Mirrors the logic of build-matrix.sh but in pure Python.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import ServerConfig


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class MatrixEntry:
    """One (region × server_type) deployment job."""
    region: str
    region_label: str
    server_type: str
    type_label: str
    host: str
    username: str
    port: int
    wf_params: str          # Full "+exec … +set …" command-line string


@dataclass
class ServerMatrixEntry:
    """One region/host (used for per-server setup steps)."""
    region: str
    region_label: str
    host: str
    username: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_keys(requested: str, available: list[str], kind: str) -> list[str]:
    """Return the concrete list of keys, or raise if any is unknown."""
    if requested.strip().lower() == "all":
        return available
    keys = [k.strip() for k in requested.split(",") if k.strip()]
    unknown = [k for k in keys if k not in available]
    if unknown:
        raise ValueError(
            f"Unknown {kind}: {', '.join(unknown)}. "
            f"Available: {', '.join(available)}"
        )
    return keys


def _cvars_to_params(cvars: dict[str, Any]) -> str:
    """Serialize a cvar dict to a '+set key value' string."""
    parts: list[str] = []
    for key, value in cvars.items():
        val_str = str(value)
        if " " in val_str:
            val_str = f'"{val_str}"'
        parts.append(f"+set {key} {val_str}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build(
    config: ServerConfig,
    regions: str = "all",
    types: str = "all",
) -> tuple[list[MatrixEntry], list[ServerMatrixEntry]]:
    """
    Build the full deployment matrix.

    Returns:
        (entries, server_entries) where *entries* is the per-job matrix
        and *server_entries* is the per-host matrix.
    """
    region_keys = _resolve_keys(regions, config.region_keys(), "regions")
    type_keys = _resolve_keys(types, config.type_keys(), "server types")

    default_cvars: dict[str, Any] = config.server_defaults.get("cvars", {})

    entries: list[MatrixEntry] = []
    server_entries: list[ServerMatrixEntry] = []

    for region in region_keys:
        srv = config.servers[region]

        server_entries.append(
            ServerMatrixEntry(
                region=region,
                region_label=srv.label,
                host=srv.host,
                username=srv.username,
            )
        )

        for stype in type_keys:
            st = config.server_types[stype]

            # Merge: defaults ← type cvars ← port
            merged: dict[str, Any] = {
                **default_cvars,
                **st.cvars,
                "sv_port": st.port,
                "sv_port6": st.port,
            }

            cvar_str = _cvars_to_params(merged)
            wf_params = f"+exec configs/{stype}.cfg {cvar_str}"

            entries.append(
                MatrixEntry(
                    region=region,
                    region_label=srv.label,
                    server_type=stype,
                    type_label=st.label,
                    host=srv.host,
                    username=srv.username,
                    port=st.port,
                    wf_params=wf_params,
                )
            )

    if not entries:
        raise ValueError(
            "No valid region/type combinations found. "
            "Check --regions / --types and server-config.json."
        )

    return entries, server_entries
