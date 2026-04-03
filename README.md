# Warfork Server Deployment CLI

This project manages Warfork dedicated servers over SSH.

The main entrypoint is `cli.py`, which can:
- Build and inspect a region/server-type deployment matrix
- Provision or update remote hosts
- Start, stop, restart, and check server status

## Requirements

- Linux or macOS shell environment
- Python 3.10+ (3.11+ recommended)
- SSH access to all target servers
- A valid SSH private key file for remote login

Python dependencies (from `requirements.txt`):
- `click`
- `paramiko`
- `scp`

## Project Layout

- `cli.py`: Command-line entrypoint
- `wf_deploy/config.py`: Loads `server-config.json`
- `wf_deploy/matrix.py`: Builds region x server-type deployment jobs
- `wf_deploy/remote.py`: SSH/SCP actions against remote hosts
- `server-management/server-config.json`: Server and game type definitions
- `server-management/Warfork.sh`: Remote lifecycle script uploaded/executed on servers
- `server-management/configs/*.cfg`: Warfork config files per game type

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If you see `ModuleNotFoundError: No module named 'scp'`, the virtual environment is missing dependencies. Re-run `pip install -r requirements.txt`.

## Configuration

By default the CLI uses:
- Config file: `server-management/server-config.json`
- Scripts directory: `server-management/`

Important fields in `server-config.json`:
- `servers`: Region key -> host + label + username
- `server_defaults.cvars`: Baseline cvars for all servers
- `server_types`: Server type key -> label + port + cvars
- `steam_branches`: Allowed branch names (for metadata)

## Authentication

You must provide an SSH key for remote operations:
- Option 1: pass `--ssh-key /path/to/key`
- Option 2: set environment variable `WF_SSH_KEY=/path/to/key`

Optional environment variables used by `deploy`:
- `RCON_PASSWORD`: Injected as `+set rcon_password`
- `OPERATOR_PASSWORD`: Injected as `+set g_operator_password`

## CLI Overview

Top-level commands:
- `matrix`: Preview what region/type jobs will be targeted
- `deploy`: Perform provisioning/update/lifecycle actions
- `status`: Shorthand for `deploy --action status`

General selectors shared by commands:
- `--config, -c`: Path to config JSON
- `--regions, -r`: Comma-separated region keys, or `all`
- `--types, -t`: Comma-separated server-type keys, or `all`

## Command Usage

### 1) Preview matrix

Show all jobs in table format:

```bash
python cli.py matrix
```

Only selected regions/types:

```bash
python cli.py matrix -r US,EU -t clan-arena,duel
```

JSON output:

```bash
python cli.py matrix --json
```

### 2) Deploy and manage servers

Default action is `update-and-restart`:

```bash
python cli.py deploy --ssh-key ~/.ssh/id_ed25519
```

Dry run (no SSH connections):

```bash
python cli.py deploy --dry-run
```

Provision selected targets:

```bash
python cli.py deploy \
  --action provision \
  --regions US \
  --types clan-arena,duel \
  --ssh-key ~/.ssh/id_ed25519
```

Use public branch and higher parallelism:

```bash
python cli.py deploy \
  --branch public \
  --parallel 8 \
  --ssh-key ~/.ssh/id_ed25519
```

Deploy options:
- `--action, -a`: `provision`, `update-and-restart`, `update-only`, `restart-only`, `stop`, `status`
- `--branch, -b`: `beta` or `public`
- `--scripts-dir`: local path to folder containing `Warfork.sh` and `configs/`
- `--parallel, -p`: max concurrent SSH connections (1-16)
- `--rcon-password`: override or pass directly
- `--operator-password`: override or pass directly
- `--dry-run`: print actions without remote execution

### 3) Status shorthand

Equivalent to running `deploy --action status`:

```bash
python cli.py status --ssh-key ~/.ssh/id_ed25519
```

## Typical Workflows

Preview before deploy:

```bash
python cli.py matrix -r all -t all
python cli.py deploy --dry-run -r all -t all
```

Roll out one game type to one region:

```bash
python cli.py deploy \
  -r US \
  -t race \
  -a update-and-restart \
  --ssh-key ~/.ssh/id_ed25519
```

Stop all servers in EU:

```bash
python cli.py deploy -r EU -a stop --ssh-key ~/.ssh/id_ed25519
```

## Exit Behavior

- On success, deploy prints a summary and exits `0`.
- If any job fails, deploy lists failed targets and exits `1`.
- Invalid region/type keys produce a validation error showing available keys.

## Troubleshooting

- `ModuleNotFoundError` for `click`, `paramiko`, or `scp`:
  - Activate the correct virtual environment and run `pip install -r requirements.txt`.
- `SSH private key is required`:
  - Pass `--ssh-key` or set `WF_SSH_KEY`.
- Unknown region/server type:
  - Check keys in `server-management/server-config.json` and pass exact names.
- Remote command failed:
  - Verify host reachability, key permissions, remote user privileges, and that `/app/scripts/Warfork.sh` exists after setup.

## Related Docs

Additional server operations background is documented in:
- `server-management/README.md`
