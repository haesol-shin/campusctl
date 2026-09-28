<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><b>English</b> | <a href="README.ko.md">한국어</a></p>

<p align="center"><em>Check your lectures, assignments, notices and materials from the terminal or your AI agent.</em></p>

Keep up with your lectures, assignments, notices and materials from the terminal with `campusctl`. It supports only the Chungnam National University (CNU) LMS.

## Highlights

- **Refresh your coursework.** Run `campusctl sync` to update lectures, assignments, notices and materials together.
- **See what needs attention.** Run `campusctl status` for a summary and `campusctl lectures list` for unfinished lectures.
- **Refresh only what you need.** Run `campusctl sync --only lectures` or add `--refresh` to a list command; see [usage](docs/usage.md) for other domains.
- **Read selected details.** Fetch readable packages with `campusctl assignments fetch ID` or `campusctl notices fetch ID` for one or more full IDs. Opening a notice may change its view or read state.
- **Save a material.** Run `campusctl materials download ID` to save one file to your OS Downloads folder under `campusctl/<course label>/`.
- **Play one lecture at a time.** Run `campusctl lectures play ID` in CNU's visible official player; campusctl checks the LMS state afterward.
- **Work with scripts or agents.** Run `campusctl status --json` for structured output.
- **Keep your password private.** Run `campusctl auth set` to store it in your OS keyring by default.
- **Run when you need it.** Try `campusctl --help` on Windows, macOS or Linux; no background service or resident browser is needed.

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Git](https://git-scm.com/downloads) first; campusctl requires Python 3.11 or newer.

Run this same command on Windows (PowerShell), macOS or Linux:

```sh
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

Open a new terminal, then run `campusctl setup` for guided account and browser setup, hidden password entry and an optional first sync.

On Linux, install the browser's system dependencies if needed:

```bash
uv tool run --from playwright playwright install-deps chromium
```

This may require administrator access. For PATH setup, Linux system libraries, browser cache locations, pinning, updates, uninstall, or a checkout install, see the [installation guide](docs/installation.md). If `campusctl` is not found, run `uv tool update-shell` and open a new terminal.

<a id="quick-start"></a>
## Quickstart

Run guided setup in a terminal. Password input is hidden and stored in the OS keyring by default; never pass a password as an argument. The first full sync is optional during setup.

```console
# First-time setup
$ campusctl setup
$ campusctl sync
# Check your coursework
$ campusctl status
$ campusctl lectures list
$ campusctl courses list
$ campusctl assignments list
$ campusctl notices list
$ campusctl materials list
# Details and downloads
$ campusctl assignments fetch <ASSIGNMENT_ENTITY_ID>
$ campusctl notices fetch <NOTICE_ENTITY_ID>
$ campusctl materials download 1
```

If setup already synced, you can skip the `campusctl sync` line. Replace `1` in the download command with the number shown for your chosen file by `campusctl materials list`.

This synthetic `campusctl lectures list` output was captured from the CLI at 80 columns with `CAMPUSCTL_OUTPUT=human`. Due dates use CNU local time, `-` means no due date is listed, and `opens MM-DD` marks a lecture that is not open yet:

```text
2 lectures (updated 2026-09-28 18:09 UTC)

Course: Example Course
  Welcome lecture  -    unfinished
    cnu_lecture:example-course:welcome-01

Course: Practice Course
  Later lecture  2026-10-20 23:59  opens 10-15
    cnu_lecture:practice-course:later-01

To play one: campusctl lectures play cnu_lecture:example-course:welcome-01
To refresh: campusctl sync
Catalog generated 1 seconds ago.
```

<details>
<summary>Browser and local data</summary>

The catalog and browser profile stay in campusctl's local data directory; the local browser context closes when the command ends. See the [configuration guide](docs/configuration.md) for the paths and shared-browser options.

</details>

## Use with AI agents

Install the skill globally with either command:

```sh
npx skills add https://github.com/haesol-shin/campusctl -g
# or
bunx skills add https://github.com/haesol-shin/campusctl -g
```

See [details, manual install, and a copy-paste prompt](docs/agent-skill.md).

## Why campusctl?

- See what is unfinished or due before opening each lecture.
- Refresh and inspect a local catalog without keeping a browser process running.
- Let an agent read structured results while you remain in control of which lecture to play.
- Playback outcomes follow the official LMS row. `watched (not counted)` means the displayed progress met the required duration, possibly before this play; it does not promise attendance credit.

## When shouldn't I use campusctl?

- campusctl does not seek ahead, force unsupported speeds, fake progress, or play in the background.
- Playback uses the official player, one lecture at a time in a visible browser. YouTube relies on native autoplay at 1x only; other supported media use only the playback rates their player supports.
- CNU is observed to allow one LMS login session per account; do not run campusctl alongside another logged-in automation on that account.
- campusctl is not affiliated with CNU, and changes to the LMS may break it.

## Upgrading

Run `uv tool upgrade campusctl` to update. Run `campusctl sync` to refresh all four catalogs; use `--only` to limit a refresh.

## Documentation

- [Installation](docs/installation.md)
- [Usage and commands](docs/usage.md)
- [Configuration and browser sessions](docs/configuration.md)
- [Unattended Linux and server setup](docs/configuration.md#unattended-linux)
- [Agent skill setup](docs/agent-skill.md)
- [Troubleshooting](docs/troubleshooting.md)
- [CLI contract](docs/contracts/cli.md)
- [Assignments](docs/contracts/assignments.md), [notices](docs/contracts/notices.md), and [materials](docs/contracts/materials.md) contracts

## Contributing: scan exceptions

For scan exceptions, see [Contributing](CONTRIBUTING.md#scan-exceptions).

## FAQ

#### Is campusctl official?

No. It is an independent project and is not affiliated with CNU.

#### Does it mark my attendance?

campusctl plays through the official player and reads the LMS lecture state afterward; it sends no separate progress or attendance request. What the player records is up to the LMS.

#### Where are my password and data stored?

The default password provider is your OS keyring. The catalog and browser profile stay in campusctl's local data directory; see the [configuration guide](docs/configuration.md#paths).

#### Where are downloaded materials saved?

In your OS Downloads folder under `campusctl/<course label>/` (including a redirected Windows Downloads folder). Use `campusctl materials download <ENTITY_ID> --out DIR` to choose another folder.

#### Can I read assignment or notice details here?

Yes, choose full IDs from `assignments list` or `notices list` and fetch one or more selected details. See [usage](docs/usage.md) for packages, JSON results and notice-read caveats.

## License

MIT — see [LICENSE](LICENSE).
