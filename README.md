# Warfork Server Deployment CLI

This project manages Warfork dedicated servers over SSH.

The main entrypoint is `cli.py`, which can:
- Build and inspect a region/server-type deployment matrix
- Provision or update remote hosts
- Start, stop, restart, and check server status

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
- `wf_deploy/remote.py`: SSH/SCP actions against remote hosts
- `server-management/server-config.json`: Server and game type definitions
- `server-management/Warfork.sh`: Remote lifecycle script uploaded/executed on servers
- `server-management/configs/*.cfg`: Warfork config files per game type

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

You can put any of these in a project-local `.env` file; pipenv automatically loads it for `pipenv run` and `pipenv shell`.

## CLI Overview

Top-level commands:
- `matrix`: Preview what region/type jobs will be targeted
- `bootstrap`: Log in as root and prepare each host (creates `wf` user, fixes `/home/wf` ownership)
- `deploy`: Perform provisioning/update/lifecycle actions
- `status`: Shorthand for `deploy --action status`

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

Run this once on each new server, or any time you see permission errors like
`ln: failed to create symbolic link '/home/wf/.steam/sdk64/linux64': Permission denied`.
The command logs in as `root` (override with `--root-user`) and:

- Creates the `wf` user with `/home/wf` as a real home directory
- Removes any stale `/home/wf/.steam/sdk{32,64}` paths left as root-owned directories
- Recursively chowns `/home/wf` to `wf:wf`
- Ensures `/app/{Steam,server,scripts}` exist

```bash
pipenv run python cli.py bootstrap --ssh-key ~/.ssh/id_ed25519
```

Target specific hosts:

```bash
pipenv run python cli.py bootstrap -r US,EU --ssh-key ~/.ssh/id_ed25519
```

After bootstrap succeeds, run `deploy --action provision` to install SteamCMD
and the game files.

### 3) Deploy and manage servers

Default action is `update-and-restart`:

```bash
pipenv run python cli.py deploy --ssh-key ~/.ssh/id_ed25519
```

Dry run (no SSH connections):

```bash
pipenv run python cli.py deploy --dry-run
```

Provision selected targets:

```bash
pipenv run python cli.py deploy \
  --action provision \
  --regions US \
  --types clan-arena,duel \
  --ssh-key ~/.ssh/id_ed25519
```

Use public branch and higher parallelism:

```bash
pipenv run python cli.py deploy \
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

### 4) Status shorthand

Equivalent to running `deploy --action status`:

```bash
pipenv run python cli.py status --ssh-key ~/.ssh/id_ed25519
```

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
  -a update-and-restart \
  --ssh-key ~/.ssh/id_ed25519
```

Stop all servers in EU:

```bash
pipenv run python cli.py deploy -r EU -a stop --ssh-key ~/.ssh/id_ed25519
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
  - Verify host reachability, key permissions, remote user privileges, and that `/app/scripts/Warfork.sh` exists after setup.

## Related Docs

Additional server operations background is documented in:
- `server-management/README.md`
