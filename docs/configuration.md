# Configuration

`campusctl` uses a local TOML file for the account name and runtime settings. By default, the password stays in your operating system keyring; do not put it in `config.toml`, command arguments, or environment variables.

## Paths

| OS | Configuration directory | Data directory |
| --- | --- | --- |
| Windows | `%APPDATA%\campusctl` | `%LOCALAPPDATA%\campusctl` |
| macOS | `~/Library/Application Support/campusctl` | `~/Library/Application Support/campusctl` |
| Linux | `$XDG_CONFIG_HOME/campusctl`, or `~/.config/campusctl` | `$XDG_DATA_HOME/campusctl`, or `~/.local/share/campusctl` |

The configuration file is `config.toml` inside the configuration directory. Set `CAMPUSCTL_CONFIG_DIR` or `CAMPUSCTL_DATA_DIR` to override the respective directory. Relative `XDG_CONFIG_HOME` and `XDG_DATA_HOME` values are ignored in favor of the platform defaults. Directories are created with POSIX mode `0700` where supported; campusctl does not configure or verify Windows ACLs.

## `config.toml`

Create the initial configuration with:

```sh
campusctl config init
```

`config init` creates `config.toml` at the resolved configuration path and never overwrites an existing file. It asks for the CNU login ID and whether to save the password (default yes) only when stdin and stdout are both terminals and human output mode is selected; password input is hidden. In that interactive flow, it also offers Chromium setup with default yes if the local managed browser is missing. Otherwise pass a login ID explicitly, for example `campusctl config init --username YOUR_LOGIN_ID --json`; omitting it for a missing file is invalid usage, exit 2. An existing file is checked first and may offer password recovery without being rewritten.

Output is human-readable by default when stdout is a terminal and JSON when stdout is redirected. `--json` always selects JSON; `CAMPUSCTL_OUTPUT=json` or `CAMPUSCTL_OUTPUT=human` can override auto mode. Other `CAMPUSCTL_OUTPUT` values are ignored. The environment override does not enable prompts: prompts still require both terminal stdin and terminal stdout.

To edit settings manually or when `config init` is unavailable, use the platform-specific paths above:

- **Windows (PowerShell):** `New-Item -ItemType Directory -Force "$env:APPDATA\campusctl"`; then `notepad "$env:APPDATA\campusctl\config.toml"`.
- **macOS:** `mkdir -p "$HOME/Library/Application Support/campusctl"`; then `${EDITOR:-vi} "$HOME/Library/Application Support/campusctl/config.toml"`.
- **Linux (default path):** `mkdir -p "$HOME/.config/campusctl"`; then `${EDITOR:-vi} "$HOME/.config/campusctl/config.toml"`.

For custom `CAMPUSCTL_CONFIG_DIR` or `XDG_CONFIG_HOME`, open the resolved path from the table. TOML strings use double quotes by default; in PowerShell, use forward slashes or single-quoted TOML literal strings for Windows paths.

Replace the username placeholder locally before saving.

```toml
provider = "cnu"

[account]
username = "<login-id>"

[credentials]
provider = "keyring" # or "command"
# command = ["/absolute/path/to/credential-helper", "--profile", "<profile>"]

[browser]
# cdp_endpoint = "http://127.0.0.1:9223/json/version"
# lock_path = "/absolute/path/to/shared-browser.lock"
# executable_path = "/absolute/path/to/chromium"

[playback]
default_speed = 1.0
```

`playback.default_speed` accepts `1.0`, `1.25`, or `1.5`. On Windows, write absolute TOML paths with forward slashes (for example, `C:/path/to/file`) or use single-quoted TOML literal strings.

## Authentication and first-run check

If `config init` reports that a password or local browser is still missing, run the corresponding command in a terminal:

```sh
campusctl auth set   # only if the password was not saved
campusctl setup     # only if the local browser is missing
campusctl doctor
```

If the configuration sets `browser.executable_path` to a browser that cannot be found, fix or remove that path in `config.toml`; `campusctl setup` does not replace a custom browser path.

`auth set` requires terminal stdin, prompts without echoing the password, and saves it to a secure OS keyring backend. It may prompt even when `--json` is selected or stdout is redirected; its response still follows the selected output mode. `setup` installs or checks the local Chromium browser and does not take the browser-session lock. Supported Windows installations use Windows Credential Manager. `doctor` is read-only: it checks local configuration, credential availability, browser/display setup, and catalog presence, but it does not log in, launch a browser, or run a credential helper. `auth status --check` checks keyring presence; with a command helper it runs that helper.

A fresh install can report `catalog-missing` with exit code 2 after all other setup checks pass. This is expected before the first sync; correct any earlier setup errors, then run:

```sh
campusctl sync --only lectures
```

`doctor` reports the first actionable remediation. See the [CLI contract](contracts/cli.md#response-envelope-and-exit-codes) for exit codes and response envelopes.

## Browser sessions and locking

Local playback uses headed Chromium with a persistent profile under `<data-dir>/profile/cnu`; campusctl closes the browser context when the command ends. Browser-mutating commands hold an exclusive, non-blocking lock for the full operation. The default lock is `<data-dir>/session.lock`.

Observed CNU behavior is one active LMS login session per account: signing in from another browser or device can end the current session. Do not run campusctl alongside another logged-in automation on the same account.

To share an already-running browser, start and authenticate it yourself and configure a CDP endpoint and a lock shared by every client that can mutate that browser session:

```toml
[browser]
cdp_endpoint = "http://127.0.0.1:9223/json/version"
lock_path = "/absolute/path/to/shared-browser.lock"
```

Use an absolute path valid on the current OS; on Windows, write a path such as `C:/path/to/shared-browser.lock`. CDP mode attaches to the external browser and never falls back to a local one. Keep the debugging endpoint local and private, and use the same exclusive lock for every cooperating client.

## Credential helpers

For unattended credentials, set `credentials.provider = "command"` and provide `credentials.command` as an argv array with an absolute executable path. campusctl launches the helper without a shell, gives it no stdin, expects a JSON object on stdout with `username` and `password`, and enforces a 30-second timeout. Keep secrets out of arguments and logs; helper output is not echoed.

For example, a helper can emit this JSON object when invoked:

```json
{"username":"<login-id>","password":"<secret>"}
```

### Windows helper

On Windows, invoke PowerShell scripts through an explicit interpreter rather than setting a `.ps1`, `.cmd`, or `.bat` file as the executable by itself. The interpreter path in the first array item must be absolute; replace the example paths with the absolute paths on your system.

```toml
[credentials]
provider = "command"
command = ["C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-File", "C:/path/helper.ps1"]
```

Each executable and argument is a separate TOML array item; paths containing spaces remain one item. For the full command-helper contract, see [credential providers](contracts/cli.md#credential-providers).

## Unattended Linux

Install Xvfb with your distribution's package manager. It provides a virtual display for the headed browser; playback still occurs in the official player and is neither skipped nor hidden. For example:

```sh
xvfb-run -a campusctl sync --only lectures --json
```

