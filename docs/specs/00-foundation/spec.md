# Shared LMS source foundation

The [constitution](../constitution.md) governs all domains. Page requests run unfiltered; campusctl limits its own actions, verifies identity before publication, and never treats a failed course as empty.

## Identity and catalogs

Assignment IDs bind course and native task ID; material IDs bind course and native file ID. Notice IDs bind course, displayed date/time and ordinal, while `legacy_key` preserves the exact displayed course-label/date/number spelling required by existing consumers. Empty or duplicate native IDs make the affected course unaddressable. Components containing `%` or `:` are reversibly escaped.

Separate atomic lecture, assignment, notice and material catalogs hold a roster, rows and freshness information. Successful courses replace their own rows, including verified empty results. Failed courses retain old rows and carry stale markers; partial discovery defers removal. Full discovery failure preserves records and timestamp while marking enrollment unknown; filtered failure leaves other courses untouched. Lists read local catalogs without the browser lock; network commands use one non-blocking account session lock.

## Course entry and effect limits

After authentication, select each enrolled course through its roster row and enter sections via the rendered menu. Verify the committed page and responses belong to that course; an opaque navigation POST is not a plaintext ID. A combined sync selects a course once and independently records each selected domain's success or failure. LMS pages issue their own requests; no request-origin or per-operation route gate filters them. Only the selected official attachment transfer binds its file ID and validates response type, signature and size. No video/audio download or coursework-control interaction occurs.

Package IDs, safe names and atomic content-addressed publication are described in [fetch](../40-fetch/spec.md); fetch commands remain unregistered until owner sign-off.
