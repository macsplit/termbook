"""Regression tests for the code-detection and language-detection heuristics.

These are the Phase 0 "safety net" tests from REMEDIATION_PLAN.md. Two groups:

- xfail cases: known-bad classifications confirmed during the code audit
  (CODE_AUDIT.md, section 2). These currently FAIL and are marked xfail so
  the suite stays green; when Phase 2 fixes the heuristics, remove the
  xfail marker (or flip to a plain assertion) and the test becomes a
  permanent regression guard.
- corpus cases: known-good classifications that must not regress while
  Phase 2 is reworking the same code paths.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from termbook import HTMLtoLines


@pytest.fixture
def parser():
    return HTMLtoLines()


# ---------------------------------------------------------------------------
# Known-bad cases (audit section 2) -- currently fail, xfail until Phase 2.
# ---------------------------------------------------------------------------

class TestKnownBadLooksLikeCode:
    """_looks_like_code under-detects short, unambiguous code snippets."""

    @pytest.mark.xfail(reason="_looks_like_code prose bias misses short snippets (audit 2.2)", strict=True)
    def test_two_line_python_function_is_code(self, parser):
        text = "def add(a, b):\n    return a + b"
        assert parser._looks_like_code(text) is True

    @pytest.mark.xfail(reason="_looks_like_code prose bias misses short snippets (audit 2.2)", strict=True)
    def test_single_line_def_is_code(self, parser):
        text = "def square(x): return x * x"
        assert parser._looks_like_code(text) is True

    @pytest.mark.xfail(
        reason="_looks_like_code misses even a 7-line, unambiguous Python function "
               "(discovered while writing this regression suite -- not just the 2-line "
               "case originally flagged in the audit; the bias affects realistic "
               "function-length snippets, not just toy one-liners)",
        strict=True,
    )
    def test_medium_length_python_function_is_code(self, parser):
        text = (
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    a, b = 0, 1\n"
            "    for _ in range(n - 1):\n"
            "        a, b = b, a + b\n"
            "    return b\n"
        )
        assert parser._looks_like_code(text) is True


class TestKnownBadDetectLanguage:
    """detect_language's Java branch pre-empts TS/JS and matches on substrings."""

    @pytest.mark.xfail(reason="Java branch pre-empts TypeScript via 'this.'/'private ' (audit 2.1a)", strict=True)
    def test_typescript_class_detected_as_typescript(self, parser):
        code = (
            "export class UserService {\n"
            "  private users: User[] = [];\n"
            "\n"
            "  constructor(private http: HttpClient) {}\n"
            "\n"
            "  getUser(id: number): User {\n"
            "    return this.users.find(u => u.id === id);\n"
            "  }\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "TypeScript"

    @pytest.mark.xfail(reason="Java branch pre-empts JavaScript via 'this.'/'new ' (audit 2.1a)", strict=True)
    def test_javascript_class_detected_as_javascript(self, parser):
        code = (
            "class ShoppingCart {\n"
            "  constructor() {\n"
            "    this.items = [];\n"
            "  }\n"
            "  addItem(item) {\n"
            "    this.items.push(item);\n"
            "    return this.items.length;\n"
            "  }\n"
            "}\n"
            "const cart = new ShoppingCart();\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "JavaScript"

    @pytest.mark.xfail(reason="'new '/'static' substrings match inside prose words (audit 2.1b)", strict=True)
    def test_prose_with_renew_and_static_is_not_code(self, parser):
        text = (
            "You should renew your subscription before it lapses, and static "
            "analysis of this text shows nothing special."
        )
        lexer = parser.detect_language(text)
        # A correct implementation should not confidently return a
        # programming-language lexer for plain prose.
        assert lexer is None or lexer.name in ("Text output", "Text only")

    @pytest.mark.xfail(
        reason="the TypeScript branch's bare `'?' in code_text` check (audit 2.1) fires "
               "on ANY '?' character and is checked before the XML branch, so an XML "
               "document's own <?xml ...?> prolog routes it to TypeScript instead of XML "
               "(discovered while writing this regression suite)",
        strict=True,
    )
    def test_xml_prolog_detected_as_xml_not_typescript(self, parser):
        code = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<catalog>\n"
            "  <book id=\"1\"><title>Example</title></book>\n"
            "</catalog>\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "XML"

    @pytest.mark.xfail(
        reason="the TypeScript branch's bare `'?' in code_text` check (audit 2.1) fires "
               "on the ternary operator in C/Java/JS, so a plain C function using `? :` "
               "is detected as TypeScript instead of C (discovered while writing this "
               "regression suite)",
        strict=True,
    )
    def test_c_ternary_detected_as_c_not_typescript(self, parser):
        code = "int abs(int x) {\n  return x < 0 ? -x : x;\n}\n"
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "C"


# ---------------------------------------------------------------------------
# Known-good corpus -- must not regress while Phase 2 reworks the same code.
# ---------------------------------------------------------------------------

class TestLooksLikeCodeCorpus:

    def test_brace_heavy_js_snippet_is_code(self, parser):
        text = "if (x > 0) {\n  console.log(x);\n}"
        assert parser._looks_like_code(text) is True

    def test_python_class_is_code(self, parser):
        # Unlike a short/medium standalone function (see
        # TestKnownBadLooksLikeCode), a full class with varied line lengths
        # and indentation levels currently clears the prose-bias threshold.
        text = (
            "class BankAccount:\n"
            "    def __init__(self, balance=0):\n"
            "        self.balance = balance\n"
            "\n"
            "    def deposit(self, amount):\n"
            "        self.balance += amount\n"
            "        return self.balance\n"
            "\n"
            "    def withdraw(self, amount):\n"
            "        if amount > self.balance:\n"
            "            raise ValueError(\"Insufficient funds\")\n"
            "        self.balance -= amount\n"
            "        return self.balance\n"
        )
        assert parser._looks_like_code(text) is True

    def test_sql_query_is_code(self, parser):
        text = (
            "SELECT customers.name, orders.total\n"
            "FROM customers\n"
            "JOIN orders ON customers.id = orders.customer_id\n"
            "WHERE orders.total > 100\n"
            "ORDER BY orders.total DESC;\n"
        )
        assert parser._looks_like_code(text) is True

    def test_plain_prose_paragraph_is_not_code(self, parser):
        text = (
            "The committee met on Thursday to discuss the proposal, and "
            "although several members raised concerns about the timeline, "
            "a majority ultimately agreed that the plan, however imperfect, "
            "represented meaningful progress toward the stated goals."
        )
        assert parser._looks_like_code(text) is False

    def test_narrative_prose_with_punctuation_is_not_code(self, parser):
        text = (
            "It was a bright, cold day in April, and the clocks were "
            "striking thirteen. Winston Smith, his chin nuzzled into his "
            "breast in an effort to escape the vile wind, slipped quickly "
            "through the glass doors, though not quickly enough to prevent "
            "a swirl of gritty dust from entering along with him."
        )
        assert parser._looks_like_code(text) is False


class TestDetectLanguageCorpus:

    def test_python_snippet_detected_as_python(self, parser):
        code = (
            "import json\n"
            "\n"
            "def load_config(path):\n"
            "    with open(path) as f:\n"
            "        return json.load(f)\n"
            "\n"
            "print(load_config('config.json'))\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Python"

    def test_c_snippet_detected_as_c(self, parser):
        code = (
            "#include <stdio.h>\n"
            "\n"
            "int main(void) {\n"
            "    printf(\"hello, world\\n\");\n"
            "    return 0;\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "C"

    def test_xml_snippet_without_prolog_detected_as_xml(self, parser):
        # Note: an XML document WITH its <?xml ...?> prolog currently fails
        # (see TestKnownBadDetectLanguage.test_xml_prolog_detected_as_xml_not_typescript)
        # because the literal '?' routes it to the TypeScript branch first.
        # This variant, without the prolog, is unaffected and currently passes.
        code = (
            "<catalog>\n"
            "  <book id=\"1\"><title>Example</title></book>\n"
            "</catalog>\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "XML"

    def test_hint_lang_short_circuits_heuristics(self, parser):
        # A valid hint (e.g. from a <pre class="language-python"> tag) must
        # win outright, regardless of what the heuristics would guess.
        code = "this.value = 1;"  # looks Java/JS-ish, but hint says Python
        lexer = parser.detect_language(code, hint_lang="python")
        assert lexer is not None
        assert lexer.name == "Python"
