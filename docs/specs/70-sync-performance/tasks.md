# Sync performance — tasks

[Spec](spec.md) · [Design](design.md)

| Item | Files | Status |
| --- | --- | --- |
| Profiling recorder, CLI flag and harness | `src/campusctl/profiling.py`, `tools/profile_sync.py`, instrumentation in browser and providers | Done |
| Domain collectors | `providers/cnu/{assignments,notices,materials,sync}.py` | Done |
| Combined pass, single-domain cutover and menu-click section entry | `providers/cnu/sync_all.py`, `src/campusctl/sync.py`, `tests/test_sync_all.py` | Done |
| To-do before course selection | `providers/cnu/sync_all.py` | Done; verified by the seven-course synthetic pass, not yet on the live LMS |
| Archive restoration elision | `providers/cnu/materials.py` | Next optimization; needs its own recorded live run |

## Evidence

- **Baseline** (owner-held sanitized evidence (2026-09-26)): four-domain headless sync with one session per domain took 270–300 seconds per run over five runs on a low-power ARM host.
- **Full-path live run** (owner-held sanitized evidence (2026-09-27)): headed four-domain sync took 189 seconds with seven course selections. Lectures and assignments completed on all seven courses; notices and materials failed on the first course because the to-do page has no course menu, which the to-do reordering fixes. Materials took about 74 seconds, 52 of them in archive restoration after each attachment modal, the largest remaining cost.
