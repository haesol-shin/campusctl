# Browser modes and explicit replay

[Constitution](../constitution.md) · [Design](design.md) · [Tasks](tasks.md)

Global `--headless` and `--headed` precede the command, override Boolean `browser.headless` and default to headed. Local Chromium supports headless sync for lectures, assignments, notices and materials, selected assignment and notice fetch, and material download. A configured CDP browser cannot use headless. Official-player lecture playback stays headed and serial. Unsupported mode requests return `headless-unavailable` before browser startup and do not fall back silently.

`lectures play ENTITY_ID... --replay` explicitly authorizes replay of selected completed/recorded lecture IDs through the official player; normal play still refuses those IDs. Validate each full ID and media/open/speed constraints before browser startup. In interactive human mode, replay requires one dual-TTY default-no confirmation for the whole selected set. JSON `--replay` never prompts, so a caller must obtain explicit user intent first. Playback never seeks, fakes visibility, captures media or forces an unsupported rate. YouTube uses native autoplay at 1.0. An item whose player does not open cannot count as successful replay; official row verification determines the outcome. Failed/unverified playback stops the remaining queue without claiming new attendance credit.
