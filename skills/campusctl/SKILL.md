---
name: campusctl
description: "Use campusctl's documented JSON CLI to check lectures, assignments, course notices, and materials, or play or download only user-selected items."
---

# Campusctl

Translate the user's intent into documented `campusctl` CLI calls. Follow the [public CLI contract](https://github.com/haesol-shin/campusctl/blob/main/docs/contracts/cli.md) and linked domain contracts. Present results without interpreting course content or making decisions for the user.

## Use this skill for

- Checking remaining or incomplete lectures: “What lectures are left?” / “남은 강의 보여줘.”
- Refreshing the lecture catalog: “Refresh the lecture list.” / “강의 목록 새로고침해 줘.”
- Listing courses: “Show my courses.” / “수강 과목 목록 보여줘.”
- Playing lectures the user selected: “Play the lecture I selected.” / “선택한 강의를 재생해 줘.”
- Listing assignment status, course notices, or materials: “Show my assignments.” / “과제 목록 보여줘.”
- Downloading one material the user selected from the list.

## Procedure

1. Before any other command, run `campusctl --version --json`. Require version `0.4.0` or later; otherwise tell the user to run `uv tool upgrade campusctl` and stop.
2. Run `campusctl doctor --json` to check readiness. If configuration is missing, ask only for the login ID, run `campusctl config init --username <ID> --json`, and tell the user to run `campusctl auth set` themselves in a terminal. Never ask for, receive, or handle the password. `campusctl setup` guides an interactive user through setup; `setup --json` only checks/installs Chromium and requires agreement before a download.
3. Run `campusctl sync --json` only when fresh data is requested or a catalog is missing; bare sync refreshes lectures, assignments, notices, and materials in one combined pass with one course selection per course. Use `--only DOMAIN` (or a comma-separated subset) when the user wants only specific domains, and `--course ID` only for an identified course. Cached lists and `status` need no network. `--profile` is a global option for sync timing on stderr, not a performance promise. `--headless` and `--headed` are global options preceding the command; headed is the default, and local headless supports sync and material download, not CDP or playback. Report each partial domain/course failure and retained stale rows honestly.
4. To list courses, run `campusctl courses list --json`; for an overview or lecture health, run `campusctl status --json`. Match course names to returned `course_id` values; ask if multiple records match. Human printed course numbers are selection aliases bound to that printed catalog, but use full IDs for all agent JSON calls. To list lectures, run `campusctl lectures list --json`; incomplete lectures are the default. Add `--all` when completed or recorded rows are requested, and `--course ID` only for a course the user identified or selected. Rows marked `recorded` do not imply attendance credit or LMS completion. Use the latest list response for playback.
   For assignment, notice, or material questions, read `campusctl assignments list --json`, `campusctl notices list --json`, or `campusctl materials list --json` after any requested sync (or immediately when a cached list is requested). Use `--course ID` only for a course identified or selected by the user. `result.cache.enrollment_state: "unknown"` and `result.cache.failed_courses` indicate potentially stale records, even for empty filtered lists; report that uncertainty. `is_unread: null` for a notice means read state is unknown, not read. `notice-board-paginated` means that course's board exceeds the supported first page (more than 10 notices); report the affected course and ask the user to check its board in the LMS, not that its list is complete.

   For a material download, resolve one user-selected full `entity_id` from `campusctl materials list --json`, ask if the selection is ambiguous, then run `campusctl materials download <ENTITY_ID> --json` (with `--out DIR` only if the user chose a directory). Human terminal users may use a number from their last printed materials list or the single-file picker; agents must pass full IDs. Report `result.material.path` and outcome; the default is the OS Downloads folder under `campusctl/<course label>/`. Never bulk-download or select files on the user's behalf.
5. For deadline questions, filter `due_date`; for release questions, filter `available_from`. Interpret relative calendar periods such as “this week” using ISO calendar-week boundaries in CNU local time (Monday 00:00 inclusive to the following Monday 00:00 exclusive), not the agent's host timezone. State the exact date/time boundaries and inclusivity. Treat null dates as unknown. Ask only if the requested period is non-calendar or remains ambiguous.
6. Before any playback request, run `campusctl lectures list [--course ID] --json`; include `--course ID` only when the user identified or selected that course. If listing fails, report it and do not play. Use only this latest response to resolve the user's choice and define the scope of any explicit “play all” request.
   - A prior clear choice or a unique title the user specifically asked to play is explicit; a vague request is not. If multiple records match, show candidates and ask which to play. If the requested record is absent, use `campusctl lectures list --all --json` to check whether it is already complete or recorded; report that status and do not play it. Otherwise, ask the user to identify a listed lecture.
   - For a single selection, resolve the user's choice against the latest list and use only its `entity_id`. `video` and `youtube` are playable; `offline` and `other` are rejected as `lecture-not-playable`, so do not send those IDs to play. YouTube uses native autoplay only at `1.0`. If the user requests another YouTube speed, explain that it is unavailable and do not play. When a selected set contains YouTube and no unsupported speed was requested, pass `--speed 1.0` for the invocation (including mixed queues) so a non-1.0 configured default cannot prevent native autoplay. For a playable explicit selection, run `campusctl lectures play <ENTITY_ID> --json`; include `--speed 1.0` when its media is YouTube, or pass a requested supported speed for other media.
   - Treat an explicit “play all/everything” request as selecting the finite set of incomplete rows from the latest `lectures list`; a request just to list or ask what remains is not permission to play. Deduplicate `entity_id` values in first-seen order. Select only rows with `open: true`, `completion: incomplete`, playable media (`video` or `youtube`), and a non-empty `entity_id`; never select `open: null`. Report how many incomplete rows have `open: false` and their earliest `available_from` date; do not call unknown availability “not open.” Tell the user why any `offline` or `other` rows were excluded. If no eligible open rows remain, do not call play. Otherwise, show the selected count and titles, then ask exactly once: “Play these N lectures now?” Invoke only after a clear yes, using exactly those IDs. If any selected row is YouTube and the user requested a speed other than `1.0`, explain the restriction and do not invoke any lecture in the queue, including non-YouTube selections. Otherwise, if any selected row is YouTube, pass `--speed 1.0 --json`; for other media, pass a requested supported speed or omit `--speed`.
   - If a play request returns `lecture-not-open`, do not retry or replace the selection; report the opening date in its message and tell the user to retry after it opens.
7. Campusctl validates every requested ID before opening the browser and processes playback serially through the official player; local mode uses a visible browser. Report each playback outcome. `recorded` describes the LMS row's full-watch/no-attendance status, not credit. `unverified` and `failed` stop the queue and leave later IDs `not-started`; report each failure's `reason_code` without automatically resuming. Replay of completed/recorded items requires explicit user intent, `--replay` and full IDs; do not infer consent from a request to list or play unfinished lectures. JSON replay attests that intent without a prompt.

## Result handling

Every command run by this skill includes `--json`. Read `status`, `result`, and `errors`; relay each error's `code`, safe `message`, and `remediation`. For combined sync, report each `result.domains[domain]` status, `failed_courses` and stale coverage. For playback, report each item and any failed `reason_code`, without repeating the pre-play announcement. Interpret status with the process exit code:

| Status | Exit | Response |
| --- | ---: | --- |
| `ok` | 0 | Report returned results. |
| `partial` | 1 | Report completed work and what failed or was not verified. |
| `error` | 1 | Report the operational error and remediation, if supplied. |
| `user-action` | 2 | Tell the user what action/remediation is required. |
| `busy` | 75 | Report that the browser session is busy; do not retry automatically. |

Invalid usage also exits 2 with a `user-action` envelope. If the envelope status and process exit code conflict, report both and stop. Never automatically retry `busy` or `unverified`; for `failed` playback, report the failure and do not resume the queue automatically.

## Hard rules

- Never run `campusctl auth set` for the user or ask for, receive, or handle a password. If credentials must be configured, tell the user to run `campusctl auth set` themselves in a terminal.
- Never read campusctl configuration, catalog, or other data files directly; use only its documented CLI and returned JSON.
- Present records and outcomes as returned. Do not infer lecture meaning, completion, or user intent from titles or other metadata.
- Never read assignment or notice detail text or invoke fetch: these commands are not registered. Do not submit work. Sync and selected material download may use a local headless browser via the global flag; CDP sessions and official-player playback remain headed.
- When deciding whether a lecture is already done without opening a player, only official row state `F` or the documented full-watch/not-counted row condition may justify that outcome. Never seek or bypass the official player to simulate a watch, run playback in the background, or start concurrent playback. Do not submit assignments or otherwise mutate assignment state.
- Run only documented commands and flags. The skill passes `--json` to every campusctl command it runs; the user-run `campusctl auth set` instruction is not executed by the skill.
