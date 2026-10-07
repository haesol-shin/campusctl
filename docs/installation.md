**English** | [한국어](installation.ko.md)

# Installation

`campusctl` supports Windows, macOS, and Linux. It requires Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and Git; uv can manage a compatible Python when one is not already available.

To let an agent do this, paste the one line from the [README AI agents section](../README.md#agent-setup).

## Prerequisites

- **Windows (PowerShell):** install uv with `winget install --id astral-sh.uv -e` and Git with `winget install --id Git.Git -e`. Alternatively, use the [official uv PowerShell installer](https://docs.astral.sh/uv/getting-started/installation/): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
- **macOS:** install uv with `curl -LsSf https://astral.sh/uv/install.sh | sh` and Git with Homebrew: `brew install git`.
- **Linux (Debian/Ubuntu):** install uv with `curl -LsSf https://astral.sh/uv/install.sh | sh` and Git with `sudo apt-get install git`; use your distribution's package manager on other Linux distributions.

Open a new shell and verify `uv --version` and `git --version` before continuing.

## Install campusctl and its browser

The unpinned Git URL installs from the repository's default branch. The install pattern matches the repository's [Windows smoke job](../.github/workflows/ci.yml#L212-L247), which also checks the uv tool command shims. `campusctl setup` is the primary way to install Chromium from the same environment as campusctl; the manual Playwright command remains a fallback.

Noninteractive or JSON `setup` only checks or installs Chromium; use `campusctl config init --username ID` and `campusctl auth set` separately when guided configuration is unavailable.

```powershell
# On Windows.
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

Open a new PowerShell window, run setup, then confirm the command resolves:

```powershell
campusctl setup
Get-Command campusctl
```

If setup cannot install Chromium, use the manual fallback:

```powershell
playwright install chromium
```

```bash
# On macOS.
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

Open a new terminal, run setup, then confirm campusctl is on `PATH`:

```bash
campusctl setup
command -v campusctl
```

If setup cannot install Chromium, use the manual fallback:

```bash
playwright install chromium
```

```bash
# On Linux.
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

Open a new terminal, run setup, and install Linux system libraries if needed:

```bash
campusctl setup
uv tool run --from playwright playwright install-deps chromium
command -v campusctl
```

If setup cannot install Chromium, install it manually with `playwright install chromium` before running `install-deps`.

`install-deps` installs OS packages and may require administrator access. Windows needs no additional system packages. Visible playback needs a logged-in desktop session on Windows and macOS; Windows `doctor` does not test that a desktop is available. On Linux, use a desktop session or Xvfb.

## PATH and browser cache

If `campusctl` or `playwright` is not found, run `uv tool update-shell`, open a new shell, and check the tool's bin directory is on `PATH`. On Windows, `Get-Command campusctl, playwright` shows the resolved commands; on macOS and Linux, use `command -v campusctl` and `command -v playwright`.

Playwright's browser cache defaults to:

| OS | Default cache |
| --- | --- |
| Windows | `%USERPROFILE%\AppData\Local\ms-playwright` |
| macOS | `~/Library/Caches/ms-playwright` |
| Linux | `~/.cache/ms-playwright` |

If you set `PLAYWRIGHT_BROWSERS_PATH`, use the same value when installing Chromium and when running campusctl.

## Pin a version

Install the pinned `v0.6.2` release with:

```bash
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl@v0.6.2
```

## Update or uninstall

Update the CLI and check or install its browser:

```bash
uv tool upgrade campusctl
campusctl setup
```

If setup cannot install Chromium, use the manual fallback `playwright install chromium`.

Remove campusctl and its `campusctl` / `playwright` command shims with:

```bash
uv tool uninstall campusctl
```

Uninstalling the tool does not remove Playwright's downloaded browser cache.

## Install from a checkout

Choose a stable parent directory for the clone. From a terminal in that parent directory:

```bash
git clone https://github.com/haesol-shin/campusctl.git
cd campusctl
uv sync
uv run campusctl setup
uv run campusctl --help
```

Run commands from that checkout as `uv run campusctl <command>` and install or update Chromium with `uv run campusctl setup`. If setup cannot install Chromium, use `uv run playwright install chromium` as the manual fallback. The browser cache and Linux system-library requirements are the same as for a uv tool install.
