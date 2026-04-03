"""
remote.py – SSH/SCP helpers and action runners.

Uses Paramiko for SSH and the `scp` package for file uploads.
Each public function is designed to run against a single host so that
callers can parallelise with ThreadPoolExecutor.
"""
from __future__ import annotations

import os
from pathlib import Path

import paramiko
from scp import SCPClient


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _connect(host: str, username: str, ssh_key: str) -> paramiko.SSHClient:
    """Open an SSH connection and return the client."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        username=username,
        key_filename=ssh_key,
        timeout=30,
    )
    return client


def _run(client: paramiko.SSHClient, cmd: str, timeout: int = 900) -> str:
    """
    Execute *cmd* on the remote host.

    Streams stdout/stderr to the terminal and returns combined output.
    Raises RuntimeError on non-zero exit.
    """
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout, get_pty=True)
    output_lines: list[str] = []
    for line in iter(stdout.readline, ""):
        print(line, end="", flush=True)
        output_lines.append(line)

    exit_code = stdout.channel.recv_exit_status()
    if exit_code != 0:
        err = stderr.read().decode(errors="replace")
        raise RuntimeError(
            f"Remote command failed (exit {exit_code}):\n{cmd}\n{err}"
        )
    return "".join(output_lines)


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def upload_file(host: str, username: str, ssh_key: str,
                local_path: Path, remote_path: str) -> None:
    """SCP a single file to *remote_path* on the server."""
    client = _connect(host, username, ssh_key)
    try:
        with SCPClient(client.get_transport()) as scp:
            scp.put(str(local_path), remote_path)
    finally:
        client.close()


def upload_directory(host: str, username: str, ssh_key: str,
                     local_dir: Path, remote_dir: str) -> None:
    """Recursively SCP all files in *local_dir* to *remote_dir*."""
    client = _connect(host, username, ssh_key)
    try:
        # Ensure remote directory exists
        _run(client, f"mkdir -p {remote_dir}")
        with SCPClient(client.get_transport()) as scp:
            scp.put(str(local_dir), remote_path=remote_dir, recursive=True)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Setup step  (runs once per host)
# ---------------------------------------------------------------------------

def setup_server(
    host: str,
    username: str,
    ssh_key: str,
    scripts_dir: Path,          # local path to server-management/
    action: str,
) -> None:
    """
    Upload Warfork.sh + configs to the server, make executable.
    Only runs for update/provision actions (not for stop/status/restart-only).
    """
    if action not in ("provision", "update-and-restart", "update-only"):
        return

    print(f"\n[{host}] Uploading Warfork.sh …")
    client = _connect(host, username, ssh_key)
    try:
        _run(client, "mkdir -p /app/scripts /app/server/basewf")

        with SCPClient(client.get_transport()) as scp:
            scp.put(
                str(scripts_dir / "Warfork.sh"),
                remote_path="/app/scripts/Warfork.sh",
            )

        print(f"[{host}] Uploading configs …")
        configs_dir = scripts_dir / "configs"
        with SCPClient(client.get_transport()) as scp:
            # Upload individual cfg files into /app/server/basewf/configs/
            _run(client, "mkdir -p /app/server/basewf/configs")
            for cfg in configs_dir.glob("*.cfg"):
                scp.put(str(cfg), remote_path=f"/app/server/basewf/configs/{cfg.name}")

        _run(client, "chmod +x /app/scripts/Warfork.sh")

        if action == "provision":
            print(f"[{host}] Running provision …")
            _run(client, "/app/scripts/Warfork.sh provision", timeout=1800)

    finally:
        client.close()


# ---------------------------------------------------------------------------
# Per-instance action
# ---------------------------------------------------------------------------

def run_action(
    host: str,
    username: str,
    ssh_key: str,
    server_type: str,
    wf_params: str,
    region_label: str,
    action: str,
    steam_branch: str,
    rcon_password: str = "",
    operator_password: str = "",
) -> None:
    """
    Execute *action* for a single (region × server_type) on the remote host.
    Mirrors the shell script inside the deploy job of deploy-servers.yml.
    """
    # Substitute region label placeholder
    wf_params = wf_params.replace("${REGION_LABEL}", region_label)

    # Inject optional credentials
    if rcon_password:
        wf_params += f' +set rcon_password "{rcon_password}"'
    if operator_password:
        wf_params += f' +set g_operator_password "{operator_password}"'

    # Build env exports for the wf user
    env = (
        f"STEAM_BRANCH='{steam_branch}' "
        f"SERVER_TYPE='{server_type}' "
        f"WF_PARAMS='{wf_params}' "
        f"REGION_LABEL='{region_label}'"
    )
    sudo_cmd = f"sudo -u wf {env}"

    action_map = {
        "provision":          f"{sudo_cmd} /app/scripts/Warfork.sh run",
        "update-and-restart": f"{sudo_cmd} /app/scripts/Warfork.sh stop; {sudo_cmd} /app/scripts/Warfork.sh start",
        "update-only":        f"{sudo_cmd} /app/scripts/Warfork.sh restart",
        "restart-only":       f"{sudo_cmd} /app/scripts/Warfork.sh restart",
        "stop":               f"{sudo_cmd} /app/scripts/Warfork.sh stop",
        "status":             f"{sudo_cmd} /app/scripts/Warfork.sh status || true",
    }

    if action not in action_map:
        raise ValueError(f"Unknown action: {action!r}")

    preamble = (
        "useradd wf 2>/dev/null || true; "
        "mkdir -p /home/wf; "
        "chown wf:wf /home/wf; "
    )
    full_cmd = preamble + action_map[action]

    client = _connect(host, username, ssh_key)
    try:
        _run(client, full_cmd, timeout=900)
    finally:
        client.close()
