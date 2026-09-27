<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><b>English</b> | <a href="README.ko.md">한국어</a></p>

<p align="center"><em>Keep track of your campus lectures from the terminal — or through your AI agent.</em></p>

`campusctl` is a local CLI for tracking lectures, assignments, course notices, and materials in the Chungnam National University (CNU) LMS, its only supported provider. It keeps local catalogs and opens Chromium for LMS operations.

## Highlights

- Sync lectures, assignments, notices, and materials in one combined pass: `campusctl sync` selects each course once.
- List unfinished lectures and inspect lecture health and cached coursework: `campusctl lectures list`, `campusctl status`.
- Refresh a particular domain with `sync --only DOMAIN` or `list --refresh`.
- Fetch readable source packages for one selected assignment or notice by its full ID; notice reading can change view and read state.
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

After installing, open a new terminal. Run `campusctl setup` for guided configuration, browser installation, credentials, and an optional first sync. In noninteractive or JSON mode, setup checks or installs Chromium without prompting; use `campusctl config init --username ID` and `campusctl auth set` separately.

On Linux, install the browser's system dependencies if needed:

```bash
uv tool run --from playwright playwright install-deps chromium
```

This may require administrator access. For PATH setup, Linux system libraries, browser cache locations, pinning, updates, uninstall, or a checkout install, see the [installation guide](docs/installation.md). If `campusctl` is not found, run `uv tool update-shell` and open a new terminal.

## Quickstart

Run guided setup in a terminal. Password input is hidden and stored in the OS keyring by default; never pass a password as an argument. The first full sync is optional during setup.

```console
$ campusctl setup
$ campusctl sync
$ campusctl status
$ campusctl lectures list
$ campusctl courses list
$ campusctl assignments list
$ campusctl notices list
$ campusctl assignments fetch <ASSIGNMENT_ENTITY_ID>
$ campusctl notices fetch <NOTICE_ENTITY_ID>
$ campusctl materials list
$ campusctl materials download 1
```

`sync` refreshes all four metadata domains in one browser session, selecting each course once; `--only lectures,notices` narrows the domains. A failing course or domain retains its previous catalog rows and is reported as partial, not freshly checked. Lists read local catalogs without network access unless you pass `--refresh`. Use `campusctl --profile sync` to print phase timings on stderr; these timings make no performance guarantee.

In human output, `courses list` numbers courses for `--course NUMBER`; a unique course-name fragment is also accepted. `materials list` numbers files for `materials download NUMBER`; a bare download opens a single-file picker in an interactive terminal. Numbers refer to the last printed list and fail if its catalog changes. Full IDs work without a printed list and are required with `--json`. Downloads save one selected official attachment under your OS Downloads folder at `campusctl/<course label>/`; use `--out DIR` for another directory.

Global `--headless` and `--headed` precede the command, overriding `browser.headless` in configuration; headed is the default. For example, `campusctl --headless sync` and `campusctl --headless materials download <ENTITY_ID>` use a local Chromium profile. A configured CDP session cannot use headless mode. Official-player playback remains headed.

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

Run `uv tool upgrade campusctl` to update. Run `campusctl sync` to refresh all four catalogs; use `--only` to limit a refresh.

## Documentation

- [Configuration and browser sessions](docs/configuration.md)
- [Unattended Linux and server setup](docs/configuration.md#unattended-linux)
- [Agent skill setup](docs/agent-skill.md)
- [Troubleshooting](docs/troubleshooting.md)
- [CLI contract](docs/contracts/cli.md)
- [Assignments](docs/contracts/assignments.md), [notices](docs/contracts/notices.md), and [materials](docs/contracts/materials.md) contracts

## Contributing: scan exceptions

CI scans commits and the current tree with `.gitleaks.toml` for default secret detectors and `.gitleaks-public.toml` for local paths and emails, then checks tracked files for binary content. If a safe fixture or example needs an exception, request it in a PR for review. A narrowly scoped path regex in `.gitleaks-public.toml` `[allowlist] paths` exempts that path from the public path/email rules and the binary check only; a default secret-detector finding needs its specific fingerprint in `.gitleaksignore`. Do not add private data to justify an exception.

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

Yes. Choose a full ID from `assignments list` or `notices list`, then run `campusctl assignments fetch <ENTITY_ID>` or `campusctl notices fetch <ENTITY_ID>`. Fetch writes `content.md` and `package.json` under the local data directory's `sources/` tree; `--out DIR` selects a new, nonexistent package directory. `--json` reports the path, completeness and any omitted resources. A notice detail open may add one view and change its read state, even though fetch never clicks mark-read. Notice attachments are omitted, not downloaded. Fetch requires a headed browser; it does not submit assignments. See the [assignment](docs/contracts/assignments.md#selected-detail-fetch) and [notice](docs/contracts/notices.md#selected-detail-fetch) contracts. Notices come from each course board; read state is unknown when no matching to-do row exists. A board with additional pages reports `notice-board-paginated` for that course and retains its previous rows; check the LMS for that course.

## License

MIT — see [LICENSE](LICENSE).
