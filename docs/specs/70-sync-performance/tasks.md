# Sync performance — tasks

[Spec](spec.md) · [Design](design.md)

| Item | Files | Status |
| --- | --- | --- |
| Profiling recorder, CLI flag and harness | `src/campusctl/profiling.py`, `tools/profile_sync.py`, instrumentation in browser and providers | Done |
| Domain collectors | `providers/cnu/{assignments,notices,materials,sync}.py` | Done |
| Combined pass and single-domain cutover | `providers/cnu/sync_all.py`, `src/campusctl/sync.py`, `tests/test_sync_all.py` | Done; stops live on the first course |
| Guard removal and menu-click section entry | `providers/cnu/sync_all.py`, `providers/cnu/ui_policy.py`, domain commands | Step 1 of the [v0.4.0 plan](../README.md#v040-completion-plan) |
| Live acceptance | none; evidence only | Step 2 of the plan |

## Evidence

- **Baseline** (owner-held sanitized evidence (2026-09-26)): four-domain headless sync, one session per domain, 270–300 seconds per run over five runs on a low-power ARM host; the three-domain sequence took 235 seconds.
- **Live traffic** (owner-held sanitized evidence (2026-09-27)): the combined pass stopped on the first course. The request guard stopped on a lecture-page script it had not seen before, and a direct navigation to the notice board was redirected to the site root. These are the inputs to step 1.
