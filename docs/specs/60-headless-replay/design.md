# Browser mode and replay design

[Requirements](spec.md) · [Constitution](../constitution.md)

Resolve explicit global `--headless`/`--headed` before `browser.headless` and the headed default. Check each network operation's support before acquiring a session; all four sync domains and material download support local headless mode, whereas CDP and `lectures.play` do not. Cached lists and status do not depend on a graphical session. Browser mode changes presentation only, never the LMS page's requests or identity/attachment checks.

`--replay` changes only the refusal/skip of a complete or recorded lecture. Require explicit full IDs, validate all choices before opening Chromium, and obtain one default-no dual-TTY confirmation for human mode. JSON callers attest selection via the explicit flag; no prompt appears. Open the official player at its naturally presented position, never write a playback position or spoof progress. Verify the authoritative row after playback; include whether replay was requested and whether the player opened in each item. A failed or unverified item leaves later items not-started.
