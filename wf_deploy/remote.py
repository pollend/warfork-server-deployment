"""
remote.py – SSH/SCP helpers and action runners.

Uses Paramiko for SSH and the `scp` package for file uploads. Each public
function is designed to run against a single host so callers can parallelise
with ThreadPoolExecutor.

All server-side logic that used to live in `server-management/Warfork.sh` is
now built as inline bash strings here. Bootstrap runs as the privileged SSH
user (typically root); start/stop/status drop to the `wf` user via sudo.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

import paramiko
from scp import SCPClient


# ---------------------------------------------------------------------------
# Remote-host constants  (mirror the layout previously hard-coded in Warfork.sh)
# ---------------------------------------------------------------------------

WF_USER               = "wf"
WF_HOME               = "/home/wf"
APP_DIR               = "/app"
APP_STEAM_DIR         = f"{APP_DIR}/Steam"
APP_SERVER_DIR        = f"{APP_DIR}/server"
APP_WF_DIR            = f"{APP_SERVER_DIR}/basewf"
APP_INSTALLED_LOCK    = f"{APP_SERVER_DIR}/installed.lock"
WF_CUSTOM_CONFIGS_DIR = "/var/wf"
STEAM_APP_ID          = "1136510"
WF_BINARY             = "wf_server.x86_64"
DEFAULT_PORT          = 44400

_SV_PORT_RE = re.compile(r"sv_port\s+(\d+)")


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


def _as_wf(script: str) -> str:
    """Wrap a multi-line bash script to run as the `wf` user via sudo."""
    return f"sudo -u {WF_USER} -H bash -lc {shlex.quote(script)}"


def session_name_for(wf_params: str, override: str | None = None) -> str:
    """Derive the tmux session name (`wf-<sv_port>`) from WF_PARAMS."""
    if override:
        return override
    m = _SV_PORT_RE.search(wf_params)
    return f"wf-{m.group(1) if m else DEFAULT_PORT}"


def _steam_app_args(steam_branch: str) -> str:
    if steam_branch == "beta":
        return f"{STEAM_APP_ID} -beta beta"
    return STEAM_APP_ID


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
        _run(client, f"mkdir -p {remote_dir}")
        with SCPClient(client.get_transport()) as scp:
            scp.put(str(local_dir), remote_path=remote_dir, recursive=True)
    finally:
        client.close()


def _rsync(sources: list[str], host: str, username: str, ssh_key: str,
           remote_dir: str, extra_flags: list[str] | None = None) -> None:
    """Rsync *sources* into *remote_dir* on the host over SSH."""
    ssh_cmd = (
        f"ssh -i {shlex.quote(ssh_key)} "
        f"-o StrictHostKeyChecking=no -o BatchMode=yes"
    )
    cmd = [
        "rsync", "-a", "--info=progress2",
        *(extra_flags or []),
        "-e", ssh_cmd,
        *sources,
        f"{username}@{host}:{remote_dir}/",
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"rsync failed (exit {result.returncode}): "
            f"{' '.join(shlex.quote(c) for c in cmd)}"
        )


def upload_maps(
    host: str,
    username: str,
    ssh_key: str,
    maps_dir: Path,
    remote_dir: str = APP_WF_DIR,
) -> None:
    """Rsync every .pk3/.pak in *maps_dir* into /app/server/basewf on the host.

    Only new or changed archives are transferred. Existing files in the remote
    directory are never deleted, so the game's own paks are left alone. The
    result is chowned to wf:wf since rsync writes as the SSH user.

    Maps must be present before the server process starts: `sv_pure 1` builds
    its pure list at startup, and clients can only download archives that are
    on that list.
    """
    archives = sorted(
        p for p in maps_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".pk3", ".pak")
    )
    if not archives:
        print(f"[{host}] No .pk3/.pak files in {maps_dir}, skipping map upload")
        return

    print(f"\n[{host}] Uploading {len(archives)} map archive(s) -> {remote_dir}/ …")
    for a in archives:
        print(f"  - {a.name}")

    client = _connect(host, username, ssh_key)
    try:
        _run(client, f"mkdir -p {remote_dir}", timeout=60)
    finally:
        client.close()

    _rsync([str(a) for a in archives], host, username, ssh_key, remote_dir)

    client = _connect(host, username, ssh_key)
    try:
        names = " ".join(shlex.quote(f"{remote_dir}/{a.name}") for a in archives)
        _run(client, f"sudo chown {WF_USER}:{WF_USER} {names}", timeout=120)
    finally:
        client.close()


def rsync_local_build(
    host: str,
    username: str,
    ssh_key: str,
    local_dir: Path,
    remote_dir: str = APP_SERVER_DIR,
) -> None:
    """Rsync the contents of *local_dir* into *remote_dir* on the host.

    Used as a SteamCMD replacement for testing local game builds. Files are
    written as the SSH user; callers are responsible for chowning the result
    to wf:wf if needed (bootstrap's final step does this; update_and_upload
    runs an explicit chown after).

    The trailing slash on the source path means we copy the directory's
    contents, not the directory itself, so the game files land directly
    under /app/server.
    """
    src = str(local_dir).rstrip("/") + "/"
    dst = f"{username}@{host}:{remote_dir}/"
    ssh_cmd = (
        f"ssh -i {shlex.quote(ssh_key)} "
        f"-o StrictHostKeyChecking=no -o BatchMode=yes"
    )
    cmd = [
        "rsync", "-az", "--info=progress2",
        "-e", ssh_cmd,
        src, dst,
    ]
    print(f"\n[{host}] Rsyncing local build {src} -> {remote_dir}/ …")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"rsync failed (exit {result.returncode}): "
            f"{' '.join(shlex.quote(c) for c in cmd)}"
        )


# ---------------------------------------------------------------------------
# Bootstrap step  (logs in as root: full first-time host setup)
# ---------------------------------------------------------------------------

def _step(client: paramiko.SSHClient, host: str, label: str,
          script: str, timeout: int = 900) -> None:
    """Run a single bootstrap step with a clear header so failures are easy to locate."""
    print(f"\n[{host}] >>> {label}")
    try:
        _run(client, script, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"[{host}] step failed: {label}\n{exc}") from None


def bootstrap_host(
    host: str,
    username: str,
    ssh_key: str,
    scripts_dir: Path,
    steam_branch: str | None = "public",
    local_build: Path | None = None,
    maps_dir: Path | None = None,
) -> None:
    """
    Prepare a freshly-imaged host so that `wf` can run the game server.

    Runs as *username* (typically root) and performs the full first-time
    install as a sequence of discrete steps so a failure points at the
    exact step that broke:
      1. Create the `wf` user + home, clean stale .steam sdk paths.
      2. apt-get update.
      3. Install system packages.
      4. Configure en_US.UTF-8 locale.
      5. Create /app layout.
      6. Download SteamCMD (skipped if already present).
      7. Install game files into /app/server — either via SteamCMD for the
         given *steam_branch*, or via rsync from *local_build* if set.
      8. Sync optional /var/wf overrides into /app/server/basewf.
      9. Create configs dir + installed.lock marker.
     10. Upload local configs/*.cfg.
     11. Upload custom map archives from *maps_dir* (skipped if unset).
     12. chown -R wf:wf on /home/wf and /app/{Steam,server}.

    Idempotent — safe to re-run to recover a host or refresh game files.
    """
    if local_build:
        source_label = f"local build {local_build}"
    else:
        source_label = f"branch={steam_branch}"

    print(f"\n[{host}] Bootstrapping host as {username} ({source_label}) …")
    client = _connect(host, username, ssh_key)
    try:
        _step(client, host, "1/12 create wf user + home", """\
set -e
id -u wf >/dev/null 2>&1 || useradd -m -d /home/wf -s /bin/bash wf
mkdir -p /home/wf/.steam
for p in /home/wf/.steam/sdk64 /home/wf/.steam/sdk32; do
    if [ -e "$p" ] && [ ! -L "$p" ]; then rm -rf "$p"; fi
done
""", timeout=60)

        _step(client, host, "2/12 apt-get update",
              "set -e\nexport DEBIAN_FRONTEND=noninteractive\napt-get update",
              timeout=300)

        _step(client, host, "3/12 install system packages", """\
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends \\
    lib32gcc-s1 lib32stdc++6 wget ca-certificates rsync unzip tmux jq bc \\
    binutils util-linux python3 curl file tar bzip2 gzip bsdmainutils \\
    libcurl4 libcurl3-gnutls locales
""", timeout=900)

        _step(client, host, "4/12 configure locales", """\
set -e
export DEBIAN_FRONTEND=noninteractive
sed -i -e 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
dpkg-reconfigure --frontend=noninteractive locales
rm -rf /var/lib/apt/lists/*
""", timeout=120)

        _step(client, host, "5/12 create /app layout",
              f"set -e\nmkdir -p {APP_STEAM_DIR} {APP_SERVER_DIR} {APP_DIR}/.steam",
              timeout=30)

        _step(client, host, "6/12 download SteamCMD (if missing)", f"""\
set -e
if [ ! -x {APP_STEAM_DIR}/steamcmd.sh ]; then
    cd {APP_STEAM_DIR}
    wget -qO- https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar zxf -
    {APP_STEAM_DIR}/steamcmd.sh +quit
else
    echo '> SteamCMD already present, skipping'
fi
""", timeout=300)

        if local_build:
            _step(client, host, "7/12 prep /app/server for local build",
                  f"set -e\nmkdir -p {APP_SERVER_DIR}", timeout=30)
            print(f"\n[{host}] >>> 7/12 rsync local build")
            try:
                rsync_local_build(host, username, ssh_key, local_build, APP_SERVER_DIR)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"[{host}] step failed: 7/12 rsync local build\n{exc}"
                ) from None
        else:
            app_args = _steam_app_args(steam_branch)
            _step(client, host, f"7/12 fetch game files (branch={steam_branch})", f"""\
set -e
{APP_STEAM_DIR}/steamcmd.sh \\
    +force_install_dir {APP_SERVER_DIR} \\
    +login anonymous \\
    +app_update {app_args} validate \\
    +quit
""", timeout=1800)

        _step(client, host, "8/12 sync /var/wf overrides (if any)", f"""\
set -e
if [ -d {WF_CUSTOM_CONFIGS_DIR} ]; then
    echo '> Syncing custom files from {WF_CUSTOM_CONFIGS_DIR}'
    cp -asf {WF_CUSTOM_CONFIGS_DIR}/* {APP_WF_DIR}/ 2>/dev/null || true
    find {APP_WF_DIR} -xtype l -delete
else
    echo '> No {WF_CUSTOM_CONFIGS_DIR} present, skipping'
fi
""", timeout=120)

        _step(client, host, "9/12 create configs dir + lock",
              f"set -e\nmkdir -p {APP_WF_DIR}/configs\ntouch {APP_INSTALLED_LOCK}",
              timeout=30)

        configs_dir = scripts_dir / "configs"
        if configs_dir.is_dir():
            print(f"\n[{host}] >>> 10/12 upload configs from {configs_dir}")
            try:
                with SCPClient(client.get_transport()) as scp:
                    for cfg in configs_dir.glob("*.cfg"):
                        print(f"  - {cfg.name}")
                        scp.put(str(cfg), remote_path=f"{APP_WF_DIR}/configs/{cfg.name}")
            except Exception as exc:
                raise RuntimeError(f"[{host}] step failed: 10/12 upload configs\n{exc}") from None
        else:
            print(f"\n[{host}] >>> 10/12 upload configs — skipped (no {configs_dir})")

        if maps_dir and maps_dir.is_dir():
            print(f"\n[{host}] >>> 11/12 upload custom maps from {maps_dir}")
            try:
                upload_maps(host, username, ssh_key, maps_dir)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"[{host}] step failed: 11/12 upload custom maps\n{exc}"
                ) from None
        else:
            print(f"\n[{host}] >>> 11/12 upload custom maps — skipped (no maps dir)")

        _step(client, host, "12/12 chown -R wf:wf",
              f"chown -R wf:wf /home/wf {APP_STEAM_DIR} {APP_SERVER_DIR}",
              timeout=300)

        print(f"\n[{host}] Bootstrap complete ✓")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Per-host deploy step  (SteamCMD update + config upload, run as wf)
# ---------------------------------------------------------------------------

def update_and_upload(
    host: str,
    username: str,
    ssh_key: str,
    scripts_dir: Path,         # local server-management/ folder
    steam_branch: str | None = None,
    local_build: Path | None = None,
    maps_dir: Path | None = None,
) -> None:
    """Refresh game files and upload local configs.

    Game files come from one of:
      * SteamCMD for *steam_branch* (default), run as the `wf` user via sudo
        so files stay wf-owned.
      * rsync from *local_build* if provided, followed by an explicit
        `chown -R wf:wf` since rsync writes as the SSH user.

    The SCP config upload runs as the SSH user — assumed to be root or wf,
    per the README. Exactly one of *steam_branch* or *local_build* must be
    set.
    """
    if not (steam_branch or local_build):
        raise ValueError("update_and_upload requires steam_branch or local_build")

    # rsync runs over its own ssh; do that first if applicable.
    if local_build:
        rsync_local_build(host, username, ssh_key, local_build, APP_SERVER_DIR)

    print(f"\n[{host}] Updating game files "
          f"({'local build' if local_build else f'branch={steam_branch}'}) …")
    client = _connect(host, username, ssh_key)
    try:
        if local_build:
            _run(client, f"sudo chown -R {WF_USER}:{WF_USER} {APP_SERVER_DIR}",
                 timeout=300)
        else:
            app_args = _steam_app_args(steam_branch)
            steamcmd_script = f"""\
set -e
echo '> Updating game files via SteamCMD (branch: {steam_branch})'
{APP_STEAM_DIR}/steamcmd.sh \\
    +force_install_dir {APP_SERVER_DIR} \\
    +login anonymous \\
    +app_update {app_args} validate \\
    +quit
"""
            _run(client, _as_wf(steamcmd_script), timeout=1800)

        print(f"[{host}] Uploading configs …")
        _run(client, f"mkdir -p {APP_WF_DIR}/configs")
        configs_dir = scripts_dir / "configs"
        with SCPClient(client.get_transport()) as scp:
            for cfg in configs_dir.glob("*.cfg"):
                scp.put(str(cfg), remote_path=f"{APP_WF_DIR}/configs/{cfg.name}")
    finally:
        client.close()

    # Maps go up last, while every session is still stopped — the pure list is
    # built at server startup, so archives added after that are invisible to
    # clients until the next restart.
    if maps_dir and maps_dir.is_dir():
        upload_maps(host, username, ssh_key, maps_dir)


# ---------------------------------------------------------------------------
# Per-instance lifecycle  (start / stop / status — run as the wf user)
# ---------------------------------------------------------------------------

def start_instance(
    host: str,
    username: str,
    ssh_key: str,
    wf_params: str,
    session_override: str | None = None,
) -> None:
    """Start the game server in a detached tmux session named wf-<sv_port>."""
    session = session_name_for(wf_params, session_override)
    log     = f"{WF_HOME}/{session}.log"
    runner  = f"{WF_HOME}/{session}.sh"

    # Launcher body — written verbatim via a quoted heredoc so wf_params
    # (which can contain spaces and double quotes) survives shell expansion.
    # Supervises the binary: if it exits for any reason, log it and relaunch
    # after a short backoff so a crash doesn't take the session down. To stop
    # the server, kill the tmux session — that takes the supervisor with it.
    launcher = (
        "#!/bin/bash\n"
        f"cd {APP_SERVER_DIR}\n"
        "while true; do\n"
        f"    ./{WF_BINARY} {wf_params} 2>&1 | tee -a {log}\n"
        f'    echo "[supervisor] {WF_BINARY} exited $?, restarting in 5s" | tee -a {log}\n'
        "    sleep 5\n"
        "done\n"
    )

    script = f"""\
set -e
mkdir -p $HOME/.steam
ln -sfn {APP_STEAM_DIR}/linux64 $HOME/.steam/sdk64
ln -sfn {APP_STEAM_DIR}/linux32 $HOME/.steam/sdk32
if tmux has-session -t {session} 2>/dev/null; then
    echo "Session {session} already running. Use restart."
    exit 1
fi
cat > {runner} <<'WF_LAUNCHER_EOF'
{launcher}WF_LAUNCHER_EOF
chmod +x {runner}
tmux new-session -d -s {session} {runner}
echo "> Started session {session}"
echo "> Log: {log}"
"""
    client = _connect(host, username, ssh_key)
    try:
        _run(client, _as_wf(script), timeout=120)
    finally:
        client.close()


def stop_instance(
    host: str,
    username: str,
    ssh_key: str,
    wf_params: str,
    session_override: str | None = None,
) -> None:
    """Kill the tmux session for this instance, if it exists."""
    session = session_name_for(wf_params, session_override)
    script = f"""\
if tmux has-session -t {session} 2>/dev/null; then
    tmux kill-session -t {session}
    echo "> Stopped session {session}"
else
    echo "> No session {session} found"
fi
"""
    client = _connect(host, username, ssh_key)
    try:
        _run(client, _as_wf(script), timeout=60)
    finally:
        client.close()


def stop_all_instances(
    host: str,
    username: str,
    ssh_key: str,
) -> None:
    """Kill every wf-* tmux session running as the wf user.

    Used before SteamCMD updates so the game binary isn't holding open
    file handles on /app/server while it's being rewritten — and to free
    RAM/CPU on small hosts during the update.
    """
    script = """\
sessions=$(tmux list-sessions -F '#S' 2>/dev/null | grep '^wf-' || true)
if [ -z "$sessions" ]; then
    echo "> No wf-* sessions running"
    exit 0
fi
for s in $sessions; do
    tmux kill-session -t "$s"
    echo "> Stopped session $s"
done
"""
    client = _connect(host, username, ssh_key)
    try:
        _run(client, _as_wf(script), timeout=60)
    finally:
        client.close()


def status_instance(
    host: str,
    username: str,
    ssh_key: str,
    wf_params: str,
    session_override: str | None = None,
) -> None:
    """Print whether the tmux session for this instance is running."""
    session = session_name_for(wf_params, session_override)
    # Always exit 0 so the status command never fails the deploy run.
    script = f"""\
if tmux has-session -t {session} 2>/dev/null; then
    echo "> Session {session} is running"
else
    echo "> Session {session} is not running"
fi
exit 0
"""
    client = _connect(host, username, ssh_key)
    try:
        _run(client, _as_wf(script), timeout=60)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Log tailing
# ---------------------------------------------------------------------------

def stream_logs(
    host: str,
    username: str,
    ssh_key: str,
    prefix: str,
    lines: int = 100,
    use_sudo: bool = True,
    log_glob: str = f"{WF_HOME}/wf-*.log",
) -> None:
    """
    Open an SSH session, run `tail -F` on the wf log files, and print each
    line to stdout prefixed with *prefix*. Blocks until the channel closes.

    Wraps `tail -F` in `bash -c` so the glob expands on the remote host, and
    optionally prepends `sudo` (the logs are owned by the `wf` user).
    """
    inner = f"tail -F -n {int(lines)} {log_glob} 2>&1"
    bash_cmd = f"bash -c {shlex.quote(inner)}"
    cmd = f"sudo {bash_cmd}" if use_sudo else bash_cmd

    client = _connect(host, username, ssh_key)
    try:
        _, stdout, _ = client.exec_command(cmd, get_pty=True)
        for line in iter(stdout.readline, ""):
            print(f"{prefix} {line.rstrip()}", flush=True)
    finally:
        try:
            client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Runtime parameter substitution
# ---------------------------------------------------------------------------

def resolve_wf_params(
    wf_params: str,
    region_label: str,
    rcon_password: str = "",
    operator_password: str = "",
) -> str:
    """Substitute ${REGION_LABEL} and inject runtime secrets into wf_params."""
    wf_params = wf_params.replace("${REGION_LABEL}", region_label)
    if rcon_password:
        wf_params += f' +set rcon_password "{rcon_password}"'
    if operator_password:
        wf_params += f' +set g_operator_password "{operator_password}"'
    return wf_params
