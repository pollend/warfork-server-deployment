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
VALID_ACTIONS = ("provision", "update-and-restart", "update-only", "restart-only", "stop", "status")


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
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON instead of a table.")
def cmd_matrix(config, regions, types, as_json):
    """Print the deployment matrix that would be used by `deploy`."""
    server_config = cfg_mod.load(config)

    try:
        entries, server_entries = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if as_json:
        data = {
            "matrix":        [e.__dict__ for e in entries],
            "server_matrix": [e.__dict__ for e in server_entries],
        }
        click.echo(json.dumps(data, indent=2))
        return

    click.echo(f"\n{'Region':<12}  {'Type':<16}  {'Host':<20}  {'Port':<6}  WF Params (truncated)")
    click.echo("-" * 90)
    for e in entries:
        params_preview = e.wf_params[:40] + ("…" if len(e.wf_params) > 40 else "")
        click.echo(f"{e.region_label:<12}  {e.type_label:<16}  {e.host:<20}  {e.port:<6}  {params_preview}")
    click.echo(f"\n{len(entries)} job(s) across {len(server_entries)} server(s).")


# ---------------------------------------------------------------------------
# deploy command
# ---------------------------------------------------------------------------

@cli.command("deploy")
@_common_options
@_ssh_key_option
@click.option(
    "--action", "-a",
    default="update-and-restart",
    show_default=True,
    type=click.Choice(VALID_ACTIONS, case_sensitive=False),
    help="Action to perform on matched servers.",
)
@click.option(
    "--branch", "-b",
    default="beta",
    show_default=True,
    type=click.Choice(["beta", "public"], case_sensitive=False),
    help="Steam branch to deploy.",
)
@click.option(
    "--scripts-dir",
    default=str(DEFAULT_SCRIPTS_DIR),
    show_default=True,
    type=click.Path(exists=True, file_okay=False),
    help="Local path to the server-management/ folder (contains Warfork.sh + configs/).",
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
    "--dry-run",
    is_flag=True,
    help="Print what would happen without connecting to any server.",
)
def cmd_deploy(
    config, regions, types, ssh_key, action, branch,
    scripts_dir, parallel, rcon_password, operator_password, dry_run,
):
    """Deploy, update, restart, stop, or check status of game servers."""

    if not ssh_key and not dry_run:
        raise click.ClickException(
            "SSH private key is required. Use --ssh-key or set $WF_SSH_KEY."
        )

    server_config = cfg_mod.load(config)

    try:
        entries, server_entries = matrix_mod.build(server_config, regions, types)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    scripts_path = Path(scripts_dir)
    action = action.lower()

    # ── Summary ──────────────────────────────────────────────────────────
    click.echo(f"\n{'='*60}")
    click.echo(f"  Action  : {action}")
    click.echo(f"  Branch  : {branch}")
    click.echo(f"  Regions : {regions}")
    click.echo(f"  Types   : {types}")
    click.echo(f"  Jobs    : {len(entries)} across {len(server_entries)} server(s)")
    click.echo(f"  Dry run : {dry_run}")
    click.echo(f"{'='*60}\n")

    if dry_run:
        for e in entries:
            click.echo(f"  [DRY-RUN] {e.region_label} / {e.type_label} @ {e.host}:{e.port}")
            click.echo(f"            {e.wf_params[:80]}")
        return

    # ── Phase 1: per-host setup (upload scripts + provision) ─────────────
    if action in ("provision", "update-and-restart", "update-only"):
        click.echo("Phase 1/2 — uploading scripts to servers …\n")
        seen_hosts: set[str] = set()

        def _setup(se):
            if se.host in seen_hosts:
                return
            seen_hosts.add(se.host)
            click.echo(f"  [{se.region_label}] Setting up {se.host} …")
            remote.setup_server(
                host=se.host,
                username=se.username,
                ssh_key=ssh_key,
                scripts_dir=scripts_path,
                action=action,
            )
            click.echo(f"  [{se.region_label}] Setup complete ✓")

        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {pool.submit(_setup, se): se for se in server_entries}
            for fut in as_completed(futures):
                se = futures[fut]
                exc = fut.exception()
                if exc:
                    click.echo(
                        click.style(f"  [{se.region_label}] SETUP FAILED: {exc}", fg="red"),
                        err=True,
                    )

    # ── Phase 2: per-instance action ─────────────────────────────────────
    step = "2/2" if action in ("provision", "update-and-restart", "update-only") else "1/1"
    click.echo(f"\nPhase {step} — running '{action}' on {len(entries)} instance(s) …\n")

    failures: list[str] = []

    def _deploy(entry):
        label = f"{entry.region_label} / {entry.type_label}"
        click.echo(f"  [{label}] Starting …")
        remote.run_action(
            host=entry.host,
            username=entry.username,
            ssh_key=ssh_key,
            server_type=entry.server_type,
            wf_params=entry.wf_params,
            region_label=entry.region_label,
            action=action,
            steam_branch=branch,
            rcon_password=rcon_password,
            operator_password=operator_password,
        )
        click.echo(f"  [{label}] Done ✓")

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_deploy, e): e for e in entries}
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
# status shorthand
# ---------------------------------------------------------------------------

@cli.command("status")
@_common_options
@_ssh_key_option
@click.pass_context
def cmd_status(ctx, config, regions, types, ssh_key):
    """Shorthand for `deploy --action status`."""
    ctx.invoke(
        cmd_deploy,
        config=config,
        regions=regions,
        types=types,
        ssh_key=ssh_key,
        action="status",
        branch="beta",
        scripts_dir=str(DEFAULT_SCRIPTS_DIR),
        parallel=4,
        rcon_password="",
        operator_password="",
        dry_run=False,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
