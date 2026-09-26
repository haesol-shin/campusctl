# Sync performance

[Constitution](../constitution.md) · [Design](design.md) · [Tasks](tasks.md)

Full sync visits every course once and runs the requested sections in one session, instead of logging in and selecting every course once per domain.

## What it does

1. **Profiling.** The global `--profile` flag prints sanitized phase timings and counts to stderr as one `campusctl-profile:` JSON line; stdout keeps the normal envelope.
2. **Combined course pass.** One session, one login, one roster read and one global to-do read. Each course is then selected once and its lecture, assignment, notice and archive sections are collected in order. Seven courses take seven selections, down from one per course per domain. Single-domain commands run the same pass restricted to their domain.

## Acceptance

- The synthetic seven-course Chromium fixture shows one login, one roster, one to-do and seven selections, with every normalized record equal to the independent single-domain expected output.
- The [full-path live run](../live-run.md) completes all four domains on every course, with record sets equal to the catalogs from before the run, and reports its wall time next to the 2026-09-26 headless baseline (owner-held sanitized evidence (2026-09-26)).

## Not in scope

Parallel course tabs, reusing attachment metadata between runs, skipping archive restoration, and multi-trial benchmarking. Each would need its own recorded live run.
