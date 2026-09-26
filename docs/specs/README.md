# campusctl specifications

Start with the [constitution](constitution.md) and the [live run protocol](live-run.md). Each folder has `spec.md` for what and why, `design.md` for how and `tasks.md` for work items.

| Spec | Scope |
| --- | --- |
| [00-foundation](00-foundation/spec.md) | Private IDs, catalogs, course context, request guard and shared fixtures |
| [10-assignments](10-assignments/spec.md) | Assignment sync and list |
| [20-notices](20-notices/spec.md) | Notice sync and list with legacy-compatible identities |
| [30-materials](30-materials/spec.md) | Archive metadata sync and list, selected official file download |
| [40-fetch](40-fetch/spec.md) | Selected assignment and notice source packages |
| [50-v040-ux](50-v040-ux/spec.md) | Full sync, shared course selection, numbered materials, status, global browser mode, guided setup |
| [60-headless-replay](60-headless-replay/spec.md) | Headless sync and official-player replay |
| [70-sync-performance](70-sync-performance/spec.md) | Profiling and the combined course pass |

## v0.4.0 completion plan

**On main now:** headless sync for all domains, guided setup, status and lecture health, course selection, profiling, the combined course pass (one login, roster and to-do, one selection per course), unpublished fetch with live-bound identities, and official-file download.

**Blocking the release:** on the live LMS the combined pass stops on the first course, because the guard still stops on unknown requests (a lecture page script and a notice-board redirect after a direct navigation) and does not say which request stopped it.

**Steps, in order:**

1. **Guard rewrite, offline.** Implement the [request guard](constitution.md#request-guard) rules, record every suppressed and unreviewed request in the diagnostics and name the triggering request in any error. Enter the notice board by clicking its menu, as the per-domain sync did. Build the replay test from the traffic already recorded on 2026-09-27; every recorded request replays to its expected disposition.
2. **One full-path live run.** Follow the [live run protocol](live-run.md) at the full scope: full sync of all domains, notice and assignment fetch with state before and after, one material download, timings. This run is both the acceptance check and the source for any remaining fix.
3. **Offline follow-up.** Fix anything the run shows from its log alone, extending the replay fixture. A fix that needs traffic the log does not contain goes to the owner before another run.
4. **Publish fetch.** With the run's read-state evidence and the owner's sign-off, register `assignments fetch` and `notices fetch` and publish their contracts.
5. **Docs and changelog.** Bring the README, skill, contracts and specs in line with what ships, and write the changelog fragments.
6. **Release readiness report.** Measured timings from the run against the 2026-09-26 headless baseline, test and CI status, known limits. The owner approves the release.

**Not in v0.4.0:** the per-course selection-epoch layer, five-trial performance benchmarking and archive restoration elision. They return only with their own recorded run.
