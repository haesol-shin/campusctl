## Added
- Add guided setup, cached status and lecture health, numbered course selection and single-file materials download selection
- Add global headed/headless browser options for supported operations and sync profiling on stderr

## Changed
- Sync all four metadata domains in one combined pass with one course selection per course and independent stale-course retention
- Let LMS pages issue their own requests without a sync/fetch request guard while keeping selected-file download checks

## Fixed
- Enter course sections through their rendered menus and name the failing domain and step in operational errors
- Keep roster diagnostic records when old-record pruning hits a transient Windows file lock
