---
name: campusctl
description: "Use campusctl's documented JSON CLI to check lectures, assignments, course notices, and materials, or play or download only user-selected items."
---

# Campusctl

Translate the user's intent into documented `campusctl` CLI calls. Follow the [public CLI contract](https://github.com/haesol-shin/campusctl/blob/v0.3.2/docs/contracts/cli.md) and its linked domain contracts. Present CLI results without interpreting course content or making decisions for the user.

## Use this skill for

- Checking remaining or incomplete lectures: “What lectures are left?” / “남은 강의 보여줘.”
- Refreshing the lecture catalog: “Refresh the lecture list.” / “강의 목록 새로고침해 줘.”
- Listing courses: “Show my courses.” / “수강 과목 목록 보여줘.”
- Playing lectures the user selected: “Play the lecture I selected.” / “선택한 강의를 재생해 줘.”
- Listing assignment status, course notices, or materials: “Show my assignments.” / “과제 목록 보여줘.”
- Downloading one material the user selected from the list.

## Procedure

1. Before any other `campusctl` command, run `campusctl --version --json`. If `result.version` is below `0.3.2`, tell the user to run `uv tool upgrade campusctl` and stop. Do not recommend or run `config init` or `setup` on an older version.
2. Run `campusctl doctor --json` to check readiness; it is read-only. If it reports `config-missing`, ask only for the user's CNU login ID, then run `campusctl config init --username <ID> --json`. That JSON invocation is non-interactive. After configuration succeeds, tell the user to run `campusctl auth set` themselves in a terminal. Pause commands that need credentials until the user confirms they have run it; never ask for, receive, or handle their password.
3. If an error has code `browser-not-installed`, ask the user to agree to the browser download before running `campusctl setup --json`. Run setup only after they agree.
4. Run `campusctl sync --only DOMAIN --json` only when the user asks for fresh data or a CLI response recommends rebuilding that domain's catalog (including `catalog-missing`); after that error, rerun the requested list command. Select `lectures`, `assignments`, `notices`, or `materials` for DOMAIN; a bare sync refreshes lectures only. Do not judge freshness by age. Report `partial` results and `result.failed_courses` objects; do not present retained rows for failed courses as freshly checked.
5. To list courses, run `campusctl courses list --json`. Match course names to returned `course_id` values; ask if multiple records match. To list lectures, run `campusctl lectures list --json`; incomplete lectures are the default. Add `--all` when completed or recorded rows are requested, and `--course ID` only for a course the user identified or selected. Rows marked `recorded` are done for default filtering and replay prevention because the catalog records the documented full-watch/not-counted condition; this does not imply attendance credit or LMS completion. Use the latest list response for any playback decision.
   For assignment, notice, or material questions, read `campusctl assignments list --json`, `campusctl notices list --json`, or `campusctl materials list --json` after any requested sync (or immediately when a cached list is requested). Use `--course ID` only for a course identified or selected by the user. `result.cache.enrollment_state: "unknown"` and `result.cache.failed_courses` indicate potentially stale records, even for empty filtered lists; report that uncertainty. `is_unread: null` for a notice means read state is unknown, not read. `notice-board-paginated` means that course's board exceeds the supported first page (more than 10 notices); report the affected course and ask the user to check its board in the LMS, not that its list is complete.

   For a material download, resolve one user-selected full `entity_id` from `campusctl materials list --json`, ask if the selection is ambiguous, then run `campusctl materials download <ENTITY_ID> --json` (with `--out DIR` only if the user chose a directory). Report the returned `result.material.path` and outcome; the default destination is the OS Downloads folder under `campusctl/<course label>/`. Each download requires its own individually selected ID; never bulk-download, download a whole course or post at once, or select files on the user's behalf.
6. For deadline questions, filter `due_date`; for release questions, filter `available_from`. Interpret relative calendar periods such as “this week” using ISO calendar-week boundaries in CNU local time (Monday 00:00 inclusive to the following Monday 00:00 exclusive), not the agent's host timezone. State the exact date/time boundaries and inclusivity. Treat null dates as unknown. Ask only if the requested period is non-calendar or remains ambiguous.
7. Before any playback request, run `campusctl lectures list [--course ID] --json`; include `--course ID` only when the user identified or selected that course. If listing fails, report it and do not play. Use only this latest response to resolve the user's choice and define the scope of any explicit “play all” request.
   - A prior clear choice or a unique title the user specifically asked to play is explicit; a vague request is not. If multiple records match, show candidates and ask which to play. If the requested record is absent, use `campusctl lectures list --all --json` to check whether it is already complete or recorded; report that status and do not play it. Otherwise, ask the user to identify a listed lecture.
   - For a single selection, resolve the user's choice against the latest list and use only its `entity_id`. `video` and `youtube` are playable; `offline` and `other` are rejected as `lecture-not-playable`, so do not send those IDs to play. YouTube uses native autoplay only at `1.0`. If the user requests another YouTube speed, explain that it is unavailable and do not play. When a selected set contains YouTube and no unsupported speed was requested, pass `--speed 1.0` for the invocation (including mixed queues) so a non-1.0 configured default cannot prevent native autoplay. For a playable explicit selection, run `campusctl lectures play <ENTITY_ID> --json`; include `--speed 1.0` when its media is YouTube, or pass a requested supported speed for other media.
   - Treat an explicit “play all/everything” request as selecting the finite set of incomplete rows from the latest `lectures list`; a request just to list or ask what remains is not permission to play. Deduplicate `entity_id` values in first-seen order. Select only rows with `open: true`, `completion: incomplete`, playable media (`video` or `youtube`), and a non-empty `entity_id`; never select `open: null`. Report how many incomplete rows have `open: false` and their earliest `available_from` date; do not call unknown availability “not open.” Tell the user why any `offline` or `other` rows were excluded. If no eligible open rows remain, do not call play. Otherwise, show the selected count and titles, then ask exactly once: “Play these N lectures now?” Invoke only after a clear yes, using exactly those IDs. If any selected row is YouTube and the user requested a speed other than `1.0`, explain the restriction and do not invoke any lecture in the queue, including non-YouTube selections. Otherwise, if any selected row is YouTube, pass `--speed 1.0 --json`; for other media, pass a requested supported speed or omit `--speed`.
   - If a play request returns `lecture-not-open`, do not retry or replace the selection; report the opening date in its message and tell the user to retry after it opens.
8. Campusctl validates every requested ID before opening the browser and processes playback serially through the official player; local mode uses a visible browser. Report each playback outcome. `recorded` means the authoritative row has state other than `F`, is marked not counted for attendance, and its displayed watched progress reaches the required duration. It may be observed before this invocation opens a player or after playback; it reports the LMS row's full-watch/no-attendance status, not that this invocation played it or that attendance was credited. It is done and does not stop the queue. `unverified` (`playback-unverified`) and `failed` stop the queue, leave later IDs `not-started`, and make the envelope `partial`; for `failed`, report the actual error code rather than assuming it is `playback-failed`. Never call either outcome completed. `already-complete` means campusctl confirmed state `F` before opening the player.

## Result handling

Every `campusctl` command run by the skill includes `--json`. Read `status`, `result`, and `errors`; relay each error's `code`, safe `message`, and `remediation` as returned. For sync, report `result.failed_courses` objects. For playback, report each `result.items[]` outcome and any failed item's `reason_code`, without repeating the pre-play title announcement. Interpret status with the process exit code:

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
- Do not submit assignments or read assignment/notice detail text; v0.3 lists metadata only, with detail reading planned for v0.4.0. New-domain sync and material download need a visible browser; never request `--headless` for them.
- When deciding whether a lecture is already done without opening a player, only official row state `F` or the documented full-watch/not-counted row condition may justify that outcome. Never seek or bypass the official player to simulate a watch, run playback in the background, or start concurrent playback. Do not submit assignments or otherwise mutate assignment state.
- Run only documented commands and flags. The skill passes `--json` to every campusctl command it runs; the user-run `campusctl auth set` instruction is not executed by the skill.
