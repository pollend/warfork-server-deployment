#!/usr/bin/env python3
"""
cli.py – Warfork server management CLI.

Usage:
    python cli.py deploy   [options]
    python cli.py status   [options]
    python cli.py matrix   [options]

Run `python cli.py --help` for full documentation.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# Make the repo root importable even when the script is run directly
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

from wf_deploy import config as cfg_mod
from wf_deploy import matrix as matrix_mod
from wf_deploy import remote


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = Path(__file__).parent / "server-management" / "server-config.json"
DEFAULT_SCRIPTS_DIR = Path(__file__).parent / "server-management"
DEFAULT_MAPS_DIR = Path(__file__).parent / "server-management" / "downloads"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_host_branches(server_entries) -> dict[str, str]:
    """Map host -> Steam branch. Every server must declare steam_branch.

    SteamCMD installs one branch per /app/server, so the branch is pinned
    per host (per server entry in the config).
    """
    host_branch: dict[str, str] = {}
    for se in server_entries:
        if not se.steam_branch:
            raise click.ClickException(
                f"Server {se.region} ({se.region_label}) has no steam_branch. "
                f"Set steam_branch under servers.{se.region}."
            )
        host_branch[se.host] = se.steam_branch
    return host_branch


# ---------------------------------------------------------------------------
# Shared options
# ---------------------------------------------------------------------------

def _common_options(f):
    """Decorator that attaches shared options to every sub-command."""
    f = click.option(
        "--config", "-c",
        default=str(DEFAULT_CONFIG),
        show_default=True,
        type=click.Path(exists=True, dir_okay=False),
        help="Path to server-config.json",
    )(f)
    f = click.option(
        "--regions", "-r",
        default="all",
        show_default=True,
        help='Comma-separated region keys, or "all".',
    )(f)
    f = click.option(
        "--types", "-t",
        default="all",
        show_default=True,
        help='Comma-separated server-type keys, or "all".',
    )(f)
    return f


def _maps_dir_option(f):
    """Local directory of custom .pk3/.pak archives pushed into basewf."""
    f = click.option(
        "--maps-dir",
        default=str(DEFAULT_MAPS_DIR),
        show_default=True,
        type=click.Path(file_okay=False),
        help=(
            "Local directory of custom map archives (*.pk3/*.pak) to rsync into "
            "/app/server/basewf. Pass --no-maps to skip."
        ),
    )(f)
    f = click.option(
        "--no-maps",
        is_flag=True,
        help="Don't upload custom maps, even if the maps directory exists.",
    )(f)
    return f


def _resolve_maps_dir(maps_dir: str, no_maps: bool) -> Path | None:
    if no_maps:
        return None
    path = Path(maps_dir)
    return path if path.is_dir() else None


def _ssh_key_option(f):
    f = click.option(
        "--ssh-key", "-k",
        default=lambda: os.environ.get("WF_SSH_KEY", ""),
        show_default="$WF_SSH_KEY",
        type=click.Path(exists=True, dir_okay=False),
        help="Path to SSH private key file. Falls back to $WF_SSH_KEY.",
    )(f)
    return f


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """Warfork dedicated-server management CLI.

    Connects to remote game servers over SSH and manages
    installation, updates, and lifecycle (start / stop / restart).
    """


# ---------------------------------------------------------------------------
# matrix command
# ---------------------------------------------------------------------------

@cli.command("matrix")
@_common_options
@click.option(
    "--branch", "-b",
    default=None,
    type=click.Choice(["beta", "public"], case_sensitive=False),
    help="Fallback Steam branch shown for entries that don't pin one.",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON instead of a table.")
def cmd_matrix(config, regions, types, branch, as_json):
    """Print the deployment matrix that would be used by `deploy`."""
    server_config = cfg_mod.load(config)

    try:
        entries, server_entries = matrix_mod.build(
            server_config, regions, types, default_branch=branch,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if as_json:
        data = {
            "matrix":        [e.__dict__ for e in entries],
            "server_matrix": [e.__dict__ for e in server_entries],
        }
        click.echo(json.dumps(data, indent=2))
        return

    click.echo(f"\n{'Region':<12}  {'Type':<16}  {'Host':<20}  {'Port':<6}  {'Branch':<8}  WF Params (truncated)")
    click.echo("-" * 100)
    for e in entries:
        params_preview = e.wf_params[:40] + ("…" if len(e.wf_params) > 40 else "")
        branch_str = e.steam_branch or "—"
        click.echo(
            f"{e.region_label:<12}  {e.type_label:<16}  {e.host:<20}  {e.port:<6}  "
            f"{branch_str:<8}  {params_preview}"
        )
    click.echo(f"\n{len(entries)} job(s) across {len(server_entries)} server(s).")


# ---------------------------------------------------------------------------
# deploy command
# ---------------------------------------------------------------------------

@cli.command("deploy")
@_common_options
@_ssh_key_option
@_maps_dir_option
@click.option(
    "--scripts-dir",
    default=str(DEFAULT_SCRIPTS_DIR),
    show_default=True,
    type=click.Path(exists=True, file_okay=False),
    help="Local path to the server-management/ folder (contains configs/).",
)
@click.option(
    "--parallel", "-p",
    default=4,
    show_default=True,
    type=click.IntRange(1, 16),
    help="Maximum concurrent SSH connections.",
)
@click.option(
    "--rcon-password",
    default=lambda: os.environ.get("RCON_PASSWORD", ""),
    show_default="$RCON_PASSWORD",
    help="rcon_password injected into server params.",
)
@click.option(
    "--operator-password",
    default=lambda: os.environ.get("OPERATOR_PASSWORD", ""),
    show_default="$OPERATOR_PASSWORD",
    help="g_operator_password injected into server params.",
)
@click.option(
    "--local-build",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help=(
        "Local game build directory to rsync onto each host (replaces SteamCMD). "
        "Useful for testing a local build without publishing it to a Steam branch."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would happen without connecting to any server.",
)
def cmd_deploy(
    config, regions, types, ssh_key, maps_dir, no_maps,
    scripts_dir, parallel, rcon_password, operator_password, local_build, dry_run,
):
    """Update game files via SteamCMD, refresh configs, and restart all
    selected server processes.

    For each host: stop the matching tmux sessions, run SteamCMD against the
    host's pinned Steam branch, upload configs, then start the sessions
    again. Use `bootstrap` for first-time host setup; use `stop` / `status`
    for read-only or maintenance operations.
    """

    if not ssh_key and not dry_run:
        raise click.ClickException(
            "SSH private key is required. Use --ssh-key or set $WF_SSH_KEY."
        )

    server_config = cfg_mod.load(config)

    try:
        entries, server_entries = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    local_build_path = Path(local_build) if local_build else None
    host_branch = (
        {se.host: None for se in server_entries}
        if local_build_path else
        _resolve_host_branches(server_entries)
    )
    scripts_path = Path(scripts_dir)
    maps_path = _resolve_maps_dir(maps_dir, no_maps)
    source_label = (
        f"local build {local_build_path}" if local_build_path else "Steam branch (per host)"
    )
    maps_label = (
        f"{len(list(maps_path.glob('*.pk3')))} pk3 from {maps_path}"
        if maps_path else "none"
    )

    # ── Summary ──────────────────────────────────────────────────────────
    click.echo(f"\n{'='*60}")
    click.echo(f"  Action  : update + restart")
    click.echo(f"  Regions : {regions}")
    click.echo(f"  Types   : {types}")
    click.echo(f"  Source  : {source_label}")
    click.echo(f"  Maps    : {maps_label}")
    click.echo(f"  Jobs    : {len(entries)} across {len(server_entries)} server(s)")
    click.echo(f"  Dry run : {dry_run}")
    click.echo(f"{'='*60}\n")

    if dry_run:
        for e in entries:
            src = "local-build" if local_build_path else f"branch={e.steam_branch}"
            click.echo(
                f"  [DRY-RUN] {e.region_label} / {e.type_label} "
                f"@ {e.host}:{e.port}  {src}"
            )
            click.echo(f"            {e.wf_params[:80]}")
        return

    # ── Phase 1: per-host (parallel across hosts; sequential within) ─────
    # Kill all wf-* sessions on the host, run SteamCMD update, upload configs.
    # Killing every session (not just the selected ones) frees RAM/CPU on
    # small hosts and prevents the running binary from locking files that
    # SteamCMD is trying to overwrite.
    click.echo("Phase 1/2 — stopping sessions, updating game files, uploading configs …\n")

    def _prepare_host(se):
        click.echo(f"  [{se.region_label}] Stopping all sessions on {se.host} …")
        remote.stop_all_instances(
            host=se.host,
            username=se.username,
            ssh_key=ssh_key,
        )
        remote.update_and_upload(
            host=se.host,
            username=se.username,
            ssh_key=ssh_key,
            scripts_dir=scripts_path,
            steam_branch=host_branch[se.host],
            local_build=local_build_path,
            maps_dir=maps_path,
        )
        click.echo(f"  [{se.region_label}] Host ready ✓")

    setup_failures: set[str] = set()
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_prepare_host, se): se for se in server_entries}
        for fut in as_completed(futures):
            se = futures[fut]
            exc = fut.exception()
            if exc:
                setup_failures.add(se.host)
                click.echo(
                    click.style(f"  [{se.region_label}] SETUP FAILED: {exc}", fg="red"),
                    err=True,
                )

    # ── Phase 2: per-instance start ─────────────────────────────────────
    click.echo(f"\nPhase 2/2 — starting {len(entries)} instance(s) …\n")

    failures: list[str] = []

    def _start(entry):
        label = f"{entry.region_label} / {entry.type_label}"
        if entry.host in setup_failures:
            raise RuntimeError(f"skipping start; host {entry.host} setup failed")
        wf_params = remote.resolve_wf_params(
            entry.wf_params, entry.region_label,
            rcon_password=rcon_password,
            operator_password=operator_password,
        )
        click.echo(f"  [{label}] Starting …")
        remote.start_instance(
            host=entry.host,
            username=entry.username,
            ssh_key=ssh_key,
            wf_params=wf_params,
        )
        click.echo(f"  [{label}] Done ✓")

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_start, e): e for e in entries}
        for fut in as_completed(futures):
            e = futures[fut]
            label = f"{e.region_label} / {e.type_label}"
            exc = fut.exception()
            if exc:
                failures.append(label)
                click.echo(
                    click.style(f"  [{label}] FAILED: {exc}", fg="red"),
                    err=True,
                )

    # ── Final summary ─────────────────────────────────────────────────────
    click.echo(f"\n{'='*60}")
    ok = len(entries) - len(failures)
    click.echo(f"  {ok}/{len(entries)} succeeded")
    if failures:
        click.echo(click.style("  Failed:", fg="red"))
        for f in failures:
            click.echo(click.style(f"    • {f}", fg="red"))
        sys.exit(1)
    else:
        click.echo(click.style("  All done ✓", fg="green"))
    click.echo(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# bootstrap command
# ---------------------------------------------------------------------------

@cli.command("bootstrap")
@_common_options
@_ssh_key_option
@_maps_dir_option
@click.option(
    "--root-user", "-u",
    default="root",
    show_default=True,
    help="Privileged SSH user used to prepare the host (must be able to useradd/chown).",
)
@click.option(
    "--scripts-dir",
    default=str(DEFAULT_SCRIPTS_DIR),
    show_default=True,
    type=click.Path(exists=True, file_okay=False),
    help="Local path to the server-management/ folder (contains configs/).",
)
@click.option(
    "--parallel", "-p",
    default=4,
    show_default=True,
    type=click.IntRange(1, 16),
    help="Maximum concurrent SSH connections.",
)
@click.option(
    "--local-build",
    default=None,
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help=(
        "Local game build directory to rsync onto each host (replaces SteamCMD). "
        "Useful for testing a local build without publishing it to a Steam branch."
    ),
)
def cmd_bootstrap(
    config, regions, types, ssh_key, maps_dir, no_maps,
    root_user, scripts_dir, parallel, local_build,
):
    """Log in as root and perform full first-time setup for each host.

    Creates the wf user, installs system packages, downloads SteamCMD, fetches
    the game files for the host's Steam branch, uploads configs, and chowns
    everything to wf:wf. Idempotent — safe to re-run to recover or refresh.

    Subsequent `deploy` calls run as the regular SSH user.
    """
    if not ssh_key:
        raise click.ClickException(
            "SSH private key is required. Use --ssh-key or set $WF_SSH_KEY."
        )

    server_config = cfg_mod.load(config)

    try:
        entries, server_entries = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    local_build_path = Path(local_build) if local_build else None
    host_branch = (
        {se.host: None for se in server_entries}
        if local_build_path else
        _resolve_host_branches(server_entries)
    )
    scripts_path = Path(scripts_dir)
    maps_path = _resolve_maps_dir(maps_dir, no_maps)

    source_label = (
        f"local build {local_build_path}" if local_build_path else "Steam branch (per host)"
    )
    click.echo(f"\n{'='*60}")
    click.echo(f"  Bootstrapping {len(server_entries)} host(s) as {root_user!r}")
    click.echo(f"  Source : {source_label}")
    click.echo(f"  Maps   : {maps_path or 'none'}")
    click.echo(f"{'='*60}\n")

    failures: list[str] = []

    def _bootstrap(se):
        branch = host_branch[se.host]
        src = "local build" if local_build_path else f"branch={branch}"
        click.echo(f"  [{se.region_label}] Preparing {se.host} ({src}) …")
        remote.bootstrap_host(
            host=se.host,
            username=root_user,
            ssh_key=ssh_key,
            scripts_dir=scripts_path,
            steam_branch=branch,
            local_build=local_build_path,
            maps_dir=maps_path,
        )
        click.echo(f"  [{se.region_label}] Ready ✓")

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_bootstrap, se): se for se in server_entries}
        for fut in as_completed(futures):
            se = futures[fut]
            exc = fut.exception()
            if exc:
                failures.append(se.region_label)
                click.echo(
                    click.style(f"  [{se.region_label}] BOOTSTRAP FAILED: {exc}", fg="red"),
                    err=True,
                )

    click.echo(f"\n{'='*60}")
    ok = len(server_entries) - len(failures)
    click.echo(f"  {ok}/{len(server_entries)} host(s) bootstrapped")
    if failures:
        click.echo(click.style("  Failed:", fg="red"))
        for f in failures:
            click.echo(click.style(f"    • {f}", fg="red"))
        sys.exit(1)
    else:
        click.echo(click.style("  All done ✓", fg="green"))
    click.echo(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# maps command
# ---------------------------------------------------------------------------

@cli.command("maps")
@_common_options
@_ssh_key_option
@_maps_dir_option
@click.option(
    "--parallel", "-p",
    default=4,
    show_default=True,
    type=click.IntRange(1, 16),
    help="Maximum concurrent SSH connections.",
)
@click.option(
    "--restart",
    is_flag=True,
    help="Restart the selected sessions afterwards so the new maps enter the pure list.",
)
@click.option(
    "--rcon-password",
    default=lambda: os.environ.get("RCON_PASSWORD", ""),
    show_default="$RCON_PASSWORD",
    help="rcon_password injected into server params on restart.",
)
@click.option(
    "--operator-password",
    default=lambda: os.environ.get("OPERATOR_PASSWORD", ""),
    show_default="$OPERATOR_PASSWORD",
    help="g_operator_password injected into server params on restart.",
)
def cmd_maps(config, regions, types, ssh_key, maps_dir, no_maps, parallel,
             restart, rcon_password, operator_password):
    """Push custom map archives to /app/server/basewf without a SteamCMD run.

    Running servers keep serving the pure list they built at startup, so newly
    uploaded maps stay invisible to clients until the session restarts. Pass
    --restart to stop and start the selected sessions in one go.
    """
    if not ssh_key:
        raise click.ClickException(
            "SSH private key is required. Use --ssh-key or set $WF_SSH_KEY."
        )

    maps_path = _resolve_maps_dir(maps_dir, no_maps)
    if not maps_path:
        raise click.ClickException(
            f"No maps directory at {maps_dir} (or --no-maps was passed)."
        )

    server_config = cfg_mod.load(config)
    try:
        entries, server_entries = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    archives = sorted(
        p.name for p in maps_path.iterdir()
        if p.is_file() and p.suffix.lower() in (".pk3", ".pak")
    )
    click.echo(
        f"\nPushing {len(archives)} archive(s) from {maps_path} "
        f"to {len(server_entries)} host(s)\n"
    )

    failures: list[str] = []

    def _push(se):
        remote.upload_maps(
            host=se.host,
            username=se.username,
            ssh_key=ssh_key,
            maps_dir=maps_path,
        )
        click.echo(f"  [{se.region_label}] Maps uploaded ✓")

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_push, se): se for se in server_entries}
        for fut in as_completed(futures):
            se = futures[fut]
            exc = fut.exception()
            if exc:
                failures.append(se.region_label)
                click.echo(
                    click.style(f"  [{se.region_label}] UPLOAD FAILED: {exc}", fg="red"),
                    err=True,
                )

    if failures:
        sys.exit(1)

    if not restart:
        click.echo(
            "\nMaps uploaded. Restart the sessions "
            "(`cli.py maps --restart`, or stop + deploy) before clients can see them.\n"
        )
        return

    click.echo(f"\nRestarting {len(entries)} session(s) …\n")

    def _restart(entry):
        label = f"{entry.region_label} / {entry.type_label}"
        wf_params = remote.resolve_wf_params(
            entry.wf_params, entry.region_label,
            rcon_password=rcon_password,
            operator_password=operator_password,
        )
        remote.stop_instance(
            host=entry.host, username=entry.username,
            ssh_key=ssh_key, wf_params=wf_params,
        )
        remote.start_instance(
            host=entry.host, username=entry.username,
            ssh_key=ssh_key, wf_params=wf_params,
        )
        click.echo(f"  [{label}] Restarted ✓")

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_restart, e): e for e in entries}
        for fut in as_completed(futures):
            e = futures[fut]
            label = f"{e.region_label} / {e.type_label}"
            exc = fut.exception()
            if exc:
                failures.append(label)
                click.echo(
                    click.style(f"  [{label}] RESTART FAILED: {exc}", fg="red"),
                    err=True,
                )

    if failures:
        sys.exit(1)
    click.echo(click.style("\n  All done ✓\n", fg="green"))


# ---------------------------------------------------------------------------
# logs command
# ---------------------------------------------------------------------------

_LOG_PALETTE = ("green", "cyan", "yellow", "magenta", "blue", "red", "bright_green", "bright_cyan")


@cli.command("logs")
@_common_options
@_ssh_key_option
@click.option(
    "--lines", "-n",
    default=100,
    show_default=True,
    type=click.IntRange(0, 100000),
    help="Lines of history to show before tailing.",
)
@click.option(
    "--no-sudo",
    is_flag=True,
    help="Don't prefix the remote tail with sudo (logs must be readable by the SSH user).",
)
def cmd_logs(config, regions, types, ssh_key, lines, no_sudo):
    """Tail /home/wf/wf-*.log on every selected host (Ctrl+C to stop).

    Opens one SSH session per host (deduplicated across server types) and
    streams each line prefixed with the region label. Logs are owned by the
    `wf` user, so the remote command runs under sudo unless --no-sudo is set.
    """
    if not ssh_key:
        raise click.ClickException(
            "SSH private key is required. Use --ssh-key or set $WF_SSH_KEY."
        )

    server_config = cfg_mod.load(config)

    try:
        _, server_entries = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    click.echo(f"Tailing logs from {len(server_entries)} host(s). Ctrl+C to stop.\n")

    threads: list[threading.Thread] = []
    for i, se in enumerate(server_entries):
        color = _LOG_PALETTE[i % len(_LOG_PALETTE)]
        prefix = click.style(f"[{se.region_label}]", fg=color)

        t = threading.Thread(
            target=remote.stream_logs,
            kwargs=dict(
                host=se.host,
                username=se.username,
                ssh_key=ssh_key,
                prefix=prefix,
                lines=lines,
                use_sudo=not no_sudo,
            ),
            daemon=True,
            name=f"tail-{se.region}",
        )
        t.start()
        threads.append(t)

    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo("\nStopping log tail …", err=True)


# ---------------------------------------------------------------------------
# Lifecycle helpers shared by status / stop
# ---------------------------------------------------------------------------

def _run_per_instance(entries, ssh_key, parallel, label_verb, fn):
    """Apply *fn(entry, wf_params)* to each entry in parallel and report."""
    failures: list[str] = []

    def _wrap(entry):
        label = f"{entry.region_label} / {entry.type_label}"
        wf_params = remote.resolve_wf_params(entry.wf_params, entry.region_label)
        click.echo(f"  [{label}] {label_verb} …")
        fn(entry, wf_params)

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_wrap, e): e for e in entries}
        for fut in as_completed(futures):
            e = futures[fut]
            label = f"{e.region_label} / {e.type_label}"
            exc = fut.exception()
            if exc:
                failures.append(label)
                click.echo(
                    click.style(f"  [{label}] FAILED: {exc}", fg="red"),
                    err=True,
                )

    if failures:
        sys.exit(1)


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------

@cli.command("status")
@_common_options
@_ssh_key_option
@click.option(
    "--parallel", "-p",
    default=4,
    show_default=True,
    type=click.IntRange(1, 16),
    help="Maximum concurrent SSH connections.",
)
def cmd_status(config, regions, types, ssh_key, parallel):
    """Report whether each selected tmux session is currently running."""
    if not ssh_key:
        raise click.ClickException(
            "SSH private key is required. Use --ssh-key or set $WF_SSH_KEY."
        )

    server_config = cfg_mod.load(config)
    try:
        entries, _ = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    _run_per_instance(
        entries, ssh_key, parallel, "Checking",
        lambda entry, wf_params: remote.status_instance(
            host=entry.host,
            username=entry.username,
            ssh_key=ssh_key,
            wf_params=wf_params,
        ),
    )


# ---------------------------------------------------------------------------
# stop command
# ---------------------------------------------------------------------------

@cli.command("stop")
@_common_options
@_ssh_key_option
@click.option(
    "--parallel", "-p",
    default=4,
    show_default=True,
    type=click.IntRange(1, 16),
    help="Maximum concurrent SSH connections.",
)
def cmd_stop(config, regions, types, ssh_key, parallel):
    """Stop every selected tmux session (no-op if not running)."""
    if not ssh_key:
        raise click.ClickException(
            "SSH private key is required. Use --ssh-key or set $WF_SSH_KEY."
        )

    server_config = cfg_mod.load(config)
    try:
        entries, _ = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    _run_per_instance(
        entries, ssh_key, parallel, "Stopping",
        lambda entry, wf_params: remote.stop_instance(
            host=entry.host,
            username=entry.username,
            ssh_key=ssh_key,
            wf_params=wf_params,
        ),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
