# campusctl code standard

[Package structure plan](spec.md) · [Constitution](../constitution.md) · [Contribution policy](../../../CONTRIBUTING.md)

Applies to every implementation PR of the [structure plan](spec.md) and later feature code; reviewers check PRs against it. The plan decides ownership and behavior equivalence; this document decides how code inside an owner is written. It governs new code and functions a PR must change anyway. Untouched code is not rewritten to conform.

## 0. Move and cleanup boundaries

In a structural move, bodies move verbatim first; idiom and logic cleanup belongs in a separate PR, not merely a separate commit. At each P-PR review record whether an L-PR is “needed” or “not needed”, with a code-quality/complexity reason. Do not create cosmetic or no-op L-PRs; required complexity coverage remains binding. Intentional behavior changes require their own PR and acceptance, not a neutral move/cleanup claim.

Baseline facts (`331a51e`, `src/`): ruff C901 above 10 in 50 functions, above 15 in 29, above 25 in 15; `dict[str, Any]` on 299 lines; dataclasses in 9 files; one `NamedTuple`; one `Protocol` (`wait_clock.Clock`); no `Enum`, `TypedDict`, `match` statement, `typing.cast` or bare `except`. No type checker runs in CI, so annotations are review-checked documentation; prefer constructs that also fail at runtime.

## 1. Data shapes

| Shape | Use | Existing example |
| --- | --- | --- |
| JSON read from or written to envelopes, catalogs, receipts or packages and passed through | plain `dict` | catalog rows, `make_envelope` |
| Typed view of such a dict whose keys are read in two or more modules | `TypedDict` in the schema owner, still a dict at runtime | lecture row `media`/`completion`/`open` |
| Internal value with fixed fields crossing a function or module boundary | `@dataclass(frozen=True, slots=True)` | `ResourceReference`, `OfficialAttachmentTarget` |
| Accumulator owned by one workflow | `@dataclass(slots=True)` | `_Stage`, `DomainOutcome` |
| Small result that callers unpack positionally | `NamedTuple` | `PublishedAttachment(path, outcome)` |

Reason: records catch misspelled fields at construction; dicts stay where bytes are the contract. Conversion happens only at the owner edge: load, validate, then build the record; to write, one `to_json()` lists keys literally in today's order with today's omission conditions. Never `dataclasses.asdict` or `vars()`: they emit `None` keys and follow declaration order. Envelopes and catalogs are dumped without `sort_keys` (only selection-snapshot hashes and package digests sort), so insertion order is output.

Records stay in their workflow/schema owner until a second real module consumes them. `DomainOutcome` belongs in `sync.py`; resource/snapshot records in `source_resources.py`; transfer boundary records in `attachment_policy.py`. Do not pre-create `*_types.py`. Retain a separate types module only for actual shared consumers where owner placement would cause an import cycle: `command_types.py` avoids command-to-composition imports, and `player_types.py` avoids handler-to-preview imports. Name the otherwise-required cycle in review.

```python
# before (cli.py): output state smuggled through argparse.Namespace
args._status = status
args._status_errors = errors
# after (command_types.py, P3/P4): one plain record, converted at the envelope
@dataclass(frozen=True, slots=True)
class CommandResult:
    result: Any
    status: str | None = None
    errors: tuple[CampusError, ...] = ()
    publish: Callable[[], None] | None = None
```

## 2. Fixed value sets

Use `StrEnum` when a closed set is compared or iterated at two or more sites: lecture media (`player.py:316,894,917,1168,1182`), envelope status (`EXIT_CODES`, `CampusError.__post_init__`) and canonical domain names (`domains.py`, P10). Keep `Literal` for single-site parameter narrowing (`Domain`, `PublishedAttachment.outcome`). Error codes stay string literals at the raise site: the vocabulary is owned by the contracts and compared almost nowhere. Reason: members equal their strings and `json.dumps` writes the value, so JSON is unchanged, while a misspelled member fails loudly. Catch: `type(member) is str` is `False`; values loaded from JSON stay plain strings, and validators keep their `isinstance`/`type` checks. Introduce an enum in the PR that owns the set (media in P5/P6, domains in P10) and migrate every comparison of that set there.

```python
# before (player.py)
if lecture.get("media") not in {"video", "youtube"}: ...
if media == "youtube" and not math.isclose(speed, 1.0): ...
# after (providers/cnu/media/__init__.py)
class Media(StrEnum):
    VIDEO = "video"  # catalog label of the Panopto path
    YOUTUBE = "youtube"
if lecture.get("media") not in MEDIA_HANDLERS: ...
if media == Media.YOUTUBE and not math.isclose(speed, 1.0): ...
```

## 3. Branching

- Guard clauses first: reject, raise or return early; keep the main path unindented. `validate_requested_lectures` is the model.
- Branches that differ only in data become one table. Branches that select a module or handler become a table of modules or functions (the plan's command and media tables), not `if`/`elif`.
- `match` for three or more cases on one value that run different code, such as a subcommand inside its command module. Not for two cases, not instead of a table.
- Long sequential functions stay when they run an ordered LMS or filesystem-safety protocol: each step depends on the previous state and one `try`/`finally` owns cleanup (`notices._grid_snapshot`, `login.ensure_logged_in`, `browser.open_session`, `material_files.publish_attachment`, the preview lifetime in `_play_visible_lecture`). Extract only pure steps (parsing, predicates, payload checks) with no `await` or cleanup interplay.

```python
# before (providers/cnu/sync_all.py): three maps plus an inline menu dict keyed by domain
_SECTION_PATH = {"lectures": "/std/course", ...}
_PAGE_KIND = {"lectures": "lecture", ...}
menu = {"lectures": COURSE_ROOM_URL_ANCHOR, "assignments": 'a[href="/std/task"]', ...}[domain]
# after (P10): one entry per section, read in one place
section = SECTIONS[domain]
with browser.profile_labels(domain=domain, course=ordinal, page_kind=section.page_kind):
    await browser.bounded(page.wait_for_selector(section.menu, state="attached"), ...)
```

## 4. Functions and the complexity gate

- One job per function; the name says it as verb plus object. A name that needs "and", or a Boolean parameter that switches between two jobs, means two functions.
- Split along workflow steps (validate, resolve, act, publish) or per domain, never at a line count. Helpers stay private (`_name`) in their owner until a second module needs them.
- Options after the first few parameters are keyword-only (`*`), as `sync_all` already does. Closures over workflow state are fine (`_grid_snapshot.completed`); ruff counts them in the outer score.

```python
# before (status.py): one loop with if/elif per domain, complexity 15
for domain in ("assignments", "notices", "lectures"):
    for row in catalogs[domain][domain]:
        if domain == "assignments": ...
        elif domain == "notices": ...
# after: one function per projection; _summarize builds the same keys in the same order
return {
    "assignments": _due_assignments(rows["assignments"], start, end),
    "notices": _unread_notices(rows["notices"]),
    "lectures": _open_lectures(rows["lectures"], start),
}
```

Gate, landed as its own lint-only PR (not inside P1):

```toml
[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "B", "C4", "SIM", "C901", "RUF100"]
ignore = ["E501"]
external = ["BLE001"]  # keeps the existing documented BLE001 noqa valid under RUF100

[tool.ruff.lint.mccabe]
max-complexity = 15

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["C901"]
"tools/**" = ["C901"]
```

Then `ruff check src --select C901 --add-noqa` stamps the 29 baseline functions with `# noqa: C901`. Why 15: the 21 functions scoring 11–15 today are mostly flat validators, parsers and renderers that read fine (`validate_config` 12, `parse_notice_rows` 13, `resolve_course` 15), so 10 would baseline 50 functions with little signal; 20 would leave plan targets such as `_play_youtube_video` (19) and `render_human` (18) unbaselined and free to grow. RUF100 makes the list shrink by itself: once a function drops to 15 or below, its `noqa` is unused and CI fails until it is removed. New functions over 15 fail outright. A touched baseline function must not score higher than on its parent: compare `ruff check src --select C901 --ignore-noqa --output-format concise`. A deliberate step sequence from section 3 keeps its marker with a reason, in the repo's existing style: `# noqa: C901 - ordered LMS step sequence`. Tests and tools are excluded: their fakes are scripted state machines, and restructuring them is not part of the plan. The E0 comparison tool is still held to the limit: its review checks complexity by hand against the same limit of 15, because later PRs reuse it.

## 5. Classes

- A class only when an instance owns mutable state across calls or a resource lifetime: `BrowserSession`, `_DocumentTimeline`, `_TodoCapture`, incremental attachment validators. A lifetime within one scope is a `@contextmanager`/`@asynccontextmanager` function, as in `lock.py` and `open_session`.
- No inheritance hierarchies and no abstract base class for one implementation. `CampusError` subclasses such as `UsageError` are the exception.
- `Protocol` only at a seam with two real implementations: `Clock` has the real clock and the tests' `VirtualClock`. Bound operations are small records of callables (plan section 2), not protocols.
- No class as a namespace of static methods; the module is the namespace.

```python
# rejected: class YouTubeHandler(MediaHandler) with abstract play()/prepare()
# accepted (media/youtube.py): plain functions, one table entry
MEDIA_HANDLERS = {Media.VIDEO: panopto.HANDLER, Media.YOUTUBE: youtube.HANDLER}
```

## 6. Typing

- Use precise types where the shape is known: records, or `TypedDict` once the owner defines one, instead of `dict[str, Any]`. `Any` remains for Playwright handles (`page`, `frame`, `locator`), because tests pass duck-typed fakes, and for raw JSON before validation.
- Parameters take `collections.abc` types (`Callable`, `Iterator`, `Mapping`, `Sequence`); return the concrete `list`/`dict` you build. Write `X | None`, never `Optional`; ruff `UP` already enforces the syntax.
- No `typing.cast` and no `# type: ignore` to silence a doubt. Check the value with `isinstance`, as `_validate_catalog` does.

```python
# before (sync_all.py)
courses: list[dict[str, Any]]
# after: read-only input, typed row from the roster owner
courses: Sequence[CourseRow]
```

## 7. Errors and async

- A user-visible failure is `CampusError(code, message, remediation, status)` using a code from the contracts; a new code is a contract change. Workflows keep returning `(result, error)`; only the command boundary builds the envelope.
- `except Exception` only to convert into `CampusError`, record a diagnostic and re-raise, or at an existing per-item/per-domain isolation boundary. New best-effort cleanup uses `with contextlib.suppress(SpecificError):` plus a reason, never silent `pass`.
- In touched functions, convert existing silent `except ...: pass` to `contextlib.suppress(<specific exception>)` only after demonstrating the same caught exception set, action order and primary/cleanup precedence. Narrowing an existing broad catch is not automatically equivalent: if proof fails, preserve it for the neutral PR and separate any behavior change. Untouched catches are not churn targets.
- `except BaseException` only to remember the primary error and re-raise; never swallow `asyncio.CancelledError` or `KeyboardInterrupt`. Named temporary exception: baseline `providers/cnu/player.py:1150-1154` `_emit` swallows `BaseException` from callbacks. Preserve that behavior through neutral P5/P6 and needed L5/L6; B1 separately makes cancellation/interruption propagate before YouTube speed, with dedicated callback/cleanup tests and acceptance. Remove this exception in B1, not during extraction.
- Resources use `async with`/`asynccontextmanager`. Existing `try`/`finally` cleanup-error precedence is behavior: do not replace it with `ExitStack` in a structural PR. Every browser/protocol await stays bounded (`browser.bounded`).

```python
# Only after proving this specific catch and suppression are equivalent:
except PlaywrightError:  # popup may already be closed
    pass
# equivalent cleanup in a separate logic PR:
with contextlib.suppress(PlaywrightError):  # popup may already be closed
    await popup.wait_for_event("close")
```

## 8. Comments and docstrings

Keep the current density: an optional one-line module docstring, a one-line docstring for functions whose constraint is not obvious ("Validate the complete request before any browser session can be opened."), inline comments for identity, lifecycle and protocol constraints. Explain why, never restate the code. No parameter docstrings, banners or commented-out code. Moved comments move with their code.

## 9. Do not

- No cosmetic churn: renames, reordering, reformatting or annotation-only edits outside lines the PR must change.
- No renaming of public commands, options, JSON keys, error codes, file names or catalog fields.
- No clever one-liners: nested comprehensions with side effects, `and`/`or` used as control flow, walrus chains.
- No generic helper, base class, registry or utility module before a second real caller exists.
- No new dependency or type checker for this standard.

## 10. Reviewer checklist

1. Behavior: neutral PRs pass offline equivalence with no public name or JSON byte change; B1 and feature PRs use explicit behavior acceptance and compare unaffected paths.
2. Scope: only lines the outcome needs; moves are verbatim, with idiom changes in a separate PR and a reasoned needed/not-needed L decision.
3. Data: dicts stay at JSON edges; records convert in one owner-edge function with explicit key order/omission, never `asdict`; no speculative types modules.
4. Values: compared closed sets use their `StrEnum`; error codes stay literals.
5. Branching: guard clauses; tables for data branches; kept step sequences are marked with a reason.
6. Functions: one job, name matches; `ruff check` is clean, with no new `# noqa: C901` and no baseline score increase.
7. Classes: each owns state or lifetime; no single-implementation base or protocol.
8. Types: no new `Any` for a known shape; no `cast` or `type: ignore`.
9. Errors: contract codes, no new silent `except`, suppression equivalence proved, cancellation propagates except the named pre-B1 `_emit` exception; cleanup order kept.
10. Comments explain why; nothing dead is left behind.
