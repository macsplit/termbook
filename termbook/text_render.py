"""HTML-to-plain-text conversion and code-block language detection.

Curses-independent: also used by --dump mode, which renders EPUB chapters
without a terminal UI.
"""

import re
import sys
import textwrap
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote

from termbook import state

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
    from pygments.util import ClassNotFound
    from pygments.formatters import get_formatter_by_name
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


class HTMLtoLines(HTMLParser):
    para = {"p", "div"}
    inde = {"q", "dt", "dd", "blockquote"}
    pref = {"pre"}
    code = {"code"}  # Add code tag detection
    bull = {"li"}
    hide = {"script", "style", "head"}
    # hide = {"script", "style", "head", ", "sub}

    # Shared with detect_language()'s SQL branch, so the two "is this SQL"
    # signals can't silently drift apart the way the Java keyword lists did
    # (see CODE_AUDIT.md section 2.1(d) / 2.4).
    SQL_KEYWORDS = frozenset({
        'select', 'from', 'where', 'join', 'inner', 'left', 'right', 'outer', 'on',
        'group', 'order', 'having', 'distinct', 'limit', 'offset', 'union', 'intersect',
        'create', 'alter', 'drop', 'insert', 'update', 'delete', 'truncate',
    })

    def __init__(self, dump_mode=False):
        HTMLParser.__init__(self)
        self.text = [""]
        self.imgs = []
        self.img_alts = []  # Store alt text for images
        self.dump_mode = dump_mode  # Flag for plain text dump mode
        self.ishead = False
        self.isinde = False
        self.isbull = False
        self.ispref = False
        self.iscode = False  # Track if we're in a code block
        self.isprose = False  # Track if explicitly marked as prose via class
        self.ishidden = False
        self.in_sup = False  # Track if we're in a superscript tag
        self.in_sub = False  # Track if we're in a subscript tag
        self.iscaption = False  # Track if we're in a listing/figure caption
        self.in_pre_block = False  # Track if we're inside a <pre> block
        self.current_link_href = None  # Track current link href
        self.idhead = set()
        self.idinde = set()
        self.idbull = set()
        self.idpref = set()
        self.idcode = set()  # Track code block line indices
        self.idprose = set()  # Track prose block line indices
        self.idcaption = set()  # Track listing/figure caption line indices
        self.code_lang = None  # Track detected language for current code block

    def handle_starttag(self, tag, attrs):
        # In dump mode, skip image and link tracking
        if self.dump_mode:
            if tag == "img":
                return  # Skip images in dump mode
        
        # Check for special classes on ANY tag (highest priority)
        for attr_name, attr_value in attrs:
            if attr_name == "class" and attr_value:
                for class_name in attr_value.split():
                    if "caption" in class_name.lower():
                        # Any class containing "caption" indicates a caption
                        self.iscaption = True
                        break
                    elif "text" in class_name.lower():
                        # Any class containing "text" indicates prose, not code
                        self.isprose = True
                        break
                if self.isprose or self.iscaption:
                    break
        
        if re.match("h[1-6]", tag) is not None:
            self.ishead = True
        elif tag in self.inde:
            self.isinde = True
        elif tag in self.pref:
            self.ispref = True
            # Check if this pre tag has language info or indicates code
            self.code_lang = None
            # Reset prose flag - <pre> tags should take priority over parent class attributes
            self.isprose = False
            # Mark that we're in a pre block for nested tag handling
            self.in_pre_block = True
            
            for attr_name, attr_value in attrs:
                if attr_name == "class" and attr_value:
                    # Look for prose-indicating classes FIRST (highest priority)
                    for class_name in attr_value.split():
                        if "text" in class_name.lower():
                            # Any class containing "text" indicates prose, not code
                            self.isprose = True  # Force this to be treated as prose
                            break
                    
                    # Only check for code classes if not already marked as prose
                    if not self.isprose:
                        for class_name in attr_value.split():
                            if class_name.startswith(("language-", "lang-")):
                                self.code_lang = class_name.replace("language-", "").replace("lang-", "")
                                break
                            # Also detect common code block class names including "code-area"
                            elif class_name in ("programlisting", "code", "codeintext", "sourceCode", "highlight", "code-area") or "screen" in class_name.lower():
                                # This pre tag likely contains code, mark it as code
                                self.iscode = True  # Force this to be treated as code
                                break
        elif tag in self.code:
            self.iscode = True
            # Reset prose flag - <code> tags should take priority over parent class attributes
            self.isprose = False
            # Check for language info in code tag
            self.code_lang = None 
            for attr_name, attr_value in attrs:
                if attr_name == "class" and attr_value:
                    for class_name in attr_value.split():
                        if class_name.startswith(("language-", "lang-")):
                            self.code_lang = class_name.replace("language-", "").replace("lang-", "")
                            break
        elif tag in self.bull:
            self.isbull = True
        elif tag in self.hide:
            self.ishidden = True
        elif tag == "sup":
            # Handle superscript - use superscript numbers if possible, otherwise brackets
            self.in_sup = True
            self.text[-1] += "["
        elif tag == "sub":
            # Handle subscript - use brackets for clarity
            self.in_sub = True
            self.text[-1] += "₍"
        elif tag == "a":
            # Handle anchor/link tags
            for attr_name, attr_value in attrs:
                if attr_name == "href" and attr_value:
                    # Store the href for potential use
                    self.current_link_href = attr_value
                    break
            else:
                self.current_link_href = None
        # NOTE: "img" and "image"
        # In HTML, both are startendtag (no need endtag)
        # but in XHTML both need endtag
        elif tag in {"img", "image"}:
            if self.dump_mode:
                # Skip images completely in dump mode
                return
            img_src = None
            img_alt = ""
            for i in attrs:
                if (tag == "img" and i[0] == "src")\
                   or (tag == "image" and i[0].endswith("href")):
                    img_src = unquote(i[1])
                elif i[0] == "alt":
                    img_alt = i[1]
            if img_src:
                self.text.append("[IMG:{}]".format(len(self.imgs)))
                self.imgs.append(img_src)
                self.img_alts.append(img_alt)

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.text += [""]
        elif tag in {"img", "image"}:
            if self.dump_mode:
                # Skip images completely in dump mode
                return
            img_src = None
            img_alt = ""
            for i in attrs:
                if (tag == "img" and i[0] == "src")\
                   or (tag == "image" and i[0].endswith("href")):
                    img_src = unquote(i[1])
                elif i[0] == "alt":
                    img_alt = i[1]
            if img_src:
                self.text.append("[IMG:{}]".format(len(self.imgs)))
                self.imgs.append(img_src)
                self.img_alts.append(img_alt)
                self.text.append("")

    def handle_endtag(self, tag):
        if re.match("h[1-6]", tag) is not None:
            self.text.append("")
            self.text.append("")
            self.ishead = False
        elif tag in self.para:
            self.text.append("")
            self.isprose = False  # Reset prose flag when paragraph ends
            self.iscaption = False  # Reset caption flag when paragraph ends
        elif tag in self.hide:
            self.ishidden = False
        elif tag in self.inde:
            if self.text[-1] != "":
                self.text.append("")
            self.isinde = False
        elif tag in self.pref:
            if self.text[-1] != "":
                self.text.append("")
            self.ispref = False
            self.in_pre_block = False  # No longer in pre block
            # Reset iscode when pre tag ends (it may have been set by class attribute)
            # But only if we're not inside a nested code tag
            self.iscode = False
            self.isprose = False  # Reset prose flag
            self.code_lang = None  # Reset language
        elif tag in self.code:
            # Don't reset iscode if we're still inside a pre block
            # This prevents nested <pre><code> from losing code context
            if not self.in_pre_block:
                self.iscode = False
            self.code_lang = None  # Reset language
        elif tag in self.bull:
            if self.text[-1] != "":
                self.text.append("")
            self.isbull = False
        elif tag == "sup":
            self.text[-1] += "]"
            self.in_sup = False
        elif tag == "sub":
            self.text[-1] += "₎"
            self.in_sub = False
        elif tag == "a":
            # Clear the href when the link ends
            self.current_link_href = None
        elif tag in {"img", "image"}:
            self.text.append("")
        
        # Reset annotation flag when any tag ends (since annotations are span-based)
        if tag == "span":
            self.isannotation = False

    def handle_data(self, raw):
        if raw and not self.ishidden:
            if self.text[-1] == "":
                tmp = raw.lstrip()
            else:
                tmp = raw
            if self.ispref:
                line = unescape(tmp)
            else:
                line = unescape(re.sub(r"\s+", " ", tmp))
            
            # Replace problematic bullet characters with proper Unicode bullet
            line = line.replace('■', '•')  # Replace black square with bullet
            line = line.replace('▪', '•')  # Replace black small square with bullet
            line = line.replace('▫', '◦')  # Replace white small square with white bullet
            line = line.replace('◾', '•')  # Replace black medium small square with bullet
            line = line.replace('◽', '◦')  # Replace white medium small square with white bullet
            
            # Convert circled numbers to readable annotation format for code listings
            # These are commonly used in technical books to annotate code
            circled_numbers = {
                '①': '#1', '②': '#2', '③': '#3', '④': '#4', '⑤': '#5',
                '⑥': '#6', '⑦': '#7', '⑧': '#8', '⑨': '#9', '⑩': '#10',
                '⑪': '#11', '⑫': '#12', '⑬': '#13', '⑭': '#14', '⑮': '#15',
                '⑯': '#16', '⑰': '#17', '⑱': '#18', '⑲': '#19', '⑳': '#20'
            }
            for circled, annotation in circled_numbers.items():
                # Replace circled numbers with compact annotation format
                # Instead of adding lots of padding, just use minimal spacing
                line = line.replace(circled, f' {annotation}')
            
            # Special handling for links - use href for external URLs, keep text for internal refs
            if self.dump_mode:
                # In dump mode, just use the text content
                self.text[-1] += line
            elif self.current_link_href and re.match(r'https?://', self.current_link_href):
                # For external URLs, use the href URL and ignore the text
                # The URL will be highlighted by the URL detection later
                self.text[-1] += self.current_link_href
            else:
                # For internal references or non-URL hrefs, keep the original text
                self.text[-1] += line
            if self.ishead:
                self.idhead.add(len(self.text)-1)
            elif self.isbull:
                self.idbull.add(len(self.text)-1)
            elif self.isinde:
                self.idinde.add(len(self.text)-1)
            elif self.ispref or self.in_pre_block:
                # Mark as preformatted if we're in a <pre> block OR if we were in one recently
                # This handles inline tags within <pre> blocks that might lose context
                self.idpref.add(len(self.text)-1)
                if self.iscode or self.in_pre_block:  # If we're in any form of code context
                    self.idcode.add(len(self.text)-1)
                    # Remove from prose if it was previously marked (prevents dual marking)
                    self.idprose.discard(len(self.text)-1)
                # NOTE: Don't mark as prose here - <pre> tags override parent class attributes
            elif self.iscaption:  # Mark as caption if class contains "caption"
                self.idcaption.add(len(self.text)-1)
            elif self.isprose:  # Mark regular text as prose if class contains "text"
                self.idprose.add(len(self.text)-1)
            # Default case: if we're not in any special block, treat as regular prose
            # This handles regular <p> paragraphs that don't have special classes
            else:
                pass  # Regular text - will be processed as normal prose in get_lines()

    def _is_continuation_line(self, current_line, next_line, current_idx=None, next_idx=None):
        """Check if next_line is a continuation of current_line"""
        if not current_line or not next_line:
            return False
        
        # CRITICAL: Don't concatenate different content types (code, bullets, prose, headers)
        if current_idx is not None and next_idx is not None:
            current_is_code = current_idx in self.idcode
            next_is_code = next_idx in self.idcode
            current_is_bullet = current_idx in self.idbull
            next_is_bullet = next_idx in self.idbull
            current_is_prose = current_idx in self.idprose
            next_is_prose = next_idx in self.idprose
            current_is_header = current_idx in self.idhead
            next_is_header = next_idx in self.idhead
            current_is_caption = current_idx in self.idcaption
            next_is_caption = next_idx in self.idcaption
            
            # Never concatenate headers or captions with anything else
            if current_is_header or next_is_header or current_is_caption or next_is_caption:
                return False
            
            # Never concatenate code with anything else
            if current_is_code or next_is_code:
                return False
            
            # Never concatenate bullets with different types
            if current_is_bullet and not next_is_bullet:
                return False
            if next_is_bullet and not current_is_bullet:
                return False
            
            # Only concatenate prose with prose
            if current_is_prose != next_is_prose:
                return False
        
        current_stripped = current_line.strip()
        next_stripped = next_line.strip()
        
        # Don't concatenate if current line is empty or next line is empty
        if not current_stripped or not next_stripped:
            return False
        
        # Don't concatenate if current line ends with sentence endings
        if current_stripped.endswith(('.', '!', '?', ':', ';')):
            return False
        
        # Don't concatenate if next line appears to be a figure/listing caption or title
        import re
        caption_pattern = re.compile(r'^(Figure|Listing|Table|Example|Exhibit|Diagram|Chart|Graph|Illustration|Code|Algorithm|Equation)\s+\d+\.?\d*', re.IGNORECASE)
        if caption_pattern.match(next_stripped):
            return False
        # Also don't concatenate if current line IS a listing/figure caption
        if caption_pattern.match(current_stripped):
            return False
        
        # Don't concatenate if next line starts with typical paragraph starters
        paragraph_starters = ['Chapter', 'CHAPTER', 'Part', 'PART', '1.', '2.', '3.', '4.', '5.', 
                             '6.', '7.', '8.', '9.', '0.', 'I.', 'II.', 'III.', 'IV.', 'V.',
                             'A.', 'B.', 'C.', 'D.', 'E.', 'F.', 'G.', 'H.', 'I.', 'J.',
                             '*', '-', '•', '◦', '▪', '▫']
        
        if any(next_stripped.startswith(starter) for starter in paragraph_starters):
            return False
        
        # Don't concatenate if next line starts with a capital letter after a line that doesn't end mid-sentence
        if (next_stripped[0].isupper() and 
            not current_stripped.endswith((',', 'and', 'or', 'but', 'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'with'))):
            return False
        
        # Don't concatenate if there's a significant indentation change
        current_indent = len(current_line) - len(current_line.lstrip())
        next_indent = len(next_line) - len(next_line.lstrip())
        if abs(current_indent - next_indent) > 2:
            return False
        
        # Don't concatenate lines that look like code (contain import, class, etc)
        code_keywords = ['import ', 'class ', 'public ', 'private ', 'def ', 'function ', 'var ', 'const ', 'let ']
        if any(keyword in current_line.lower() or keyword in next_line.lower() for keyword in code_keywords):
            return False
        
        # Don't concatenate if line contains footnote markers
        if re.search(r'\[\d+\]|\^\d+|［\d+］', current_stripped) or re.search(r'\[\d+\]|\^\d+|［\d+］', next_stripped):
            return False
        
        return True

    def _looks_like_code(self, text):
        """
        Comprehensive weighted heuristic to determine if text looks like code.
        Uses multiple factors with different weights to score code vs prose likelihood.
        """
        if not text.strip():
            return False
        
        lines = [line.rstrip() for line in text.split('\n') if line.strip()]
        if not lines:
            return False
        
        # Check if this is a "Listing X.X" style code block
        first_line = lines[0].strip()
        if re.match(r'^Listing\s+\d+\.?\d*\s+', first_line, re.IGNORECASE):
            # This is a code listing - always treat as code
            return True
        
        # Check for XML content - very strong indicator
        if any(line.strip().startswith('<?xml') for line in lines):
            return True
        if any(line.strip().startswith('<!DOCTYPE') for line in lines):
            return True
        # XML tags pattern (but not HTML-like tags that appear in prose)
        xml_tag_count = len(re.findall(r'<[^/>][^>]*>[^<]*</[^>]+>', text))
        if xml_tag_count >= 2:
            return True

        # Structural short-circuits: a handful of syntactic shapes that are
        # effectively never produced by English prose, checked before the
        # weighted score below. Without this, the scoring's flat prose bias
        # (prose_score starts at 20, and several of its strongest signals
        # need 2-3+ lines to engage) meant a short, completely ordinary
        # function -- e.g. a 1-2 line Python function, exactly the kind of
        # thing a "Listing 3.1 shows a simple accessor" caption introduces --
        # was classified as prose, since it never accumulated enough score
        # to clear the threshold.
        structural_code_patterns = (
            r'^\s*def\s+\w+\s*\([^)]*\)\s*:',                 # Python def line
            r'^\s*async\s+def\s+\w+\s*\([^)]*\)\s*:',         # Python async def
            r'^\s*class\s+\w+\s*(\([^)]*\))?\s*:\s*$',        # Python class Foo(Base):
            r'^\s*class\s+\w+[^{]*\{\s*$',                    # JS/TS/Java class Foo {
            r'^\s*#include\s*[<"]',                           # C/C++ #include
            r'^\s*(select|insert|update|delete)\b.*\b(from|into|set)\b',  # SQL statement
        )
        for line in lines:
            for pattern in structural_code_patterns:
                if re.match(pattern, line, re.IGNORECASE):
                    return True

        code_score = 0
        prose_score = 20   # Start with reasonable prose advantage
        
        # Strong programming keywords (unambiguous code indicators). The SQL
        # subset is shared with detect_language()'s SQL branch via
        # SQL_KEYWORDS (see class attribute) rather than kept as a second,
        # independently-maintained copy.
        strong_code_keywords = {
            'import', 'export', 'def', 'class', 'const', 'let', 'var', 'async', 'await',
            'yield', 'lambda', 'implements', 'interface', 'enum', 'struct', 'union',
            'public', 'private', 'protected', 'static', 'final', 'void', 'null', 'undefined',
            'extends', 'super', 'this', 'self', 'typeof', 'instanceof', 'new', 'delete',
            'throw', 'throws', 'catch', 'finally', 'try',
            # Cypher/Neo4j keywords
            'match', 'merge', 'optional', 'with', 'unwind', 'return', 'skip', 'collect',
            'load', 'csv', 'headers', 'node', 'relationship', 'path', 'call', 'yield',
            # Shell/Bash keywords
            'echo', 'cd', 'ls', 'mkdir', 'chmod', 'grep', 'awk', 'sed', 'ps', 'kill'
        } | self.SQL_KEYWORDS
        
        # Weak programming keywords (appear in both code and prose contexts)
        # Removed: 'in', 'for', 'as', 'is', 'of', 'out', 'if', 'then', 'else' - too common in English
        weak_code_keywords = {
            'function', 'return', 'elif', 'while', 'do', 'switch', 
            'case', 'break', 'continue', 'and', 'or', 'not', 'true', 'false',
            'int', 'string', 'bool', 'boolean', 'float', 'double', 'char', 'end'
        }
        
        # Count programming keywords with different weights
        text_lower = text.lower()
        strong_keyword_count = sum(1 for keyword in strong_code_keywords if re.search(r'\b' + keyword + r'\b', text_lower))
        weak_keyword_count = sum(1 for keyword in weak_code_keywords if re.search(r'\b' + keyword + r'\b', text_lower))
        
        # Keywords need to compete with massive start consistency weighting
        if strong_keyword_count >= 6:
            code_score += 140  # OVERWHELMING for 6+ keywords (SQL queries, etc.)
        elif strong_keyword_count >= 4:
            code_score += 80   # Very strong for 4+ keywords
        elif strong_keyword_count >= 3:
            code_score += 40   # Strong for 3+ keywords
        elif strong_keyword_count >= 2:
            code_score += 20   # Moderate for 2+ keywords
        elif strong_keyword_count >= 1:
            code_score += 8    # Basic for 1+ keyword
            
        # Weak keywords have almost no impact
        if weak_keyword_count >= 5:
            code_score += 1   # Minimal impact
        
        # PROGRAMMING PATTERNS: Specific code patterns that are rare in prose
        pattern_score = 0
        
        # if(! pattern is very strong indicator of conditional negation in code
        if_not_pattern = len(re.findall(r'if\s*\(\s*!', text_lower))
        if if_not_pattern >= 1:
            pattern_score += if_not_pattern * 30  # Strong boost per occurrence
        
        # Other strong programming patterns
        # while(condition) pattern
        while_paren_pattern = len(re.findall(r'while\s*\([^)]+\)', text_lower))
        if while_paren_pattern >= 1:
            pattern_score += while_paren_pattern * 25
        
        # for(initialization; condition; increment) pattern
        for_loop_pattern = len(re.findall(r'for\s*\([^)]*;[^)]*;[^)]*\)', text_lower))
        if for_loop_pattern >= 1:
            pattern_score += for_loop_pattern * 35  # Very strong C-style for loop
        
        # switch(variable) pattern
        switch_paren_pattern = len(re.findall(r'switch\s*\([^)]+\)', text_lower))
        if switch_paren_pattern >= 1:
            pattern_score += switch_paren_pattern * 30
        
        code_score += pattern_score
        
        # Analyze line length irregularity - MAJOR CODE INDICATOR (both overall and line-to-line variation)
        line_lengths = [len(line.strip()) for line in lines]
        if len(line_lengths) > 2:  # Need at least 3 lines to assess irregularity
            
            # 1. OVERALL VARIATION (coefficient of variation across all lines)
            avg_length = sum(line_lengths) / len(line_lengths)
            length_variance = sum((length - avg_length) ** 2 for length in line_lengths) / len(line_lengths)
            length_stddev = length_variance ** 0.5
            overall_variation_score = 0
            
            if avg_length > 0:
                cv = length_stddev / avg_length  # Coefficient of variation
                
                # High overall irregularity is a STRONG code indicator
                if cv > 0.4:  # High variability (>40% of mean)
                    overall_variation_score = 40  # Major code indicator
                elif cv > 0.25: # Moderate-high variability (>25% of mean)
                    overall_variation_score = 25  # Strong code indicator
                elif cv > 0.15: # Moderate variability (>15% of mean)
                    overall_variation_score = 15  # Moderate code indicator
            
            # 2. LINE-TO-LINE VARIATION (how much adjacent lines differ)
            line_to_line_diffs = []
            for i in range(len(line_lengths) - 1):
                diff = abs(line_lengths[i+1] - line_lengths[i])
                if line_lengths[i] > 0:  # Avoid division by zero
                    relative_diff = diff / line_lengths[i]
                    line_to_line_diffs.append(relative_diff)
            
            line_to_line_score = 0
            if line_to_line_diffs:
                avg_line_diff = sum(line_to_line_diffs) / len(line_to_line_diffs)
                
                # Extreme line-to-line variation overwhelms consistency (as important as overall variation)
                if avg_line_diff > 2.0:  # >200% average change (alternating very long/short)
                    line_to_line_score = 140  # OVERWHELMING code indicator - beats consistency
                elif avg_line_diff > 1.0:  # >100% average change
                    line_to_line_score = 80   # Very strong code indicator
                elif avg_line_diff > 0.5:  # >50% average change
                    line_to_line_score = 50   # Strong code indicator
                elif avg_line_diff > 0.3:  # >30% average change between adjacent lines
                    line_to_line_score = 40   # Major code indicator
                elif avg_line_diff > 0.2:  # >20% average change
                    line_to_line_score = 25   # Strong code indicator  
                elif avg_line_diff > 0.1:  # >10% average change
                    line_to_line_score = 15   # Moderate code indicator
            
            # 3. RANGE VARIATION (min-max difference) 
            range_variation_score = 0
            if line_lengths:
                min_length = min(line_lengths)
                max_length = max(line_lengths)
                if max_length > 0:
                    length_range_ratio = (max_length - min_length) / max_length
                    if length_range_ratio > 0.7:  # >70% difference between shortest and longest
                        range_variation_score = 25  # Strong code indicator
                    elif length_range_ratio > 0.5:  # >50% difference
                        range_variation_score = 15  # Moderate code indicator
            
            # Take the MAXIMUM of the three variation types (they reinforce each other)
            variation_score = max(overall_variation_score, line_to_line_score, range_variation_score)
            code_score += variation_score
        
        # Check for consistent indentation patterns (multiples of 2, 4, 8 spaces or tabs)
        indented_lines = 0
        consistent_indent = 0
        for line in lines:
            if line.startswith((' ', '\t')):
                indented_lines += 1
                leading_spaces = len(line) - len(line.lstrip(' '))
                if leading_spaces > 0 and leading_spaces % 4 == 0:
                    consistent_indent += 1
                elif leading_spaces > 0 and leading_spaces % 2 == 0:
                    consistent_indent += 1
                elif line.startswith('\t'):
                    consistent_indent += 1
        
        if indented_lines > 0:
            indent_ratio = consistent_indent / indented_lines
            if indent_ratio > 0.7:  # >70% of indented lines follow patterns
                code_score += 1   # Further reduced
            # Removed lower tier entirely
        
        # Check for code-specific punctuation patterns with MAJOR focus on curly brackets
        semicolon_count = text.count(';')
        open_braces = text.count('{')
        close_braces = text.count('}')
        bracket_count = text.count('[') + text.count(']')
        # Plain parentheses removed - too common in prose (e.g., "the function (described above)")
        
        # MAJOR CODE INDICATOR: Curly brackets (especially if they match)
        if open_braces > 0 or close_braces > 0:
            brace_score = 0
            total_braces = open_braces + close_braces
            
            # Base score for having curly brackets at all (rare in prose)
            brace_score += total_braces * 12  # 12 points per curly bracket (increased)
            
            # MASSIVE bonus if brackets are balanced (very strong code indicator)
            if open_braces == close_braces and open_braces > 0:
                brace_score += 40  # Huge bonus for matched brackets (increased)
            
            # Additional bonus based on bracket density
            if len(text) > 0:
                brace_density = total_braces / len(text)
                if brace_density > 0.05:  # >5% of text is curly brackets
                    brace_score += 30  # Massive density bonus (increased)
                elif brace_density > 0.03:  # >3% of text is curly brackets
                    brace_score += 20  # High density bonus (increased)
                elif brace_density > 0.01:  # >1% of text is curly brackets
                    brace_score += 10  # Moderate density bonus (increased)
            
            code_score += brace_score
        
        # Parentheses with commas inside (likely function calls with parameters)
        paren_with_comma_count = len(re.findall(r'\([^)]*,[^)]*\)', text))
        if paren_with_comma_count >= 3:
            code_score += 8   # Multiple function calls with params
        elif paren_with_comma_count >= 2:
            code_score += 5   # Some function calls with params
        elif paren_with_comma_count >= 1:
            code_score += 3   # At least one function call with params
        
        # Other punctuation has much less impact now (excluding plain parentheses)
        total_chars = len(text)
        if total_chars > 0:
            # Don't count parentheses here - they're too common in prose
            other_punct = semicolon_count + bracket_count
            punct_density = other_punct / total_chars
            if punct_density > 0.05:  # >5% other punctuation
                code_score += 3   # Modest impact
            elif punct_density > 0.02:  # >2% other punctuation
                code_score += 1   # Minimal impact
        
        # Assignments and function calls have minimal impact
        assignment_count = len(re.findall(r'\w+\s*[=!<>]=?\s*', text))
        if assignment_count >= 3:
            code_score += 2   # Further reduced
        elif assignment_count >= 1:
            code_score += 1   # Further reduced
        
        # Function calls have minimal impact
        function_calls = len(re.findall(r'\w+\s*\([^)]*\)', text))
        if function_calls >= 3:
            code_score += 2   # Further reduced
        elif function_calls >= 1:
            code_score += 1   # Further reduced
        
        # PROSE INDICATORS (things that suggest it's regular text)
        
        # Sentence structure has reduced impact (paragraph formatting is dominant)
        sentence_endings = text.count('.') + text.count('!') + text.count('?')
        sentences_per_line = sentence_endings / len(lines) if lines else 0
        if sentences_per_line > 0.8:  # Almost every line ends with sentence punctuation
            prose_score += 5   # Reduced impact
        elif sentences_per_line > 0.4:
            prose_score += 3   # Reduced impact
        
        # MAJOR PROSE INDICATOR: Proper sentence patterns (capital + words + period + space/end)
        # Pattern: Capital letter starting sentence, ending with period followed by whitespace or end of text
        sentence_pattern_count = len(re.findall(r'[A-Z][a-z][^.!?]*[.!?](?:\s|$)', text))
        if sentence_pattern_count >= 6:
            prose_score += 150  # Very strong prose indicator
        elif sentence_pattern_count >= 4:
            prose_score += 120  # Strong prose indicator
        elif sentence_pattern_count >= 3:
            prose_score += 100  # Strong prose indicator
        elif sentence_pattern_count >= 2:
            prose_score += 80   # Good prose indicator  
        elif sentence_pattern_count >= 1:
            prose_score += 60   # Moderate prose indicator
        
        # Check for common English words that are strong indicators of prose
        # Exclude words that commonly appear in code contexts
        strong_prose_words = {
            'the', 'but', 'with', 'into', 'during', 'including', 'until', 'against', 
            'among', 'throughout', 'despite', 'towards', 'upon', 'concerning', 'about', 
            'over', 'after', 'section', 'chapter', 'figure', 'listing', 'example', 
            'shown', 'explained', 'becomes', 'together', 'everything', 'because', 
            'however', 'therefore', 'meanwhile', 'furthermore', 'moreover', 'although',
            'whereas', 'nevertheless', 'consequently', 'subsequently'
        }
        
        # Words that appear in both contexts (these favor prose now)
        ambiguous_words = {
            'and', 'or', 'not', 'in', 'for', 'of', 'at', 'by', 'from', 'up', 'to',
            'is', 'as', 'out', 'with', 'will', 'can', 'may', 'should', 'would',
            'if', 'then', 'else', 'when', 'where', 'while', 'before', 'after',
            'function', 'data', 'file', 'name', 'value', 'number', 'text', 'item'
        }
        
        words = re.findall(r'\b[a-zA-Z]+\b', text_lower)
        strong_prose_count = sum(1 for word in words if word in strong_prose_words)
        ambiguous_count = sum(1 for word in words if word in ambiguous_words)
        
        if len(words) > 0:
            # COSMIC level word analysis - prose words dominate absolutely everything
            strong_prose_ratio = strong_prose_count / len(words)
            if strong_prose_ratio > 0.25:  # >25% strong prose words
                prose_score += 50   # Strong prose increase
            elif strong_prose_ratio > 0.15:  # >15% strong prose words
                prose_score += 40   # Good prose increase
            elif strong_prose_ratio > 0.08:  # >8% strong prose words
                prose_score += 30   # Moderate prose increase
            elif strong_prose_ratio > 0.05:  # >5% strong prose words
                prose_score += 25   # Moderate prose increase
            elif strong_prose_ratio > 0.02:  # >2% strong prose words
                prose_score += 15   # Small prose increase
            elif strong_prose_ratio > 0.01:  # >1% strong prose words
                prose_score += 10   # Small prose increase
                
            # Ambiguous words COSMIC-level favor prose
            ambiguous_ratio = ambiguous_count / len(words)
            if ambiguous_ratio > 0.4:  # >40% ambiguous words
                prose_score += 25   # Good prose indicator
            elif ambiguous_ratio > 0.3:  # >30% ambiguous words  
                prose_score += 20   # Good prose indicator
            elif ambiguous_ratio > 0.2:  # >20% ambiguous words
                prose_score += 15   # Moderate prose indicator
            elif ambiguous_ratio > 0.15:  # >15% ambiguous words
                prose_score += 12   # Moderate prose indicator
            elif ambiguous_ratio > 0.1:  # >10% ambiguous words
                prose_score += 8    # Small prose indicator
            elif ambiguous_ratio > 0.05:  # >5% ambiguous words
                prose_score += 5    # Small prose indicator
        
        # DOMINANT FACTOR: Paragraph formatting pattern (consistent line starts + regular lengths)
        # This overwhelmingly determines prose vs code classification
        if len(lines) > 1:
            line_lengths = [len(line.strip()) for line in lines]
            
            # Check for consistent line start positions - THE MOST IMPORTANT FACTOR
            indent_levels = []
            for line in lines:
                stripped = line.lstrip()
                if stripped:  # Only count non-empty lines
                    indent = len(line) - len(stripped)
                    indent_levels.append(indent)
            
            consistent_start_bonus = 0
            if len(indent_levels) > 1:
                # If most lines start at the same column, it's very likely prose
                most_common_indent = max(set(indent_levels), key=indent_levels.count)
                same_indent_count = indent_levels.count(most_common_indent)
                same_indent_ratio = same_indent_count / len(indent_levels)
                
                if same_indent_ratio >= 0.9:  # 90% of lines start at same column
                    consistent_start_bonus = 80   # Strong prose indicator
                elif same_indent_ratio >= 0.8:  # 80% of lines start at same column  
                    consistent_start_bonus = 60   # Good prose indicator
                elif same_indent_ratio >= 0.7:  # 70% of lines start at same column
                    consistent_start_bonus = 50   # Good prose indicator
                elif same_indent_ratio >= 0.6:  # 60% of lines start at same column
                    consistent_start_bonus = 40   # Moderate prose indicator
                elif same_indent_ratio >= 0.5:  # 50% of lines start at same column
                    consistent_start_bonus = 30   # Moderate prose indicator
                elif same_indent_ratio >= 0.4:  # 40% of lines start at same column
                    consistent_start_bonus = 25   # Small prose indicator
                elif same_indent_ratio >= 0.3:  # 30% of lines start at same column
                    consistent_start_bonus = 20   # Small prose indicator
            
            # Check line length regularity (except last line) - SECOND MOST IMPORTANT
            regular_length_bonus = 0
            if len(lines) >= 2:
                non_last_lines = line_lengths[:-1]
                last_line = line_lengths[-1]
                
                if non_last_lines:  # Make sure we have non-last lines
                    avg_non_last = sum(non_last_lines) / len(non_last_lines)
                    
                    # Calculate regularity of non-last lines using coefficient of variation
                    if len(non_last_lines) > 1 and avg_non_last > 0:
                        non_last_variance = sum((length - avg_non_last) ** 2 for length in non_last_lines) / len(non_last_lines)
                        non_last_stddev = non_last_variance ** 0.5
                        non_last_cv = non_last_stddev / avg_non_last
                        
                        # Low variability in non-last lines = high regularity = prose
                        # Also require reasonable length (not just consistently short)
                        long_lines = [length for length in non_last_lines if length > 60]
                        long_ratio = len(long_lines) / len(non_last_lines) if non_last_lines else 0
                        
                        if non_last_cv < 0.15 and long_ratio >= 0.8:  # Very regular + mostly long
                            regular_length_bonus = 40   # Strong prose indicator
                        elif non_last_cv < 0.20 and long_ratio >= 0.7:  # Regular + reasonably long
                            regular_length_bonus = 35   # Good prose indicator
                        elif non_last_cv < 0.25 and long_ratio >= 0.6:  # Somewhat regular + some long
                            regular_length_bonus = 30   # Good prose indicator
                        elif non_last_cv < 0.30 and long_ratio >= 0.4:  # More lenient
                            regular_length_bonus = 25   # Moderate prose indicator
                        elif non_last_cv < 0.35 and long_ratio >= 0.2:  # Even more lenient
                            regular_length_bonus = 20   # Moderate prose indicator
                        elif non_last_cv < 0.40:  # Any regularity at all
                            regular_length_bonus = 15   # Small prose indicator
                        
                        # Additional bonus if last line is notably shorter (classic paragraph ending)
                        if regular_length_bonus > 0 and last_line < avg_non_last * 0.7:  # Last line <70% of average
                            regular_length_bonus += 20   # Additional bonus
            
            # Apply the dominant paragraph formatting scores
            prose_score += consistent_start_bonus + regular_length_bonus
        
        # Average line length has minimal impact (paragraph formatting is dominant)
        avg_line_length = sum(len(line) for line in lines) / len(lines)
        if avg_line_length > 80:  # Very long lines suggest prose
            prose_score += 3   # Reduced impact
        elif avg_line_length > 60:
            prose_score += 1   # Reduced impact
        
        # Final decision: prefer prose over code but allow reasonable code detection
        # Need good evidence to classify as code
        return code_score > prose_score + 15   # Moderate prose favoritism

    def _concatenate_paragraphs(self, text_lines):
        """Concatenate lines that appear to be continuations of paragraphs"""
        if not text_lines:
            return text_lines, {}
        
        result = []
        index_mapping = {}  # Maps new index to original indices that were combined
        i = 0
        
        while i < len(text_lines):
            current_line = text_lines[i]
            
            # Start with the current line
            combined_line = current_line
            original_indices = [i]
            
            # Look ahead to see if we should concatenate following lines
            j = i + 1
            while j < len(text_lines) and self._is_continuation_line(text_lines[j-1], text_lines[j], j-1, j):
                # Add a space and concatenate
                combined_line += " " + text_lines[j].strip()
                original_indices.append(j)
                j += 1
            
            result.append(combined_line)
            index_mapping[len(result) - 1] = original_indices
            i = j
        
        return result, index_mapping

    @staticmethod
    def _kw_present(keyword, text):
        """Whole-word/whole-phrase substring match. Plain `in` checks (the
        previous implementation here) match inside unrelated words too --
        e.g. 'new ' inside 'renew ' -- which is what let a stray fragment
        of English prose get classified as Java."""
        return re.search(r'\b' + re.escape(keyword) + r'\b', text) is not None

    def detect_language(self, code_text, hint_lang=None):
        """Auto-detect programming language from code text."""
        if not PYGMENTS_AVAILABLE:
            return None

        # Try hint first if provided
        if hint_lang:
            try:
                return get_lexer_by_name(hint_lang)
            except ClassNotFound:
                pass

        # Strip "Listing X.X" prefix if present for better language detection
        cleaned_code = code_text
        if re.match(r'^Listing\s+\d+\.?\d*\s+', code_text, re.IGNORECASE):
            # Remove the "Listing X.X Title" line for better language detection
            lines = code_text.split('\n')
            if len(lines) > 1:
                cleaned_code = '\n'.join(lines[1:])

        code_lower = cleaned_code.lower().strip()
        kw = lambda word: self._kw_present(word, code_lower)

        # Checks below are ordered from most to least language-specific, so a
        # snippet is claimed by the first branch with genuinely distinctive
        # evidence for it, rather than by the first branch with merely
        # plausible-looking evidence (the earlier ordering put Java first,
        # so any TypeScript/JavaScript class using ordinary OOP words like
        # `this.`/`private`/`new` was claimed by Java before TS/JS ever got
        # a chance to look at it).

        # XML heuristics - checked first since a stray '?' or '<'/'>' pair
        # used to get intercepted by later, broader branches.
        if ('<?xml' in code_lower or '<!doctype' in code_lower or
                ('<!entity' in code_lower and '&' in code_lower and ';' in code_lower) or
                (code_lower.count('<') >= 2 and code_lower.count('>') >= 2)):
            try:
                return get_lexer_by_name('xml')
            except ClassNotFound:
                pass

        # C/C++ heuristics. Note: an earlier version of this also matched a
        # marker-free "primitive-type function(...) {" pattern (to catch
        # bare C functions with no #include anywhere in the snippet, e.g.
        # "int abs(int x) {"), but validating against real books surfaced
        # genuine Java methods/classes misdetected as C through it (a
        # package-private "void addOrderLine(...) {" method has the exact
        # same shape and no access-modifier prefix to exclude it by). A
        # marker-free C snippet with no #include/main/printf anywhere falls
        # through to guess_lexer instead, same as before this rework -- a
        # known, pre-existing gap rather than something this phase promised
        # to close, and not worth reintroducing false Java positives for.
        elif ('#include' in code_lower or 'int main(' in code_lower or 'printf(' in code_lower):
            try:
                return get_lexer_by_name('c')
            except ClassNotFound:
                pass

        # Python heuristics - `def`/`elif`/`lambda` as whole words are
        # distinctive enough to check early, before any of the curly-brace
        # languages get a chance to claim the snippet via generic overlap.
        # Note: an earlier version of this also accepted `import` + `print`
        # together as a weaker signal (for one-liners lacking def/elif/
        # lambda), but validating against real books surfaced a genuine
        # Java class misdetected as Python through it -- both words are
        # completely ordinary in Java (import statements, print() calls)
        # too, so that combination doesn't actually discriminate the two.
        elif kw('def') or kw('elif') or kw('lambda'):
            try:
                return get_lexer_by_name('python')
            except ClassNotFound:
                pass

        # SQL heuristics (need at least two distinctive SQL keywords together,
        # since any single one of these words can appear in prose or in
        # other languages' identifiers). Shares SQL_KEYWORDS with
        # _looks_like_code's strong_code_keywords rather than keeping a
        # second, independently-maintained word list (audit 2.1(d)/2.4).
        elif sum(1 for word in self.SQL_KEYWORDS if kw(word)) >= 2:
            try:
                return get_lexer_by_name('sql')
            except ClassNotFound:
                pass

        # Cypher heuristics (Neo4j query language). Note: the old version
        # accepted a bare ':' anywhere in the text as supporting evidence,
        # which matches Python type hints, JS/TS object literals, and plain
        # prose ("at 5:00", "as follows:") -- replaced with the actual
        # Cypher relationship-arrow/type syntax `[:` instead.
        elif ((kw('create (') or kw('match (') or kw('load csv') or
                   kw('merge (') or kw('return') or kw('where')) and
                  (kw('businessobject') or '[:' in code_text or
                   kw('neo4j') or kw('cypher') or kw('graph') or
                   kw('objectid') or kw('row.'))):
            try:
                return get_lexer_by_name('cypher')
            except ClassNotFound:
                pass

        # TypeScript heuristics (checked before JavaScript since TS is a
        # superset). Note: the old bare `'?' in code_text` check (meant to
        # catch optional properties like `email?: string`) matched ANY
        # question mark anywhere, including a ternary's `? :` or an XML
        # prolog's `?>` -- replaced with a pattern that actually requires
        # the optional-property shape. Also removed the old bare `interface`/
        # `enum` keyword checks and the `class + public/private` combinator:
        # Java has `public interface`/`public enum`/`public class` too, so
        # those signals don't distinguish TS from Java at all -- they were
        # claiming ordinary Java (checked below) as TypeScript. The signals
        # that remain here are TypeScript's actual, unambiguous
        # `name: Type`-style type annotations (Java/C# put the type first:
        # `String name`) plus `readonly`, which Java has no equivalent of
        # (it uses `final` instead).
        elif (kw('readonly') or
                ': string' in code_lower or ': number' in code_lower or
                ': boolean' in code_lower or ': void' in code_lower or
                re.search(r'\w\?\s*:', code_text) is not None):
            try:
                return get_lexer_by_name('typescript')
            except ClassNotFound:
                try:
                    return get_lexer_by_name('javascript')
                except ClassNotFound:
                    pass

        # JavaScript heuristics
        elif (kw('function') or '=>' in code_text or 'console.log' in code_lower or
                kw('var') or kw('let') or kw('const')):
            try:
                return get_lexer_by_name('javascript')
            except ClassNotFound:
                pass

        # Java heuristics - narrowed to signals that are actually
        # Java-specific. The old list included generic OOP vocabulary
        # (`this.`, `new `, `extends`, `private `, bare `string `/`boolean `)
        # shared by TypeScript, JavaScript, C#, and others, and being
        # checked first meant it claimed most curly-brace snippets
        # regardless of which language they actually were.
        #
        # The dotted-import and `throws SomeException` checks were added
        # after validating against real books: excerpted Java methods (the
        # kind books show without their surrounding class, e.g. a single
        # method with a `throws` clause, or with a bare `import a.b.C;`
        # above it but no class/main/println in view) don't match any of
        # the other signals here, and were falling through to guess_lexer,
        # which isn't reliable on short snippets and was guessing "Python".
        # Both shapes are distinctly Java/C#-like: TS/JS imports use
        # `import {x} from 'y'` (braces and a quoted path, not a bare
        # dotted identifier), and TS/JS don't declare checked-exception
        # types in a function signature at all.
        elif ('system.out.print' in code_lower or kw('import java.') or '@override' in code_lower or
                '@autowired' in code_lower or
                re.search(r'\bpublic\s+static\s+void\s+main\b', code_lower) is not None or
                re.search(r'\b(public|private|protected)\s+(final\s+|static\s+|abstract\s+)*(class|interface|enum)\b',
                          code_lower) is not None or
                re.search(r'^\s*import\s+(static\s+)?[\w.]+(\.\*)?;\s*$', code_text, re.MULTILINE) is not None or
                re.search(r'\bthrows\s+\w*(exception|error)\b', code_lower) is not None or
                # Type-first field declaration ("String title;", "private
                # ISBN isbn;"): Java/C# put the type before the name;
                # TypeScript puts the name first with a colon ("title:
                # string;"), so this shape doesn't occur there. Added after
                # validating against real books: simplified/pedagogical
                # Java classes with no imports, modifiers-on-class, or
                # println calls in view (just bare fields and methods) were
                # falling through to guess_lexer and landing on essentially
                # random lexers.
                re.search(r'^\s*(private\s+|public\s+|protected\s+|final\s+|static\s+)*'
                          r'[A-Z]\w*(<[^>]*>)?(\[\])?\s+[a-z_]\w*\s*;\s*$', code_text, re.MULTILINE) is not None):
            try:
                return get_lexer_by_name('java')
            except ClassNotFound:
                pass

        # CSV heuristics (comma-separated values) - requires a consistent
        # comma count across most non-empty lines, which is the actual
        # structural invariant of CSV data. The old check ("any comma on 2+
        # lines") matched almost any multi-line prose paragraph that
        # happened to use commas.
        else:
            csv_lines = [line for line in code_text.split('\n') if line.strip()]
            if len(csv_lines) >= 2:
                comma_counts = [line.count(',') for line in csv_lines]
                if all(c >= 1 for c in comma_counts):
                    most_common = max(set(comma_counts), key=comma_counts.count)
                    matching_ratio = comma_counts.count(most_common) / len(comma_counts)
                    if most_common >= 1 and matching_ratio >= 0.7:
                        try:
                            return get_lexer_by_name('text')
                        except ClassNotFound:
                            pass

        # If heuristics didn't match, try guess_lexer as fallback. Note:
        # guess_lexer alone (tested empirically while writing this fix) does
        # noticeably worse than the heuristics above on short/ambiguous
        # snippets -- it picks essentially arbitrary lexers (e.g. "GDScript"
        # for a TypeScript class, "Tera Term macro" for a two-line C
        # function) out of its ~500-lexer candidate pool. It stays as a
        # last resort only, not the primary strategy.
        try:
            lexer = guess_lexer(cleaned_code)
            # If guess_lexer returns TextLexer, reject it
            if lexer.__class__.__name__ == 'TextLexer':
                return TextLexer()
            return lexer
        except ClassNotFound:
            # Default to TextLexer if nothing matched
            return TextLexer()
        except Exception:
            # guess_lexer() iterates Pygments' entire lexer registry, which
            # has been observed to raise a bare KeyError on some Pygments
            # releases with a broken/stale registry entry -- a bug in
            # Pygments itself, not anything specific to the text being
            # classified. Fall back to plain text rather than let a broken
            # third-party lexer registry crash the reader.
            return TextLexer()
    
    def reorganize_callouts(self, code_text):
        """Reorganize callout annotations (#1, #2, etc.) to align at end of lines."""
        import re
        
        lines = code_text.split('\n')
        processed_lines = []
        
        # First pass: find the longest line to determine alignment position
        max_length = 0
        for line in lines:
            clean_line = re.sub(r'\s*#\d+\s*', '', line)
            max_length = max(max_length, len(clean_line.rstrip()))
        
        # Set alignment position: at least column 60, or 4 spaces past longest line
        align_pos = max(60, max_length + 4)
        
        # Second pass: reorganize callouts
        for line in lines:
            # Extract callout annotations (like #1, #2, #3, etc.)
            callouts = re.findall(r'#\d+', line)
            # Remove callouts from the line, preserving indentation
            clean_line = re.sub(r'\s*#\d+\s*', '', line)
            
            if callouts:
                # Pad line to alignment position and add callouts
                padded_line = clean_line.rstrip().ljust(align_pos)
                callout_str = ' '.join(callouts)
                processed_lines.append(f"{padded_line}  {callout_str}")
            else:
                processed_lines.append(clean_line)
        
        return '\n'.join(processed_lines)

    def smart_code_wrap(self, line_text, width):
        """
        Intelligently wrap code lines to avoid breaking at syntactically inappropriate places
        """
        if len(line_text) <= width:
            return [line_text]
        
        # Define patterns that should not be broken
        no_break_patterns = [
            r'if\s*\(',           # if (condition
            r'while\s*\(',        # while (condition  
            r'for\s*\(',          # for (initialization
            r'switch\s*\(',       # switch (variable
            r'function\s*\(',     # function (params
            r'catch\s*\(',        # catch (error
            r'typeof\s+\w+',      # typeof variable
            r'instanceof\s+\w+',  # instanceof Class
            r'new\s+\w+',         # new Constructor
            r'\w+\.\w+',          # object.property
            r'=>\s*\{',           # arrow function
            r'\?\s*\.',           # optional chaining ?.
        ]
        
        # Try to find good break points (after certain characters)
        good_break_chars = [';', ',', '{', '}', ')', '|', '&']
        
        lines = []
        remaining = line_text
        
        while len(remaining) > width:
            # Find the latest good break point within width
            best_break = -1
            
            # Look for good break characters from right to left within width
            for i in range(min(width, len(remaining)) - 1, width // 2, -1):
                if remaining[i] in good_break_chars:
                    # Check if breaking here would violate no-break patterns
                    would_break_pattern = False
                    for pattern in no_break_patterns:
                        # Look for patterns that span across the potential break point
                        search_start = max(0, i - 20)
                        search_end = min(len(remaining), i + 20)
                        search_text = remaining[search_start:search_end]
                        
                        for match in re.finditer(pattern, search_text, re.IGNORECASE):
                            match_start = search_start + match.start()
                            match_end = search_start + match.end()
                            if match_start < i < match_end:
                                would_break_pattern = True
                                break
                        
                        if would_break_pattern:
                            break
                    
                    if not would_break_pattern:
                        best_break = i
                        break
            
            if best_break == -1:
                # No good break point found, fall back to regular wrapping
                # but try to avoid breaking inside quotes or parentheses
                best_break = width
                paren_depth = 0
                quote_char = None
                
                for i in range(width):
                    char = remaining[i]
                    if quote_char:
                        if char == quote_char and (i == 0 or remaining[i-1] != '\\'):
                            quote_char = None
                    elif char in ['"', "'"]:
                        quote_char = char
                    elif char == '(':
                        paren_depth += 1
                    elif char == ')':
                        paren_depth -= 1
                    
                    # Prefer to break when not inside quotes or parentheses
                    if paren_depth == 0 and quote_char is None and i > width // 2:
                        best_break = i
            
            # Extract the line and prepare remainder
            if best_break < len(remaining):
                line_part = remaining[:best_break + 1].rstrip()
                remaining = remaining[best_break + 1:].lstrip()
            else:
                line_part = remaining
                remaining = ""
            
            lines.append(line_part)
        
        # Add any remaining text
        if remaining.strip():
            lines.append(remaining)
        
        return lines
    
    def apply_block_coalescence(self):
        """
        Apply block coalescence - lines next to confirmed code blocks are more likely to be code.
        This helps with inconsistent line-by-line detection in code listings.
        """
        if not self.text:
            return
        
        # First pass: identify high-confidence code blocks (already marked as code or in pre tags)
        high_confidence_code = set()
        high_confidence_code.update(self.idcode)  # Already marked as code
        high_confidence_code.update(self.idpref)  # Pre-formatted blocks
        
        # Second pass: check lines adjacent to high-confidence code
        lines_to_recheck = []
        for i in range(len(self.text)):
            # Skip if already marked as code or prose explicitly
            if i in self.idcode or i in self.idprose:
                continue
                
            # Check if this line is adjacent to high-confidence code
            has_code_neighbor = False
            for offset in [-2, -1, 1, 2]:  # Check 2 lines in each direction
                neighbor_idx = i + offset
                if 0 <= neighbor_idx < len(self.text):
                    if neighbor_idx in high_confidence_code:
                        has_code_neighbor = True
                        break
            
            if has_code_neighbor and self.text[i].strip():
                lines_to_recheck.append(i)
        
        # Third pass: apply weighted heuristic with coalescence bonus
        for i in lines_to_recheck:
            text_line = self.text[i]
            if not text_line.strip():
                continue
                
            # Apply standard heuristic
            base_result = self._looks_like_code(text_line)
            
            # Add coalescence weighting
            code_neighbors = 0
            for offset in [-2, -1, 1, 2]:
                neighbor_idx = i + offset
                if 0 <= neighbor_idx < len(self.text):
                    if neighbor_idx in high_confidence_code:
                        code_neighbors += 1
            
            # Strong coalescence: if surrounded by code, likely code
            if code_neighbors >= 2:  # At least 2 code neighbors
                self.idcode.add(i)
            elif code_neighbors >= 1 and base_result:  # 1 neighbor + heuristic says code
                self.idcode.add(i)
    
    def apply_syntax_highlighting(self, code_text, hint_lang=None):
        """Apply syntax highlighting to code text and return color info."""
        if not PYGMENTS_AVAILABLE or not code_text.strip():
            return [(code_text, [])]  # Return original text with no color info
        
        # Reorganize callout annotations before highlighting
        organized_text = self.reorganize_callouts(code_text)
        
        try:
            # Detect language (use original text for detection)
            lexer = self.detect_language(code_text, hint_lang)
            
            # If no lexer detected, use theme-appropriate fallback
            if lexer is None:
                lines = organized_text.split('\n')
                result = []
                for line in lines:
                    if line.strip():  # Only color non-empty lines
                        # Use dual color format: (dark_theme_color, light_theme_color)
                        dual_colors = [((255, 255, 255), (50, 50, 50))] * len(line)
                        result.append((line, dual_colors))
                    else:
                        result.append((line, []))
                return result if result else [(code_text, [((255, 255, 255), (50, 50, 50))] * len(code_text))]
            
            # Get tokens from organized text
            tokens = list(lexer.get_tokens(organized_text))
            
            # Convert tokens to colored text
            result = []
            current_line = ""
            current_colors = []
            
            for token_type, token_value in tokens:
                # Map token types to colors (returns tuple of (dark_color, light_color))
                color = self.get_token_color(token_type)
                
                # Handle line breaks
                if '\n' in token_value:
                    lines = token_value.split('\n')
                    # Add first part to current line
                    current_line += lines[0]
                    current_colors.extend([color] * len(lines[0]))
                    
                    # Save current line and start new ones
                    if current_line:
                        result.append((current_line, current_colors))
                    
                    # Add intermediate lines
                    for line in lines[1:-1]:
                        if line:
                            result.append((line, [color] * len(line)))
                        else:
                            result.append(("", []))
                    
                    # Start new line with last part
                    current_line = lines[-1]
                    current_colors = [color] * len(lines[-1])
                else:
                    current_line += token_value
                    current_colors.extend([color] * len(token_value))
            
            # Add final line
            if current_line or current_colors:
                result.append((current_line, current_colors))
            
            return result if result else [(code_text, [])]
            
        except Exception as e:
            # Fall back to theme-appropriate colors if highlighting fails
            if state.DEBUG_MODE:
                print(f"Syntax highlighting failed: {e}", file=sys.stderr)
            lines = code_text.split('\n')
            result = []
            for line in lines:
                if line.strip():  # Only color non-empty lines
                    # Use dual color format: (dark_theme_color, light_theme_color)
                    dual_colors = [((255, 255, 255), (50, 50, 50))] * len(line)
                    result.append((line, dual_colors))
                else:
                    result.append((line, []))
            return result if result else [(code_text, [((255, 255, 255), (50, 50, 50))] * len(code_text))]
    
    def get_token_color(self, token_type):
        """Map Pygments token types to terminal colors."""
        # We'll store both light and dark theme colors
        # Format: (dark_theme_color, light_theme_color)
        # For light theme, we use darker, more saturated colors for better contrast
        token_colors_dual = {
            'Keyword.Constant': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Declaration': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Namespace': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Pseudo': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Reserved': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Type': ((100, 200, 255), (0, 100, 180)),
            'Keyword': ((0, 150, 255), (0, 50, 200)),
            
            'Name.Class': ((255, 255, 0), (180, 140, 0)),
            'Name.Function': ((255, 255, 0), (180, 140, 0)),
            'Name.Builtin': ((255, 100, 255), (150, 0, 150)),
            'Name.Exception': ((255, 100, 0), (200, 50, 0)),
            
            'Literal.String.Double': ((0, 255, 0), (0, 140, 0)),
            'Literal.String.Single': ((0, 255, 0), (0, 140, 0)),
            'Literal.String': ((0, 255, 0), (0, 140, 0)),
            'Literal.Number.Integer': ((255, 165, 0), (180, 90, 0)),
            'Literal.Number.Float': ((255, 165, 0), (180, 90, 0)),
            'Literal.Number': ((255, 165, 0), (180, 90, 0)),
            
            'Comment.Single': ((128, 128, 128), (100, 100, 100)),
            'Comment.Multiline': ((128, 128, 128), (100, 100, 100)),
            'Comment.Preproc': ((255, 255, 255), (50, 50, 50)),
            'Comment': ((128, 128, 128), (100, 100, 100)),
            
            'Operator.Word': ((255, 100, 255), (150, 0, 150)),
            'Operator': ((255, 100, 255), (150, 0, 150)),
            'Punctuation.Bracket': ((255, 255, 0), (180, 140, 0)),
            'Punctuation': ((255, 255, 0), (180, 140, 0)),
            
            # XML-specific token types
            'Name.Tag': ((0, 150, 255), (0, 50, 200)),
            'Name.Attribute': ((255, 255, 0), (180, 140, 0)),
            'Literal.String.Doc': ((0, 255, 0), (0, 140, 0)),
            'Generic.Emph': ((255, 255, 255), (50, 50, 50)),
            'Generic.Strong': ((255, 255, 255), (50, 50, 50)),
            
            # TypeScript/JavaScript-specific tokens
            'Name.Other': ((0, 255, 255), (0, 150, 150)),
            'Name.Variable': ((0, 255, 255), (0, 150, 150)),
            'Name.Property': ((255, 255, 0), (180, 140, 0)),
            'Name.Constant': ((255, 165, 0), (180, 90, 0)),
            'Name.Builtin.Pseudo': ((255, 100, 255), (150, 0, 150)),
            'Name.Decorator': ((255, 100, 255), (150, 0, 150)),
            'Literal.String.Backtick': ((0, 255, 0), (0, 140, 0)),
            'Literal.String.Interpol': ((255, 255, 0), (180, 140, 0)),
            'Literal.Number.Bin': ((255, 165, 0), (180, 90, 0)),
            'Literal.Number.Hex': ((255, 165, 0), (180, 90, 0)),
            'Literal.Number.Oct': ((255, 165, 0), (180, 90, 0)),
            
            # Special tokens
            'Error': ((255, 0, 0), (200, 0, 0)),
            'Text': ((255, 255, 255), (50, 50, 50)),
        }
        
        # Convert token type to string and find best match
        token_str = str(token_type)
        
        # Remove "Token." prefix if present
        if token_str.startswith("Token."):
            token_str = token_str[6:]
        
        # Try exact match first
        if token_str in token_colors_dual:
            return token_colors_dual[token_str]
        
        # Try partial matches (e.g., "Name.Function.Magic" -> "Name.Function")
        # Sort keys by length descending to match most specific first
        for pattern in sorted(token_colors_dual.keys(), key=len, reverse=True):
            if token_str.startswith(pattern):
                return token_colors_dual[pattern]
        
        # Default to white for dark theme, dark gray for light theme
        return ((255, 255, 255), (50, 50, 50))

    def wrap_text_preserve_urls(self, text, width):
        """Wrap text while preserving URLs on separate lines when needed."""
        import re
        import textwrap
        
        # Find all URLs in the original text using central function
        url_data = find_urls_in_text(text)
        urls = [re.match(re.escape(url), text[start:end]) for url, start, end in url_data]
        # Convert to Match objects for compatibility
        urls = []
        for url, start, end in url_data:
            class MockMatch:
                def __init__(self, text, start, end):
                    self._text = text
                    self._start = start
                    self._end = end
                def group(self): return self._text
                def start(self): return self._start
                def end(self): return self._end
            urls.append(MockMatch(url, start, end))
        
        if not urls:
            # No URLs, use normal wrapping
            wrapped = textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=True, expand_tabs=True)
            if not wrapped and text.strip():
                wrapped = textwrap.wrap(text, width, break_long_words=True)
            return wrapped
        
        result_lines = []
        current_pos = 0
        
        for url_match in urls:
            # Add text before URL (if any)
            before_text = text[current_pos:url_match.start()]
            if before_text.strip():
                wrapped_before = textwrap.wrap(before_text.strip(), width, break_long_words=False, break_on_hyphens=True)
                result_lines.extend(wrapped_before)
            
            # Get the URL and text after it
            url_text = url_match.group()
            after_start = url_match.end()
            
            # Find text that comes after this URL (until next URL or end of text)
            next_url = None
            for next_match in urls[urls.index(url_match) + 1:]:
                if next_match.start() > after_start:
                    next_url = next_match
                    break
            
            if next_url:
                after_text = text[after_start:next_url.start()]
            else:
                after_text = text[after_start:]
            
            # If URL is long or close to width, give it its own line
            if len(url_text) > width * 0.8:  # If URL is more than 80% of line width
                # URL needs its own line
                result_lines.append(f"__URLSTART__{url_text}__URLEND__")
                current_pos = url_match.end()
            else:
                # Check if URL + immediate after text fits on one line
                # Handle immediate punctuation (no space) vs words (with space)
                immediate_after = after_text.lstrip()  # Remove leading spaces
                first_char = immediate_after[0] if immediate_after else ""
                
                if first_char and first_char in '.!?,:;':
                    # Immediate punctuation - no space needed
                    punctuation = first_char
                    remaining_after = immediate_after[1:]
                    if len(url_text + punctuation) <= width:
                        result_lines.append(f"__URLSTART__{url_text}__URLEND__{punctuation}")
                        current_pos = after_start + (len(after_text) - len(immediate_after)) + 1
                        continue
                else:
                    # Regular word - needs space
                    immediate_after = after_text.split()[0] if after_text.strip() else ""
                    if len(url_text + " " + immediate_after) <= width:
                        # URL and next word fit together  
                        result_lines.append(f"__URLSTART__{url_text}__URLEND__ {immediate_after}".strip())
                        # Update position to skip the word we just added
                        if immediate_after:
                            # Find the actual position after the word (handling multiple spaces)
                            word_end = after_text.find(immediate_after) + len(immediate_after)
                            current_pos = after_start + word_end
                            # Skip any trailing spaces
                            while current_pos < len(text) and text[current_pos] == ' ':
                                current_pos += 1
                        else:
                            current_pos = url_match.end()
                    else:
                        # URL needs its own line
                        result_lines.append(f"__URLSTART__{url_text}__URLEND__")
                        current_pos = url_match.end()
        
        # Add any remaining text after the last URL
        remaining_text = text[current_pos:].strip()
        if remaining_text:
            wrapped_remaining = textwrap.wrap(remaining_text, width, break_long_words=False, break_on_hyphens=True)
            result_lines.extend(wrapped_remaining)
        
        return result_lines if result_lines else ['']
    
    def highlight_urls_in_prose(self, text_lines):
        """Apply URL highlighting to prose text lines using URL markers."""
        import re
        
        highlighted_lines = []
        
        for line in text_lines:
            # Check if line contains marked URLs
            if "__URLSTART__" in line and "__URLEND__" in line:
                # This line contains one or more marked URLs
                result_line = line
                
                # Find all marked URL sections and replace with URL_HL format
                url_pattern = r'__URLSTART__(.*?)__URLEND__'
                matches = list(re.finditer(url_pattern, line))
                
                # Process matches in reverse order to avoid position shifts
                for match in reversed(matches):
                    url_text = match.group(1)
                    # Replace the marked section with URL_HL format
                    # But we need to handle this carefully since we're in the middle of a line
                    start_pos = match.start()
                    end_pos = match.end()
                    
                    # For now, just mark the whole line as URL if it contains a URL
                    # This is simpler and will work for most cases
                    result_line = "URL_HL:" + line.replace("__URLSTART__", "").replace("__URLEND__", "")
                    break  # Process only the first URL per line for simplicity
                
                highlighted_lines.append(result_line)
            else:
                # No marked URLs, check for regular URL patterns as fallback
                if re.search(r'https?://', line):
                    highlighted_lines.append("URL_HL:" + line)
                else:
                    highlighted_lines.append(line)
                
        return highlighted_lines

    def add_table_background(self, text_lines):
        """Add background highlighting to table-like content for consistency."""
        highlighted_lines = []
        
        for line in text_lines:
            # Detect table-like content that should have consistent backgrounds
            line_lower = line.lower().strip()
            
            # Check for technical table indicators
            has_tech_keywords = any(keyword in line_lower for keyword in [
                'component', 'url', 'route', 'path', 'api', 'endpoint',
                'method', 'function', 'class', 'import', 'export',
                'next.js', 'remix', 'react', 'javascript', 'typescript'
            ])
            
            # Check for table-like structure patterns
            has_table_structure = any(pattern in line for pattern in [
                '  ', '   ', '\t',  # Multiple spaces/tabs (column alignment)
            ]) and len(line.strip()) > 0
            
            # Check for file path patterns
            has_file_paths = any(pattern in line for pattern in [
                '/pages', '/app/', '/routes', '/src/', '/components',
                '.js', '.jsx', '.ts', '.tsx', '.html'
            ])
            
            # If it looks like technical table content, add background
            if (has_tech_keywords and has_table_structure) or has_file_paths:
                highlighted_line = "TABLE_BG:" + line
                highlighted_lines.append(highlighted_line)
            else:
                highlighted_lines.append(line)
                
        return highlighted_lines

    def get_lines(self, width=0):
        text = []
        # Default to 100 columns if no width specified
        if width == 0:
            width = 100
        
        # In dump mode, return plain text without any formatting
        if self.dump_mode:
            # Simply return the text content without any special processing
            for i in self.text:
                if i:  # Include all lines, even empty ones for paragraph breaks
                    # Basic text wrapping for long lines
                    if len(i) <= width:
                        text.append(i)
                    else:
                        # Simple word wrapping
                        wrapped = textwrap.wrap(i, width, break_long_words=False, break_on_hyphens=True)
                        text.extend(wrapped)
                else:
                    # Preserve empty lines for paragraph breaks
                    text.append("")
            return text, [], []  # Return empty lists for images and image alts in dump mode
        
        # Apply block coalescence to improve code detection consistency
        self.apply_block_coalescence()
        
        # First, concatenate paragraph continuations
        processed_text, index_mapping = self._concatenate_paragraphs(self.text)
        
        for n, i in enumerate(processed_text):
            # Check if any of the original indices had special formatting
            original_indices = index_mapping[n]
            is_head = any(idx in self.idhead for idx in original_indices)
            is_inde = any(idx in self.idinde for idx in original_indices)
            is_bull = any(idx in self.idbull for idx in original_indices)
            is_pref = any(idx in self.idpref for idx in original_indices)
            is_code = any(idx in self.idcode for idx in original_indices)
            is_prose = any(idx in self.idprose for idx in original_indices)
            is_caption = any(idx in self.idcaption for idx in original_indices)
            
            if is_head:
                # Add HEADER: prefix to preserve header info for rendering
                centered_text = i.rjust(width//2 + len(i)//2)
                text += ["HEADER:" + centered_text] + [""]
            elif is_inde:
                wrapped_lines = ["   "+j for j in self.wrap_text_preserve_urls(i, width - 3)]
                highlighted_lines = self.highlight_urls_in_prose(wrapped_lines)
                text += highlighted_lines + [""]
            elif is_bull:
                tmp = self.wrap_text_preserve_urls(i, width - 3)
                bullet_lines = [" - "+j if j == tmp[0] else "   "+j for j in tmp]
                highlighted_lines = self.highlight_urls_in_prose(bullet_lines)
                text += highlighted_lines + [""]
            elif is_caption:
                # Handle listing captions with proper spacing and formatting
                text.append("")  # Empty line before caption
                wrapped_caption = textwrap.wrap(i, width, break_long_words=False, break_on_hyphens=True)
                text += ["CAPTION:" + line for line in wrapped_caption]
                text.append("")  # Empty line after caption
            elif is_pref:
                # Apply syntax highlighting to code blocks (HTML class takes priority over heuristics)
                if is_prose:
                    # Explicitly marked as prose via HTML class attribute (highest priority)
                    should_highlight = False
                elif is_code and PYGMENTS_AVAILABLE:
                    # Explicitly marked as code via HTML class attribute
                    should_highlight = True
                elif not is_code and PYGMENTS_AVAILABLE:
                    # Check heuristic for unmarked pre blocks
                    should_highlight = self._looks_like_code(i)
                else:
                    should_highlight = False
                
                if should_highlight:
                    # Apply syntax highlighting to code blocks (no borders)
                    highlighted_lines = self.apply_syntax_highlighting(i, self.code_lang)
                    text.append("")  # Empty line before code
                    text.append("")  # Extra padding before code
                    for line_text, line_colors in highlighted_lines:
                        if line_text:
                            # Store syntax-highlighted code as-is without wrapping to preserve formatting
                            text.append("SYNTAX_HL:" + line_text + "|" + str(line_colors))
                        else:
                            # Empty line within code block - mark as syntax highlighted to preserve context
                            text.append("SYNTAX_HL:|[]")
                    text.append("")  # Empty line after code block
                    text.append("")  # Extra padding after code
                else:
                    # Regular preformatted text (no syntax highlighting) - preserve original formatting
                    tmp = i.splitlines()
                    # Don't wrap preformatted text - it should maintain its original formatting
                    text.append("")  # Extra padding before preformatted text
                    text += ["   "+line for line in tmp]
                    text.append("")  # Original empty line after preformatted text
                    text.append("")  # Extra padding after preformatted text
            else:
                # Use URL-aware text wrapping to preserve URLs
                wrapped_lines = self.wrap_text_preserve_urls(i, width)
                highlighted_lines = self.highlight_urls_in_prose(wrapped_lines)
                background_lines = self.add_table_background(highlighted_lines)
                text += background_lines + [""]
        return text, self.imgs, self.img_alts


def find_urls_in_text(text):
    """Find all URLs in text using whitelist-based pattern.
    Returns list of (url, start_pos, end_pos) tuples."""
    # Whitelist-based URL pattern: protocol + domain + optional port + optional path + optional query string
    # Includes query strings but excludes fragments
    # Ensures URLs don't end with punctuation marks
    url_pattern = r'https?://[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?(?::[0-9]+)?(?:/[a-zA-Z0-9._/\-~]*[a-zA-Z0-9/\-~])?(?:\?[a-zA-Z0-9._/\-~&=+%]*[a-zA-Z0-9/\-~&=+%])?'

    urls = []
    for match in re.finditer(url_pattern, text):
        urls.append((match.group(), match.start(), match.end()))
    return urls
