# campusctl v0.4 command-line contract

`campusctl` is a local CLI for the CNU LMS. `sync` collects lectures, assignments, notices, and materials by default in one session, with one verified course selection per course. `--only` narrows the domains; list and status commands use local catalogs. Playback, download and assignment/notice detail fetch require an explicitly selected item.

For installation and browser setup, see the [installation guide](../installation.md).
Domain-specific commands and cache/error behavior: [assignments](assignments.md), [notices](notices.md), [materials](materials.md).

## Commands

```text
campusctl [--headless|--headed] [--profile] [--json] COMMAND
campusctl --version [--json]
campusctl config init [--username ID] [--json]
campusctl doctor [--json]
campusctl setup [--json]
campusctl auth set [--json]
campusctl auth status [--check] [--json]
campusctl sync [--only lectures,assignments,notices,materials] [--course ID] [--json]
campusctl status [--course ID] [--json]
campusctl courses list [--refresh] [--json]
campusctl lectures list [--all] [--course ID] [--refresh] [--json]
campusctl lectures play ENTITY_ID... [--speed 1.0|1.25|1.5] [--replay] [--json]
campusctl assignments list [--course ID] [--refresh] [--json]
campusctl assignments fetch ENTITY_ID [--out DIR] [--json]
campusctl notices list [--course ID] [--refresh] [--json]
campusctl notices fetch ENTITY_ID [--out DIR] [--json]
campusctl materials list [--course ID] [--refresh] [--json]
campusctl materials download [ENTITY_ID|NUMBER] [--out DIRECTORY] [--json]
```

Output mode is resolved before argument parsing. `--json` anywhere in the arguments always selects JSON. Otherwise, `CAMPUSCTL_OUTPUT=json` selects JSON and `CAMPUSCTL_OUTPUT=human` selects human output; any other value is ignored and falls back to auto mode. Auto mode selects human output when stdout is a terminal and JSON otherwise. Long options must be spelled exactly; abbreviations are disabled. Help (`--help`/`-h`) remains human-readable and exits with status 0. `--version` returns the version in `result.version`.

- Configuration and setup prompts require terminal stdin and stdout and human output mode. Explicit `auth set` password input requires terminal stdin even with JSON output or redirected stdout.
- `setup` guides a dual-TTY human through configuration, browser installation, hidden credential entry/check, and an optional first full sync. Noninteractive or JSON setup checks or installs local Chromium without interactive configuration.
- `auth set` prompts whenever stdin is a terminal, regardless of output mode or stdout; its response still uses the selected output mode. It saves only to a secure keyring backend. Without terminal stdin it returns `auth-tty-required`. For `credentials.provider = "command"`, it explains that the helper supplies credentials and does not save a password.
- `config init` creates a default CNU configuration at the resolved path and never overwrites an existing file. An existing file returns `user-action` code `config-exists`, exit 2, unless an interactive recovery saves missing keyring credentials.
- With interactive human output, `config init` prompts for a missing login ID, offers password saving with default yes, then offers Chromium setup with default yes when the local managed browser is missing. Password input is hidden. The final `Next:` command reflects the current state: missing password → `campusctl auth set`; missing local browser → `campusctl setup`; otherwise → `campusctl sync`. If a configured `browser.executable_path` is unavailable, setup is not offered; `Next:` explains how to fix or remove that setting in the configuration file, and `campusctl auth set` remains the final step when a password is missing. A newly created file exits 0 unless password saving or browser installation fails. Existing files can use the same password recovery without being rewritten.
- In JSON mode, `config init` suppresses prompts and never installs the browser. When the target file is missing, non-interactive use requires `--username`; otherwise the command returns invalid usage, exit 2. An existing-file check takes precedence. Login IDs must be non-empty and contain no control characters; invalid IDs return `config-invalid`, exit 2. Passwords are never accepted as arguments or environment variables.
- Noninteractive `setup` reports `result.browser` and `result.next` after checking/installing Chromium; dual-TTY human setup also reports `result.steps` for guided configuration, browser, credentials, check and optional sync. Declined required steps return `setup-incomplete`; installer failures return `browser-install-failed`. Existing invalid configuration remains unchanged.
- `auth status` reports provider, configured state, and keyring backend class or helper executable basename. It never executes a helper unless `--check` is supplied. On keyring, `--check` performs the normal read-only presence check; missing saved credentials return `user-action` code `credentials-not-configured`, preserving provider/backend/configured in the result. A keyring backend failure returns its user-action error and preserves provider with `configured: null` because password presence could not be determined. With the command provider, `--check` runs the helper and reports `ok` or `failed`; a failed check returns status `error`, exit 1, and `credential-helper-failed`, while retaining provider, configured, helper, and `check: "failed"` in the result.
- `sync` selects all four metadata domains by default, or the canonical-order comma-separated subset in `--only`. One session reads the roster and to-do, selects each course once, and collects the selected sections through their rendered menus. No attachment download, fetch or playback occurs. A failed domain/course retains its previous rows and marks them stale; other domains continue. Multi-domain JSON uses `result.domains[domain]` with each domain's `status`, `result`, and `errors`. `--profile` before sync writes phase measurements to stderr; it does not change the result. The global `--headless` and `--headed` override Boolean `browser.headless` (headed by default); local Chromium supports headless sync and material download, while CDP and playback require headed mode.
- `courses list` reads a deduplicated roster from available local domain catalogs and displays numbered course IDs in human mode; `--refresh` first syncs lectures.
- `lectures list` shows incomplete lecture rows by default; `--all` includes complete and `recorded` rows. `--refresh` first syncs lectures; `--course` uses the shared selector (human ID, last-printed number or unique name; JSON exact ID). Unknown filters return `course-not-found`. All `LV` media are listed; `video` and `youtube` are playable, `offline` and `other` are not.
- `lectures play` requires explicit full lecture IDs, validates the entire request before browser startup and runs the official player serially in a headed browser. `video` and `youtube` are playable; `offline` and `other` return `lecture-not-playable`. Closed lectures return `lecture-not-open`; completed/recorded lectures return `lecture-complete` unless `--replay` explicitly requests them. Interactive replay requires one dual-TTY confirmation (default no); JSON replay is an explicit caller attestation and does not prompt. `--speed` permits `1.0`, `1.25`, or `1.5`, subject to player support; YouTube relies on native autoplay at `1.0` only. Campusctl never seeks, fakes progress, forces a YouTube rate or captures media. Official row state determines completion or `recorded` full-watch/not-counted status, without implying attendance credit. A failed or unverified item stops the queue and leaves later items `not-started`.

Press Ctrl-C to interrupt the active playback queue. The command exits without a JSON response; completed catalog updates remain, and player, browser-session, and lock cleanup is attempted before exit.

### LMS collection

Sync opens sections through the rendered course menu and lets pages issue their own requests. Each operational sync error names its domain and failing step in human output and JSON `errors[].message`; a failed course retains its previous catalog rows. Selected attachment transfers verify the clicked file identity, response type/signature, and size before publishing bytes.

## Command results

In JSON mode, successful configuration creation has status `ok` and returns `{"config_path":"<config-path>","created":true,"next":["campusctl auth set"]}` in `result`. JSON mode does not prompt or install the browser during `config init`; the explicit `auth set` password prompt is the exception and can still prompt with terminal stdin, while keeping its response in JSON. In interactive human mode, the `Next:` command or configuration guidance reflects the current password and browser state as described above. Existing-file recovery exits 0 only if the password is saved; otherwise it returns `config-exists`, exit 2, without rewriting the file.

`doctor` returns the following fields in `result`; `display_available` is `null` outside Linux, including Windows, where doctor does not determine whether a visible browser session is available. The Playwright fields are `null` when using CDP mode:

```json
{
  "version": "0.4.0",
  "python": "<version>",
  "platform": "<platform>",
  "config": {"path": "<config-path>", "present": true},
  "data_dir": "<data-directory>",
  "provider": "cnu",
  "credentials": {"provider": "keyring", "configured": false},
  "browser": {"mode": "local", "headless": false, "headless_support": {"lectures.sync": true, "assignments.sync": true, "notices.sync": true, "materials.sync": true, "materials.download": true, "lectures.play": false}, "playwright_importable": true, "chromium_installed": true},
  "display_available": true,
  "playback": {"default_speed": 1.0, "supported_speeds": [1.0, 1.25, 1.5]},
  "catalog": {"present": false, "generated_at": null},
  "capabilities": {"sync": ["lectures", "assignments", "notices", "materials"], "lectures": ["list", "play"], "status": ["local"], "assignments": ["list", "fetch"], "materials": ["list", "download"], "notices": ["list", "fetch"]}
}
```

`auth set` returns `{"provider":"keyring","configured":true}` after storing a password. `auth status` returns provider and configured state, plus the keyring backend class or helper executable basename. Without `--check`, a keyring with no saved password returns status `ok` and `configured: false`; with `--check`, it returns `user-action` code `credentials-not-configured` with provider, backend, and configured fields in `result`. A keyring backend failure returns its `user-action` error with provider and `configured: null`, since password presence could not be determined. For the command provider, status includes the helper basename; `--check` adds `check: "ok"` or `"failed"`. `configured` records that the helper command is set, regardless of whether its check succeeds. When the helper check fails, the envelope has status `error` and exit code 1 with code `credential-helper-failed`, while retaining the `provider`, `helper`, `configured`, and `check` fields in `result`.

For `sync --only lectures`, the result returns `courses`, `lectures`, `incomplete`, `failed_courses`, and `catalog.generated_at`. For multi-domain sync, `result.domains` contains each requested domain's corresponding result and status; completed domains and failed courses are distinguishable. Counts exclude retained stale rows. Course failures produce `partial`/1 with errors naming the domain and step. Domain contracts describe their fields and errors.

`assignments fetch` and `notices fetch` each require exactly one full `entity_id` from their matching cached list; numeric selection is not supported. They open only the selected detail in a headed browser under the exclusive session lock, verify identity, and write `package.json`, `content.md` and any included resources to a source package; `--out DIR` must name a nonexistent destination. JSON returns `result.source_package` (paths, completeness, resources, omissions, provenance); human output shows source ID, package/content paths, completeness and omissions. Omissions produce `partial`/1 with `resource-omitted` and a usable package; `entity-unknown`, missing catalog, output conflict and unsupported headless mode return `user-action`/2; operational failures return `error`/1; session contention returns `busy`/75. Notice detail opening can add one view and may flip read state, though fetch never clicks mark-read. Notice attachments are omitted rather than transferred. See the [assignment](assignments.md#selected-detail-fetch) and [notice](notices.md#selected-detail-fetch) contracts for layout and omissions.

`courses list` returns the cache metadata and course records:

```json
{"cache":{"generated_at":"<UTC timestamp>","path_present":true},"courses":[{"course_id":"<course-id>","label":"<course-label>","class_no":null}]}
```

`lectures list` returns the same cache metadata and filtered lecture records:

```json
{"cache":{"generated_at":"<UTC timestamp>","path_present":true},"lectures":[]}
```

`lectures play` returns `items` in first-seen order after catalog validation and browser startup. Each item includes `entity_id`, `outcome` (`completed`, `recorded`, `unverified`, `already-complete`, `failed`, or `not-started`), `elapsed_seconds`, `watch_time`, `provider_state`, `replay_requested` and `player_opened`. `recorded` means the official row shows full watched progress but no attendance credit; it may predate this invocation. Replay does not count as a played success unless its official player opened. Failures before browser startup use an ordinary error envelope without item records.

```json
{"items":[{"entity_id":"cnu_lecture:<course-id>:<row-id>","outcome":"completed","elapsed_seconds":42.0,"watch_time":"00:00:42","provider_state":"F"}]}
```

Before browser-session startup, catalog validation rejects a selected row with `open: false` as `lecture-not-open` (`user-action`, exit 2); `open: null` does not reject. The complete request is validated before any item starts, so a closed row prevents playback of every ID in a multi-ID request. When `available_from` is present, the error message includes the opening date.

Every item with outcome `failed` includes a `reason_code` matching the CLI error code for that failure.

## Browser and session

Local mode uses headed Chromium by default with a persistent profile at `<data-dir>/profile/cnu`; `browser.headless` or the global CLI override selects headless only for supported operations. `browser.executable_path` can select a local Chromium binary. CDP mode uses `browser.cdp_endpoint` and never silently falls back to local mode; headless with CDP returns `headless-unavailable`. An unreachable endpoint returns `browser-endpoint-unreachable`.

Browser-mutating commands hold one non-blocking exclusive lock for the whole operation. Its default path is `<data-dir>/session.lock`; `browser.lock_path` can override it. If another process holds the lock, the command returns `busy` with exit code 75.

Local mode closes its persistent browser context after the command. CDP mode leaves the external browser and its contexts open, closing only pages opened by campusctl.

### CNU login and course discovery

Course discovery reuses an authenticated LMS landing page without reading credentials. It reads credentials from the configured provider only after confirming the visible login form, then submits them through the normal LMS form. Credential values and raw login responses are never included in errors.

- `login-failed` means the LMS rejected the configured credentials. It is a `user-action` error; `auth status --check` only checks keyring presence or command-helper execution, not LMS authentication. Correct the stored account credentials, then retry.
- `login-action-required` means the LMS requires terms acceptance or a password change. It is a `user-action` error.
  - For terms, log in once in a normal browser, accept them, then retry.
  - For a password change, change or confirm the password in a normal browser, update the stored credentials through the configured provider, then retry.
- `lms-unavailable` means the LMS navigation, login response, or landing page did not complete within its bound. It is an `error`; check the LMS session and network connection, then retry.

If sync login or course discovery fails, `sync` aborts before reading or writing the catalog. If play login fails after ID validation, `lectures play` reports every deduplicated ID as `not-started`; no player is opened. Catalog, lock, or browser-session failures before the browser session opens produce no play item records.

## Response envelope and exit codes

In JSON mode, every command response is one JSON object encoded as UTF-8 on stdout, including when stdout is redirected to a pipe:

```json
{"schema_version":1,"tool":"campusctl","tool_version":"0.4.0","status":"ok","result":{},"errors":[],"generated_at":"2026-01-02T03:04:05Z"}
```

`generated_at` is a UTC RFC3339 timestamp ending in `Z`. `status` is `ok`, `partial`, `user-action`, `busy`, or `error`. Each error has `code`, safe `message`, and nullable `remediation` fields.

| Status | Exit code | Meaning |
| --- | ---: | --- |
| `ok` | 0 | Command completed. |
| `partial` | 1 | Requested work failed or could not be verified; completed work is reported. |
| `error` | 1 | An operational failure occurred. |
| `user-action` | 2 | Configuration, setup, or user input requires action. |
| invalid usage | 2 | Arguments are invalid; the envelope uses `user-action`. |
| `busy` | 75 | Another process owns the exclusive browser session lock. |

In JSON mode, unknown exceptions are returned with code `internal` and their exception class name only. Human mode renders the generic error without a traceback. Raw exception messages are never returned.

### Error codes

- `usage-error`, `not-implemented`, `unsupported-domain`, `course-not-found`, `course-discovery-failed`, `course-sync-failed`, `item-identity-missing`, `notice-identity-ambiguous`, `notice-board-paginated`, `selection-missing`, `selection-stale`, `selection-invalid`, `course-index-unavailable`, `course-id-required`
- `config-missing`, `config-invalid`, `config-exists`
- `auth-tty-required`, `auth-command-provider`, `auth-empty-password`
- `credentials-username-missing`, `credentials-not-configured`
- `credential-backend-insecure`, `credential-backend-unavailable`
- `credential-helper-invalid`, `credential-helper-failed`
- `lock-unavailable`, `session-busy`
- `catalog-missing`, `catalog-invalid`, `catalog-schema-unsupported`, `catalog-outdated`, `catalog-write-failed`
- `browser-not-installed` remediation is `Run 'campusctl setup' to install the browser.`; `browser-install-failed` reports installer failure with status `error`, exit 1.
- `browser-endpoint-unreachable`, `lecture-unknown`, `lecture-not-open`, `lecture-complete`, `lecture-unsupported`, `lecture-not-playable`, `login-failed`, `login-action-required`, `lms-unavailable`, `playback-failed`, `playback-unverified`, `youtube-autoplay-blocked`, `playback-speed-unavailable`
- `headless-unavailable`, `entity-unknown`, `resource-omitted`, `fetch-failed`, `unsupported-media-type`, `file-too-large`, `download-failed`, `output-path-conflict`; `policy-blocked` applies only to rejected selected-file transfers, not page requests
- `internal`

## Paths and configuration

The configuration file is `config.toml` inside the platform config directory. `CAMPUSCTL_CONFIG_DIR` overrides the directory; otherwise campusctl uses `$XDG_CONFIG_HOME/campusctl` or `~/.config/campusctl` on Linux, `%APPDATA%/campusctl` on Windows, and `~/Library/Application Support/campusctl` on macOS. Data lives in `CAMPUSCTL_DATA_DIR` when set, otherwise the platform data directory (`$XDG_DATA_HOME/campusctl` or `~/.local/share/campusctl` on Linux, `%LOCALAPPDATA%/campusctl` on Windows, and the application-support directory on macOS). Relative XDG paths are ignored in favor of platform defaults. Application-owned directories are created with POSIX mode `0700` where supported; campusctl does not configure or verify Windows ACLs.

Example configuration (replace placeholders locally; do not commit credentials):

```toml
provider = "cnu"

[account]
username = "<login-id>"

[credentials]
provider = "keyring" # or "command"
# command = ["<absolute-helper-path>", "--profile", "<profile>"]

[browser]
# cdp_endpoint = "https://browser.invalid/json/version"
# lock_path = "<absolute-lock-path>"
# executable_path = "<absolute-chromium-path>"

[playback]
default_speed = 1.0
```

Missing configuration is a `user-action` error whose remediation directs the user to `campusctl config init` and names the expected config path. An existing file is never overwritten; unless an interactive recovery saves missing keyring credentials, `config init` returns `user-action` code `config-exists` with remediation naming that path. Invalid-value messages identify the key and path, not the untrusted value. `playback.default_speed` accepts `1.0`, `1.25`, or `1.5`.

## Credential providers

### Keyring

The service name is `campusctl:cnu`; the account key is `account.username`. For keyring configurations, `auth set` prompts whenever stdin is a terminal, regardless of output mode or stdout; otherwise it returns `auth-tty-required`. It uses a non-echoing password prompt. Backends identified as fail, null, plaintext, `keyrings.alt` file storage, or with priority below 1 are rejected. The Windows `WinVaultKeyring` backend (Windows Credential Manager) is accepted when available. A `ChainerBackend` is rejected if any child backend fails these checks.

### Command helper

Set `credentials.provider = "command"` and provide a non-empty argv list containing the executable and optional arguments. The command is launched without shell mode, with stdin connected to the platform's null device, in an isolated process group/session, and with a 30-second timeout. stdout is capped at 64 KiB and stderr at 4 KiB; timeout or output overflow triggers forced termination of the helper tree. On Windows, campusctl creates a process group and a kill-on-close job. If job setup fails after process launch, campusctl requests helper termination and reports an error rather than accepting its output. POSIX helpers run in a new session and the process group is killed on timeout. Reader threads share the 30-second deadline, so a descendant keeping stdout or stderr open cannot wait indefinitely. Cleanup waits up to two one-second intervals for the direct process and joins each output reader for at most one second. POSIX descendants that deliberately leave the process group may outlive campusctl.

```json
{"username":"<login-id>","password":"<secret>"}
```

The username must match `account.username` when configured. Without a configured username, the helper's username is used. When a sync or play command needs credentials, launch failure, nonzero status, timeout, output-limit overflow, malformed JSON, missing or empty fields, or username mismatch is reported as `credential-helper-failed`; only safe status information is exposed. stdout/stderr from the helper is never echoed. `auth status --check` is the only status command that runs it.

On Windows, `.ps1`, `.cmd`, and `.bat` files must be invoked through an explicit interpreter, and the interpreter path must be absolute. For example, `credentials.command = ["C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-File", "C:/Program Files/campusctl/helper.ps1"]` or `credentials.command = ["C:/Windows/System32/cmd.exe", "/d", "/c", "C:/Program Files/campusctl/helper.cmd"]`. Keep each executable and argument as a separate array item; paths containing spaces stay a single item. campusctl does not enable shell mode automatically.

## Catalog records

The catalog is `<data-dir>/catalog/lectures.json` and has this top-level form:

```json
{"schema_version":1,"generated_at":"<UTC timestamp>","courses":[],"lectures":[]}
```

Within the version-1 JSON envelope, lecture `completion` and `lectures play` item `outcome` values may be extended without a schema-version bump. Consumers must treat unrecognized values for either field as not complete and must not infer completion from an unrecognized playback outcome.

Course record:

```json
{"course_id":"<course-id>","label":"<course-label>","class_no":null}
```

Course IDs, labels, and non-null `class_no` values are strings.

Lecture record:

```json
{"entity_id":"cnu_lecture:<course-id>:<row-id>","course":{"id":"<course-id>","label":"<course-label>"},"kind":"lecture","title":"<title>","week":null,"sequence":null,"progress_text":null,"duration_minutes":null,"available_from":null,"due_date":null,"late_until":null,"media":"video","attendance_counted":null,"open":null,"completion":"incomplete","provider_state":null}
```

`progress_text` is the full displayed progress, and `duration_minutes` is the integer total after `/` or `null`. Watched and required minute/second counters are parsed from that displayed text; malformed or missing counters are not considered full. `available_from`, `due_date`, and `late_until` are ISO-8601 local strings without a timezone; `available_from` falls back to the date-only `data-strdt` value when the period is absent. `media` maps the `동영상`, `유튜브`, and `오프라인` badges to `video`, `youtube`, and `offline`; a missing or unrecognized badge is `other`. `attendance_counted` is `false` when the row says `출석 미반영`, otherwise `null`. `week` and `sequence` retain their CNU text values or are `null`; `open` maps `Y`/`N` to `true`/`false`, otherwise `null`. `provider_state` is the CNU state text or `null`; `completion` is `complete` for state `F`, `recorded` when state is not `F`, attendance is not counted, and displayed watched progress reaches the required duration, and `incomplete` otherwise. All `LV` rows are cataloged; only `video` and `youtube` are playable.

Nullable fields remain JSON `null`. Rows with empty title text use the CNU fallback title `강의영상`; `kind` is `lecture`. Entity IDs retain the stable `cnu_lecture:<course_id>:<row_id>` format. Unknown schema versions require rebuilding with `campusctl sync --only lectures`. Sync writes the catalog under the session lock using a sibling temporary file, fsync, and atomic replacement; readers do not take the lock.

Catalog merge behavior is scoped to the sync request. A `--course` sync replaces only that course's records and preserves every other course. A full sync with per-course failures keeps previous records for failed courses and does not drop previously cataloged courses absent from discovery. Only a full, non-filtered sync with no course failures drops records for courses no longer enrolled. A successfully scraped course with no lecture rows replaces its previous lecture records with an empty set.

An absent catalog is a `user-action` error with remediation `campusctl sync --only lectures`.

## Redaction and local security

Never place passwords in environment variables, arguments, TOML, source control, logs, or error responses. Never expose cookies, tokens, CDP/WebSocket URLs, helper output, raw exception text, or secrets in envelopes. Doctor does not log in, run helpers, or launch the browser. Directories are created with POSIX mode `0700` where supported; campusctl does not configure or verify Windows ACLs. The catalog is local state. The browser session lock is non-blocking and held by browser-mutating commands only.
