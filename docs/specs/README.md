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

## v0.4.0 scope

The combined sync collects all four domains with one roster, one to-do read and one verified selection per course. Each domain can fail independently, retaining that course's old catalog rows as stale. Pages issue their ordinary requests unfiltered; selected material downloads still enforce file identity, type/signature and size. Sections open through rendered course menus, and operational errors name the failed step.

The [live run protocol](live-run.md) governs LMS-facing checks. Assignment and notice fetch are implemented internally but not registered as public commands until the owner signs off on the observed detail and read-state effects; [40-fetch](40-fetch/spec.md) holds their design. Release integration owns the changelog and version change.
