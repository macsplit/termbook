#!/usr/bin/env python3
"""\
Usages:
    termbook             read last epub
    termbook EPUBFILE    read EPUBFILE
    termbook STRINGS     read matched STRINGS from history
    termbook NUMBER      read file from history
                         with associated NUMBER

Options:
    -r              print reading history
    -d              dump epub
    -h, --help      print short, long help

Key Binding:
    Help             : ?
    Quit             : q
    Scroll down      : DOWN      j
    Scroll up        : UP        k
    Half screen up   : C-u
    Half screen dn   : C-d
    Page down        : PGDN      RIGHT   SPC
    Page up          : PGUP      LEFT
    Next chapter     : n
    Prev chapter     : p
    Beginning of ch  : HOME      g
    End of ch        : END       G
    Open image       : o
    Search           : /
    Next Occurrence  : n
    Prev Occurrence  : N
    Toggle width     : =
    Set width        : [count]=
    Shrink           : -
    Enlarge          : +
    ToC              : TAB       t
    Metadata         : m
    Mark pos to n    : b[n]
    Jump to pos n    : `[n]
    Switch colorsch  : [default=0, dark=1, light=2]c
"""


__version__ = "1.0.0"
__license__ = "MIT"
__author__ = "Lee Hanken (based on epr by Benawi Adha)"
__email__ = ""
__url__ = "https://github.com/leehanken/termbook"


import curses
import zipfile
import sys
import re
import os
import textwrap
import json
import tempfile
import shutil
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from html import unescape
from html.parser import HTMLParser
from difflib import SequenceMatcher as SM
from io import BytesIO
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
    from pygments.util import ClassNotFound
    from pygments.formatters import get_formatter_by_name
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False



# key bindings
SCROLL_DOWN = {curses.KEY_DOWN}
SCROLL_DOWN_J = {ord("j")}
SCROLL_UP = {curses.KEY_UP}
SCROLL_UP_K = {ord("k")}
HALF_DOWN = {4}
HALF_UP = {21}
PAGE_DOWN = {curses.KEY_NPAGE, ord("l"), ord(" "), curses.KEY_RIGHT}
PAGE_UP = {curses.KEY_PPAGE, ord("h"), curses.KEY_LEFT}
CH_NEXT = {ord("n")}
CH_PREV = {ord("p")}
CH_HOME = {curses.KEY_HOME, ord("g")}
CH_END = {curses.KEY_END, ord("G")}
SHRINK = ord("-")
WIDEN = ord("+")
WIDTH = ord("=")
META = {ord("m")}
TOC = {9, ord("\t"), ord("t")}
FOLLOW = {10}
QUIT = {ord("q"), 3, 27, 304}
HELP = {ord("?")}
MARKPOS = ord("b")
JUMPTOPOS = ord("`")
COLORSWITCH = ord("c")


# colorscheme
# DARK/LIGHT = (fg, bg)
# -1 is default terminal fg/bg
DARK = (252, 235)
LIGHT = (239, 223)


# some global envs, better leave these alone
STATEFILE = ""
STATE = {}
LINEPRSRV = 0  # default = 2
COLORSUPPORT = False
SEARCHPATTERN = None
VWR = None
JUMPLIST = {}


class Epub:
    NS = {
        "DAISY": "http://www.daisy.org/z3986/2005/ncx/",
        "OPF": "http://www.idpf.org/2007/opf",
        "CONT": "urn:oasis:names:tc:opendocument:xmlns:container",
        "XHTML": "http://www.w3.org/1999/xhtml",
        "EPUB": "http://www.idpf.org/2007/ops"
    }

    def __init__(self, fileepub):
        self.path = os.path.abspath(fileepub)
        self.file = zipfile.ZipFile(fileepub, "r")
        cont = ET.parse(self.file.open("META-INF/container.xml"))
        self.rootfile = cont.find(
            "CONT:rootfiles/CONT:rootfile",
            self.NS
        ).attrib["full-path"]
        self.rootdir = os.path.dirname(self.rootfile)\
            + "/" if os.path.dirname(self.rootfile) != "" else ""
        cont = ET.parse(self.file.open(self.rootfile))
        # EPUB3
        self.version = cont.getroot().get("version")
        if self.version == "2.0":
            # self.toc = self.rootdir + cont.find("OPF:manifest/*[@id='ncx']", self.NS).get("href")
            self.toc = self.rootdir\
                + cont.find(
                    "OPF:manifest/*[@media-type='application/x-dtbncx+xml']",
                    self.NS
                ).get("href")
        elif self.version == "3.0":
            self.toc = self.rootdir\
                + cont.find(
                    "OPF:manifest/*[@properties='nav']",
                    self.NS
                ).get("href")

        self.contents = []
        self.toc_entries = []

    def get_meta(self):
        meta = []
        # why self.file.read(self.rootfile) problematic
        cont = ET.fromstring(self.file.open(self.rootfile).read())
        for i in cont.findall("OPF:metadata/*", self.NS):
            if i.text is not None:
                meta.append([re.sub("{.*?}", "", i.tag), i.text])
        return meta

    def initialize(self):
        cont = ET.parse(self.file.open(self.rootfile)).getroot()
        manifest = []
        for i in cont.findall("OPF:manifest/*", self.NS):
            # EPUB3
            # if i.get("id") != "ncx" and i.get("properties") != "nav":
            if i.get("media-type") != "application/x-dtbncx+xml"\
               and i.get("properties") != "nav":
                manifest.append([
                    i.get("id"),
                    i.get("href")
                ])

        spine, contents = [], []
        for i in cont.findall("OPF:spine/*", self.NS):
            spine.append(i.get("idref"))
        for i in spine:
            for j in manifest:
                if i == j[0]:
                    self.contents.append(self.rootdir+unquote(j[1]))
                    contents.append(unquote(j[1]))
                    # Don't remove from manifest to avoid iteration issues
                    break

        toc = ET.parse(self.file.open(self.toc)).getroot()
        # EPUB3
        if self.version == "2.0":
            navPoints = toc.findall("DAISY:navMap//DAISY:navPoint", self.NS)
        elif self.version == "3.0":
            navPoints = toc.findall(
                "XHTML:body//XHTML:nav[@EPUB:type='toc']//XHTML:a",
                self.NS
            )
        for i in contents:
            name = "-"
            for j in navPoints:
                # EPUB3
                if self.version == "2.0":
                    # if i == unquote(j.find("DAISY:content", self.NS).get("src")):
                    if re.search(i, unquote(j.find("DAISY:content", self.NS).get("src"))) is not None:
                        name = j.find("DAISY:navLabel/DAISY:text", self.NS).text
                        break
                elif self.version == "3.0":
                    # if i == unquote(j.get("href")):
                    if re.search(i, unquote(j.get("href"))) is not None:
                        name = "".join(list(j.itertext()))
                        break
            self.toc_entries.append(name)


class HTMLtoLines(HTMLParser):
    para = {"p", "div"}
    inde = {"q", "dt", "dd", "blockquote"}
    pref = {"pre"}
    code = {"code"}  # Add code tag detection
    bull = {"li"}
    hide = {"script", "style", "head"}
    # hide = {"script", "style", "head", ", "sub}

    def __init__(self):
        HTMLParser.__init__(self)
        self.text = [""]
        self.imgs = []
        self.ishead = False
        self.isinde = False
        self.isbull = False
        self.ispref = False
        self.iscode = False  # Track if we're in a code block
        self.isprose = False  # Track if explicitly marked as prose via class
        self.ishidden = False
        self.idhead = set()
        self.idinde = set()
        self.idbull = set()
        self.idpref = set()
        self.idcode = set()  # Track code block line indices
        self.idprose = set()  # Track prose block line indices
        self.code_lang = None  # Track detected language for current code block

    def handle_starttag(self, tag, attrs):
        # Check for prose-indicating classes on ANY tag (highest priority)
        for attr_name, attr_value in attrs:
            if attr_name == "class" and attr_value:
                for class_name in attr_value.split():
                    if "text" in class_name.lower():
                        # Any class containing "text" indicates prose, not code
                        self.isprose = True
                        break
                if self.isprose:
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
                            elif class_name in ("programlisting", "code", "codeintext", "sourceCode", "highlight", "code-area"):
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
            self.text[-1] += "^{"
        elif tag == "sub":
            self.text[-1] += "_{"
        # NOTE: "img" and "image"
        # In HTML, both are startendtag (no need endtag)
        # but in XHTML both need endtag
        elif tag in {"img", "image"}:
            for i in attrs:
                if (tag == "img" and i[0] == "src")\
                   or (tag == "image" and i[0].endswith("href")):
                    self.text.append("[IMG:{}]".format(len(self.imgs)))
                    self.imgs.append(unquote(i[1]))

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.text += [""]
        elif tag in {"img", "image"}:
            for i in attrs:
                if (tag == "img" and i[0] == "src")\
                   or (tag == "image" and i[0].endswith("href")):
                    self.text.append("[IMG:{}]".format(len(self.imgs)))
                    self.imgs.append(unquote(i[1]))
                    self.text.append("")

    def handle_endtag(self, tag):
        if re.match("h[1-6]", tag) is not None:
            self.text.append("")
            self.text.append("")
            self.ishead = False
        elif tag in self.para:
            self.text.append("")
            self.isprose = False  # Reset prose flag when paragraph ends
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
            # Reset iscode when pre tag ends (it may have been set by class attribute)
            # But only if we're not inside a nested code tag
            self.iscode = False
            self.isprose = False  # Reset prose flag
            self.code_lang = None  # Reset language
        elif tag in self.code:
            self.iscode = False
            self.code_lang = None  # Reset language
        elif tag in self.bull:
            if self.text[-1] != "":
                self.text.append("")
            self.isbull = False
        elif tag in {"sub", "sup"}:
            self.text[-1] += "}"
        elif tag in {"img", "image"}:
            self.text.append("")

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
            
            self.text[-1] += line
            if self.ishead:
                self.idhead.add(len(self.text)-1)
            elif self.isbull:
                self.idbull.add(len(self.text)-1)
            elif self.isinde:
                self.idinde.add(len(self.text)-1)
            elif self.ispref or self.iscode:
                # Mark as preformatted - <pre> and <code> tags take priority over parent class attributes
                self.idpref.add(len(self.text)-1)
                if self.iscode:  # Only mark as code if it's actually a code tag
                    self.idcode.add(len(self.text)-1)
                    # Remove from prose if it was previously marked (prevents dual marking)
                    self.idprose.discard(len(self.text)-1)
                # NOTE: Don't mark as prose here - <pre>/<code> tags override parent class attributes
            elif self.isprose:  # Mark regular text as prose if class contains "text"
                self.idprose.add(len(self.text)-1)

    def _is_continuation_line(self, current_line, next_line, current_idx=None, next_idx=None):
        """Check if next_line is a continuation of current_line"""
        if not current_line or not next_line:
            return False
        
        # CRITICAL: Don't concatenate different content types (code, bullets, prose)
        if current_idx is not None and next_idx is not None:
            current_is_code = current_idx in self.idcode
            next_is_code = next_idx in self.idcode
            current_is_bullet = current_idx in self.idbull
            next_is_bullet = next_idx in self.idbull
            current_is_prose = current_idx in self.idprose
            next_is_prose = next_idx in self.idprose
            
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
        
        code_score = 0
        prose_score = 20   # Start with reasonable prose advantage
        
        # Strong programming keywords (unambiguous code indicators)
        strong_code_keywords = {
            'import', 'export', 'def', 'class', 'const', 'let', 'var', 'async', 'await',
            'yield', 'lambda', 'implements', 'interface', 'enum', 'struct', 'union',
            'public', 'private', 'protected', 'static', 'final', 'void', 'null', 'undefined',
            'extends', 'super', 'this', 'self', 'typeof', 'instanceof', 'new', 'delete',
            'throw', 'throws', 'catch', 'finally', 'try',
            # SQL keywords (unambiguous database code)
            'select', 'from', 'where', 'join', 'inner', 'left', 'right', 'outer', 'on',
            'group', 'order', 'having', 'distinct', 'limit', 'offset', 'union', 'intersect',
            'create', 'alter', 'drop', 'insert', 'update', 'delete', 'truncate',
            # Cypher/Neo4j keywords
            'match', 'merge', 'optional', 'with', 'unwind', 'return', 'skip', 'collect',
            'load', 'csv', 'headers', 'node', 'relationship', 'path', 'call', 'yield',
            # Shell/Bash keywords  
            'echo', 'cd', 'ls', 'mkdir', 'chmod', 'grep', 'awk', 'sed', 'ps', 'kill'
        }
        
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
        
        # Try to guess the language
        try:
            lexer = guess_lexer(code_text)
            # If guess_lexer returns TextLexer, try heuristics
            if lexer.__class__.__name__ == 'TextLexer':
                raise ClassNotFound("Guessing failed, trying heuristics")
            return lexer
        except ClassNotFound:
            # Use heuristics for common languages
            code_lower = code_text.lower().strip()
            
            # Java heuristics
            if ('public class' in code_lower or 'private class' in code_lower or 
                'public static void main' in code_lower or 'system.out.print' in code_lower):
                try:
                    return get_lexer_by_name('java')
                except ClassNotFound:
                    pass
            
            # Python heuristics  
            elif ('def ' in code_lower or 'import ' in code_lower or 'print(' in code_lower):
                try:
                    return get_lexer_by_name('python')
                except ClassNotFound:
                    pass
            
            # JavaScript heuristics
            elif ('function ' in code_lower or 'console.log' in code_lower or 'var ' in code_lower or 'let ' in code_lower):
                try:
                    return get_lexer_by_name('javascript')
                except ClassNotFound:
                    pass
            
            # C/C++ heuristics
            elif ('#include' in code_lower or 'int main(' in code_lower or 'printf(' in code_lower):
                try:
                    return get_lexer_by_name('c')
                except ClassNotFound:
                    pass
            
            # Cypher heuristics (Neo4j query language)
            elif (('create (' in code_lower or 'match (' in code_lower or 'load csv' in code_lower or 
                   'merge (' in code_lower or 'return ' in code_lower or 'where ' in code_lower) and 
                  ('businessobject' in code_lower or ':' in code_text or '[:' in code_text or 
                   'neo4j' in code_lower or 'cypher' in code_lower or 'graph' in code_lower or
                   'objectid' in code_lower or 'row.' in code_lower)):
                try:
                    return get_lexer_by_name('cypher')
                except ClassNotFound:
                    pass
            
            # CSV heuristics (comma-separated values)
            elif (',' in code_text and code_text.count('\n') > 0 and 
                  len([line for line in code_text.split('\n') if ',' in line]) >= 2):
                try:
                    # CSV doesn't have a dedicated lexer, use text with basic highlighting
                    return get_lexer_by_name('text')
                except ClassNotFound:
                    pass
            
            # Fall back to plain text
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
            
            # Get tokens from organized text
            tokens = list(lexer.get_tokens(organized_text))
            
            # Convert tokens to colored text
            result = []
            current_line = ""
            current_colors = []
            
            for token_type, token_value in tokens:
                # Map token types to colors
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
            
        except Exception:
            # Fall back to original text if highlighting fails
            return [(code_text, [])]
    
    def get_token_color(self, token_type):
        """Map Pygments token types to terminal colors."""
        # Bright, clean color scheme - no backgrounds, just bright foreground colors
        token_colors = {
            'Keyword': (0, 150, 255),           # Bright blue for keywords (import, export, etc.)
            'Keyword.Constant': (0, 150, 255),
            'Keyword.Declaration': (0, 150, 255), 
            'Keyword.Namespace': (0, 150, 255),
            'Keyword.Pseudo': (0, 150, 255),
            'Keyword.Reserved': (0, 150, 255),
            'Keyword.Type': (100, 200, 255),    # Lighter blue for types
            
            'Name.Class': (255, 255, 0),        # Bright yellow for classes
            'Name.Function': (255, 255, 0),     # Bright yellow for functions
            'Name.Builtin': (255, 100, 255),    # Bright magenta for builtins
            'Name.Exception': (255, 100, 0),    # Bright orange for exceptions
            
            'Literal.String': (0, 255, 0),      # Bright green for strings
            'Literal.String.Double': (0, 255, 0),
            'Literal.String.Single': (0, 255, 0),
            'Literal.Number': (255, 165, 0),    # Bright orange for numbers
            'Literal.Number.Integer': (255, 165, 0),
            'Literal.Number.Float': (255, 165, 0),
            
            'Comment': (128, 128, 128),         # Medium gray for comments
            'Comment.Single': (128, 128, 128),
            'Comment.Multiline': (128, 128, 128),
            
            'Operator': (255, 255, 255),        # White for operators
            'Punctuation': (255, 255, 255),     # White for punctuation
        }
        
        # Convert token type to string and find best match
        token_str = str(token_type)
        
        # Remove "Token." prefix if present
        if token_str.startswith("Token."):
            token_str = token_str[6:]
        
        # Try exact match first
        if token_str in token_colors:
            return token_colors[token_str]
        
        # Try partial matches (e.g., "Name.Function.Magic" -> "Name.Function")
        for pattern, color in token_colors.items():
            if token_str.startswith(pattern):
                return color
        
        # Default to white for unknown tokens
        return (255, 255, 255)

    def highlight_urls_in_prose(self, text_lines):
        """Apply URL highlighting to prose text lines."""
        # URL highlighting works even without full color support (uses fallback styling)
            
        import re
        # URL regex pattern - matches http(s):// URLs
        url_pattern = r'https?://[^\s\)\]\}]+'
        highlighted_lines = []
        
        for line in text_lines:
            if re.search(url_pattern, line):
                # Line contains URLs - apply highlighting
                highlighted_line = "URL_HL:" + line
                highlighted_lines.append(highlighted_line)
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
            
            if is_head:
                # Add HEADER: prefix to preserve header info for rendering
                centered_text = i.rjust(width//2 + len(i)//2)
                text += ["HEADER:" + centered_text] + [""]
            elif is_inde:
                wrapped_lines = ["   "+j for j in textwrap.wrap(i, width - 3)]
                highlighted_lines = self.highlight_urls_in_prose(wrapped_lines)
                text += highlighted_lines + [""]
            elif is_bull:
                tmp = textwrap.wrap(i, width - 3)
                bullet_lines = [" - "+j if j == tmp[0] else "   "+j for j in tmp]
                highlighted_lines = self.highlight_urls_in_prose(bullet_lines)
                text += highlighted_lines + [""]
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
                    for line_text, line_colors in highlighted_lines:
                        if line_text:
                            # Store syntax-highlighted code as-is without wrapping to preserve formatting
                            text.append("SYNTAX_HL:" + line_text + "|" + str(line_colors))
                        else:
                            # Empty line within code block - mark as syntax highlighted to preserve context
                            text.append("SYNTAX_HL:|[]")
                    text.append("")  # Empty line after code block
                else:
                    # Regular preformatted text (no syntax highlighting) - preserve original formatting
                    tmp = i.splitlines()
                    # Don't wrap preformatted text - it should maintain its original formatting
                    text += ["   "+line for line in tmp] + [""]
            else:
                wrapped_lines = textwrap.wrap(i, width)
                highlighted_lines = self.highlight_urls_in_prose(wrapped_lines)
                background_lines = self.add_table_background(highlighted_lines)
                text += background_lines + [""]
        return text, self.imgs


def loadstate():
    global STATE, STATEFILE
    if os.getenv("HOME") is not None:
        STATEFILE = os.path.join(os.getenv("HOME"), ".termbook")
        if os.path.isdir(os.path.join(os.getenv("HOME"), ".config")):
            configdir = os.path.join(os.getenv("HOME"), ".config", "epr")
            os.makedirs(configdir, exist_ok=True)
            if os.path.isfile(STATEFILE):
                if os.path.isfile(os.path.join(configdir, "config")):
                    os.remove(os.path.join(configdir, "config"))
                shutil.move(STATEFILE, os.path.join(configdir, "config"))
            STATEFILE = os.path.join(configdir, "config")
    elif os.getenv("USERPROFILE") is not None:
        STATEFILE = os.path.join(os.getenv("USERPROFILE"), ".termbook")
    else:
        STATEFILE = os.devnull

    if os.path.exists(STATEFILE):
        with open(STATEFILE, "r") as f:
            STATE = json.load(f)


def savestate(file, index, width, pos, pctg ):
    for i in STATE:
        STATE[i]["lastread"] = str(0)
    STATE[file]["lastread"] = str(1)
    STATE[file]["index"] = str(index)
    STATE[file]["width"] = str(width)
    STATE[file]["pos"] = str(pos)
    STATE[file]["pctg"] = str(pctg)
    with open(STATEFILE, "w") as f:
        json.dump(STATE, f, indent=4)


def pgup(pos, winhi, preservedline=0, c=1):
    if pos >= (winhi - preservedline) * c:
        return pos - (winhi - preservedline) * c
    else:
        return 0


def pgdn(pos, tot, winhi, preservedline=0,c=1):
    if pos + (winhi * c) <= tot - winhi:
        return pos + (winhi * c)
    else:
        pos = tot - winhi
        if pos < 0:
            return 0
        return pos


def pgend(tot, winhi):
    if tot - winhi >= 0:
        return tot - winhi
    else:
        return 0


def toc(stdscr, src, index):
    rows, cols = stdscr.getmaxyx()
    hi, wi = rows - 4, cols - 4
    Y, X = 2, 2
    oldindex = index
    toc = curses.newwin(hi, wi, Y, X)
    if COLORSUPPORT:
        toc.bkgd(stdscr.getbkgd())

    toc.box()
    toc.keypad(True)
    toc.addstr(1,2, "Table of Contents")
    toc.addstr(2,2, "-----------------")
    key_toc = 0

    totlines = len(src)
    toc.refresh()
    pad = curses.newpad(totlines, wi - 2 )
    if COLORSUPPORT:
        pad.bkgd(stdscr.getbkgd())

    pad.keypad(True)

    padhi = rows - 5 - Y - 4 + 1
    y = 0
    if index in range(padhi//2, totlines - padhi//2):
        y = index - padhi//2 + 1
    d = len(str(totlines))
    span = []

    for n, i in enumerate(src):
        # strs = "  " + str(n+1).rjust(d) + " " + i[0]
        strs = "  " + i
        strs = strs[0:wi-3]
        pad.addstr(n, 0, strs)
        span.append(len(strs))

    countstring = ""
    while key_toc not in TOC|QUIT:
        if countstring == "":
            count = 1
        else:
            count = int(countstring)
        if key_toc in range(48, 58): # i.e., k is a numeral
            countstring = countstring + chr(key_toc)
        else:
            if key_toc in SCROLL_UP|SCROLL_UP_K or key_toc in PAGE_UP:
                index -= count
                if index < 0:
                    index = 0
            elif key_toc in SCROLL_DOWN|SCROLL_DOWN_J or key_toc in PAGE_DOWN:
                index += count
                if index + 1 >= totlines:
                    index = totlines - 1
            elif key_toc in FOLLOW:
                # if index == oldindex:
                #     break
                return index
            # elif key_toc in PAGE_UP:
            #     index -= 3
            #     if index < 0:
            #         index = 0
            # elif key_toc in PAGE_DOWN:
            #     index += 3
            #     if index >= totlines:
            #         index = totlines - 1
            elif key_toc in CH_HOME:
                index = 0
            elif key_toc in CH_END:
                index = totlines - 1
            elif key_toc in {curses.KEY_RESIZE}|HELP|META:
                return key_toc
            countstring = ""

        while index not in range(y, y+padhi):
            if index < y:
                y -= 1
            else:
                y += 1

        for n in range(totlines):
            att = curses.A_REVERSE if index == n else curses.A_NORMAL
            pre = ">>" if index == n else "  "
            pad.addstr(n, 0, pre)
            pad.chgat(n, 0, span[n], pad.getbkgd() | att)

        pad.refresh(y, 0, Y+4,X+4, rows - 5, cols - 6)
        key_toc = toc.getch()

    toc.clear()
    toc.refresh()
    return


def meta(stdscr, ebook):
    rows, cols = stdscr.getmaxyx()
    hi, wi = rows - 4, cols - 4
    Y, X = 2, 2
    meta = curses.newwin(hi, wi, Y, X)
    if COLORSUPPORT:
        meta.bkgd(stdscr.getbkgd())

    meta.box()
    meta.keypad(True)
    meta.addstr(1,2, "Metadata")
    meta.addstr(2,2, "--------")
    key_meta = 0

    mdata = []
    for i in ebook.get_meta():
        data = re.sub("<[^>]*>", "", i[1])
        data = re.sub("\t", "", data)
        mdata += textwrap.wrap(i[0].upper() + ": " + data, wi - 6)
    src_lines = mdata
    totlines = len(src_lines)

    pad = curses.newpad(totlines, wi - 2 )
    if COLORSUPPORT:
        pad.bkgd(stdscr.getbkgd())

    pad.keypad(True)
    for n, i in enumerate(src_lines):
        pad.addstr(n, 0, i)
    y = 0
    meta.refresh()
    pad.refresh(y,0, Y+4,X+4, rows - 5, cols - 6)

    padhi = rows - 5 - Y - 4 + 1

    while key_meta not in META|QUIT:
        if key_meta in SCROLL_UP|SCROLL_UP_K and y > 0:
            y -= 1
        elif key_meta in SCROLL_DOWN|SCROLL_DOWN_J and y < totlines - hi + 6:
            y += 1
        elif key_meta in PAGE_UP:
            y = pgup(y, padhi)
        elif key_meta in PAGE_DOWN:
            y = pgdn(y, totlines, padhi)
        elif key_meta in CH_HOME:
            y = 0
        elif key_meta in CH_END:
            y = pgend(totlines, padhi)
        elif key_meta in {curses.KEY_RESIZE}|HELP|TOC:
            return key_meta
        pad.refresh(y,0, 6,5, rows - 5, cols - 5)
        key_meta = meta.getch()

    meta.clear()
    meta.refresh()
    return


def help(stdscr):
    rows, cols = stdscr.getmaxyx()
    hi, wi = rows - 4, cols - 4
    Y, X = 2, 2
    help = curses.newwin(hi, wi, Y, X)
    if COLORSUPPORT:
        help.bkgd(stdscr.getbkgd())

    help.box()
    help.keypad(True)
    help.addstr(1,2, "Help")
    help.addstr(2,2, "----")
    key_help = 0

    src = re.search("Key Bind(\n|.)*", __doc__).group()
    src_lines = src.splitlines()
    totlines = len(src_lines)

    pad = curses.newpad(totlines, wi - 2 )
    if COLORSUPPORT:
        pad.bkgd(stdscr.getbkgd())

    pad.keypad(True)
    for n, i in enumerate(src_lines):
        pad.addstr(n, 0, i)
    y = 0
    help.refresh()
    pad.refresh(y,0, Y+4,X+4, rows - 5, cols - 6)

    padhi = rows - 5 - Y - 4 + 1

    while key_help not in HELP|QUIT:
        if key_help in SCROLL_UP|SCROLL_UP_K and y > 0:
            y -= 1
        elif key_help in SCROLL_DOWN|SCROLL_DOWN_J and y < totlines - hi + 6:
            y += 1
        elif key_help in PAGE_UP:
            y = pgup(y, padhi)
        elif key_help in PAGE_DOWN:
            y = pgdn(y, totlines, padhi)
        elif key_help in CH_HOME:
            y = 0
        elif key_help in CH_END:
            y = pgend(totlines, padhi)
        elif key_help in {curses.KEY_RESIZE}|META|TOC:
            return key_help
        pad.refresh(y,0, 6,5, rows - 5, cols - 5)
        key_help = help.getch()

    help.clear()
    help.refresh()
    return


def dots_path(curr, tofi):
    candir = curr.split("/")
    tofi = tofi.split("/")
    alld = tofi.count("..")
    t = len(candir)
    candir = candir[0:t-alld-1]
    try:
        while True:
            tofi.remove("..")
    except ValueError:
        pass
    return "/".join(candir+tofi)


def find_media_viewer():
    global VWR
    VWR_LIST = [
        "feh",
        "gio",
        "sxiv",
        "gnome-open",
        "gvfs-open",
        "xdg-open",
        "kde-open",
        "firefox"
    ]
    if sys.platform == "win32":
        VWR = ["start"]
    elif sys.platform == "darwin":
        VWR = ["open"]
    else:
        for i in VWR_LIST:
            if shutil.which(i) is not None:
                VWR = [i]
                break

    if VWR[0] in {"gio"}:
        VWR.append("open")


def open_media(scr, epub, src):
    sfx = os.path.splitext(src)[1].lower()
    
    # Try to display image in terminal with 24-bit color if it's an image file
    if PIL_AVAILABLE and sfx in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']:
        try:
            # Read image data
            img_data = epub.file.read(src)
            img = Image.open(BytesIO(img_data))
            
            # Get terminal dimensions
            rows, cols = scr.getmaxyx()
            
            # Calculate size to fit in terminal (leave some margin)
            max_width = min(cols - 4, 100)
            max_height = rows - 4
            
            # Always use 24-bit color with horizontal slab characters
            color_lines = render_image_with_quarter_blocks(img, max_width, max_height)
            
            # Temporarily exit curses mode to display with full color
            curses.endwin()
            
            # Clear screen and display with full 24-bit color
            print("\033[2J\033[H")  # Clear screen and move cursor to top
            print("Press Enter to continue...")
            print()
            
            # Display the image with full 24-bit color
            for line in color_lines:
                print(line)
            
            # Wait for user input
            input()
            
            # Restart curses
            scr = curses.initscr()
            curses.start_color()
            curses.use_default_colors()
            curses.noecho()
            curses.cbreak()
            scr.keypad(True)
            curses.curs_set(0)
            
            return ord('\n')  # Return Enter key
                
        except Exception as e:
            # Fall back to external viewer if terminal display fails
            pass
    
    # Fall back to external viewer for non-images or if display failed
    fd, path = tempfile.mkstemp(suffix=sfx)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(epub.file.read(src))
        subprocess.call(
            VWR + [path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        k = scr.getch()
    finally:
        os.remove(path)
    return k


def searching(stdscr, pad, src, width, y, ch, tot):
    global SEARCHPATTERN
    rows, cols = stdscr.getmaxyx()
    x = (cols - width) // 2

    if SEARCHPATTERN is None:
        stat = curses.newwin(1, cols, rows-1, 0)
        if COLORSUPPORT:
            stat.bkgd(stdscr.getbkgd())
        stat.keypad(True)
        curses.echo(1)
        curses.curs_set(1)
        SEARCHPATTERN = ""
        stat.addstr(0, 0, " Regex:", curses.A_REVERSE)
        stat.addstr(0, 7, SEARCHPATTERN)
        stat.refresh()
        while True:
            ipt = stat.get_wch()
            if type(ipt) == str:
                ipt = ord(ipt)

            if ipt == 27:
                stat.clear()
                stat.refresh()
                curses.echo(0)
                curses.curs_set(0)
                SEARCHPATTERN = None
                return None, y
            elif ipt == 10:
                SEARCHPATTERN = "/"+SEARCHPATTERN
                stat.clear()
                stat.refresh()
                curses.echo(0)
                curses.curs_set(0)
                break
            # TODO: why different behaviour unix dos or win lin
            elif ipt in {8, 127, curses.KEY_BACKSPACE}:
                SEARCHPATTERN = SEARCHPATTERN[:-1]
            elif ipt == curses.KEY_RESIZE:
                stat.clear()
                stat.refresh()
                curses.echo(0)
                curses.curs_set(0)
                SEARCHPATTERN = None
                return curses.KEY_RESIZE, None
            else:
                SEARCHPATTERN += chr(ipt)

            stat.clear()
            stat.addstr(0, 0, " Regex:", curses.A_REVERSE)
            # stat.addstr(0, 7, SEARCHPATTERN)
            stat.addstr(
                    0, 7,
                    SEARCHPATTERN if 7+len(SEARCHPATTERN) < cols else "..."+SEARCHPATTERN[7-cols+4:]
                    )
            stat.refresh()

    if SEARCHPATTERN in {"?", "/"}:
        SEARCHPATTERN = None
        return None, y

    found = []
    try:
        pattern = re.compile(SEARCHPATTERN[1:], re.IGNORECASE)
    except re.error:
        stdscr.addstr(rows-1, 0, "Invalid Regex!", curses.A_REVERSE)
        SEARCHPATTERN = None
        s = stdscr.getch()
        if s in QUIT:
            return None, y
        else:
            return s, None

    for n, i in enumerate(src):
        for j in pattern.finditer(i):
            found.append([n, j.span()[0], j.span()[1] - j.span()[0]])

    if found == []:
        if SEARCHPATTERN[0] == "/" and ch + 1 < tot:
            return None, 1
        elif SEARCHPATTERN[0] == "?" and ch > 0:
            return None, -1
        else:
            s = 0
            while True:
                if s in QUIT:
                    SEARCHPATTERN = None
                    stdscr.clear()
                    stdscr.refresh()
                    return None, y
                elif s == ord("n") and ch == 0:
                    SEARCHPATTERN = "/"+SEARCHPATTERN[1:]
                    return None, 1
                elif s == ord("N") and ch +1 == tot:
                    SEARCHPATTERN = "?"+SEARCHPATTERN[1:]
                    return None, -1

                stdscr.clear()
                stdscr.addstr(rows-1, 0, " Finished searching: " + SEARCHPATTERN[1:cols-22] + " ", curses.A_REVERSE)
                stdscr.refresh()
                pad.refresh(y,0, 0,x, rows-2,x+width)
                s = pad.getch()

    sidx = len(found) - 1
    if SEARCHPATTERN[0] == "/":
        if y > found[-1][0]:
            return None, 1
        for n, i in enumerate(found):
            if i[0] >= y:
                sidx = n
                break

    s = 0
    msg = " Searching: " + SEARCHPATTERN[1:] + " --- Res {}/{} Ch {}/{} ".format(
        sidx + 1,
        len(found),
        ch+1, tot)
    while True:
        if s in QUIT:
            SEARCHPATTERN = None
            for i in found:
                pad.chgat(i[0], i[1], i[2], pad.getbkgd())
            stdscr.clear()
            stdscr.refresh()
            return None, y
        elif s == ord("n"):
            SEARCHPATTERN = "/"+SEARCHPATTERN[1:]
            if sidx == len(found) - 1:
                if ch + 1 < tot:
                    return None, 1
                else:
                    s = 0
                    msg = " Finished searching: " + SEARCHPATTERN[1:] + " "
                    continue
            else:
                sidx += 1
                msg = " Searching: " + SEARCHPATTERN[1:] + " --- Res {}/{} Ch {}/{} ".format(
                    sidx + 1,
                    len(found),
                    ch+1, tot)
        elif s == ord("N"):
            SEARCHPATTERN = "?"+SEARCHPATTERN[1:]
            if sidx == 0:
                if ch > 0:
                    return None, -1
                else:
                    s = 0
                    msg = " Finished searching: " + SEARCHPATTERN[1:] + " "
                    continue
            else:
                sidx -= 1
                msg = " Searching: " + SEARCHPATTERN[1:] + " --- Res {}/{} Ch {}/{} ".format(
                    sidx + 1,
                    len(found),
                    ch+1, tot)
        elif s == curses.KEY_RESIZE:
            return s, None

        while found[sidx][0] not in list(range(y, y+rows-1)):
            if found[sidx][0] > y:
                y += rows - 1
            else:
                y -= rows - 1
                if y < 0:
                    y = 0

        for n, i in enumerate(found):
            # attr = (pad.getbkgd() | curses.A_REVERSE) if n == sidx else pad.getbkgd()
            attr = curses.A_REVERSE if n == sidx else curses.A_NORMAL
            pad.chgat(i[0], i[1], i[2], pad.getbkgd() | attr)

        stdscr.clear()
        stdscr.addstr(rows-1, 0, msg, curses.A_REVERSE)
        stdscr.refresh()
        pad.refresh(y,0, 0,x, rows-2,x+width)
        s = pad.getch()


# Smart color palette system
_color_palette = []  # Pre-computed palette of color indices
_color_pairs = {}    # Cache of created color pairs  
_next_color_pair = 1
_MAX_COLOR_PAIRS = 5000  # Generous limit for images - much more color variety
_SYNTAX_COLOR_PAIRS = {}  # Dedicated cache for syntax highlighting pairs
_SYNTAX_PAIR_START = 5001  # Reserve pairs 5001-5050 for syntax highlighting

def init_syntax_color_pairs():
    """Pre-allocate color pairs for syntax highlighting in reserved range."""
    global _SYNTAX_COLOR_PAIRS
    
    # Define syntax highlighting colors
    syntax_colors = [
        (255, 150, 200),  # Light pink for keywords
        (180, 255, 180),  # Very light green for strings  
        (220, 220, 220),  # Very light gray for punctuation
        (100, 100, 100),  # Dark gray for comments
        (255, 235, 150),  # Light yellow for classes
        (200, 255, 200),  # Light green for functions
        (220, 180, 255),  # Light purple for builtins
        (255, 200, 150),  # Light orange for numbers
    ]
    
    # Pre-allocate these colors in the reserved range (5001-5050)
    pair_id = _SYNTAX_PAIR_START
    for color in syntax_colors:
        if COLORSUPPORT and pair_id <= 5050:
            try:
                # Convert to color indices
                fg_color = find_closest_palette_color(color)
                
                fg_idx = rgb_to_color_index(*fg_color)
                bg_idx = 0  # Black background
                
                # Validate indices
                if fg_idx < 0 or fg_idx > 255: 
                    continue
                    
                # Initialize the pair in the reserved range
                curses.init_pair(pair_id, fg_idx, bg_idx)
                _SYNTAX_COLOR_PAIRS[color] = pair_id
                pair_id += 1
            except (curses.error, ValueError):
                continue

def init_smart_color_palette():
    """Initialize a smart color palette with commonly used colors."""
    global _color_palette
    if _color_palette:
        return  # Already initialized
    
    # Create a strategic palette of 64 well-distributed colors
    # This includes grayscale and color cube samples
    palette = []
    
    # Add grayscale (16 levels)
    for i in range(16):
        gray = i * 255 // 15
        palette.append((gray, gray, gray))
    
    # Add color cube samples (48 colors - 4x4x3 grid)  
    for r in range(4):
        for g in range(4):
            for b in range(3):
                red = r * 255 // 3
                green = g * 255 // 3
                blue = b * 255 // 2
                palette.append((red, green, blue))
    
    _color_palette = palette

def find_closest_palette_color(target_rgb):
    """Find the closest color in our palette using fuzzy matching."""
    if not _color_palette:
        init_smart_color_palette()
    
    target_r, target_g, target_b = target_rgb
    best_match = _color_palette[0]
    best_distance = float('inf')
    
    for palette_rgb in _color_palette:
        # Use weighted RGB distance (human eye is more sensitive to green)
        r_diff = (target_r - palette_rgb[0]) * 0.3
        g_diff = (target_g - palette_rgb[1]) * 0.59  
        b_diff = (target_b - palette_rgb[2]) * 0.11
        distance = r_diff*r_diff + g_diff*g_diff + b_diff*b_diff
        
        if distance < best_distance:
            best_distance = distance
            best_match = palette_rgb
    
    return best_match

def rgb_to_color_index(r, g, b):
    """Convert RGB to 256-color palette index."""
    try:
        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        
        # Check if it's grayscale
        if abs(r - g) < 12 and abs(g - b) < 12 and abs(r - b) < 12:
            gray = int((r + g + b) / 3)
            if gray < 8:
                return 0  # Black
            elif gray > 248:  
                return 15  # White
            else:
                # Map to grayscale 232-255 (24 levels)
                level = min(23, max(0, (gray - 8) * 23 // 240))
                return 232 + level
        else:
            # Map to 6x6x6 color cube (16-231)
            r_level = min(5, r * 6 // 256)
            g_level = min(5, g * 6 // 256)
            b_level = min(5, b * 6 // 256)
            return 16 + r_level * 36 + g_level * 6 + b_level
    except:
        return 7  # Default white

def get_color_pair_with_reversal(fg_color, bg_color, allow_reversal=True):
    """Get color pair, potentially reversing colors to reuse existing pairs."""
    global _next_color_pair
    
    if not COLORSUPPORT:
        return 0, False  # No color support, no reversal
    
    # Simplify colors using palette matching
    if fg_color:
        fg_color = find_closest_palette_color(fg_color)
    if bg_color:
        bg_color = find_closest_palette_color(bg_color)
    
    # Convert to color indices
    fg_idx = rgb_to_color_index(*fg_color) if fg_color else -1
    bg_idx = rgb_to_color_index(*bg_color) if bg_color else -1
    
    # Validate indices
    if fg_idx < 0 or fg_idx > 255: fg_idx = 7
    if bg_idx < 0 or bg_idx > 255: bg_idx = 0
    
    # Check if we already have this pair
    key = (fg_idx, bg_idx)
    if key in _color_pairs:
        return _color_pairs[key], False
    
    # Check if we have the reversed pair (and reversal is allowed)
    reversed_key = (bg_idx, fg_idx)
    if allow_reversal and reversed_key in _color_pairs:
        return _color_pairs[reversed_key], True  # Use reversed pair
    
    # Create new pair if we have room
    if _next_color_pair < _MAX_COLOR_PAIRS:
        try:
            curses.init_pair(_next_color_pair, fg_idx, bg_idx)
            _color_pairs[key] = _next_color_pair
            result_pair = _next_color_pair
            _next_color_pair += 1
            return result_pair, False
        except (curses.error, ValueError):
            pass  # Fall through to default
    
    return 0, False  # Default pair

def get_syntax_color_pair(color):
    """Get a pre-allocated color pair for syntax highlighting."""
    if not COLORSUPPORT:
        return 0
    
    # Try to find exact match in pre-allocated pairs
    if color in _SYNTAX_COLOR_PAIRS:
        return _SYNTAX_COLOR_PAIRS[color]
    
    # Fall back to regular color pair system with black background
    return get_color_pair(color, (0, 0, 0))

def get_color_pair(fg_color, bg_color=None):
    """Legacy interface that uses the new smart color system."""
    if bg_color is None:
        bg_color = (0, 0, 0)  # Default black background
    
    color_pair, _ = get_color_pair_with_reversal(fg_color, bg_color, allow_reversal=False)
    return color_pair

def render_image_curses(pad, img, start_y, start_x, max_width, max_height):
    """Render image using curses with smaller Unicode block characters."""
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Account for character aspect ratio (characters are ~2x taller than wide)
    # Use smaller blocks for higher resolution
    img.thumbnail((max_width * 2, max_height * 2), Image.Resampling.LANCZOS)
    width, height = img.size
    
    # Use smaller Unicode block characters for better resolution
    # Each character represents 2x2 pixels using quarter-block characters
    blocks = [' ', '▘', '▝', '▀', '▖', '▌', '▞', '▛', '▗', '▚', '▐', '▜', '▄', '▙', '▟', '█']
    
    for y in range(0, height, 2):
        if start_y + y // 2 >= start_y + max_height:
            break
        
        for x in range(0, width, 2):
            if start_x + x // 2 >= start_x + max_width:
                break
                
            # Get 2x2 pixel block
            pixels = []
            colors = []
            
            for py in range(2):
                for px in range(2):
                    pixel_y = min(y + py, height - 1)
                    pixel_x = min(x + px, width - 1)
                    r, g, b = img.getpixel((pixel_x, pixel_y))
                    colors.append((r, g, b))
                    # Convert to grayscale for block selection
                    luminance = int(0.299 * r + 0.587 * g + 0.114 * b)
                    pixels.append(1 if luminance > 128 else 0)
            
            # Create bit pattern for block character selection
            block_idx = (pixels[0] << 3) | (pixels[1] << 2) | (pixels[2] << 1) | pixels[3]
            char = blocks[block_idx]
            
            # Use dominant color for foreground
            avg_color = tuple(sum(c[i] for c in colors) // 4 for i in range(3))
            color_pair = get_color_pair(avg_color)
            
            try:
                if color_pair and COLORSUPPORT:
                    pad.addstr(start_y + y // 2, start_x + x // 2, char, curses.color_pair(color_pair))
                else:
                    pad.addstr(start_y + y // 2, start_x + x // 2, char)
            except curses.error:
                pass


def supports_24bit_color():
    """Assume terminal supports 24-bit color (truecolor)."""
    # Always return True to assume truecolor support as requested
    return True


def render_image_with_quarter_blocks(img, max_width, max_height):
    """Render image using horizontal slab character (▀) with 24-bit color foreground and background."""
    if img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Calculate proper aspect ratio - terminal chars are ~2x taller than wide
    # Each character will represent 2 pixels vertically (top and bottom)
    orig_width, orig_height = img.size
    aspect_ratio = orig_width / orig_height
    
    # Account for character aspect ratio (chars are ~2x taller than wide)
    # Each slab char represents 1x2 pixels vertically
    terminal_char_aspect = 2.0
    
    # Calculate the effective display area aspect ratio
    display_area_aspect = max_width / max_height * terminal_char_aspect
    
    if aspect_ratio > display_area_aspect:
        # Image is wider - fit to width
        target_width = max_width
        target_height = int(target_width / aspect_ratio) 
    else:
        # Image is taller - fit to height  
        target_height = max_height * 2  # 2 pixels per char height
        target_width = int(target_height * aspect_ratio)
    
    # Ensure even height for proper pairing
    if target_height % 2 == 1:
        target_height += 1
        
    img.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
    width, height = img.size
    
    color_lines = []
    
    # Process image in pairs of rows (top and bottom of each character)
    for y in range(0, height, 2):
        if y // 2 >= max_height:
            break
            
        line = ""
        for x in range(width):
            # Get top pixel color
            top_r, top_g, top_b = img.getpixel((x, y))
            
            # Get bottom pixel color (or same as top if at edge)
            if y + 1 < height:
                bottom_r, bottom_g, bottom_b = img.getpixel((x, y + 1))
            else:
                bottom_r, bottom_g, bottom_b = top_r, top_g, top_b
            
            # Use horizontal slab character ▀ with:
            # - foreground color = top pixel color
            # - background color = bottom pixel color
            line += f"\033[38;2;{top_r};{top_g};{top_b}m\033[48;2;{bottom_r};{bottom_g};{bottom_b}m▀\033[0m"
        
        color_lines.append(line)
    
    return color_lines



def render_images_inline(ebook, chpath, src_lines, imgs, max_width):
    """Convert image placeholders to block-based representation inline with color info."""
    if not PIL_AVAILABLE or not imgs:
        return src_lines, []
    
    new_lines = []
    image_info = []
    
    for line in src_lines:
        # Check if line contains an image placeholder
        img_match = re.search(r"\[IMG:([0-9]+)\]", line)
        if img_match:
            img_idx = int(img_match.group(1))
            if img_idx < len(imgs):
                try:
                    # Get image path
                    impath = imgs[img_idx]
                    imgsrc = dots_path(chpath, impath)
                    
                    # Read image data
                    img_data = ebook.file.read(imgsrc)
                    img = Image.open(BytesIO(img_data))
                    
                    # Smart scaling based on image size and available screen space
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    # Get original image dimensions to make intelligent scaling decisions
                    orig_width, orig_height = img.size
                    orig_aspect = orig_width / orig_height
                    
                    # Calculate available screen space
                    max_chars_available = max_width - 8
                    
                    # Account for terminal character aspect ratio (chars are 2:1 height:width)
                    # Each output character represents 2 pixels vertically with half-block technique
                    terminal_char_aspect = 2.0
                    
                    # Width-based scaling approach: expand small images to 75% of available width
                    max_width_by_screen = min(max_chars_available - 4, 80)  # Cap at 80 chars
                    max_height_available = 30  # Conservative max height
                    
                    # Calculate what percentage of screen width this image would naturally take
                    natural_char_width = min(orig_width // 2, max_width_by_screen)  # Rough conversion
                    width_percentage = natural_char_width / max_width_by_screen
                    
                    if width_percentage < 0.50:  # Image is less than 50% of available width
                        # Scale up to 75% of available width
                        target_char_width = int(max_width_by_screen * 0.75)
                        target_char_height = int(target_char_width / orig_aspect / terminal_char_aspect)
                        
                        # Ensure it fits vertically
                        if target_char_height > max_height_available:
                            target_char_height = max_height_available
                            target_char_width = int(target_char_height * orig_aspect * terminal_char_aspect)
                            target_char_width = min(target_char_width, max_width_by_screen)
                        
                        char_width = target_char_width
                        char_height = target_char_height
                    else:
                        # Image is already reasonably sized, just fit it properly
                        if natural_char_width <= max_width_by_screen:
                            char_width = natural_char_width
                            char_height = int(char_width / orig_aspect / terminal_char_aspect)
                        else:
                            # Too wide, constrain by width
                            char_width = max_width_by_screen
                            char_height = int(char_width / orig_aspect / terminal_char_aspect)
                        
                        # Ensure it fits vertically
                        if char_height > max_height_available:
                            char_height = max_height_available
                            char_width = int(char_height * orig_aspect * terminal_char_aspect)
                    
                    # Final bounds checking
                    char_width = max(12, min(char_width, max_width_by_screen))  # Minimum 12 chars wide
                    char_height = max(6, min(char_height, max_height_available))  # Minimum 6 chars tall
                    
                    # Determine scale factor for rendering quality
                    scale_factor = 2 if char_width >= 40 else 1
                    
                    # Set up rendering parameters to match the calculated dimensions exactly
                    # Each character represents 1 pixel horizontally and 2 pixels vertically (half-block technique)
                    target_pixel_width = char_width      # 1 pixel per character horizontally
                    target_pixel_height = char_height * 2 # 2 pixels per character vertically (half-block)
                    
                    # Processing parameters: process the entire width at once, 1 character per pixel
                    pixels_per_block = char_width  # Process entire width 
                    chars_per_block = char_width   # Output entire width
                    
                    # Use high-quality resampling and ensure dimensions are even to prevent interlacing
                    # Make sure target dimensions are even numbers to align with half-block rendering
                    if target_pixel_height % 2 != 0:
                        target_pixel_height += 1
                    
                    img.thumbnail((target_pixel_width, target_pixel_height), Image.Resampling.LANCZOS)
                    width, height = img.size
                    
                    # Ensure height is even for proper half-block pairing
                    if height % 2 != 0:
                        # Create a new image with even height by adding a row
                        new_img = Image.new('RGB', (width, height + 1), (0, 0, 0))
                        new_img.paste(img, (0, 0))
                        img = new_img
                        width, height = img.size
                    
                    # Unicode quarter-block characters for 2x2 pixel mapping
                    blocks = [' ', '▘', '▝', '▀', '▖', '▌', '▞', '▛', '▗', '▚', '▐', '▜', '▄', '▙', '▟', '█']
                    
                    # Store color and character info for each line - 2x taller, 4x wider
                    # Now with 2x sampling resolution in both directions
                    for y in range(0, height, 2):  # Process 2 rows at a time (proper half-block technique)
                        line = ""
                        line_colors = []
                        
                        # Process each column of pixels (simple 1:1 mapping)
                        for x in range(width):
                            # Get top and bottom pixels for this character
                            top_y = min(y, height - 1)
                            bottom_y = min(y + 1, height - 1)
                            
                            # Sample the pixels
                            if x < width and top_y < height:
                                top_pixel = img.getpixel((x, top_y))
                            else:
                                top_pixel = (0, 0, 0)
                                
                            if x < width and bottom_y < height:
                                bottom_pixel = img.getpixel((x, bottom_y))
                            else:
                                bottom_pixel = (0, 0, 0)
                            
                            # Ensure colors are valid
                            fg_color = tuple(max(0, min(255, c)) for c in top_pixel)
                            bg_color = tuple(max(0, min(255, c)) for c in bottom_pixel)
                            
                            # Use smart color system with potential slab reversal
                            color_pair, use_reversed = get_color_pair_with_reversal(fg_color, bg_color, allow_reversal=True)
                            
                            if use_reversed:
                                # Use lower slab (▄) with reversed colors  
                                line += '▄'
                                line_colors.append((bg_color, fg_color))  # Colors are swapped for the reversal
                            else:
                                # Use upper slab (▀) with normal colors
                                line += '▀'
                                line_colors.append((fg_color, bg_color))
                        
                        # Add the line to output
                        padding = " " * ((max_width - len(line)) // 2)
                        centered_line = padding + line
                        padded_colors = [((0, 0, 0), (0, 0, 0))] * len(padding) + line_colors
                        
                        # Add each line once (no repetition - half-block technique handles vertical resolution)
                        new_lines.append("IMG_LINE:" + centered_line)
                        image_info.append(padded_colors)
                    
                    new_lines.append("")  # Empty line after image
                    image_info.append([])  # Empty color info for empty line
                    
                except Exception as e:
                    # If image can't be processed, show error message
                    error_msg = f"[Error loading image: {imgs[img_idx]}]"
                    new_lines.append(" " * ((max_width - len(error_msg)) // 2) + error_msg)
                    image_info.append([])
            else:
                # Image index out of range
                new_lines.append(line)
                image_info.append([])
        else:
            new_lines.append(line)
            image_info.append([])
    
    return new_lines, image_info


def show_loading_animation(stdscr, message="Loading..."):
    """Display a centered loading animation with rolling spectrum effect."""
    rows, cols = stdscr.getmaxyx()
    
    # Center position
    center_row = rows // 2
    center_col = cols // 2
    
    # Don't clear screen - preserve current background
    # Just clear the message area to avoid artifacts
    msg_len = len(message)
    start_col = center_col - msg_len // 2
    
    # Clear just the message line area
    try:
        stdscr.addstr(center_row, 0, " " * cols)
    except:
        pass
    
    # Create saturated two-color gradient with doubled sequence
    gradient_steps = 16  # Steps for one direction
    start_color = (100, 150, 255)  # Bright blue (saturated)
    end_color = (100, 255, 200)    # Bright cyan-green (saturated)
    
    # Generate doubled sequence: dark→light→dark→light
    spectrum_colors = []
    
    # First half: start_color → end_color
    for i in range(gradient_steps):
        t = i / (gradient_steps - 1)  # 0.0 to 1.0
        r = int(start_color[0] + t * (end_color[0] - start_color[0]))
        g = int(start_color[1] + t * (end_color[1] - start_color[1]))
        b = int(start_color[2] + t * (end_color[2] - start_color[2]))
        spectrum_colors.append((r, g, b))
    
    # Second half: end_color → start_color (back to beginning)
    for i in range(gradient_steps):
        t = i / (gradient_steps - 1)  # 0.0 to 1.0
        r = int(end_color[0] + t * (start_color[0] - end_color[0]))
        g = int(end_color[1] + t * (start_color[1] - end_color[1]))
        b = int(end_color[2] + t * (start_color[2] - end_color[2]))
        spectrum_colors.append((r, g, b))
    
    return message, start_col, center_row, spectrum_colors

def update_loading_animation(stdscr, message, start_col, center_row, spectrum_colors, step):
    """Update the rolling spectrum animation."""
    try:
        # Create rolling wave effect across the message
        for i, char in enumerate(message):
            # Calculate color index with rolling wave
            color_idx = (step + i) % len(spectrum_colors)
            r, g, b = spectrum_colors[color_idx]
            
            # Get color pair for this RGB value
            if COLORSUPPORT:
                color_pair = get_color_pair((r, g, b), (-1, -1, -1))  # No background
                if color_pair > 0:
                    stdscr.addstr(center_row, start_col + i, char, 
                                curses.color_pair(color_pair) | curses.A_BOLD)
                else:
                    # Fallback to bold if color fails
                    stdscr.addstr(center_row, start_col + i, char, curses.A_BOLD)
            else:
                # No color support, just use bold
                stdscr.addstr(center_row, start_col + i, char, curses.A_BOLD)
        
        stdscr.refresh()
    except:
        pass  # Ignore any display errors

def reader(stdscr, ebook, index, width, y, pctg):
    k = 0 if SEARCHPATTERN is None else ord("/")
    rows, cols = stdscr.getmaxyx()
    x = (cols - width) // 2

    contents = ebook.contents
    toc_src = ebook.toc_entries
    chpath = contents[index]
    content = ebook.file.open(chpath).read()
    content = content.decode("utf-8")

    parser = HTMLtoLines()
    try:
        parser.feed(content)
        parser.close()
    except:
        pass

    src_lines, imgs = parser.get_lines(width)
    
    # Process images inline if PIL is available
    image_info = []
    if PIL_AVAILABLE:
        src_lines, image_info = render_images_inline(ebook, chpath, src_lines, imgs, width)
    
    totlines = len(src_lines)

    if y < 0 and totlines <= rows:
        y = 0
    elif pctg is not None:
        y = round(pctg*totlines)
    else:
        y = y % totlines

    pad = curses.newpad(totlines, width + 2) # + 2 unnecessary

    if COLORSUPPORT:
        pad.bkgd(stdscr.getbkgd())

    pad.keypad(True)
    
    # Render text with color support for images
    for n, line in enumerate(src_lines):
        try:
            # Check if this is an image line with color information
            if line.startswith("IMG_LINE:") and n < len(image_info) and image_info[n]:
                actual_line = line[9:]  # Remove "IMG_LINE:" prefix
                # Render character by character with foreground and background colors
                for char_idx, char in enumerate(actual_line):
                    if char_idx < len(image_info[n]):
                        fg_color, bg_color = image_info[n][char_idx]
                        if char != ' ' and COLORSUPPORT:
                            # Get appropriate color pair for this foreground/background combination
                            color_pair = get_color_pair(fg_color, bg_color)
                            if color_pair:
                                pad.addstr(n, char_idx, char, curses.color_pair(color_pair))
                            else:
                                pad.addstr(n, char_idx, char)
                        else:
                            pad.addstr(n, char_idx, char)
                    else:
                        pad.addstr(n, char_idx, char)
            elif line.startswith("SYNTAX_HL:"):
                # Syntax highlighted line with color information
                content = line[10:]  # Remove "SYNTAX_HL:" prefix
                
                # First, fill the entire line with dark background
                if COLORSUPPORT:
                    # Create dark background color pair (white text on dark gray background)
                    dark_bg_pair = get_color_pair((200, 200, 200), (32, 32, 32))  # Light gray text on dark gray background
                    if dark_bg_pair > 0:
                        # Fill from text start to right edge of terminal with dark background
                        for bg_col in range(cols - x):
                            try:
                                pad.addstr(n, bg_col, " ", curses.color_pair(dark_bg_pair))
                            except:
                                pass  # Ignore if we can't write at this position
                
                if "|" in content:
                    text_part, color_part = content.split("|", 1)
                    try:
                        # Parse the color list
                        import ast
                        colors = ast.literal_eval(color_part)
                        # Make ALL syntax highlighted text BOLD for visibility testing
                        # Check if this line contains keywords
                        line_lower = text_part.lower()
                        is_keyword_line = any(keyword in line_lower for keyword in ['import', 'export', 'from', 'const', 'let', 'var', 'function'])
                        
                        # Apply syntax highlighting with colors
                        for char_idx, char in enumerate(text_part):
                            if char_idx < len(colors) and colors[char_idx]:
                                # Get color tuple (r, g, b)
                                color_tuple = colors[char_idx]
                                if isinstance(color_tuple, (tuple, list)) and len(color_tuple) == 3:
                                    # Get or create color pair for this syntax color
                                    color_pair = get_syntax_color_pair(color_tuple)
                                    if color_pair > 0:
                                        pad.addstr(n, char_idx, char, curses.color_pair(color_pair))
                                    else:
                                        # Fallback to bold if color pair couldn't be created
                                        pad.addstr(n, char_idx, char, curses.A_BOLD)
                                else:
                                    # Invalid color format, use regular text
                                    pad.addstr(n, char_idx, char)
                            else:
                                # Regular text
                                pad.addstr(n, char_idx, char)
                    except Exception as e:
                        # If color parsing fails, just display as regular text
                        pad.addstr(n, 0, text_part)
                else:
                    # No color info, but still a syntax highlighted line - add background
                    if COLORSUPPORT:
                        # The dark background was already filled above
                        pass
                    # Display the text (which might be empty for blank lines)
                    if content.strip():
                        pad.addstr(n, 0, content)
            elif line.startswith("URL_HL:"):
                # URL highlighted line
                import re
                content = line[7:]  # Remove "URL_HL:" prefix
                url_pattern = r'https?://[^\s\)\]\}]+'
                
                # Find all URLs in the line
                urls = list(re.finditer(url_pattern, content))
                if urls:
                    current_pos = 0
                    for url_match in urls:
                        # Add text before URL
                        if url_match.start() > current_pos:
                            before_text = content[current_pos:url_match.start()]
                            pad.addstr(n, current_pos, before_text)
                        
                        # Add URL with bright cyan color (0, 255, 255)
                        url_text = url_match.group()
                        url_color_pair = get_syntax_color_pair((0, 255, 255))  # Bright cyan
                        if url_color_pair > 0:
                            pad.addstr(n, url_match.start(), url_text, curses.color_pair(url_color_pair) | curses.A_BOLD)
                        else:
                            # Fallback to bold + underline if color not available
                            pad.addstr(n, url_match.start(), url_text, curses.A_BOLD | curses.A_UNDERLINE)
                        
                        current_pos = url_match.end()
                    
                    # Add any remaining text after the last URL
                    if current_pos < len(content):
                        remaining_text = content[current_pos:]
                        pad.addstr(n, current_pos, remaining_text)
                else:
                    # No URLs found, display as regular text
                    pad.addstr(n, 0, content)
            elif line.startswith("TABLE_BG:"):
                # Table background line - similar to syntax highlighting background
                content = line[9:]  # Remove "TABLE_BG:" prefix
                
                # Fill background with slightly lighter gray than code blocks
                if COLORSUPPORT:
                    # Create table background color pair (lighter than code blocks)
                    table_bg_pair = get_color_pair((220, 220, 220), (48, 48, 48))  # Light gray text on medium gray background
                    if table_bg_pair > 0:
                        # Fill from text start to right edge with table background
                        for bg_col in range(cols - x):
                            try:
                                pad.addstr(n, bg_col, " ", curses.color_pair(table_bg_pair))
                            except:
                                pass  # Ignore if we can't write at this position
                
                # Add the actual text content
                if content.strip():
                    # Check for URLs within table content and highlight them
                    import re
                    url_pattern = r'https?://[^\s\)\]\}]+'
                    urls = list(re.finditer(url_pattern, content))
                    if urls:
                        # Handle URLs within table background
                        current_pos = 0
                        for url_match in urls:
                            # Add text before URL
                            if url_match.start() > current_pos:
                                before_text = content[current_pos:url_match.start()]
                                pad.addstr(n, current_pos, before_text)
                            
                            # Add URL with cyan color on table background
                            url_text = url_match.group()
                            url_color_pair = get_color_pair((0, 255, 255), (48, 48, 48))  # Cyan on table background
                            if url_color_pair > 0:
                                pad.addstr(n, url_match.start(), url_text, curses.color_pair(url_color_pair) | curses.A_BOLD)
                            else:
                                pad.addstr(n, url_match.start(), url_text, curses.A_BOLD | curses.A_UNDERLINE)
                            
                            current_pos = url_match.end()
                        
                        # Add remaining text
                        if current_pos < len(content):
                            remaining_text = content[current_pos:]
                            pad.addstr(n, current_pos, remaining_text)
                    else:
                        # No URLs, just add the text
                        pad.addstr(n, 0, content)
            elif line.startswith("HEADER:"):
                # Header line - add underline formatting only to the actual text
                content = line[7:]  # Remove "HEADER:" prefix
                if content.strip():
                    # Find the start and end of actual text (non-whitespace)
                    text_start = len(content) - len(content.lstrip())
                    text_end = len(content.rstrip())
                    
                    # Add leading whitespace without formatting
                    if text_start > 0:
                        pad.addstr(n, 0, content[:text_start])
                    
                    # Add the actual header text with underline + bold
                    header_text = content[text_start:text_end]
                    if header_text:
                        pad.addstr(n, text_start, header_text, curses.A_UNDERLINE | curses.A_BOLD)
                    
                    # Add trailing whitespace without formatting (if any)
                    if text_end < len(content):
                        pad.addstr(n, text_end, content[text_end:])
            else:
                # Regular text line
                display_line = line[9:] if line.startswith("IMG_LINE:") else line
                pad.addstr(n, 0, str(display_line))
        except:
            pass
    # Remove end markers - just display clean text

    stdscr.clear()
    stdscr.refresh()
    # try except to be more flexible on terminal resize
    try:
        pad.refresh(y,0, 0,x, rows-1,x+width)
    except curses.error:
        pass

    countstring = ""
    svline = "dontsave"
    while True:
        if countstring == "":
            count = 1
        else:
            count = int(countstring)
        if k in range(48, 58): # i.e., k is a numeral
            countstring = countstring + chr(k)
        else:
            if k in QUIT:
                if k == 27 and countstring != "":
                    countstring = ""
                else:
                    savestate(ebook.path, index, width, y, y/totlines)
                    sys.exit()
            elif k in SCROLL_UP:
                if count > 1:
                    svline = y - 1
                if y >= count:
                    y -= count
                elif y == 0 and index != 0:
                    return -1, width, -rows, None
                else:
                    y = 0
            elif k in SCROLL_UP_K:
                if count > 1:
                    svline = y - 1
                if y >= count:
                    y -= count
            elif k in PAGE_UP:
                if y == 0 and index != 0:
                    return -1, width, -rows, None
                else:
                    y = pgup(y, rows, LINEPRSRV, count)
            elif k in SCROLL_DOWN:
                if count > 1:
                    svline = y + rows - 1
                if y + count <= totlines - rows:
                    y += count
                elif y == totlines - rows and index != len(contents)-1:
                    return 1, width, 0, None
                else:
                    y = totlines - rows
            elif k in SCROLL_DOWN_J:
                if count > 1:
                    svline = y + rows - 1
                if y + count <= totlines - rows:
                    y += count
            elif k in PAGE_DOWN:
                if totlines - y - LINEPRSRV > rows:
                    # y = pgdn(y, totlines, rows, LINEPRSRV, count)
                    y += rows - LINEPRSRV
                elif index != len(contents)-1:
                    return 1, width, 0, None
            elif k in HALF_UP:
                countstring = str(rows//2)
                k = list(SCROLL_UP)[0]
                continue
            elif k in HALF_DOWN:
                countstring = str(rows//2)
                k = list(SCROLL_DOWN)[0]
                continue
            elif k in CH_NEXT:
                if index + count < len(contents) - 1:
                    return count, width, 0, None
                if index + count >= len(contents) - 1:
                    return len(contents) - index - 1, width, 0, None
            elif k in CH_PREV:
                if index - count > 0:
                   return -count, width, 0, None
                elif index - count <= 0:
                   return -index, width, 0, None
            elif k in CH_HOME:
                y = 0
            elif k in CH_END:
                y = pgend(totlines, rows)
            elif k in TOC:
                fllwd = toc(stdscr, toc_src, index)
                if fllwd is not None:
                    if fllwd in {curses.KEY_RESIZE}|HELP|META:
                        k = fllwd
                        continue
                    return fllwd - index, width, 0, None
            elif k in META:
                k = meta(stdscr, ebook)
                if k in {curses.KEY_RESIZE}|HELP|TOC:
                    continue
            elif k in HELP:
                k = help(stdscr)
                if k in {curses.KEY_RESIZE}|META|TOC:
                    continue
            elif k == WIDEN and (width + count) < cols - 4:
                width += count
                return 0, width, 0, y/totlines
            elif k == SHRINK:
                width -= count
                if width < 20:
                    width = 20
                return 0, width, 0, y/totlines
            elif k == WIDTH:
                if countstring == "":
                    # if called without a count, toggle between responsive width and full width
                    margin = 8
                    max_width = 100
                    responsive_width = min(max_width, max(20, cols - margin))
                    
                    if width != responsive_width and cols - 4 >= responsive_width:
                        return 0, responsive_width, 0, y/totlines
                    else:
                        return 0, cols - 4, 0, y/totlines
                else:
                    width = count
                if width < 20:
                    width = 20
                elif width >= cols - 4:
                    width = cols - 4
                return 0, width, 0, y/totlines
            # elif k == ord("0"):
            #     if width != 80 and cols - 2 >= 80:
            #         return 0, 80, 0, y/totlines
            #     else:
            #         return 0, cols - 2, 0, y/totlines
            elif k == ord("/"):
                ks, idxs = searching(stdscr, pad, src_lines, width, y, index, len(contents))
                if ks in {curses.KEY_RESIZE, ord("/")}:
                    k = ks
                    continue
                elif SEARCHPATTERN is not None:
                    return idxs, width, 0, None
                elif idxs is not None:
                    y = idxs
            elif k == ord("o") and VWR is not None:
                gambar, idx = [], []
                for n, i in enumerate(src_lines[y:y+rows]):
                    img = re.search(r"(?<=\[IMG:)[0-9]+(?=\])", i)
                    if img is not None:
                        gambar.append(img.group())
                        idx.append(n)

                impath = ""
                if len(gambar) == 1:
                    impath = imgs[int(gambar[0])]
                elif len(gambar) > 1:
                    p, i = 0, 0
                    while p not in QUIT and p not in FOLLOW:
                        stdscr.move(idx[i], x + width//2 + len(gambar[i]) + 1)
                        stdscr.refresh()
                        curses.curs_set(1)
                        p = pad.getch()
                        if p in SCROLL_DOWN:
                            i += 1
                        elif p in SCROLL_UP:
                            i -= 1
                        i = i % len(gambar)

                    curses.curs_set(0)
                    if p in FOLLOW:
                        impath = imgs[int(gambar[i])]

                if impath != "":
                    imgsrc = dots_path(chpath, impath)
                    k = open_media(pad, ebook, imgsrc)
                    continue
            elif k == MARKPOS:
                jumnum = pad.getch()
                if jumnum in range(49, 58):
                    JUMPLIST[chr(jumnum)] = [index, width, y, y/totlines]
                else:
                    k = jumnum
                    continue
            elif k == JUMPTOPOS:
                jumnum = pad.getch()
                if jumnum in range(49, 58) and chr(jumnum) in JUMPLIST.keys():
                    tojumpidxdiff = JUMPLIST[chr(jumnum)][0]-index
                    tojumpy = JUMPLIST[chr(jumnum)][2]
                    tojumpctg = None if JUMPLIST[chr(jumnum)][1] == width else JUMPLIST[chr(jumnum)][3]
                    return tojumpidxdiff, width, tojumpy, tojumpctg
                else:
                    k = jumnum
                    continue
            elif k == COLORSWITCH and COLORSUPPORT and countstring in {"", "0", "1", "2"}:
                if countstring == "":
                    count_color = curses.pair_number(stdscr.getbkgd())
                    if count_color not in {2, 3}: count_color = 1
                    count_color = count_color % 3
                else:
                    count_color = count
                stdscr.bkgd(curses.color_pair(count_color+1))
                return 0, width, y, None
            elif k == curses.KEY_RESIZE:
                savestate(ebook.path, index, width, y, y/totlines)
                # stated in pypi windows-curses page:
                # to call resize_term right after KEY_RESIZE
                if sys.platform == "win32":
                    curses.resize_term(rows, cols)
                    rows, cols = stdscr.getmaxyx()
                else:
                    rows, cols = stdscr.getmaxyx()
                    curses.resize_term(rows, cols)
                if cols < 22 or rows < 12:
                    sys.exit("ERR: Screen was too small (min 22cols x 12rows).")
                if cols <= width + 4:
                    return 0, cols - 4, 0, y/totlines
                else:
                    return 0, width, y, None
            countstring = ""

        if svline != "dontsave":
            pad.chgat(svline, 0, width, curses.A_UNDERLINE)
        try:
            stdscr.clear()
            stdscr.addstr(0, 0, countstring)
            stdscr.refresh()
            if totlines - y < rows:
                pad.refresh(y,0, 0,x, totlines-y,x+width)
            else:
                pad.refresh(y,0, 0,x, rows-1,x+width)
        except curses.error:
            pass
        k = pad.getch()

        if svline != "dontsave":
            pad.chgat(svline, 0, width, curses.A_NORMAL)
            svline = "dontsave"


def preread(stdscr, file):
    global COLORSUPPORT

    curses.start_color()  # Enable color support
    curses.use_default_colors()
    try:
        curses.init_pair(1, -1, -1)
        curses.init_pair(2, DARK[0], DARK[1])
        curses.init_pair(3, LIGHT[0], LIGHT[1])
        COLORSUPPORT = True
        
        # Initialize smart color palette for image rendering
        init_smart_color_palette()
        global _next_color_pair
        _next_color_pair = 4  # Start after the pre-defined pairs
        
        # Pre-allocate syntax highlighting color pairs
        init_syntax_color_pairs()
        
    except:
        COLORSUPPORT  = False

    stdscr.keypad(True)
    curses.curs_set(0)
    stdscr.clear()
    rows, cols = stdscr.getmaxyx()
    stdscr.refresh()

    epub = Epub(file)

    # Calculate responsive width based on terminal size
    # Leave margin of 8 characters (4 on each side), cap at 100 columns
    margin = 8
    max_width = 100
    responsive_width = min(max_width, max(20, cols - margin))

    if epub.path in STATE:
        idx = int(STATE[epub.path]["index"])
        saved_width = int(STATE[epub.path]["width"])
        
        # Prefer responsive width, but use saved width if:
        # 1. It's within 10 columns of responsive width (user hasn't significantly customized)
        # 2. It's larger than responsive width (user prefers wider text)
        # 3. Terminal is too small for responsive width
        if (abs(saved_width - responsive_width) <= 10 or 
            saved_width > responsive_width or 
            responsive_width > cols - 4):
            width = saved_width if saved_width <= cols - 4 else responsive_width
        else:
            width = responsive_width
            
        y = int(STATE[epub.path]["pos"])
        pctg = None
    else:
        STATE[epub.path] = {}
        idx = 0
        y = 0
        width = responsive_width
        pctg = None

    # Final adjustment if width is still too large for terminal
    if cols <= width + 4:
        width = cols - 4
        if "pctg" in STATE[epub.path]:
            pctg = float(STATE[epub.path]["pctg"])

    epub.initialize()
    find_media_viewer()

    while True:
        incr, width, y, pctg = reader(stdscr, epub, idx, width, y, pctg)
        
        # Show loading animation for chapter transitions
        if incr != 0:  # Chapter navigation occurred
            import time
            
            # Start loading animation with spectrum effect
            animation_data = show_loading_animation(stdscr, "Loading chapter...")
            message, start_col, center_row, spectrum_colors = animation_data
            
            # Animate rolling spectrum effect while changing chapter
            animation_step = 0
            for _ in range(12):  # Show animation for a brief moment  
                update_loading_animation(stdscr, message, start_col, center_row, spectrum_colors, animation_step)
                animation_step += 1
                time.sleep(0.08)  # Slightly longer delay for better color visibility
        
        idx += incr


def main():
    
    termc, termr = shutil.get_terminal_size()

    args = []
    if sys.argv[1:] != []:
        args += sys.argv[1:]

    if len({"-h", "--help"} & set(args)) != 0:
        hlp = __doc__.rstrip()
        if "-h" in args:
            hlp = re.search("(\n|.)*(?=\n\nKey)", hlp).group()
        print(hlp)
        sys.exit()

    if len({"-v", "--version", "-V"} & set(args)) != 0:
        print(__version__)
        print(__license__, "License")
        print("Copyright (c) 2019", __author__)
        print(__url__)
        sys.exit()

    if len({"-d"} & set(args)) != 0:
        args.remove("-d")
        dump = True
    else:
        dump = False

    loadstate()

    if args == []:
        file, todel = False, []
        for i in STATE:
            if not os.path.exists(i):
                todel.append(i)
            elif STATE[i]["lastread"] == str(1):
                file = i

        for i in todel:
            del STATE[i]

        if not file:
            print(__doc__)
            sys.exit("ERROR: Found no last read file.")

    elif os.path.isfile(args[0]):
        file = args[0]

    else:
        val = cand = 0
        todel = []
        for i in STATE.keys():
            if not os.path.exists(i):
                todel.append(i)
            else:
                match_val = sum([j.size for j in SM(None, i.lower(), " ".join(args).lower()).get_matching_blocks()])
                if match_val >= val:
                    val = match_val
                    cand = i
        for i in todel:
            del STATE[i]
        with open(STATEFILE, "w") as f:
            json.dump(STATE, f, indent=4)
        if len(args) == 1 and re.match(r"[0-9]+", args[0]) is not None:
            try:
                cand = list(STATE.keys())[int(args[0])-1]
                val = 1
            except IndexError:
                val = 0
        if val != 0 and len({"-r"} & set(args)) == 0:
            file = cand
        else:
            print("Reading history:")
            dig = len(str(len(STATE.keys())+1))
            for n, i in enumerate(STATE.keys()):
                print(str(n+1).rjust(dig) + ("* " if STATE[i]["lastread"] == "1" else "  ") + i)
            if len({"-r"} & set(args)) != 0:
                sys.exit()
            else:
                print()
                sys.exit("ERROR: Found no matching history.")

    if dump:
        epub = Epub(file)
        epub.initialize()
        for i in epub.contents:
            content = epub.file.open(i).read()
            content = content.decode("utf-8")
            parser = HTMLtoLines()
            try:
                parser.feed(content)
                parser.close()
            except:
                pass
            src_lines, imgs = parser.get_lines()
            # sys.stdout.reconfigure(encoding="utf-8")  # Python>=3.7
            for j in src_lines:
                sys.stdout.buffer.write((j+"\n\n").encode("utf-8"))
        sys.exit()

    else:
        if termc < 22 or termr < 12:
            sys.exit("ERR: Screen was too small (min 22cols x 12rows).")
        curses.wrapper(preread, file)


if __name__ == "__main__":
    main()
