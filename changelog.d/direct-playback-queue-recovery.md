## Fixed
- Continue selected lecture playback after item failures or unverified results; stop the queue only for session failures.
- Reopen previews observed never to have started, up to three attempts per lecture, without replaying observed playback or adding retry delays.

## Added
- Report preview-open attempt counts in playback items and attempt numbers and preview steps in schema-version-3 diagnostics for every failed attempt, including recovered retries.
