"""Regression tests for the code-detection and language-detection heuristics.

Originally written (Phase 0 of REMEDIATION_PLAN.md) as a safety net: xfail
tests pinning down known-bad classifications from the code audit, plus a
corpus of known-good classifications that had to keep passing while Phase 2
reworked the same code paths.

Phase 2 has since fixed every case that was marked xfail here (see
CODE_AUDIT.md section 2 and REMEDIATION_PLAN.md Phase 2) -- the markers have
been removed and those tests are now plain regression guards alongside the
corpus.
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
# Fixed in Phase 2 (was xfail; see CODE_AUDIT.md 2.2 / REMEDIATION_PLAN.md).
# ---------------------------------------------------------------------------

class TestLooksLikeCodeFixedInPhase2:
    """_looks_like_code used to under-detect short, unambiguous code
    snippets due to a flat prose-favoring bias; Phase 2 added structural
    short-circuits for unambiguous syntactic shapes (def/class lines,
    #include, SQL statements) that fire regardless of snippet length."""

    def test_two_line_python_function_is_code(self, parser):
        text = "def add(a, b):\n    return a + b"
        assert parser._looks_like_code(text) is True

    def test_single_line_def_is_code(self, parser):
        text = "def square(x): return x * x"
        assert parser._looks_like_code(text) is True

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


class TestDetectLanguageFixedInPhase2:
    """Regression guards for detect_language bugs fixed in Phase 2 (was
    TestKnownBadDetectLanguage with xfail markers; those markers were
    removed once the underlying heuristic was actually fixed -- see
    REMEDIATION_PLAN.md Phase 2 / CODE_AUDIT.md section 2.1)."""

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

    def test_prose_with_renew_and_static_is_not_code(self, parser):
        text = (
            "You should renew your subscription before it lapses, and static "
            "analysis of this text shows nothing special."
        )
        lexer = parser.detect_language(text)
        # A correct implementation should not confidently return a
        # programming-language lexer for plain prose.
        assert lexer is None or lexer.name in ("Text output", "Text only")

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

    def test_c_ternary_not_misdetected_as_typescript(self, parser):
        # The original bug (fixed here): the TypeScript branch's bare
        # `'?' in code_text` check matched this ternary and claimed the
        # snippet as TypeScript. That's fixed -- it no longer does.
        #
        # What's NOT fixed, by design: a marker-free C function like this
        # (no #include/int main(/printf() doesn't positively resolve to
        # "C" either; it falls through to guess_lexer, which is not
        # reliable on short snippets (see CODE_AUDIT.md 2.1, corrected
        # recommendation). An earlier version of this fix added a
        # marker-free "primitive-type function(...) {" pattern to close
        # that gap, but validating against real books showed it caused
        # worse regressions than it solved (see
        # test_canonical_java_hello_world_detected_as_java_not_c and
        # REMEDIATION_PLAN.md Phase 2), so it was reverted. Closing this
        # gap without reintroducing that regression is left as future work.
        code = "int abs(int x) {\n  return x < 0 ? -x : x;\n}\n"
        lexer = parser.detect_language(code)
        assert lexer is None or lexer.name != "TypeScript"


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

    def test_c_include_snippet_is_code(self, parser):
        text = (
            "#include <stdio.h>\n"
            "int main(void) {\n"
            "    printf(\"hi\");\n"
            "    return 0;\n"
            "}\n"
        )
        assert parser._looks_like_code(text) is True

    def test_short_sql_statement_is_code(self, parser):
        text = "SELECT * FROM users WHERE active = 1;"
        assert parser._looks_like_code(text) is True

    def test_prose_with_class_and_colon_is_not_code(self, parser):
        # Guards the "class Foo:" structural short-circuit added in Phase 2
        # against a prose sentence that happens to start a line the same
        # way but isn't a Python class declaration -- the pattern requires
        # the colon to end the line, which this deliberately violates.
        text = "Class Rank: novice, expert, and master are the three tiers available."
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

    def test_sql_snippet_detected_as_sql(self, parser):
        # Phase 2: added an explicit SQL branch -- previously there was none,
        # and this fell through to guess_lexer, which (verified empirically)
        # misidentified it as "scdoc".
        code = (
            "SELECT customers.name, orders.total\n"
            "FROM customers\n"
            "JOIN orders ON customers.id = orders.customer_id\n"
            "WHERE orders.total > 100\n"
            "ORDER BY orders.total DESC;\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "SQL"

    def test_cypher_snippet_detected_as_cypher(self, parser):
        code = (
            "MATCH (n:Person)-[:KNOWS]->(m:Person)\n"
            "WHERE n.name = 'Alice'\n"
            "RETURN m.name\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Cypher"

    def test_python_with_colons_not_misdetected_as_cypher(self, parser):
        # Phase 2: the old Cypher branch accepted a bare ':' anywhere in the
        # text as supporting evidence, which any Python function with a
        # block-opening colon would satisfy. Replaced with the actual
        # Cypher relationship-type syntax '[:'.
        code = (
            "def fibonacci(n):\n"
            "    if n <= 1:\n"
            "        return n\n"
            "    a, b = 0, 1\n"
            "    for _ in range(n - 1):\n"
            "        a, b = b, a + b\n"
            "    return b\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Python"

    def test_canonical_java_hello_world_detected_as_java_not_c(self, parser):
        # Caught by manual sweeping after the initial Phase 2 rewrite: the
        # new C branch's marker-free "primitive-type function(...) {" pattern
        # (added to catch things like "int abs(int x) {") also matched
        # "void main(String[] args) {" and, since C was checked before Java,
        # claimed this before Java ever got a look at it.
        code = (
            "public class HelloWorld {\n"
            "    public static void main(String[] args) {\n"
            "        System.out.println(\"Hello, world!\");\n"
            "    }\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Java"

    def test_java_getter_without_main_detected_as_java_not_typescript(self, parser):
        # Caught by manual sweeping: a Java class with `public`/`private`
        # modifiers used to be claimed by the TypeScript branch's
        # `class + (public or private)` combinator, since that's just as
        # true of Java as of TypeScript. Removed that combinator; the
        # remaining TS signals are actual `name: Type` annotations, which
        # Java (type-first: `String name`) never produces.
        code = (
            "public class Person {\n"
            "    private String name;\n"
            "    public String getName() {\n"
            "        return this.name;\n"
            "    }\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Java"

    def test_java_interface_detected_as_java_not_typescript(self, parser):
        # Caught by manual sweeping: the TypeScript branch's bare `interface`
        # keyword check used to claim this, but `public interface Foo` is
        # just as valid Java as it is TypeScript.
        code = "public interface PaymentProcessor {\n    void process(double amount);\n}\n"
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Java"

    def test_java_enum_detected_as_java_not_typescript(self, parser):
        # Caught by manual sweeping: same issue as the interface case above,
        # but for the bare `enum` keyword.
        code = "public enum Status {\n    ACTIVE, INACTIVE, PENDING\n}\n"
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Java"

    def test_typescript_interface_detected_as_typescript(self, parser):
        code = (
            "interface Point {\n"
            "  x: number;\n"
            "  y: number;\n"
            "}\n"
            "function distance(a: Point, b: Point): number {\n"
            "  return Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "TypeScript"

    def test_real_java_class_from_security_book_not_misdetected_as_python(self, parser):
        # Found by validating against a real book (Secure by Design):
        # an earlier version of the Python branch accepted `import` + `print`
        # together as a weak signal, but both words are completely ordinary
        # in Java (import statements, System.out.print calls) too.
        code = (
            "import static javax.xml.XMLConstants.FEATURE_SECURE_PROCESSING;\n"
            "public final class XMLParser {\n"
            "  static final String DISALLOW_DOCTYPE =\n"
            "         \"http://apache.org/xml/features/disallow-doctype-decl\";\n"
            "  static final String ALLOW_EXT_GEN_ENTITIES =\n"
            "         \"http://xml.org/sax/features/external-general-entities\";\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Java"

    def test_real_java_method_from_security_book_not_misdetected_as_c(self, parser):
        # Found by validating against a real book (Secure by Design): an
        # earlier version of the C branch matched any bare "primitive-type
        # function(...) {" shape, including this package-private Java
        # method with no access modifier in front of `void` to exclude it.
        code = (
            "class Book {\n"
            "    String title;\n"
            "    String isbn;\n"
            "    double price;\n"
            "}\n"
            "class Order {\n"
            "    void addOrderLine(Book book, int quantity) {\n"
            "    }\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name != "C"

    def test_java_bare_method_with_throws_clause_is_java(self, parser):
        # Found by validating against a real book (Secure by Design): a
        # standalone Java method with no surrounding class visible and no
        # import/println/main in view was falling through to guess_lexer.
        # A `throws SomeException` clause is Java/C#-specific -- TS/JS have
        # no equivalent syntax.
        code = (
            "import static org.apache.commons.lang3.Validate.validState;\n"
            "private void checkInvariants()\n"
            "    throws IllegalStateException {\n"
            "    validState(fallbackAccount != null);\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Java"

    def test_java_bare_class_with_typed_fields_is_java(self, parser):
        # Found by validating against a real book: a simplified/pedagogical
        # Java class with no access modifier on the class itself, no
        # imports, and no println/main -- just type-first field
        # declarations ("String title;"), which is the actual discriminator
        # against TypeScript's name-first style ("title: string;").
        code = (
            "class Book {\n"
            "    String title;\n"
            "    String isbn;\n"
            "    double price;\n"
            "}\n"
        )
        lexer = parser.detect_language(code)
        assert lexer is not None
        assert lexer.name == "Java"
