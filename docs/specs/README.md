# campusctl specifications

Start with the [constitution](constitution.md) and [live run protocol](live-run.md). Each folder links its current requirements, design and verification tasks.

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
| [80-sync-optimization](80-sync-optimization/spec.md) | Faster combined sync: wait profiling, readiness waits, archive reload skip, course switch, session reuse |
| [90-test-strategy](90-test-strategy/spec.md) | Deterministic waits, parallel isolation, required Chromium CI and coverage signals |
| [100-download-layout](100-download-layout/spec.md) | Configurable material download paths and opt-in course-folder adoption |

## v0.4.0 scope

The combined sync collects all four domains with one roster, one to-do read and one verified selection per course. Each domain can fail independently, retaining that course's old catalog rows as stale. Pages issue their ordinary requests unfiltered; selected material downloads still enforce file identity, type/signature and size. Sections open through rendered course menus, and operational errors name the failed step.

The [live run protocol](live-run.md) governs LMS-facing checks. The release candidate registers assignment and notice fetch with documented reading effects; [40-fetch](40-fetch/spec.md) records the completed confirmation run and remaining owner sign-off before release. Release integration owns the version change.
