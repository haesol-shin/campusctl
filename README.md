<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><b>English</b> | <a href="README.ko.md">한국어</a></p>

<p align="center"><em>Check your CNU LMS lectures, assignments, notices and materials from the terminal or your AI agent.</em></p>
<p align="center"><a href="docs/installation.md">Installation</a> · <a href="docs/usage.md">Usage</a> · <a href="docs/agent-skill.md">AI skill</a> · <a href="docs/troubleshooting.md">Troubleshooting</a></p>

## Highlights

- **At a glance.** See unfinished lectures, assignments due soon and unread notices on one screen.
- **Sync once, read anytime.** After one sync, lists open without contacting the LMS.
- **The official way.** Play in the official player and save only official files, without seeking ahead or faking progress.
- **Works with AI agents.** An agent skill and JSON output let agents read results while you choose what to play.
- **Stays on your computer.** Your password stays in the OS keyring and your lists in a local folder.
- **Lightweight.** Run on demand on Windows, macOS or Linux without a background service.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) to install and run campusctl
- [Git](https://git-scm.com/downloads) to install from this repository
- Python 3.11 or newer; uv can install it for you

## Installation

Run the same commands on Windows (PowerShell), macOS or Linux:

```sh
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

Open a new terminal to put `campusctl` on `PATH`.

<details>
<summary>Linux browser system libraries</summary>

```bash
uv tool run --from playwright playwright install-deps chromium
```

This installs OS packages and may need administrator access.

</details>

For PATH fixes, browser dependencies, version pinning, updates and checkout installs, see the [installation guide](docs/installation.md).

<a id="quick-start"></a>
## Quick start

### 1. First setup

Run setup in an interactive terminal. Password entry is hidden and saved to the OS keyring; the first sync is optional.

```sh
campusctl setup
```

### 2. Sync and check

Run sync unless setup already did, then check what needs your attention.

```sh
campusctl sync
campusctl status
```

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

### 3. Details and downloads

Fetch a selected notice by full ID, or download a material by the number shown in its list.

```sh
campusctl notices fetch ID
campusctl materials download 1
```

The `1` comes from your most recent `materials list`; full IDs work without listing first. See [usage](docs/usage.md).

## Common commands

| Task | Command |
| --- | --- |
| Refresh all four lists | `campusctl sync` |
| Check coursework | `campusctl status` |
| List unfinished lectures | `campusctl lectures list` |
| Play a selected lecture | `campusctl lectures play ID` |
| List courses | `campusctl courses list` |
| List assignments | `campusctl assignments list` |
| Read a selected assignment | `campusctl assignments fetch ID` |
| List notices | `campusctl notices list` |
| Read a selected notice | `campusctl notices fetch ID` |
| List materials | `campusctl materials list` |
| Download a selected material | `campusctl materials download ID` |

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

## Limitations

- No seeking ahead, faked progress or background play: use one visible official player at a time.
- CNU has been observed to allow one LMS login session per account; don't run campusctl alongside another logged-in automation on that account.
- campusctl is unofficial and supports only the CNU LMS; changes to the LMS may break it.

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
- [Contributing](CONTRIBUTING.md)

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
