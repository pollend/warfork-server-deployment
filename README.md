# Warfork Server Deployment CLI

This project manages Warfork dedicated servers over SSH.

The main entrypoint is `cli.py`, which can:
- Build and inspect a region/server-type deployment matrix
- Bootstrap a fresh host (one-time install of packages, SteamCMD, game files)
- Deploy: update game files via SteamCMD, refresh configs, restart sessions
- Stop and check status of running sessions

## Requirements

- Linux or macOS shell environment
- Python 3.10+ (3.11+ recommended)
- [pipenv](https://pipenv.pypa.io/) for environment and dependency management
- SSH access to all target servers
- A valid SSH private key file for remote login

Python dependencies (declared in `requirements.txt`, managed via pipenv):
- `click`
- `paramiko`
- `scp`

## Project Layout

- `cli.py`: Command-line entrypoint
- `wf_deploy/config.py`: Loads `server-config.json`
- `wf_deploy/matrix.py`: Builds region x server-type deployment jobs
- `wf_deploy/remote.py`: SSH/SCP actions against remote hosts (bootstrap, lifecycle, log tailing — all logic lives here)
- `server-management/server-config.json`: Server and game type definitions
- `server-management/configs/*.cfg`: Warfork config files per game type
- `server-management/downloads/*.pk3`: Custom map archives pushed into `/app/server/basewf`

## Setup

Install pipenv if you don't already have it:

```bash
python3 -m pip install --user pipenv
```

From the repository root, create the virtual environment and install dependencies from `requirements.txt`:

```bash
pipenv install -r requirements.txt
```

This creates a `Pipfile` (and `Pipfile.lock`) and a managed virtualenv. On subsequent setups, just run:

```bash
pipenv install
```

To enter the project shell:

```bash
pipenv shell
```

Or run individual commands without activating a shell:

```bash
pipenv run python cli.py matrix
```

If you see `ModuleNotFoundError: No module named 'scp'`, the virtual environment is missing dependencies. Re-run `pipenv install`.

## Configuration

By default the CLI uses:
- Config file: `server-management/server-config.json`
- Scripts directory: `server-management/`

Important fields in `server-config.json`:
- `servers`: Region key -> host + label + username + `steam_branch` + `configuration`
- `servers.<region>.steam_branch`: Pin this host to `beta` or `public`. Required for any host that will be bootstrapped. SteamCMD installs one branch per `/app/server`, so the branch is per host, not per server type.
- `servers.<region>.configuration`: Server type key -> label + port + cvars (each region declares its own set; types may differ region to region)
- `server_defaults.cvars`: Baseline cvars merged into every job
- `steam_branches`: Allowed branch names (for metadata)

## Authentication

You must provide an SSH key for remote operations:
- Option 1: pass `--ssh-key /path/to/key`
- Option 2: set environment variable `WF_SSH_KEY=/path/to/key`

Optional environment variables used by `deploy`:
- `RCON_PASSWORD`: Injected as `+set rcon_password`
- `OPERATOR_PASSWORD`: Injected as `+set g_operator_password`

You can put any of these in a project-local `.env` file; pipenv automatically loads it for `pipenv run` and `pipenv shell`.

## CLI Overview

Top-level commands:
- `matrix`: Preview what region/type jobs will be targeted
- `bootstrap`: Log in as root and fully install each host (wf user, packages, SteamCMD, game files, configs)
- `deploy`: Stop sessions, update game files via SteamCMD, refresh configs, restart sessions
- `stop`: Stop every selected tmux session
- `status`: Report whether each selected session is running
- `logs`: Tail `/home/wf/wf-*.log` from every selected host in one terminal
- `maps`: Push custom `.pk3` archives to `/app/server/basewf` (optionally restarting sessions)

General selectors shared by commands:
- `--config, -c`: Path to config JSON
- `--regions, -r`: Comma-separated region keys, or `all`
- `--types, -t`: Comma-separated server-type keys, or `all`

## Command Usage

All commands below assume you are using `pipenv run`. If you've activated the env via `pipenv shell`, drop the `pipenv run` prefix.

### 1) Preview matrix

Show all jobs in table format:

```bash
pipenv run python cli.py matrix
```

Only selected regions/types:

```bash
pipenv run python cli.py matrix -r US,EU -t clan-arena,duel
```

JSON output:

```bash
pipenv run python cli.py matrix --json
```

### 2) Bootstrap a fresh host

Run this once on each new server (or any time you need to re-install or
recover one). The command logs in as `root` (override with `--root-user`)
and performs the full first-time setup in a single pass:

- Creates the `wf` user with `/home/wf` as a real home directory
- Removes any stale `/home/wf/.steam/sdk{32,64}` paths left as root-owned directories
- Installs system packages and downloads SteamCMD
- Installs the Warfork game files for the host's Steam branch
- Uploads the local `configs/*.cfg` files into `/app/server/basewf/configs`
- Recursively chowns `/home/wf` and `/app/{Steam,server}` to `wf:wf`

The Steam branch comes from the host's `steam_branch` field in
`server-config.json` (set under `servers.<region>`). SteamCMD installs
one branch per `/app/server`, so the branch is pinned per host.

```bash
pipenv run python cli.py bootstrap --ssh-key ~/.ssh/id_ed25519
```

Target specific hosts:

```bash
pipenv run python cli.py bootstrap -r US,EU --ssh-key ~/.ssh/id_ed25519
```

Bootstrap is idempotent — re-running it is the supported way to refresh
game files or recover a misconfigured host. After it succeeds, use
`deploy` to roll out updates.

### 3) Deploy

`deploy` always performs the same sequence per host: stop the matching
sessions, run SteamCMD against the host's pinned Steam branch, upload
configs, then start the sessions again. There is no action flag.

```bash
pipenv run python cli.py deploy --ssh-key ~/.ssh/id_ed25519
```

Target a subset:

```bash
pipenv run python cli.py deploy -r US -t race --ssh-key ~/.ssh/id_ed25519
```

Dry run (no SSH connections):

```bash
pipenv run python cli.py deploy --dry-run
```

Higher parallelism:

```bash
pipenv run python cli.py deploy \
  --parallel 8 \
  --ssh-key ~/.ssh/id_ed25519
```

To switch a host to a different Steam branch, edit its `steam_branch` field
in `server-config.json` and re-run `bootstrap` for that host.

#### Testing a local game build

Pass `--local-build PATH` to `bootstrap` or `deploy` to skip SteamCMD and
rsync the contents of a local build directory onto each host instead. Useful
when you have an unpublished build you want to try out without going through
a Steam branch.

```bash
pipenv run python cli.py deploy \
  --local-build ~/projects/warfork-qfusion/source/build/warfork-qfusion \
  --ssh-key ~/.ssh/id_ed25519
```

The directory's contents (not the directory itself) are rsynced into
`/app/server`, then the SSH user runs `sudo chown -R wf:wf /app/server`. The
host's `steam_branch` is ignored when `--local-build` is set, so it doesn't
need to be defined for local-build runs. Re-run without `--local-build` (or
re-run `bootstrap`) to switch back to a Steam branch.

Deploy options:
- `--scripts-dir`: local path to folder containing `configs/`
- `--parallel, -p`: max concurrent SSH connections (1-16)
- `--rcon-password`: override or pass directly
- `--operator-password`: override or pass directly
- `--local-build`: rsync game files from a local build directory instead of SteamCMD
- `--dry-run`: print actions without remote execution

### 4) Tail logs from every host

Stream `/home/wf/wf-*.log` from each selected host into one terminal, with
each line color-coded and prefixed with the region label. Ctrl+C to stop.

```bash
pipenv run python cli.py logs --ssh-key ~/.ssh/id_ed25519
```

Limit to a subset and show more history:

```bash
pipenv run python cli.py logs -r US,EU -n 500 --ssh-key ~/.ssh/id_ed25519
```

The remote `tail -F` runs under `sudo` because the log files are owned by
`wf`. Pass `--no-sudo` if your SSH user can already read them.

### 5) Status

Report whether each selected tmux session is currently running:

```bash
pipenv run python cli.py status --ssh-key ~/.ssh/id_ed25519
```

### 6) Stop

Stop every selected tmux session (no-op for sessions that aren't running):

```bash
pipenv run python cli.py stop -r EU --ssh-key ~/.ssh/id_ed25519
```

### 7) Custom maps

Every `.pk3`/`.pak` in `server-management/downloads/` is rsynced into
`/app/server/basewf/` by `bootstrap` and `deploy`. Only new or changed archives
go over the wire, and nothing on the host is deleted, so the game's own paks
are left alone. Use `--no-maps` to skip the upload, or `--maps-dir` to point at
a different folder.

To push maps without waiting on a SteamCMD run:

```bash
pipenv run python cli.py maps -r US -t votable --restart --ssh-key ~/.ssh/id_ed25519
```

`--restart` matters: `sv_pure 1` builds the downloadable-file list when the
server process starts, so archives added to a running server stay invisible to
clients until the session comes back.

Clients fetch missing archives from the server's built-in HTTP server, which
listens on the game port plus 100 (`44403` → `44503`). Those TCP ports must be
open in the host firewall or downloads silently fall back to nothing.

See [`server-management/downloads/README.md`](server-management/downloads/README.md)
for the current map inventory and how to wire a new map into `g_maplist`.

## Typical Workflows

Preview before deploy:

```bash
pipenv run python cli.py matrix -r all -t all
pipenv run python cli.py deploy --dry-run -r all -t all
```

Roll out one game type to one region:

```bash
pipenv run python cli.py deploy \
  -r US \
  -t race \
  --ssh-key ~/.ssh/id_ed25519
```

Stop all servers in EU:

```bash
pipenv run python cli.py stop -r EU --ssh-key ~/.ssh/id_ed25519
```

## Exit Behavior

- On success, deploy prints a summary and exits `0`.
- If any job fails, deploy lists failed targets and exits `1`.
- Invalid region/type keys produce a validation error showing available keys.

## Troubleshooting

- `ModuleNotFoundError` for `click`, `paramiko`, or `scp`:
  - Run `pipenv install` from the repo root, then prefix commands with `pipenv run` (or use `pipenv shell`).
- `pipenv: command not found`:
  - Install with `python3 -m pip install --user pipenv` and ensure `~/.local/bin` is on your `PATH`.
- Wrong Python version picked up:
  - Force a specific interpreter with `pipenv --python 3.11 install -r requirements.txt`.
- `SSH private key is required`:
  - Pass `--ssh-key` or set `WF_SSH_KEY` (a `.env` file in the repo root works with pipenv).
- Unknown region/server type:
  - Check keys in `server-management/server-config.json` and pass exact names.
- Remote command failed:
  - Verify host reachability, key permissions, and remote user privileges. The CLI runs all server-side logic over SSH — there is no longer a `Warfork.sh` to install on the host; the only on-disk remote artifacts are the game files under `/app/server`, the SteamCMD install under `/app/Steam`, and the per-instance launcher script `/home/wf/wf-<port>.sh` written each time `start` runs.

## Related Docs

Additional server operations background is documented in:
- `server-management/README.md`
