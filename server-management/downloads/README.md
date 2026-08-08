# downloads/

Custom map archives (`*.pk3`, `*.pak`) pushed to `/app/server/basewf/` on every
host. Anything dropped here is rsynced up by `bootstrap`, `deploy`, and the
standalone `maps` command, then chowned to `wf:wf`.

Existing remote files are never deleted, so the game's own `pak*.pak` files are
untouched — deleting a `.pk3` here does *not* remove it from the servers.

## How clients get them

`configs/_base.cfg` already has everything needed:

| cvar | value | why |
| ---- | ----- | --- |
| `sv_pure` | `1` | builds the pure list at startup; only listed archives are downloadable |
| `sv_uploads` | `1` | file serving on |
| `sv_uploads_from_server` | `1` | serve archives the client is missing |
| `sv_http` | `1` | built-in HTTP server that carries the transfer |
| `sv_http_port` | `44444` | **must be open in the host firewall (TCP)** |

The pure list is built **when the server process starts**. Archives uploaded to a
running server are invisible to clients until that session restarts.

Client side, downloads need `cl_downloads 1` and `cl_downloads_from_web 1`
(both default on).

## Current contents

These are the seven most-downloaded standalone maps in the
[GameBanana Warsow map category](https://gamebanana.com/mods/cats/5706), as
checked on 2026-08-06. Download totals are a point-in-time snapshot. The two
higher-ranked submissions, `map_wdm2` and `wctf1`, were skipped because their
downloads are texture-only reskins with no BSP.

| Archive | BSP map name | Downloads | Source |
| ------- | ------------ | ---------:| ------ |
| `rats_waitingroom.pk3` | `rats_waitingroom` | 1,020 | [GameBanana](https://gamebanana.com/mods/140169) |
| `z_map_36dm2_b1.pk3` | `36dm2_b1` | 810 | [GameBanana](https://gamebanana.com/mods/140156) |
| `scduel1a3.pk3` | `scduel1a3` | 799 | [GameBanana](https://gamebanana.com/mods/140170) |
| `map_whatsparadigmb3.pk3` | `whatsparadigmb3` | 728 | [GameBanana](https://gamebanana.com/mods/140176) |
| `map_babyimwet.pk3` | `BabyImWet` | 711 | [GameBanana](https://gamebanana.com/mods/140159) |
| `map_stwsw.pk3` | `stwsw` | 659 | [GameBanana](https://gamebanana.com/mods/140171) |
| `cmap_abandoned.pk3` | `abandoned` | 608 | [GameBanana](https://gamebanana.com/mods/140157) |

Each outer GameBanana download was checksum-verified before extraction. Every
PK3 above passes an archive integrity check, contains one BSP and no executable
module, and exposes deathmatch spawn points.

Map names come from the `.bsp` inside the archive, not the archive filename —
`map_babyimwet.pk3` loads as `map BabyImWet`. Check with:

```bash
unzip -l some_map.pk3 | grep '\.bsp'
```

Add the map name to `g_maplist` in the relevant `configs/*.cfg` so it shows up in
rotation and map votes.

## Usage

```bash
# push maps only, then restart the sessions so the pure list picks them up
pipenv run python cli.py maps -r US -t votable --restart --ssh-key ~/.ssh/id_ed25519

# or let a normal deploy carry them (runs SteamCMD too)
pipenv run python cli.py deploy -r US --ssh-key ~/.ssh/id_ed25519

# deploy without touching maps
pipenv run python cli.py deploy -r US --no-maps --ssh-key ~/.ssh/id_ed25519
```
