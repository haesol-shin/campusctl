**English** | [한국어](usage.ko.md)

# Usage

`campusctl` runs when you ask it to; there is no background service. It is independent of CNU, and LMS changes can break it. For installation and the first interactive setup, see [installation](installation.md) and [configuration](configuration.md).

## Setup and readiness

In a terminal, `campusctl setup` guides account configuration, Chromium setup, hidden password entry into the OS keyring, and an optional first sync. Noninteractive or JSON setup only checks or installs Chromium: use `campusctl config init --username ID` and `campusctl auth set` separately. `campusctl doctor` checks local readiness without logging in or opening a browser. Keep passwords out of command arguments and environment variables.

## Output modes

On a terminal, output is human-readable by default. Redirected stdout selects JSON automatically; `--json` forces JSON anywhere in the command. `CAMPUSCTL_OUTPUT=human` or `CAMPUSCTL_OUTPUT=json` overrides auto mode unless `--json` is set; other values are ignored. Configuration and setup prompts need terminal stdin and stdout with human output, while `auth set` can prompt with terminal stdin even when output is JSON.

JSON responses carry `status`, `result` and `errors` in a versioned envelope. Scripts and agents should use JSON and full IDs, not printed numbers. See the [CLI contract](contracts/cli.md) for fields, exit codes and exact precedence.

## Sync and cached lists

`campusctl sync` refreshes lectures, assignments, notices and materials in one browser session, selecting each course once. Use `campusctl sync --only lectures,notices` to narrow the domains. If a course or domain fails, its previous rows remain in the catalog and the result reports partial or stale coverage; a retained row is not a fresh check.

`status` and list commands read local catalogs without network access. Pass `--refresh` to a list command or run `campusctl sync` when you need current data. A stale warning, unknown enrollment, failed course or missing coverage means an empty or filtered list may be incomplete; check the LMS when a domain cannot be refreshed. `campusctl status` summarizes assignments due soon, unread notices and open unfinished lectures from the available local catalogs.

Before the first sync, human `campusctl status` says “No coursework has been synced yet. Next: campusctl sync” rather than reporting zero counts. If local catalog files exist but none can be read, it asks you to resync instead. Missing catalog errors name the affected domain and its sync command without showing local paths. JSON errors and exit codes remain unchanged.

## Courses, numbers and IDs

In human output, `campusctl courses list` numbers courses for `--course NUMBER`; a unique part of a course name also works. `campusctl materials list` numbers files for `campusctl materials download NUMBER`. With no ID, material download offers a single-file picker in an interactive terminal.

Numbers refer to the last printed list and fail if that catalog changes. Full IDs work without a printed list and are required with `--json`; use the exact course ID for `--course` in JSON mode. Do not guess an ID from a title.

## Lectures and playback

`campusctl lectures list` shows unfinished lectures by default; `--all` includes completed and `recorded` rows. Human due dates use CNU local time. A `-` means no due date is listed, and `opens MM-DD` means the lecture is not open yet.

### Example lecture list

This synthetic `campusctl lectures list` output was captured at 80 columns with `CAMPUSCTL_OUTPUT=human`:

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
Catalog generated 1 second ago.
```

Play only a lecture you chose by its full ID with `campusctl lectures play ID`. Playback uses the official player in a visible browser, one lecture at a time; campusctl checks the LMS row afterward and sends no separate progress or attendance request. It does not seek ahead, fake progress, force unsupported speeds or play in the background. YouTube uses native autoplay at 1x only; other supported media use only rates their player supports.

A row flagged as not counted for attendance is labeled `watched (not counted)` only when its displayed watched progress reaches the required duration; that progress may predate the current play command. This is not a promise of attendance credit. CNU has been observed to allow one LMS login session per account, so do not run campusctl alongside another logged-in automation on that account. See [configuration](configuration.md) for browser sessions.

If playback fails with `player-frame-unavailable`, another session for the same account can replace the player page. Close other LMS sessions for that account and retry. Failed items emit a sanitized `campusctl-play-diagnostic:` line on stderr (diagnostic schema version 2); its `frame_kind` is a fixed token (`panopto`, `youtube`, `lms`, `blank`, `other`, or `none`), not a URL. See the [CLI contract](contracts/cli.md#command-results) for the full reason-code and diagnostic fields.

## Assignment and notice details

`assignments list` and `notices list` show metadata. To read details, choose one or more full IDs and run `campusctl assignments fetch ID1 ID2` or `campusctl notices fetch ID1 ID2`; one ID works too. Selected details are fetched in one browser session. Fetch writes `content.md` and `package.json` under the local data directory's `sources/` tree. With one distinct ID, `--out DIR` must name a new, nonexistent package directory. JSON then reports `result.source_package` with its path, completeness and omitted resources.

With multiple distinct IDs, omit `--out`. JSON returns ordered `result.items`: each item has an `outcome`, partial or failed items have a `reason_code`, and completed or partial items have a `source_package`. Fetch supports local headed and headless Chromium and never submits assignments. Opening a notice detail may add one view and change its read state, even though fetch does not click mark-read; notice attachments are omitted, not downloaded.

Notices come from each course board. Read state is unknown when no matching to-do row exists. If a board has additional pages, `notice-board-paginated` keeps that course's previous rows; check its board in the LMS. See the [assignment](contracts/assignments.md#selected-detail-fetch) and [notice](contracts/notices.md#selected-detail-fetch) contracts.

## Materials and downloads

`campusctl materials download ID` saves one selected official attachment to your OS Downloads folder under `campusctl/<course label>/`, including a redirected Windows Downloads folder. Use `--out DIR` for another destination. Per-course download templates and optional adoption of existing files are in [configuration](configuration.md); see the [materials contract](contracts/materials.md) for selection and transfer rules.

## Browser modes

Global `--headless` and `--headed` go before the command and override `browser.headless`; headed is the default. For example, `campusctl --headless sync`, `campusctl --headless assignments fetch ID`, `campusctl --headless notices fetch ID` and `campusctl --headless materials download ID` use a local Chromium profile. CDP sessions cannot use headless mode; official-player playback remains headed.

The local catalog and browser profile stay in campusctl's private data directory. Local mode closes its browser context when the command ends; CDP mode leaves your external browser running. Browser-mutating commands share one exclusive lock. See [configuration](configuration.md) for paths, credentials and shared-browser setup.

## Profiling

Run `campusctl --profile sync` (or a list with `--refresh`) to emit one `campusctl-profile:` JSON line on stderr, schema version 2. It contains spans, document timings, lock coverage and a `diagnostics` array for failed page-readiness, course-identity, to-do-grid, archive-state or modal-binding checks. Diagnostics use run-local course ordinals instead of names or URLs; passing checks add no records. Profiling does not promise a particular speed.

## Updates and help

Run `uv tool upgrade campusctl` to update, then `campusctl setup` to check or install Chromium. Refresh catalogs with `campusctl sync`; see [installation](installation.md) for pinning, uninstall and checkout installs, and [troubleshooting](troubleshooting.md) for error remedies.

Root `campusctl --help` also links agents to the [setup guide](agent-guide.md) for installation tasks and the [campusctl skill](../skills/campusctl/SKILL.md) for usage tasks; an agent with the skill already loaded can skip that link.
