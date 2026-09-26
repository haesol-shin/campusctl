# Headless and replay — design

[Spec](spec.md) · [Tasks](tasks.md). Baseline anchors refer to `adb2684`.

## Mode contract and clean cutover

50's integration lane registers the global flags; this lane owns `browser_options.py`, `config.py`, and mode plumbing in `browser.py`. `resolve_headless(config, override: bool | None) -> bool` applies override/config/default. A small explicit operation capability table in browser_options contains `lectures.sync`, `assignments.sync`, `notices.sync`, `materials.sync`, `materials.download`, `lectures.play`, and fetch operations only if registered. Headless false is always allowed under existing headed rules; headless true requires reviewed support for that operation, then local browser mode. There is no environment or config bypass for a pending gate. Capability values are changed only in the release integration that records evidence, never inferred from successful launch.

Playback approval is scoped by player, selected row state, speed and verified outcome, not one blanket `lectures.play=true`. Record completed-Panopto headless playback at 1.0x as verified for the observed interval in H6; retain independent gates for native-end replay outcome, recorded rows, YouTube and first-time incomplete attendance. Preflight the entire explicit queue against supported combinations before launch and recheck the authoritative live row before opening a player; a complete-to-incomplete change cannot enter an unverified first-time path. Material download initially failed its live gate and required diagnosis and a new authorized pass; the later passing result is recorded in [Tasks](tasks.md). Fixture success alone cannot clear a live gate.

`open_session` retains its exclusive lock and persistent-context cleanup (`browser.py:300-409`). Its `headless` keyword receives the resolved Boolean; no provider should independently re-resolve config or hardcode false once approved. Keep the browser's CDP check as defense in depth. All callers receive the same mode via shared orchestration, refresh and optional setup sync. Existing per-command/provider refusal branches are replaced by centralized operation preflight, not left as contradictory rejection paths. Remove old parser options in U5 rather than preserving aliases.

Doctor reports effective headless and operation support without starting a browser; no-display Linux is not a readiness error for approved headless use. Headed default and Windows `display_available:null` stay unchanged. This is mode reporting, not an LMS compatibility probe.

## Replay flow

1. U5 passes `replay: bool = False` to `validate_requested_lectures` and `play_lectures`. Validation still checks kind, full entity identity, media field/type, availability and every item before browser startup; only `completion in {complete,recorded}` is bypassed when true (`player.py:142-202`).
2. Human confirmation belongs to CLI before `open_session`. Render selected IDs/title/count; a positive explicit yes is required, unlike setup's default-yes installer helper (`cli.py:354-355`). Agent JSON calls do not prompt; the skill owns user-intent attestation. Do not store an authorization token or silently reuse prior confirmation.
3. Live pre-play row binding and availability remain mandatory. Make both early skip branches conditional on `not replay` (`player.py:911-946`), while preserving their normal catalog updates. Even replayed state F must pass live availability at 947 before official player open.
4. The existing `_play_visible_lecture` controls official playback; its name is not a reason to bypass it headlessly. Gate operation mode before entry. Set `player_opened` true only after the actual official player has opened, not before navigation; failure before that is false. `replay_requested` echoes the explicit mode per item, including not-started items.
5. Replay must observe this invocation's native player lifecycle ending before polling row status. A row already in F cannot replace player completion evidence. Reuse existing player ended observation; do not change rate/currentTime, reset progress, or dispatch a synthetic ended event. If official UI cannot restart naturally, return playback-failed and stop the queue.
6. Keep post-play authoritative `_poll_completion`, catalog update and error propagation (`player.py:975-1087`). Prior complete/recorded values are never downgraded merely because an attempted replay fails. `unverified` does not mean incomplete or authorize another run.

Successful item example (placeholder only):

```json
{"entity_id":"<lecture-entity-id>","outcome":"completed","elapsed_seconds":42.0,"watch_time":"00:00:42","provider_state":"F","replay_requested":true,"player_opened":true}
```

`already-complete` with player_opened false remains valid only on the normal path; replay_requested true must not use it as replay success. Existing supported-speeds and YouTube error codes remain unchanged. Human output labels replay and reports outcome without promising attendance credit.

## Independent verification

No new live requests in implementation worktrees. Synthetic pages must record actual control actions and property setters; assert native menu/control actions only, no position/rate/visibility writes and no media transfer interception. Headless/headed output comparison uses independently normalized complete records, not candidate-generated expectations. A count-only report can support a gate review but cannot pass complete equivalence. Existing player tests (`tests/test_play.py`, `test_play_progress.py`) are regression inputs; add tests only for new permission/boundary/state transitions, not flag-forwarding echoes.

Release review retains each operation independently: approve metadata sync without implicitly approving file transfer, detail fetch or playback. Any unsupported mode keeps its actionable refusal and skill prohibition. No safety-sensitive launch flags or spoofing are introduced.
