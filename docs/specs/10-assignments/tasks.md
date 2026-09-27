# Assignment verification

[Requirements](spec.md) · [Design](design.md)

- Verify submitted/unsubmitted and hidden rows retain separate task ID, full entity ID and displayed due text
- Verify absent, malformed and duplicate native IDs retain previous course rows as stale while other courses commit
- Verify a completed empty task response clears prior rows only after the course task table settles
- Verify the combined pass enters the task menu after one verified course selection, reports named step failures and issues no detail/submission action
- Run the [live run protocol](../live-run.md) for changes to LMS behavior
