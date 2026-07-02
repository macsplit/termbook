# termbook.py — Code Audit

**Scope:** `termbook.py` (6,006 lines, single file), supporting scripts, and `tests/`.
**Method:** static review plus targeted runtime repros (see "Verified" tags below) run against the current working tree (`ca48dc8` + uncommitted diff). No fuzzing or full test-suite run was performed; findings are what's apparent from the code and quick, deliberately narrow reproductions.

---

## 1. Executive summary

termbook is a single 6,000-line script that has clearly grown by accretion: new features (image rendering, search, bookmarks, syntax highlighting) were bolted onto a single file with no internal module boundaries, and several subsystems — most visibly the "is this code or prose?" and "what language is this code?" heuristics — show the classic signature of being tuned against specific failing examples over time until the tuning constants contradict each other. The result works for the epub the maintainer was testing against at the time, but the heuristics **misclassify plain English prose as Java** and **misclassify real TypeScript/JavaScript as Java** in reproducible, verified tests below. There is also a large amount of copy-pasted debug logging (writing to hardcoded `/tmp` paths on every render/search call) left in from development, 66 bare/broad `except` blocks that can hide real bugs, a duplicate top-level function definition where the second silently shadows the first, and a Windows code path that shells out with `shell=True` using text extracted from untrusted ebook content.

None of this is exotic to find — it's visible by reading the file — but it adds up to a codebase that is fragile to touch and not covered by tests where it most needs to be (the heuristics have zero direct unit tests, while simpler things like color-pair math are tested).

**Top 5 priorities**, detail below:

1. **Language-detection heuristics are actively wrong on common inputs** (Java over-triggers on JS/TS, and even on plain prose) — Functional, High.
2. **Duplicate `get_visible_images` definition** — the first (line 1929) is dead code, permanently shadowed by the second (line 2104) — Functional, High.
3. **Debug logging left in production code**, writing unconditionally to `/tmp/termbook_debug.log` / `/tmp/search_debug.log` on hot paths (every render, every search keystroke) — Performance/Security/Hygiene, High.
4. **`shell=True` subprocess call on Windows using URL text scraped from ebook content** — Security, Medium-High.
5. **6,000-line monolith, ~340-line `reader()` function, no module boundaries, near-zero test coverage of the riskiest logic** — Maintainability, High (structural, not urgent, but compounds everything else).

---

## 2. Language / "is this code?" heuristics — the specific ask

This codebase has two separate heuristic systems that matter here:

- `HTMLtoLines._looks_like_code(text)` (line 612) — decides whether a block of text should be rendered as a code block at all.
- `HTMLtoLines.detect_language(code_text, hint_lang=None)` (line 1058) — once something is treated as code, guesses which Pygments lexer to use.

Both are **not** simple heuristics; they are large, hand-tuned scoring systems with dozens of magic constants that were evidently adjusted incrementally against specific books/snippets. The comments themselves are a tell: `# COSMIC level word analysis`, `# Keywords need to compete with massive start consistency weighting`, `# Further reduced`, `# increased`, `# Removed lower tier entirely`. These aren't descriptions of a design; they're a diary of successive patches, and the result is internally contradictory.

### 2.1 `detect_language` — verified misclassifications

The function is a linear `if/elif/elif/.../else` chain (lines 1081–1180). Order = priority, and it is checked with plain substring (`in`) tests, not word-boundary regexes. This has two compounding effects:

**(a) Priority ordering is wrong for the most common real case.** Java is checked *first*, before TypeScript and JavaScript, using patterns including `'this.'`, `'private '`, `'public '`, `'extends '`, `'new '`. But `this.foo`, `private`, `extends`, and `new Foo()` are all completely ordinary in JS/TS classes — arguably *more* common in modern TypeScript than in Java code you'd find in an ebook. Since Java is tested first and the chain stops at the first match, **any TypeScript or JavaScript class-style snippet is misdetected as Java**. Verified:

```python
>>> p.detect_language(typescript_class_snippet)   # export class UserService { private users...}
JavaLexer          # should be TypeScript
>>> p.detect_language(javascript_class_snippet)    # class ShoppingCart { constructor(){this.items=[]} ... new ShoppingCart()}
JavaLexer          # should be JavaScript
```
This is not a contrived edge case — class-based JS/TS is arguably the single most common code shape in modern programming books.

**(b) No word boundaries → substring false positives, including on prose.** `'new '` is checked with plain `in`, so it matches inside `"renew "`. `'static'` matches inside `"hydrostatic"`, etc. Combined with (a), plain English text can be misclassified as Java-flavored code purely from incidental substrings:

```python
>>> text = ("You should renew your subscription before it lapses, and static "
...         "analysis of this text shows nothing special.")
>>> p.detect_language(text)
JavaLexer
```
This is a sentence with zero code in it, classified as Java. (In the current call sites this function is normally only invoked on text that already passed `_looks_like_code`, but that is the only thing preventing this from firing on ordinary prose — the function is not safe on its own, and any future caller — or any future prose that *does* pass `_looks_like_code`, see 2.2 — will trigger it.)

**On how these heuristics likely got this way:** the constants and comments (`# COSMIC level`, `# Removed lower tier entirely`, `# increased`) read like they were tuned reactively against specific real EPUBs that mis-rendered — plausibly ones with messy or non-semantic HTML (scanned/converted books where `<pre>`/`<code>` markup was lost or never present, so there was no clean signal to key off). That's a believable and sympathetic origin story, and it explains the shape of the code. It doesn't change the conclusion, though: whatever the cause, the current chain is verifiably wrong on mainstream, well-formed input (plain TypeScript/JavaScript classes, a two-line Python function, a sentence containing the word "renew"), and each fix made for one badly-formed book seems to have cost accuracy on the common case. A heuristic tuned entirely on hard/adversarial cases without regression tests for the easy cases will tend to drift this way — which is also why §6.5 (zero test coverage on these two functions) matters as much as the logic itself: without tests pinning down "a 2-line Python function must be detected as code," the next reactive fix for the next badly-formed book will just as easily break the common case again.

**(c) Contradictory ordering elsewhere in the chain too:** the SQL/Cypher heuristics and the catch-all CSV heuristic (`elif ',' in code_text and ... >= 2 lines with a comma`, line 1163) sit near the bottom of the chain, but that CSV condition is broad enough to match almost any multi-line text with commas in two or more lines — it's only saved from firing constantly by everything above it in the chain accidentally catching things first. It's the kind of check that will suddenly start matching once someone reorders or removes an earlier branch, because nothing else pins it down.

**(d) Duplication with `_looks_like_code`:** both functions maintain their own, different keyword lists (`code_keywords` at line 602, `strong_code_keywords`/`weak_code_keywords` at 644/663, `java_keywords`/`java_patterns` at 1082/1093) that overlap but don't match. There's no single source of truth for "what does Python/Java/JS code look like" — three different lists answer that question three different, partially-contradictory ways in the same class.

### 2.2 `_looks_like_code` — verified under-detection of common short code

This is a ~410-line weighted scoring function (612–1026) that computes a `code_score` and a `prose_score` from a long list of signals (keyword hits, brace density, line-length coefficient of variation, "sentence pattern" regex counts, indentation consistency ratios...) and returns `code_score > prose_score + 15`. `prose_score` starts at a flat +20 bonus before any evidence is examined, and several of the strongest prose signals (`consistent_start_bonus`, `regular_length_bonus`) require ≥2–3 lines to even engage.

Net effect, verified: **short, completely ordinary code snippets are not recognized as code at all.**

```python
>>> p._looks_like_code("def add(a, b):\n    return a + b")
False        # a 2-line Python function is classified as prose
```
A one- or two-line function is one of the most common things a programming book shows (e.g. "Listing 3.1 shows a simple accessor"). It will be rendered as unstyled prose instead of a highlighted code block. Meanwhile a short brace-heavy snippet does trigger correctly:
```python
>>> p._looks_like_code("if (x > 0) {\n  console.log(x);\n}")
True
```
— so detection quality depends heavily on incidental brace density rather than on anything about the actual language. This is consistent with the tuning history visible in the comments (heavy weight on curly braces "MAJOR CODE INDICATOR", explicit note that plain parentheses were "removed - too common in prose"): the function was iteratively adjusted to stop misfiring on prose paragraphs, and prose-favoring constants were cranked up far enough that it now under-fires on short/idiomatic (especially Python, since Python code is brace-free and relies on indentation) snippets.

### 2.3 What this means practically

For a terminal ebook reader whose main differentiator is nicely-formatted code listings, these two heuristics are core functionality, and they are both demonstrably wrong on mainstream, non-adversarial input (a 2-line Python function; any TS/JS class). Given there is no test coverage on either function (see §6), this will not be caught by CI and will only surface as user bug reports ("my Python snippet isn't highlighted", "my TypeScript is colored like Java").

**Recommendation:** replace the ad hoc scoring in `detect_language` with Pygments' own `guess_lexer`/`guess_lexer_for_filename` as the primary path (it already exists as a fallback at the bottom of the chain, line 1172) and use the hand-written heuristics only to disambiguate cases Pygments' guesser is known to get wrong (there are a few, e.g. very short snippets) rather than pre-empting it entirely. For `_looks_like_code`, replace the sprawling weighted score with a small number of high-precision signals (e.g., presence of the source book's own "Listing"/"Code" caption, a language shebang/marker, or `<pre>`/`<code>` semantics carried from the EPUB's HTML, which the parser already has access to and would be far more reliable than reconstructing intent from plain text after the fact — see §2.4).

### 2.4 A more fundamental design issue

`_looks_like_code` and `detect_language` operate on *plain text after HTML has already been stripped* (`HTMLtoLines` is an `HTMLParser` subclass; by the time this code runs, `<pre>`/`<code>` tag information has been reduced to bare lines — confirm in `handle_starttag`/`handle_data`, lines 280–524). EPUB source HTML almost always marks code blocks explicitly with `<pre>`/`<code>` tags and frequently even carries a language via a CSS class (e.g. `<code class="language-python">`, the same convention markdown-derived EPUBs use). Reconstructing "is this code, and in what language" from the rendered plain text is solving a strictly harder problem than the one already answered by the source markup, and it's why the heuristics have to be this elaborate in the first place. If `idcode`/language hints were captured from the original tags in `handle_starttag` (there is already an `idcode` index set and a `hint_lang` parameter on `detect_language`, suggesting this was half-built at some point), most of §2.1–2.2 would be unnecessary.

---

## 3. Functional correctness issues

| # | Issue | Location | Severity |
|---|---|---|---|
| 3.1 | **Duplicate top-level `get_visible_images` definition.** Two functions with this exact name exist at module scope (line 1929: `(src_lines, src_imgs, src_img_alts, y, rows)` and line 2104: `(src_lines, imgs, y, rows, image_line_map=None)`). Python keeps only the last one; the first is permanently unreachable dead code, and any call site written against the first signature is broken. | `termbook.py:1929`, `:2104` | High |
| 3.2 | Inside the (surviving) `get_visible_images`, a debug block references `viewport_start`/`viewport_end` (line ~2117) **before they are assigned** (assignment happens later, line ~2124). This raises `NameError` every single call, but it's swallowed by a bare `except: pass` immediately around it, so the bug is permanently invisible and that entire debug branch never actually logs what it claims to. | `termbook.py:2111-2118` | Medium (masked bug, wasted work every call) |
| 3.3 | `supports_24bit_color()` unconditionally returns `True` ("Always return True to assume truecolor support as requested") regardless of actual `$COLORTERM`/`$TERM` capability. On a real terminal without truecolor (common over SSH, some tmux/screen configs, basic `xterm`), this will drive 24-bit-only rendering paths and produce garbled image output or escape-sequence artifacts instead of falling back gracefully. | `termbook.py:3913` | Medium |
| 3.4 | Keybinding help text documents `n`/`p` as both "Next/Prev chapter" and "Next/Prev Occurrence" (search) with no disambiguation in the help screen itself — a user reading `?` has no way to know these are context-dependent (only active as search-nav when a search is in progress). Minor but a real source of "the shortcut doesn't do what the help says" reports. | `termbook.py:16-31` (docstring), `help()` | Low |
| 3.5 | `# TODO: why different behaviour unix dos or win lin` left in `open_media` — an acknowledged, unresolved cross-platform bug in the media-opening path with no tracking beyond the inline comment. | `termbook.py:3477` | Low (flagged by the author already, but still open) |

---

## 4. Security

| # | Issue | Location | Severity |
|---|---|---|---|
| 4.1 | **`subprocess.run([..., url_to_open], shell=True, ...)` on Windows, where `url_to_open` is text extracted from EPUB chapter content via regex** (`find_urls_in_text`/inline fragment matching in `searching()`). EPUB files are untrusted, redistributable content (the whole point of the reader is to open ones you didn't author). On Windows, `shell=True` routes the argument list through `cmd.exe`; a crafted "URL-like" string in a malicious EPUB containing shell metacharacters (`&`, `|`, `^`, backticks-equivalent for cmd) that survives the URL regex could be interpreted by `cmd.exe` rather than treated as an inert opaque string, i.e. **prompted, one-keypress command execution when a user opens a malicious EPUB and presses `u` to open a link**. This pattern repeats at 4 call sites (`termbook.py:2257`, `3433`(via `open_media`), `5389`, `5427`, `5460` area). | multiple, e.g. `termbook.py:5389` | Medium-High (Windows-only, requires user keypress, but the whole product's threat model is "open files from strangers") |
| 4.2 | Debug logs are written to fixed, predictable, world-writable-directory paths (`/tmp/termbook_debug.log`, `/tmp/search_debug.log`) with plain `open(..., 'a')`/`'w'` and no `O_EXCL`/tempfile safety. On multi-user systems this is a classic symlink-race / info-disclosure smell (low severity here since content is just internal state, but it's the wrong pattern to have shipped) and, more practically, unbounded log growth in `/tmp` across sessions since it's append-mode with no rotation. | `termbook.py:1991,2111,2133,...,5533` (12 sites) | Low-Medium |

---

## 5. Performance

| # | Issue | Location | Severity |
|---|---|---|---|
| 5.1 | **Unconditional debug logging on hot paths.** `get_visible_images` (called on essentially every screen redraw/scroll) and the search functions open and append to `/tmp/*.log` files multiple times per invocation, several of them nested inside per-pixel/per-line loops. Every keystroke while typing a search, and every scroll/page-turn, incurs multiple synchronous file opens+writes for logging that isn't gated behind any debug flag (contrast with the `DEBUG_MODE` global that *does* exist and is used elsewhere, e.g. line 5874 — these debug blocks don't check it). | `termbook.py:2104-2222`, `2652-2755`, `4747-5533` | Medium-High (directly affects perceived scroll/search responsiveness) |
| 5.2 | **`render_image_curses` calls `img.getpixel()` per-pixel in nested Python loops** (4 calls per output character, in a double loop over the whole thumbnail). `Image.getpixel` is one of the slowest per-pixel access patterns in Pillow; `img.load()`'s pixel-access object or `list(img.getdata())` bulk access is an order of magnitude faster for this access pattern. Bounded by `thumbnail()` beforehand so not catastrophic, but this is the single biggest cost center in the image-render path and is easy to fix. | `termbook.py:3839-3906` | Medium |
| 5.3 | `_looks_like_code` recomputes multiple `re.findall`/`re.search` passes and rebuilds `words = re.findall(...)` over the full text on every call, with no caching — for large "is this a code block" decisions run repeatedly during pagination/re-render, this is redone from scratch each time the same block scrolls back into view. Not urgent given typical chapter sizes, but combined with §2's scoring complexity it's doing a lot of work for a boolean. | `termbook.py:612-1026` | Low |

---

## 6. Maintainability / architecture

| # | Issue | Detail | Severity |
|---|---|---|---|
| 6.1 | **Single 6,006-line file, no packages/modules.** EPUB parsing (`Epub`), HTML→text conversion + all rendering heuristics (`HTMLtoLines`, ~1,460 lines by itself, lines 244–1705), curses UI (dialogs, search, bookmarks, TOC), image rendering (4 different rendering backends: curses blocks, Fabulous, quarter-blocks, ANSI-256), and `main()`/CLI argument handling all live in one file with no internal package boundaries. There is no clear seam where "swap the image renderer" or "swap the language-detection strategy" could happen without touching a monolith. | High (compounds every other fix) |
| 6.2 | **`reader()` is ~340+ lines** (4626–4969+, continues past 5000 with duplicated inline classes, see 6.3) and is the main event-loop/dispatch function handling keypresses, search state, resize, bookmarks, and rendering in one function body. This is the kind of function where every new keybinding is a new `elif` in an already-long chain, raising the odds of shadowed/unreachable branches (as already happened at module scope with `get_visible_images`, §3.1). | High |
| 6.3 | **Repeated inline class definitions.** A small fake-`re.Match` shim (`class __init__/group/start/end`) is defined **four separate times** as a local class inside different functions (lines 1541, 4969, 5036, 5306) instead of once at module scope. Same logic, copy-pasted with drift risk each time one copy gets tweaked and the others don't. | Medium |
| 6.4 | **66 bare or effectively-bare `except` blocks** (`except:` or `except Exception: pass`-shaped) across the file. Several wrap logic that can hide real, load-bearing bugs (see §3.2, where a `NameError` in a debug block is invisibly swallowed). Broad exception handling this pervasive makes it very hard to trust that "it didn't crash" means "it worked." | High |
| 6.5 | **Near-zero test coverage of the highest-risk logic.** `tests/` has ~1,100 lines across 8 files (`test_syntax_highlighting.py`, `test_figure_detection.py`, etc.) that do exercise `get_token_color` and some dump-mode/figure-detection behavior, but **neither `_looks_like_code` nor `detect_language` is referenced anywhere in `tests/`** — the two functions this audit found to be actively wrong on common inputs have no direct tests at all, despite being the most complex, most-tuned code in the file. | High |
| 6.6 | Duplicated/overlapping keyword taxonomies for "what is code" living in three places with no shared source (§2.1(d)) is itself a maintainability smell independent of correctness: every future language added to one list needs manual, easy-to-forget updates to the other two. | Medium |
| 6.7 | Heavy reliance on module-level `global` state (19 distinct `global` declarations across `STATE`, `GLOBAL_BOOKMARKS`, `RESIZE_REQUESTED`, `_color_palette`, `_next_color_pair`, `SEARCHPATTERN`, `COLORSUPPORT`, `DEBUG_MODE`, etc.) plus a hand-rolled `Modal` class using classmethods over class-level mutable state as a singleton (line 2358). Workable for a single-threaded curses app but makes independent testing of any one subsystem (search, resize, modals) hard without dragging in the rest of the program's state. | Medium |

---

## 7. UI / UX observations

- **Image rendering has four separate backends** (curses quarter-block chars, Fabulous, a second "quarter block" implementation, ANSI-256→RGB conversion) with runtime capability detection (`find_media_viewer`, `supports_24bit_color`) that, per §3.3, is partly hardcoded rather than genuinely detected — so the "pick the best renderer for this terminal" logic is undermined by one of its own capability checks always returning the same answer.
- Color-pair allocation (`get_color_pair`, `rgb_to_color_index`, `find_closest_palette_color`) quantizes arbitrary image RGB values down to a limited curses palette — a reasonable approach — but it's invoked per-character in the render loop rather than pre-computed once per distinct color in the (thumbnailed, so bounded) image, meaning the same nearest-palette-color search can be repeated many times per frame.
- The help/hint system (`show_initial_help_message`, `show_persistent_hint`) and modal dialogs are reasonably factored as their own functions/class, which is one of the better-organized corners of the file — this part would translate cleanly into its own module if the file were split up.

---

## 8. Prioritized punch list

**Fix first (functional, user-visible, cheap to fix):**
1. Delete or rename the first `get_visible_images` (§3.1) — one is dead code, keeping both is actively misleading to future readers.
2. Reorder/rework `detect_language`'s heuristic chain so Java's overly-broad patterns (`this.`, `private `, `new `, `extends`) don't pre-empt TypeScript/JavaScript, and add `\b` word boundaries to all substring checks (§2.1). At minimum, move the Java branch after TS/JS, and drop the generic OO keywords (`this.`, `new `, `extends`) from the Java list entirely — they're not Java-specific.
3. Loosen `_looks_like_code`'s prose bias for short snippets, or better, wire it up to the source HTML's `<pre>`/`<code>` tags instead of re-deriving from stripped text (§2.4) — this is the structurally correct fix, not just a constant tweak.
4. Gate every debug-log write behind the existing `DEBUG_MODE` flag, or delete them (§5.1, §4.2) — quick, and removes a real perf cost from the hot path.

**Fix soon (correctness/security, less common but real):**
5. Fix the `NameError`-swallowing debug block in `get_visible_images` (§3.2) — trivial once you're in there for #1.
6. Replace `shell=True` Windows URL-open calls with `os.startfile(url)` (the standard, injection-safe Windows API for "open with default handler") instead of `subprocess.run(['start', url], shell=True)` (§4.1).
7. Make `supports_24bit_color()` actually check `$COLORTERM`/terminfo instead of hardcoding `True` (§3.3).

**Structural (worth planning, not urgent):**
8. Split `termbook.py` into modules (`epub.py`, `render_text.py`/heuristics, `render_image.py`, `ui/dialogs.py`, `search.py`, `cli.py`) — this is what makes 1–3 and future fixes safely testable in isolation (§6.1).
9. Add unit tests for `_looks_like_code` and `detect_language` covering short snippets, TS/JS/Java/Python/SQL, and adversarial prose, before touching their internals — right now there is no regression safety net for the exact code this audit flags (§6.5).
10. Extract the repeated fake-`re.Match` shim to a single module-level class (§6.3), and swap the widest bare `except:` blocks for narrow, named exceptions where the surrounding code doesn't obviously need "never crash the reader" behavior (§6.4).
