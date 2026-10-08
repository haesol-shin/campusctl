# Changelog

All notable changes to campusctl are documented here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Release dates follow the local date convention (KST, Asia/Seoul).

## [0.6.3] - 2026-10-08

### Fixed
- Prevent orphaned Playwright response-completion tasks from printing target-closed exceptions after browser commands finish, while preserving response failures and timeouts

## [0.6.2] - 2026-10-07

### Fixed
- Fetch unsubmitted assignments with an empty submission record while retaining strict task and course identity checks for populated responses
- Classify frame lookup timeouts and browser errors as `player-frame-unavailable` with elapsed time and guidance to close other LMS sessions for the same account before retrying
- Add fixed-token `frame_kind` to sanitized play diagnostics and bump their schema version to 2; the CLI envelope schema stays unchanged

## [0.6.1] - 2026-09-30

### Added
- Add an agent setup guide and root-help links for installing or using campusctl

### Changed
- Put skill installation and agent setup requests near the top of both READMEs
- Clarify self-install instructions and link the setup guide from the skill
- Tell agents in the skill, setup guide and root help never to run setup, config init or auth set
- Report specific play failure reason codes (`lecture-row-unavailable`, `player-frame-unavailable`, `player-video-unavailable`, `playback-stalled`, `playback-timeout`) instead of generic playback failures
- Emit one sanitized `campusctl-play-diagnostic:` JSON line on stderr for each failed lecture item

### Fixed
- Complete replay playback when this page session's covered play time reaches the duration tolerance even if the official player omits its end event
- Bounded Panopto splash play control clicks to at most twice instead of clicking on every poll

## [0.6.0] - 2026-09-29

### Added
- Korean versions of every user guide, plus a new usage guide in both languages

### Changed
- Reorganize the README around highlights, prerequisites and a three-step quick start
- Group `campusctl --help` into get started, everyday and settings commands with examples and local paths
- Start first-run `status` with the next step instead of zero counts
- Show a path-free recovery command when a catalog is missing

### Fixed
- Use singular wording for one second, one course and one lecture

## [0.5.1] - 2026-09-29

### Fixed
- Download selected archive attachments stored in the LMS
- Download PDF attachments served with the standard PDF media type

## [0.5.0] - 2026-09-28

### Added
- Fetch several explicit assignment or notice IDs in one browser session; a detail failure continues to the next ID, a session failure stops the queue, and nothing is retried
- Save materials through an optional per-course folder template, and optionally adopt a byte-identical file already stored under the course folder instead of copying it again; on Windows, enabling adoption fails safely with `output-path-conflict`
- Record which readiness, identity, to-do grid or attachment check failed, with counts and states only, in the sync profile

### Changed
- Support local headless assignment and notice fetch
- Sync and selected assignment or notice fetch wait for each page's own content and course identity instead of page-wide network idleness, and sync keeps the course archive list open after attachment inspection when it is proven unchanged
- Sync profiling reports schema 2 with wait, page and document timings

### Fixed
- Notice sync reads the to-do rows a person can reach, including grids without pagination controls
- Archive attachment controls are bound to the current post's attachment list, reloading the archive when identity cannot be verified
- A course whose page or response cannot prove an empty result keeps its previous rows as stale

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
