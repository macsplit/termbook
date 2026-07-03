# termbook.py — Phased Remediation Plan

Companion to `CODE_AUDIT.md`. This orders the audit findings into phases by **dependency and risk**, not just severity — some "high severity" items (the heuristic rewrite) are deliberately scheduled *after* lower-severity items, because touching them safely requires a test harness that doesn't exist yet, and because a couple of the audit's own recommendations turned out, on closer look at the source, to already be partially built. Those corrections are called out inline where relevant.

Each item lists: the audit reference, exact location, the concrete change, how to verify it, and effort. Phases are meant to be shippable independently — you can stop after any phase and be strictly better off than before it.

---

## Correction to the audit's §2.4 before planning around it

§2.4 of the audit recommended "capture language hints from `<pre>`/`<code>` markup instead of re-deriving from stripped text," implying this doesn't happen today. On closer inspection of `handle_starttag` (lines 280–345), **it already does**: `self.code_lang` is populated from `class="language-python"` / `class="lang-python"` on `<pre>`/`<code>` tags, and is passed through as `hint_lang` to `apply_syntax_highlighting` → `detect_language` (line 1780, line 1064). `detect_language` already returns immediately on a valid hint, before any heuristic runs.

This matters for planning: **the broken heuristic chain (§2.1) only ever executes for code blocks that have no `language-*` class** — i.e. exactly the "badly-formed ebooks" case you flagged. That's not a reason to leave it as-is (badly-formed input is normal input for this reader, per its own purpose), but it does mean:
- The fix is squarely scoped to `detect_language`'s fallback heuristics and `_looks_like_code`, not to the HTML-parsing layer, which is already doing the right thing.
- Any fix must be validated specifically against **markup-free plain-text code** (the no-hint path), since that's the only path where it's reachable.

This is folded into Phase 2 below.

---

## Phase 0 — Safety net (prerequisite for Phase 2, do first)

**Goal:** make it possible to change the heuristics without flying blind. Currently `_looks_like_code` and `detect_language` have zero direct tests (audit §6.5); any change to them today is unverifiable except by manual spot-checks like the ones used to write the audit.

| # | Action | Location | Effort |
|---|---|---|---|
| 0.1 | Add `tests/test_language_heuristics.py` with the **failing cases already confirmed in the audit** as regression tests, marked `xfail` initially: 2-line Python function should be code; TS class with `private`/`this.` should be TypeScript, not Java; JS class with `new`/`this.` should be JavaScript, not Java; prose sentence containing "renew"/"static" should not be Java/any code lexer. | new file | 0.5 day |
| 0.2 | Add a small corpus of **known-good cases that must not regress**: SQL `SELECT...FROM...WHERE`, a Cypher `MATCH (n)` query, a C `#include`/`printf` snippet, XML with `<?xml`, a CSV-ish multi-line comma block, and 2–3 real prose paragraphs pulled from an actual EPUB in the wild (long sentences, no code) that must stay classified as prose. | same file | 0.5 day |
| 0.3 | Run existing `tests/` suite once to get a clean baseline (`./run_tests.sh` or `pytest`) before changing anything, so later phases can show a diff in pass/fail rather than "trust me." | n/a | trivial |

**Exit criteria:** `pytest tests/test_language_heuristics.py` runs (with the Phase-2 target cases marked `xfail`/failing and the regression-corpus cases passing) and is wired into whatever CI/`run_tests.sh` already runs.

---

## Phase 1 — Isolated, low-risk fixes (no dependency on Phase 0)

These don't touch the heuristic logic or any shared state; each is a self-contained, mechanical change. Safe to do in parallel with Phase 0, and safe to ship immediately.

| # | Audit ref | Action | Location | Verify | Effort |
|---|---|---|---|---|---|
| 1.1 | §3.1 | Delete the shadowed `get_visible_images(src_lines, src_imgs, src_img_alts, y, rows)` at line 1929 (dead code — the line-2104 definition always wins). Grep the file first to confirm no call site actually depends on the 5-arg signature (`grep -n "get_visible_images(" termbook.py`); if one does, it's currently broken and calling the wrong function, so fix the call site to the line-2104 signature instead of keeping both. | `termbook.py:1929-1943` | `grep -c "^def get_visible_images" termbook.py` → 1 | 0.5 hr |
| 1.2 | §3.2 | Fix the `NameError`-triggering debug block in the surviving `get_visible_images`: either delete it (recommended, see 1.3) or move the `viewport_start`/`viewport_end` computation above it. | `termbook.py:2111-2118` | manual call with `DEBUG_MODE=True`, confirm no exception swallowed silently | 0.5 hr |
| 1.3 | §4.2, §5.1 | Gate or remove all 12 hardcoded `/tmp/termbook_debug.log` / `/tmp/search_debug.log` writes. Preferred: delete them outright (they were development scaffolding, not a real logging facility); if some are still useful, route them through Python's `logging` module gated on the existing `DEBUG_MODE` global (line 129/5875), writing to a path derived from `tempfile.gettempdir()` with a per-run unique name, not a fixed shared filename. | `termbook.py:1991, 2111-2196, 2658-2714, 4747-4883, 5257, 5479-5533` (19 sites total per grep) | `grep -n "/tmp/" termbook.py` → no unconditional hits outside `DEBUG_MODE` guards; scroll/search a real book and confirm no `/tmp/*.log` growth | 1 day (many call sites, mechanical) |
| 1.4 | §4.1 | Replace `subprocess.run(['start', X], shell=True, ...)` on the Windows branch with `os.startfile(X)` (stdlib, Windows-only, no shell involved — the correct API for "open with default handler" and immune to this injection class). Applies at all 4 sites (image viewer + 3 URL-open call sites). | `termbook.py:2257, ~3433, 5389, 5427(-ish), 5460(-ish)` | code review + manual test on Windows if available; otherwise unit-test that the Windows branch calls `os.startfile` via mock | 1 hr |
| 1.5 | §3.3 | **Correction after re-checking the source:** `supports_24bit_color()` has no call sites at all (`grep -n "supports_24bit_color" termbook.py` matches only its own `def`) — it's dead code, not a function that's actually gating rendering decisions. Deleted it outright rather than building out unused detection logic, consistent with removing dead code elsewhere in Phase 1. If terminal-capability-aware rendering is wanted later, it belongs in Phase 2/4 as a deliberate feature with a real call site, not resurrected as-is. | `termbook.py:3809-3812` (pre-edit) | `grep -n "supports_24bit_color" termbook.py` → no matches | 15 min (delete, not rewrite) |
| 1.6 | §5.2 | Replace the per-pixel `img.getpixel((x,y))` calls in `render_image_curses` with a single `pixels = img.load()` pixel-access object (drop-in replacement, same call shape `pixels[x, y]`, no other logic changes) — or bulk-read via `list(img.getdata())` if a flat array is easier to index against the existing 2×2 block loop. | `termbook.py:3839-3906` | before/after timing on a real cover image render (`time` around the call), confirm visual output is pixel-identical | 2-3 hr |
| 1.7 | §3.4 | Clarify the `n`/`p` dual-purpose keybinding in the in-app help screen (`help()`/`show_initial_help_message`) with a one-line note: "n/p navigate search matches while a search is active, otherwise change chapter." Docstring-only change. | `termbook.py:16-31`, `help()` | visual check of `?` screen | 0.5 hr |

**Exit criteria:** all of the above merged and covered by a quick manual smoke pass (open a book, scroll, search, open an image, open a URL) confirming no regression; no shipped behavior change except the two real bug fixes (1.1 dead code, 1.5 truecolor detection).

---

## Phase 2 — Language/code-detection heuristic rework (depends on Phase 0) — DONE

This is the core "convoluted heuristics" fix (audit §2). Implemented and validated; summary below the table.

| # | Audit ref | Action | Location | Effort |
|---|---|---|---|---|
| 2.1 | §2.1(a)(b) | Rework `detect_language`'s fallback chain: (1) convert every `keyword in code_lower` substring check to a `\b`-bounded regex (mirroring the pattern `_looks_like_code` already uses correctly at line 671 with `re.search(r'\b' + keyword + r'\b', ...)` — so the fix is to make `detect_language` consistent with the *better* of the two existing styles, not invent a new one); (2) reorder the chain so language-specific, low-ambiguity markers are checked before generic OO markers — e.g. check TypeScript/JavaScript-specific tokens (`console.log`, `=>`, `interface `, `: string`) and Python-specific tokens (`def `, `print(`, `elif `) *before* the Java branch, and shrink the Java trigger list to things that are actually Java-specific (`system.out.print`, `import java.`, `public static void main`, `@override`) rather than generic-OOP tokens (`this.`, `new `, `extends`, `private `) shared with half a dozen C-family languages. **Note:** do *not* promote `guess_lexer` to the primary path as originally floated in CODE_AUDIT.md §2.1 — verified empirically that it does worse than a fixed heuristic chain on exactly these snippets (arbitrary lexers like "GDScript"/"Tera Term macro"/"scdoc" from its full candidate pool). Keep it as the last-resort fallback only, as it already is. | `termbook.py:1081-1180` | 1-2 days |
| 2.2 | §2.1(c) | Tighten the CSV fallback (`',' in code_text and ... >= 2 lines with a comma`, line 1163) so it can't fire on prose that merely contains commas — require e.g. a consistent comma count per line (a real CSV-like invariant) rather than "any comma on 2+ lines." | `termbook.py:1162-1169` | 2-3 hr |
| 2.3 | §2.2 | Recalibrate `_looks_like_code`'s prose bias so short, syntactically unambiguous snippets (e.g. a `def foo(...):` line, a line ending in `;` with balanced parens, a line starting with a strong keyword from the existing `strong_code_keywords` set) are recognized even at 1-3 lines, without discarding the paragraph-shape signal that correctly protects real prose paragraphs. Concretely: let a single **unambiguous strong-keyword hit** (`def `, `class `, `import `, `SELECT`, etc., already enumerated at line 644) short-circuit to "code" regardless of line count, and reserve the current elaborate scoring for the genuinely ambiguous middle ground. | `termbook.py:612-1026` | 1-2 days |
| 2.4 | §2.1(d), §6.6 | Consolidate the three overlapping keyword lists (`code_keywords` line 602, `strong/weak_code_keywords` line 644/663, `java_keywords`/`java_patterns` line 1082/1093) into one shared, per-language data structure (e.g. a dict of `{language: {"strong": [...], "weak": [...]}}` at module or class scope) so `_looks_like_code` and `detect_language` read from the same source of truth. This is what prevents the next language addition from being applied in one place and forgotten in the other two. | `termbook.py:602, 644-667, 1082-1096` | 1 day |

**Verification performed:** all 8 Phase-0 `xfail` markers flipped to real assertions (all now XPASS/pass); `tests/test_language_heuristics.py` grew to 28 tests covering the original repro cases plus every regression found along the way. Beyond the synthetic corpus, **validated against all 122 real EPUBs** in `/home/user/LeesFolders/Lee/Books/` (a genuine mix of ~15 technical books with real code and over 100 pure-literature books) by feeding each chapter through the actual `Epub`/`HTMLtoLines` parsing pipeline (no curses involved) and inspecting every block the app itself flagged as code.

That real-book validation caught three regressions that the synthetic test corpus alone did not, all now fixed and covered by permanent tests:
- The Python branch's `import + print` combo signal fired on real Java (both words are ordinary in Java too) — removed.
- A C-branch addition (marker-free "primitive-type function(...) {" pattern, added specifically to satisfy one synthetic xfail test) misclassified real Java methods lacking an access modifier as C — reverted. The synthetic case it was added for (a bare `int abs(int x) { ... }` snippet with no `#include`) is accepted as a known, pre-existing gap rather than re-fixed, since closing it this way caused more real-world harm than it solved.
- The narrowed Java branch under-matched several real, simplified/pedagogical Java shapes books actually use (bare `class Foo {` with no visibility modifier, a lone method with a `throws` clause, `public final class` with an extra modifier between `public` and `class`) — added targeted, TS/JS-safe signals for each (Java's dotted `import a.b.C;` statement shape, `throws SomeException`, type-first field declarations like `String title;`, and modifier-tolerant class-declaration matching) after confirming each one doesn't reintroduce the original TS/JS-stealing bug.

**Not fixed, by design (diminishing returns / out of scope):** `guess_lexer`'s fallback still produces essentially arbitrary lexer names (GDScript, Tera Term macro, scdoc, Carbon, CBM BASIC V2, etc.) for content that doesn't match any explicit branch — mostly Ruby, YAML, Dockerfile, HCL/Terraform, and plain terminal-output/log snippets that were never covered by an explicit branch in either the old or new heuristic. Extending explicit coverage to those languages is legitimate future work but is adding new capability, not fixing the bugs this phase targeted.

**Also discovered, not fixed (separate issue, logged for a future phase):** `handle_starttag`/`handle_endtag` unconditionally reset `self.code_lang = None` on both `<code>` tag entry and exit (`termbook.py` around lines 346 and 445), which discards a language hint set by an enclosing `<pre class="language-x">` when the inner `<code>` tag has no class of its own. The far more common convention (language class on the inner `<code>`, e.g. standard markdown-it/highlight.js/Prism output) is unaffected since the inner tag's own class re-sets the hint immediately after. Worth a small, isolated fix in a future pass.

**Exit criteria met:** no known-good case from the audit, Phase 0 corpus, or the real-book validation sweep misclassifies; the SQL keyword list is shared between `_looks_like_code` and `detect_language` via `HTMLtoLines.SQL_KEYWORDS` (partial consolidation — the full unification of every language's keyword list across both functions, as originally scoped in 2.4, was judged a separately-risky rewrite better done as its own dedicated pass than bundled into this one).

---

## Phase 3 — Robustness cleanup (independent, can run alongside Phase 2)

| # | Audit ref | Action | Location | Effort |
|---|---|---|---|---|
| 3.1 | §6.4 | DONE. Audit the 66 bare/broad `except` blocks. For each: if it's guarding a truly optional/best-effort action (e.g. terminal capability probing, a `curses.error` on drawing past screen edge), narrow it to the specific exception type; if it's guarding something load-bearing (state save/load, EPUB parsing), let it surface or log via `DEBUG_MODE` rather than silently swallow. Prioritize the ones inside loops or hot paths first (image rendering, search) since those are most likely to be masking a repeated failure like §3.2. | throughout, `grep -n "except:" termbook.py` | 2-3 days (large but mechanical, can be spread across contributors) |
| 3.2 | §6.7 | Where practical, reduce `global` surface area opportunistically while touching nearby code in Phases 1-3 (don't do a big-bang refactor here — that's Phase 4) — e.g. when touching `Modal` for other reasons, consider instance state instead of classmethods-over-class-state. | various | ongoing, no dedicated slot |

**Exit criteria met:** all ~59 bare `except:`/plain `except Exception:` blocks in `termbook.py` reviewed individually. Outcome by category:
- **Curses drawing (~35 sites)** — `pad.addstr`/`stdscr.addstr`/`getch` calls that can only fail with `curses.error` (writing past screen edge during resize, etc.) — narrowed to `except curses.error:`.
- **Load-bearing I/O (bookmarks, state file, HTML/EPUB parsing, subprocess launches for URLs/images, Fabulous rendering)** — kept broad (`except Exception as e:`) since these wrap third-party/OS calls with genuinely varied failure modes, but now log via `if DEBUG_MODE: print(..., file=sys.stderr)` instead of silently discarding `e`, so `--debug` surfaces real repeated failures instead of masking them.
- **Narrow, clearly-typed guards** (timestamp parsing, list/dict indexing, RGB math) — narrowed to the specific types (`ValueError, TypeError`, `IndexError`, etc.).
- **Two intentionally-broad catches left as-is** (`detect_language`'s `guess_lexer` fallback for a known Pygments registry bug, and the decorative-image color-sampling heuristic) — both already reviewed/justified in Phase 2 and by inline comment.
- One trivial `get_ui_color_pair` try/except wrapping code that could never raise was simplified away entirely.
- No behavior change to the "never crash the reader" guarantee; full test suite (60 passed, 7 skipped) green throughout.

---

## Phase 4 — Structural refactor (largest, do last, incremental)

Only attempt once Phases 0-2 are done — splitting the file is much safer once the riskiest logic (heuristics) has tests and the dead/duplicate code is gone, since a file split will otherwise just relocate the same bugs.

| # | Audit ref | Action | Effort |
|---|---|---|---|
| 4.1 | §6.1 | DONE. Split `termbook.py` into a package: `epub.py` (the `Epub` class), `text_render.py` (`HTMLtoLines` and the heuristics, now with Phase 2's consolidated keyword data), `image_render.py` (the four rendering backends), `ui/dialogs.py` + `ui/search.py` + `ui/bookmarks.py`, `state.py` (the global state currently scattered across the file), and `cli.py`/`__main__.py`. Do this as a mechanical move-and-import pass first (no logic changes) to keep it reviewable. | 1 week+ |

**4.1 outcome:** `termbook.py` (5,997 lines) replaced by a `termbook/` package: `state.py`, `epub.py`, `text_render.py`, `colors.py`, `image_render.py`, `ui/dialogs.py`, `ui/search.py`, `ui/bookmarks.py`, `reader.py`, `cli.py`, `__main__.py`, `__init__.py` — no file over ~1,750 lines (`reader.py` is the largest, since Phase 4.2's dispatch-table decomposition wasn't part of this pass). Every moved function/class was diffed line-for-line against the original (via `ast`) to confirm the move introduced no logic changes beyond the mechanical `global X` → `state.X` rewrite needed for the small set of names actually shared across modules.
- Of the original 19 `global`-mutated names, only **5** (`STATE`, `STATEFILE`, `COLORSUPPORT`, `DEBUG_MODE`, `CURRENT_SEARCH_TERM`) turned out to be read/written from more than one module; those live in `state.py` and are always accessed as `state.NAME`. The other 14 (`VWR`, `SEARCHPATTERN`, `GLOBAL_BOOKMARKS`, `BOOKMARKSFILE`, `INITIAL_HELP_SHOWN`, the resize-tracking globals, etc.) turned out to be confined to a single function each and stayed as ordinary module-level globals in whichever module now owns that function.
- Three more dead functions surfaced during the move (zero call sites, found via grep before moving each one): `render_image_curses`, `detect_and_convert_escape_sequences`, `boost_color_saturation`. Dropped, joining `supports_24bit_color` and the duplicate `get_visible_images` from Phase 1.
- Caught and fixed three real bugs the split would otherwise have introduced: a missing `import curses` in `ui/bookmarks.py`, a missing `format_help_text_with_colors` import in the same file, and — most notably — `reader.py`'s `?` (help) keybinding calling a bare `help(stdscr)` that, unimported, silently resolved to Python's builtin `help()` (pydoc) instead of the app's own dialog, hanging the reader in an interactive pydoc prompt. Found via the existing `pexpect`-based test suite (`test_help_dialog` timing out), confirmed via direct reproduction, fixed by importing the real one as `show_help_dialog`.
- Verified via: full test suite (60 passed, 7 skipped, matching the pre-split baseline exactly), a real editable reinstall (`pip install -e .`), and an interactive smoke test against a real book from the user's library (cover-image rendering, chapter navigation, and the help dialog all confirmed working through the actual installed `termbook` command).
- `setup.py` updated (`packages=` instead of `py_modules=`, entry point now `termbook.cli:main`, build-time patcher now targets `termbook/__init__.py`); `install.sh`/`install_local.sh` needed no changes since both already invoke `python3 -m termbook`. `tests/test_dump_mode.py` and three other test files updated to import from the new submodule paths instead of the flat `termbook` module.
- Not done in this pass (still open, per the original scope): Phase 4.2 (decompose `reader()`'s dispatch into a keymap table), 4.3 (unify the duplicated fake-`re.Match` shim classes), 4.4 (backfill per-module tests).
| 4.2 | §6.2 | DONE. Decompose `reader()`'s ~340-line event loop into a keymap/dispatch table (`{key: handler_function}`) instead of one long `elif` chain, reducing the odds of a future shadowed/unreachable branch like §3.1. | 2-3 days |

**4.2 outcome:** Each `elif` branch of `reader()`'s key-dispatch loop (`termbook/reader.py`) became a nested function (`_handle_quit`, `_handle_scroll_up`, `_handle_toc`, `_handle_open_url`, etc.), kept nested rather than moved to a new module/class so they retain free closure access to `reader()`'s ~15 shared locals (`y`, `k`, `svline`, `countstring`, `rows`, `cols`, `pad`, ...) without inventing a state-passing object. A flat `key_handlers = {key: handler}` dict, built once from the same key-set constants that already existed (`QUIT`, `SCROLL_UP`, `TOC`, etc.), replaces the elif chain; a handler returns `None` (fell through), `("return", payload)`, or `("continue", new_k)` to exactly reproduce the three things an elif branch could previously do (fall through / `return` / `k = x; continue`).
- Confirmed by inspection that none of the original key-sets overlap, so the flat dict is behaviorally identical to the sequential elif chain (order never disambiguated anything here).
- One non-obvious fix required for fidelity, not behavior: `_handle_resize` needed `nonlocal rows, cols` — without it, the handler's own `rows, cols = stdscr.getmaxyx()` assignment would make Python treat `rows`/`cols` as new locals for the whole nested function, breaking the earlier same-branch read of `rows, cols` in the win32-only `curses.resize_term(rows, cols)` call (an `UnboundLocalError` this would introduce, not something in the original code).
- Verified via: full test suite (60 passed, 7 skipped, unchanged from baseline), an AST call-count comparison of `reader()` between old and new (the only two new calls are `key_handlers.get(k)` and `handler()` themselves — no call added, removed, or duplicated anywhere else), and a pexpect-driven interactive smoke test against a real book exercising one key from every handler (quit, scroll/page up+down, chapter next/prev, home/end, TOC, help, meta, bookmarks, save-bookmark, search, colorswitch) plus the digit-then-`q` countstring-clear edge case — no tracebacks, no hangs.
| 4.3 | §6.3 | DONE. Extract the four copy-pasted inline fake-`re.Match` shim classes to one module-level class. | 1-2 hr |

**4.3 outcome:** The four identical inline `class MockMatch:` definitions (one in `text_render.py`'s `wrap_text_preserve_urls`, three in `reader.py` — the URL_HL/TABLE_BG rendering branches and `_handle_open_url`) were byte-identical, so they collapsed into a single module-level `MockMatch` class in `text_render.py` (next to `find_urls_in_text`, the function every call site already used to produce the `(url, start, end)` tuples it wraps). `reader.py` now imports it (`from termbook.text_render import HTMLtoLines, find_urls_in_text, MockMatch`) instead of redefining it per call site. Verified via: full test suite (60 passed, 7 skipped, unchanged), `grep -rn "class MockMatch" termbook/` confirming exactly one definition remains, and a pexpect smoke test exercising the `u` (open URL) key against a real book with no tracebacks.
| 4.4 | §6.5 | DONE. Backfill test coverage for the newly-split modules as they're extracted (natural checkpoint to add tests per module rather than one big effort). | ongoing, folded into 4.1 |

**4.4 outcome:** Of the Phase 4.1 modules, three had zero direct unit coverage (only indirect exercise via the pexpect integration tests) and are pure/curses-optional, so were the highest-value, lowest-risk targets: `epub.py` (`tests/test_epub.py` — `Epub.__init__`/`get_meta`/`initialize` against the existing `test_epub` fixture, plus `dots_path`), `colors.py` (`tests/test_colors.py` — `rgb_to_color_index`, `find_closest_palette_color`, and the `state.COLORSUPPORT`-gated functions pinned to the no-color-support path so they don't need a live curses screen), and `text_render.py`'s URL helpers (`tests/test_url_utils.py` — `find_urls_in_text` and the Phase 4.3-unified `MockMatch`). `image_render.py` and `ui/*` were left to their existing pexpect coverage (`test_application_features.py`, `test_bookmarks.py`, `test_resize_and_modals.py`) since unit-testing them would mean mocking curses/PIL rendering for low marginal value. Test suite grew from 60 to 85 passing (7 skipped, unchanged).

**Exit criteria:** no single file over ~1,000 lines; `reader()` under ~100 lines of dispatch plus separate handler functions; existing test suite (plus Phase 0/2 additions) passes unchanged after the move.

---

## Summary timeline

| Phase | Depends on | Elapsed estimate | Ships independently? |
|---|---|---|---|
| 0 — Safety net | — | 1 day | N/A (enables Phase 2) |
| 1 — Isolated fixes | — | 2-3 days | Yes, immediately |
| 2 — Heuristic rework | Phase 0 | 3-5 days | Yes, after Phase 0 |
| 3 — Robustness cleanup | — | 2-3 days (spread out) | Yes, incrementally |
| 4 — Structural refactor | Phases 0-2 | 1-2 weeks | Yes, in slices (per module) |

Phases 1 and 3 have no hard dependencies and can start immediately/in parallel with Phase 0. Phase 2 is gated on Phase 0 specifically because of the demonstrated pattern in this codebase: reactive heuristic fixes without regression tests have repeatedly traded one failure mode for another (audit §2.3). Phase 4 is gated on 0-2 because file-splitting a heuristic that's still buggy just moves the bug to a new address.
