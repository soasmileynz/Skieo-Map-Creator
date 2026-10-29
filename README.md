# Ski-E-O Map Creator

A standalone Windows app that turns any real-world mountain into a playable map for **Ski-E-O!**. Pick a square on
the map, and it downloads the real elevation, builds a complete map (terrain, forests, snow, preview, starting
base) and installs it into the game. It needs no Unreal Engine, and no game files are included: it reads what it
needs from the player's own installed copy of the game.

Fan-made; not affiliated with or endorsed by the developers of Ski-E-O!.

## Download

Get `SkiEO-Map-Creator.exe` from [Releases](../../releases/latest). It's one file, no install. Windows may warn
because the exe is unsigned: click **More info**, then **Run anyway**. Changes: [CHANGELOG.md](CHANGELOG.md).

Needs Windows 10/11 and Ski-E-O! installed through Steam.

## Verify a download

Every release exe is built by GitHub Actions from the tagged source (`.github/workflows/release.yml`); nothing is
built or uploaded by hand. Three ways to check one:

1. **SHA-256**: compare with the hash on the release page and in its `SHA256SUMS.txt`:
   `certutil -hashfile SkiEO-Map-Creator.exe SHA256`
2. **Build attestation** (signed proof of which workflow run and commit built it), with the
   [GitHub CLI](https://cli.github.com): `gh attestation verify SkiEO-Map-Creator.exe --repo oofbyte/Skieo-Map-Creator`
3. **Compare with the source yourself**: check out the release tag, then
   ```
   py -3.12 -m pip install -r requirements.txt
   py -3.12 packaging\verify_exe.py SkiEO-Map-Creator.exe
   ```
   It opens the exe and checks every bundled file byte for byte and every app module as compiled code against the
   source, and lists everything else inside (Python and the libraries in `requirements.txt`).

What the app connects to: map tiles (OpenStreetMap, OpenTopoMap, Esri World Imagery), place search (Photon,
Nominatim) and elevation data (USGS 3DEP, AWS Terrain Tiles). retoc downloads the Oodle compression library on first
use. No accounts, no telemetry. It writes to `%LOCALAPPDATA%\SkiEO Map Creator` and the game's `Content\Paks\~mods`
folder, never to saves.

## How it works

1. **Elevation**: USGS 3DEP 1/3 arc-second (~10 m) inside the US, AWS Terrain Tiles (~30 m) elsewhere
   (`dem.py`). It is resampled onto the game's 2017 x 2017 landscape grid on a local transverse Mercator projection
   (`geo.py`).
2. **Game data**: `gamedata.py` uses retoc to extract a donor map (Mad River Glen) and a few assets from the
   player's game into a cache, and dry-runs every writer on them before the first build.
3. **Build** (`builder.py`): the donor map is copied, and these parts are rewritten:
   - **Heights and scale:** landscape heights and all their mip levels (made the way Unreal makes them), edge-normal
     rings and grid scale (`engine/terrain_write.py`, `engine/provider_write.py`); component bounds and collision;
     the map frame (size, height scale); and the edge wall, whose edge the terrain is fitted to (`engine/map_frame.py`).
   - **Forest:** in the game's baked-obstacle format, with the biome's own tree species (`trees.py`, `biomes.py`).
   - **Snow piles:** written from a cooked template mesh (`shells.py`, `data/shell_template.*`).
   - **Everything else:** terrain material overrides (`engine/material_params.py`), the height texture, the map
     preview, the starting save (ticket office on the base area), and the map list entry. Every renamed string keeps
     its length.
4. **Pack and install**: retoc `to-zen` packs a `ZZZ_MC_<name>_P` pak. `install.py` adds the map to the game's
   map list (`ZZZ_Registry_P`) and copies it into `Content/Paks/~mods`.

The window is a local web UI (`web/`: Leaflet map, three.js 3D preview) shown by pywebview / WebView2. `server.py`
serves it on 127.0.0.1, with a token-protected tile proxy on its own port.

## Map styles and units

- **Topo**, **Streets** and **Satellite** map styles. Topo and Satellite can also cover the terrain in the 3D view.
  Satellite uses Esri World Imagery tiles, credited in the app. Esri has no open tile usage policy like
  OpenStreetMap's and its terms favour ArcGIS accounts, so if that matters for your use, change the source in
  `LAYERS` in `server.py`.
- The **ft | m** switch in the "Choose the area" header sets the units for heights, distances and snowfall.

## Snowfall estimate

Under the Snowfall slider the app shows about how much snow a season brings: the biome's average annual snowfall
times the multiplier. It is a rough guide, as real seasons vary with elevation and weather.

## Run from source

Windows 10/11, Python 3.12, Ski-E-O! installed through Steam.

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

## Build the exe

```
packaging\build.bat
```

This makes `dist\SkiEO-Map-Creator.exe`, a single file that bundles `web/`, `data/` and `tools/retoc.exe`.

## Layout

| Path | What |
|---|---|
| `skieo_creator/` | app: `main.py` (window), `api.py` (calls from the page), `builder.py`, `trees.py`, `shells.py`, `dem.py`, `install.py` ... |
| `skieo_creator/engine/` | writers for the cooked map data (heightmaps, providers, bounds, collision, frame, headers, materials) |
| `web/` | the UI (HTML / CSS / JS) and vendored Leaflet + three.js |
| `data/` | the cooked snow-shell template |
| `tools/` | retoc.exe (MIT, https://github.com/trumank/retoc) |
| `packaging/` | PyInstaller spec, build script, icon, version info, credits (`THIRD-PARTY.txt`), `verify_exe.py` |
| `tests/ui_drive.py` | scripted UI sessions with screenshots (`search`, `3d`, `3d-offline`, `numbox`) |
| `.github/workflows/release.yml` | builds, checks, hashes and attests the exe and attaches it to each release |

## Releasing

1. Bump `VERSION` in `skieo_creator/__init__.py` and the versions in `packaging/version_info.txt`, and add a
   `## vX.Y.Z` section to `CHANGELOG.md`.
2. On GitHub: **Releases → Draft a new release**, create the tag `vX.Y.Z`, and publish. The description can stay
   empty.
3. The workflow builds the exe from that tag, checks it against the source, and attaches it and `SHA256SUMS.txt` to
   the release, with the notes from `CHANGELOG.md`. It stops if the tag doesn't match the versions in step 1.

## License

MIT, see [LICENSE](LICENSE). Bundled third-party software keeps its own license; credits for the libraries, fonts and
data sources are in `packaging/THIRD-PARTY.txt`.
