<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><b>English</b> | <a href="README.ko.md">한국어</a></p>

<p align="center"><em>Keep track of your campus lectures from the terminal — or through your AI agent.</em></p>

`campusctl` is a local CLI for tracking lectures, assignments, course notices, and materials in the Chungnam National University (CNU) LMS, its only supported provider. It keeps local catalogs and opens a visible browser for LMS operations.

## Highlights

- Sync enrolled courses and lecture rows with one command: `campusctl sync`.
- List unfinished lectures with status and deadlines: `campusctl lectures list`.
- Check assignment deadlines and submission state, course-board notices, and downloadable materials after syncing each domain.
- Download one selected material into your OS Downloads folder under `campusctl/<course label>/`.
- Play a lecture in CNU's official player through a visible browser, one at a time; campusctl checks the LMS state afterward.
- JSON output for scripts and agents.
- Keep the password in the operating system keyring by default.
- Run on Windows, macOS, or Linux without a background service or resident browser.

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Git](https://git-scm.com/downloads) first; campusctl requires Python 3.11 or newer.

### Windows (PowerShell)

```powershell
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

### macOS

```bash
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

### Linux

```bash
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

After installing, open a new terminal. Run `campusctl setup` to install or check Chromium. If no browser is installed, setup asks before downloading it: “campusctl needs a Chromium browser for playback and sync. Download and install it now? [Y/n]”

On Linux, install the browser's system dependencies if needed:

```bash
uv tool run --from playwright playwright install-deps chromium
```

This may require administrator access. For PATH setup, Linux system libraries, browser cache locations, pinning, updates, uninstall, or a checkout install, see the [installation guide](docs/installation.md). If `campusctl` is not found, run `uv tool update-shell` and open a new terminal.

## Quickstart

The CLI prompts are in English. For a new configuration, run `campusctl config init` in a terminal. It asks for your CNU login ID (`CNU login ID:`), then whether to save the password (`Save your password now? [Y/n]`, default yes). If you agree, enter it at `LMS password:`; input is hidden. If you decline, run `campusctl auth set` yourself before syncing. If Chromium is missing, it asks `campusctl needs a Chromium browser for playback and sync. Download and install it now? [Y/n]` (default yes); if you decline, run `campusctl setup` before syncing.

```console
$ campusctl config init
$ campusctl sync
$ campusctl lectures list
```

To check another domain, sync it first, then list its cached records. Copy a material's full ID from the list to download just that file:

```console
$ campusctl sync --only assignments
$ campusctl assignments list
$ campusctl sync --only notices
$ campusctl notices list
$ campusctl sync --only materials
$ campusctl materials list
$ campusctl materials download <ENTITY_ID>
```

Downloaded files go to your OS Downloads folder under `campusctl/<course label>/`; use `--out DIR` to choose another folder. Assignment, notice, and material sync still need a visible browser. `materials download <ENTITY_ID> --headless` works with a local Chromium profile; CDP browser sessions cannot use `--headless`.

Add `--json` when scripts or agents need JSON output; terminal output is human-readable by default.

The sample below was captured from the CLI with an English fake catalog at 80 columns and `CAMPUSCTL_OUTPUT=human`. Due dates use CNU local time, `-` means no due date is listed, and `opens MM-DD` marks a lecture that is not open yet:

```text
2 lectures (updated 2026-09-24 12:00 UTC)

Course: Introduction to Biology
  Cell Structure  2026-10-15 23:59  opens 10-15
    cnu_lecture:biology-101:lecture-01

Course: World History
  The Roman Republic  -    unfinished
    cnu_lecture:history-201:lecture-07

To play one: campusctl lectures play cnu_lecture:history-201:lecture-07
To refresh: campusctl sync
```

<details>
<summary>Browser and local data</summary>

The catalog and browser profile stay in campusctl's local data directory; the local browser context closes when the command ends. See the [configuration guide](docs/configuration.md) for the paths and shared-browser options.

</details>

## Use with AI agents

`npx skills add https://github.com/haesol-shin/campusctl -g` or `bunx skills add https://github.com/haesol-shin/campusctl -g` ([details, manual install, and a copy-paste prompt](docs/agent-skill.md)).

## Why campusctl?

- See what is unfinished or due before opening each lecture.
- Refresh and inspect a local catalog without keeping a browser process running.
- Let an agent read structured results while you remain in control of which lecture to play.
- Playback outcomes follow the official LMS row. A row flagged as not counted for attendance is labeled “watched (not counted)” only when its displayed watched progress reaches the required duration; this status may reflect progress recorded before the current play command.

## When shouldn't I use campusctl?

- campusctl does not seek ahead, force unsupported speeds, fake progress, or play in the background.
- Playback uses the official player, one lecture at a time in a visible browser. YouTube relies on native autoplay at 1x only; other supported media use only the playback rates their player supports.
- CNU is observed to allow one LMS login session per account; do not run campusctl alongside another logged-in automation on that account.
- campusctl is not affiliated with CNU, and changes to the LMS may break it.

## Upgrading

From v0.2.1, run `uv tool upgrade campusctl` to get the new domain commands; refresh each domain with `sync --only` before listing. From v0.1, terminal output is human-readable by default; add `--json` for scripts and agents.

## Documentation

- [Configuration and browser sessions](docs/configuration.md)
- [Unattended Linux and server setup](docs/configuration.md#unattended-linux)
- [Agent skill setup](docs/agent-skill.md)
- [Troubleshooting](docs/troubleshooting.md)
- [CLI contract](docs/contracts/cli.md)
- [Assignments](docs/contracts/assignments.md), [notices](docs/contracts/notices.md), and [materials](docs/contracts/materials.md) contracts

## Contributing: scan exceptions

CI scans commits and the current tree with `.gitleaks.toml` for default secret detectors and `.gitleaks-public.toml` for local paths and emails, then checks tracked files for binary content. If a safe fixture or example needs an exception, request it in a PR for review: use a narrowly scoped path regex in `.gitleaks-public.toml` `[allowlist] paths` (also used by the binary check), or a specific finding fingerprint in `.gitleaksignore` for a text finding. Do not add private data to justify an exception.

## FAQ

#### Is campusctl official?

No. It is an independent project and is not affiliated with CNU.

#### Does it mark my attendance?

campusctl plays through the official player and reads the LMS lecture state afterward; it sends no separate progress or attendance request. What the player records is up to the LMS.

#### Where are my password and data stored?

The default password provider is your OS keyring. The catalog and browser profile stay in campusctl's local data directory; see the [configuration guide](docs/configuration.md#paths).

#### Where are downloaded materials saved?

In your OS Downloads folder under `campusctl/<course label>/` (including a redirected Windows Downloads folder). Use `materials download <ENTITY_ID> --out DIR` to choose another folder.

#### Can I read assignment or notice details here?

Not yet. v0.3 lists metadata, not detail text; detail reading is planned for v0.4.0. Notices come from each course board; read state is shown only when the to-do view has a match, otherwise it is unknown. If a course has more than 10 notices, notice sync reports `notice-board-paginated` for that course and retains its previous cached rows. Check the LMS for that course.

## License

MIT — see [LICENSE](LICENSE).
