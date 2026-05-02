# Warfork Server Management

Automated deployment of Warfork dedicated servers via GitHub Actions.

## Setup

### Server prerequisites

Each server needs to be a Linux box with SSH access. The first-time setup is
performed by `cli.py bootstrap`, which logs in as the `--root-user`
(default `root`) — that account must be able to install packages, create the
`wf` user, and chown `/app`.

After bootstrap, lifecycle commands run as the SSH user listed in
`server-config.json` and drop to `wf` via `sudo -u wf …`, so that user must
have passwordless `sudo` to `wf`:

```shell
%wf-deploy ALL=(wf) NOPASSWD: ALL
```

(Or simply leave the SSH user as `root`, which the rest of the docs assume.)

### GitHub secrets

Go to **Settings → Secrets and variables → Actions** in the repository and add:

| Secret | What it is |
| -------- | ------------ |
| `SERVER_RCON_PASSWORD` (optional) | Remote console password (`rcon_password`) |
| `SERVER_OPERATOR_PASSWORD` (optional) | In-game operator password (`g_operator_password`) |
| `DO_SSH_PRIVATE_KEY_US` | SSH private key for the US East server |
| `DO_SSH_PRIVATE_KEY_EU` | SSH private key for the EU server |

Passwords are passed to the server via `+set` at deploy time. They are never written to disk on the runner or stored in this repo.

### First deploy

Run **Actions → Bootstrap Servers** (or `cli.py bootstrap` locally) to install
system packages, download SteamCMD, fetch the game files, and upload configs.
Then run **Actions → Deploy Servers** to start the server processes.

After the first deploy you can check that sessions are running:

```bash
tmux ls
```

Each server runs in a session named `wf-{port}`, e.g. `wf-44401`. Attach with `tmux attach -t wf-44401`.

## Directory structure

```shell
server-management/
  server-config.json    Server registry — regions, ports, gametypes, hostnames
  configs/
    _base.cfg           Shared settings loaded by every server type
    clan-arena.cfg      Type-specific settings (execs _base.cfg then adds overrides)
    duel.cfg
    votable.cfg
    instagib.cfg
    race.cfg
```

All server-side logic (bootstrap, deploy, stop, status) lives in
`wf_deploy/remote.py` and runs over SSH. Nothing is installed onto the host
beyond the game files themselves.

## Config layering

When a server starts, the command line looks roughly like this:

```cfg
+exec configs/clan-arena.cfg   <- runs first, loads _base.cfg then type settings
+set net_port 44401            <- CLI overrides anything set in the cfg
+set sv_hostname "..."
+set rcon_password "..."       <- injected from GitHub secret, runs last
```

Since `+set` on the command line always wins over `set` in a cfg file, the values in `server-config.json` and the GitHub secrets are always authoritative.

| Setting | Where it lives |
| --------- | --------------- |
| `net_port`, `g_gametype`, `sv_hostname`, `sv_maxclients`, `dedicated` | `server-config.json` |
| `sv_defaultmap`, `g_votable_gametypes`, per-type vote overrides | `configs/{type}.cfg` |
| Logging, masterservers, HTTP, antilag, recording, vote defaults | `configs/_base.cfg` |
| `rcon_password`, `g_operator_password` | GitHub secrets |

## Deploying

Trigger **Actions → Deploy Servers** with:

| Input | Options | Default |
| ------- | --------- | --------- |
| `regions` | `US`, `EU`, or `all` | `all` |
| `server_types` | `clan-arena`, `duel`, etc., or `all` | `all` |

For each selected host, deploy stops the matching sessions, runs SteamCMD
against the host's pinned Steam branch, uploads configs, then starts the
sessions again.

Full first-time setup (packages, SteamCMD, game files) is handled by the
`bootstrap` command — see the **First deploy** section above. Use the
separate `stop` and `status` commands for read-only or maintenance ops.

## Adding a server type

Server types are now declared per-server under `servers.<region>.configuration`.
Add a new key to the `configuration` block of every region that should host it:

```json
"US": {
  "host": "...",
  "label": "US East",
  "steam_branch": "beta",
  "configuration": {
    "my-type": {
      "label": "My Type",
      "port": 44408,
      "cvars": {
        "sv_hostname": "[ WF Beta - ${REGION_LABEL} ] My Type",
        "g_gametype": "mygametype"
      }
    }
  }
}
```

Then create `configs/my-type.cfg`:

```cfg
exec configs/_base.cfg
set sv_defaultmap "wfmap1"
set g_votable_gametypes "mygametype"
```

If you only want this type in some regions, omit it from the others' `configuration`. `deploy -t my-type -r all` will silently skip regions that don't define it.

`steam_branch` is set per host (under `servers.<region>`) and is consumed
by `bootstrap`, which installs the game files for that branch into
`/app/server`. SteamCMD installs one branch per `/app/server`, so every
server type on a given host shares the same branch. Re-run `bootstrap` to
switch a host between `beta` and `public`.

## Adding a region

Regions are per-server entries, so subregions like `US-E`, `EU-DE`, `EU-FN`, `SG` each get their own block in `servers`. Each block carries its own `configuration`:

```json
"EU-DE": {
  "host": "eu-de.warfork.com",
  "key_secret": "DO_SSH_PRIVATE_KEY_EU_DE",
  "username": "steam",
  "label": "EU Frankfurt",
  "steam_branch": "beta",
  "configuration": {
    "clan-arena": { "label": "Clan Arena", "port": 44401, "cvars": { "...": "..." } }
  }
}
```

The same `configs/` directory on disk feeds every region, so just adding the
region block (and its SSH key) is enough.

## Manual server commands

Use the CLI from the repo root rather than running anything on the host:

```bash
pipenv run python cli.py status -r US -t clan-arena --ssh-key ~/.ssh/id_ed25519
pipenv run python cli.py stop   -r US -t clan-arena --ssh-key ~/.ssh/id_ed25519
pipenv run python cli.py deploy -r US -t clan-arena --ssh-key ~/.ssh/id_ed25519
pipenv run python cli.py logs   -r US                --ssh-key ~/.ssh/id_ed25519
```

If you do need to inspect a server directly, sessions are named by port
(`wf-44401`, `wf-44402`, etc.) so multiple server types can run on the same
machine without conflicting:

```bash
sudo -u wf tmux ls
sudo -u wf tmux attach -t wf-44401
```
