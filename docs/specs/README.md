# campusctl specifications

Start with the [constitution](constitution.md) and the [live run protocol](live-run.md). Each folder has `spec.md` for what and why, `design.md` for how and `tasks.md` for work items.

| Spec | Scope |
| --- | --- |
| [00-foundation](00-foundation/spec.md) | Private IDs, catalogs, course context and shared fixtures |
| [10-assignments](10-assignments/spec.md) | Assignment sync and list |
| [20-notices](20-notices/spec.md) | Notice sync and list with legacy-compatible identities |
| [30-materials](30-materials/spec.md) | Archive metadata sync and list, selected official file download |
| [40-fetch](40-fetch/spec.md) | Selected assignment and notice source packages |
| [50-v040-ux](50-v040-ux/spec.md) | Full sync, shared course selection, numbered materials, status, global browser mode, guided setup |
| [60-headless-replay](60-headless-replay/spec.md) | Headless sync and official-player replay |
| [70-sync-performance](70-sync-performance/spec.md) | Profiling and the combined course pass |

## v0.4.0 completion plan

**On main now:** headless sync for all domains, guided setup, status and lecture health, course selection, profiling, the combined course pass (one login, roster and to-do, one selection per course), unpublished fetch with live-bound identities, and official-file download.

**Blocking the release:** on the live LMS the combined pass stops on the first course. The request guard stops on page requests it has not seen before, and the notice board is opened by direct URL, which the LMS redirects to the site root.

**Steps, in order:**

1. **Make every command work, offline.** Remove the request guard from sync and fetch, keeping the download's selected-file checks. Enter every section by clicking its course menu. Name the failing step in every error. Fixture tests cover each command end to end.
2. **One full-path live run.** Follow the [live run protocol](live-run.md): full sync of all domains, notice and assignment fetch with state before and after, one material download, timings. Update the LMS behavior notes.
3. **Fix from the record.** Correct anything the run shows, offline, from its record and the behavior notes. A fix that needs behavior the record lacks goes to the owner before another run.
4. **Publish fetch.** With the run's read-state evidence and the owner's sign-off, register `assignments fetch` and `notices fetch` and publish their contracts.
5. **Optimize.** Compare the run's timings with the 2026-09-26 headless baseline and take any measured win that keeps every command working.
6. **Docs, changelog and release report.** Bring the READMEs, the agent skill, `docs/contracts/` and specs 00–60 in line with what ships, removing request-guard, lectures-only sync and unpublished-fetch guidance; report timings, test and CI status and known limits. The owner approves the release.

**Not in v0.4.0:** the per-course selection-epoch layer, multi-trial benchmarking and archive restoration elision.
