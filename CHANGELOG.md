# Changelog

All notable changes to campusctl are documented here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Release dates follow the local date convention (KST, Asia/Seoul).

## [Unreleased]

### Added
- Fetch multiple explicit assignment or notice IDs in one browser session. An item failure continues the queue; a session failure stops it and does not retry.

### Changed
- Enable local headless assignment and notice fetch as an unreleased candidate. Release still requires the next live run to record success for both.
- Leave login-page origin recognition unchanged until landing and form origins and selector coexistence are observed.

## [0.4.0] - 2026-09-27

### Added
- Publish full-ID assignment and notice detail fetch commands with local readable source packages
- Report package completeness, omitted resources and selected notice-reading side effects
- Add guided setup, cached status and lecture health, numbered course selection and single-file materials download selection
- Add global headed/headless browser options for supported operations and sync profiling on stderr
- Add `lectures play --replay` to replay explicitly selected completed or recorded lectures in the official player after confirmation, with `replay_requested` and `player_opened` result fields
- Save a private diagnostic snapshot when roster discovery fails

### Changed
- Sync all four metadata domains in one browser session with one course selection per course, verifying each course's identity before publishing rows and retaining stale rows per course and domain
- Let LMS pages issue their own requests during sync and fetch while keeping selected-file binding and attachment response checks for downloads
- Open course sections through their rendered menus and name the failing domain and step in sync and fetch errors

### Fixed
- Read the global to-do before selecting a course so every notice board and archive stays reachable
- Read selected notice content from its verified info response and keep visible content after skipped HTML elements
- Keep roster diagnostic records when old-record pruning hits a transient Windows file lock

## [0.3.2] - 2026-09-26

### Changed
- Publish campusctl publicly with cleaned-up documentation and a hosted CI matrix.

## [0.3.1] - 2026-09-26

### Fixed
- `materials download` saves attachments hosted on the course content server; these previously failed with `policy-blocked` in every browser mode
- Block the LMS Panopto sign-in popup request so `sync` and `materials download` no longer fail intermittently; lecture playback is unaffected

### Security
- Reject overlapping or unrelated browser file reads during downloads

## [0.3.0] - 2026-09-26

### Added
- `sync --only assignments|notices|materials` refreshes separate local catalogs; list assignments, course-board notices, and materials with their domain commands
- `materials download <id>` saves a selected attachment to the OS Downloads folder under `campusctl/<course label>/` by default, or to `--out DIR`
- `doctor --json` lists the new capabilities

### Changed
- Guarded request policy limits new-domain browser requests to reviewed routes
- Notice read state comes from the to-do view where matched; unmatched notices have unknown read state. A board with more than 10 notices fails that course as `notice-board-paginated` and retains previous rows
- New-domain browser operations require a visible browser; assignment and notice detail text is planned for v0.4.0

## [0.2.1] - 2026-09-25

### Added
- The human list shows `opens MM-DD`; the play hint suggests only open, incomplete, playable lectures, or explains when none are open
- Every failed playback item carries a reason code

### Changed
- `lectures play` refuses lectures that are not open yet before opening a browser (`lecture-not-open`, exit 2) and names the opening date when known
- The agent skill plays only open lectures, reports how many are not open yet, and asks once before playing

## [0.2.0] - 2026-09-25

### Added
- `campusctl config init` asks for a login ID, saves the password, and offers the browser install; `campusctl setup` installs or repairs the browser
- Live playback progress and clearer `doctor` readiness
- YouTube lectures play through the official viewer's own autoplay at 1x; lectures that do not count toward attendance and show full LMS watch time are reported as `recorded` ("watched (not counted)")

### Changed
- Terminals show plain-English tables and messages; JSON is emitted with `--json`, `CAMPUSCTL_OUTPUT=json`, or when output is piped. Scripts and agents should pass `--json` or pipe output; the envelope remains at `schema_version` 1 with additive `recorded` values

## [0.1.0] - 2026-09-25

### Added
- Initial release: `doctor`, `auth set/status`, `sync`, `courses list`, `lectures list`, and `lectures play` with a versioned JSON envelope
- Play user-selected lectures in the official LMS player in a visible browser, one at a time; completion is read from the LMS without separate progress or attendance requests
- Store credentials via OS keyring by default or an argv `command` helper for unattended hosts; optionally attach with CDP and a shared lock
- Support Windows, macOS, and Linux, with Windows Python 3.11–3.13, macOS, Ubuntu, and Windows installation smoke in CI
- Agent skill installation via `npx skills add` (or `bunx`) from the public repository
