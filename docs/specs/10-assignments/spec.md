# Assignment metadata

[Constitution](../constitution.md) · [Design](design.md) · [Tasks](tasks.md)

`sync` includes assignments in its four-domain combined pass; `sync --only assignments` narrows the request. Course selection occurs once per course in a combined pass and the task section opens through its menu. `assignments list` reads the local catalog, while `--refresh` refreshes this domain first. Assignment detail fetch is not registered.

Every candidate task row, including an attached hidden row, must have a unique `a[data-act="detail"][data-id]` native ID matching `TB_L_REPORT{digits}`. Rows expose full opaque ID, separate course/native IDs, title, trimmed displayed due text or null, and Boolean submission status; both submitted and unsubmitted rows appear. Missing, blank, malformed or duplicate IDs fail the whole course. An empty course needs a completed course-specific task-list response and idle zero-row task table, not an unrelated empty widget.

Successful courses replace their rows; a task failure keeps previous rows stale and reports a named step, while other courses and domains continue. Full roster failure marks enrollment unknown without clearing prior rows/timestamp; only complete full success removes unenrolled courses. Sync never opens assignment detail or submission controls or downloads media. Page requests run normally, without a request guard.
