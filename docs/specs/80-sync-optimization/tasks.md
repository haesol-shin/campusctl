# Sync optimization — tasks

[Spec](spec.md)

| Item | Spec | Depends on | Status |
| --- | --- | --- | --- |
| Wait and document timing spans | [A](a-instrumentation.md) | — | Ready |
| Element readiness instead of network idle | [B](b-readiness.md) | A | Ready |
| Archive reload skip | [C](c-archive-restore.md) | A; shares response completion with B | Ready |
| Course switch without the roster | [D](d-course-switch.md) | B; live observation of the switcher | Needs observation |
| Session reuse and batched fetch | [E](e-session-reuse.md) | owner decisions on its open questions | Needs owner decision |
| Full-path live run, headless | [live run protocol](../live-run.md) | A, B, C merged | Pending owner authorization |
