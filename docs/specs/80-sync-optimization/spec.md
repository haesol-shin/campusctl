# Sync optimization

[Constitution](../constitution.md) · [Live run protocol](../live-run.md) · [Tasks](tasks.md)

The combined course pass works on every course and domain. This spec makes it faster without giving any of that up: principle 1 (every feature works) always wins over principle 2 (optimize).

## Where the time goes

Owner-held sanitized evidence (2026-09-27), headed confirmation run, seven courses across four domains:

- **Sync takes 188 seconds, and almost all of it is page loads.** 68 documents at roughly 2.4–3.1 seconds each: archive 28 (7 entries plus 21 reloads after attachment modals), roster 10, and 7 each for course entry, lecture section, assignments and notices.
- **Pages wait longer than their content needs.** Content is ready 1.5–2.0 seconds after commit, but the next navigation starts 2.4–2.7 seconds after it. About 90 seconds of the run is not covered by any profiling span.
- **Authentication takes about 11 seconds** per run (the `auth` span 9 s, including the landing check, plus a 2 s SSO popup settle), and each fetch command repeats it. How much of it a still-valid session avoids is not yet measured.
- **Headless is untested on the combined pass.** Separate per-domain syncs ran about 30% faster headless than headed (owner-held sanitized evidence (2026-09-26)).

## Slices

| Slice | Change | Estimated saving |
| --- | --- | --- |
| [A](a-instrumentation.md) | Profile every wait and time every document | none; measures the others |
| [B](b-readiness.md) | Proceed when the page's content is ready instead of when the network is idle | 30–50 s |
| [C](c-archive-restore.md) | Skip the archive reload after a modal when the list is provably unchanged | 40–50 s |
| [D](d-course-switch.md) | Reach the next course without the roster, if the LMS offers a control for it | up to 20 s |
| [E](e-session-reuse.md) | Reuse a live login, fetch several IDs in one session, allow headless fetch | part of the 11 s authentication per run; most of the per-fetch setup for each extra ID |

The savings are estimates from the numbers above, not measurements.

## Order

1. A lands first so that B and C can be measured against it.
2. B and C are independent and can be built in parallel.
3. E is independent of B and C. D waits for the live observation of a course switcher.
4. After A, B, C and E are merged, with E's headless fetch included as a candidate, one full-path live run, headless, measures them and the headless combined pass, runs both fetches headless, and records the observations D needs. Headless fetch is released only if that run passes. It follows the [live run protocol](../live-run.md).

## Acceptance

The live run completes every course and domain. Each difference between its catalogs and the ones before it is explained by a change on the LMS. Wall time is reported next to the 188-second baseline, and per-document timings come from the A spans.
