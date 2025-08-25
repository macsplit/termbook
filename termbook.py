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
    --clean         reset to fresh state (delete all bookmarks)
    --debug         show debug info (chapter, position, build time)

Key Binding:
    Help             : ?
    Quit             : q
    Scroll down      : DOWN
    Scroll up        : UP
    Page down        : PGDN      RIGHT   SPC
    Page up          : PGUP      LEFT
    Next chapter     : n
    Prev chapter     : p
    Beginning of ch  : HOME
    End of ch        : END
    Open image       : i
    Open URL         : u
    Search           : /
    Next Occurrence  : n
    Prev Occurrence  : p
    ToC              : TAB       t
    Metadata         : m
    Save bookmark    : s
    Bookmarks        : b
    Switch colorsch  : c
"""


__version__ = "1.1.1"
__build_time__ = "2025-08-25 21:03:21"
__license__ = "MIT"
__author__ = "Lee Hanken (based on epr by Benawi Adha)"
__email__ = ""
__url__ = "https://github.com/macsplit/termbook"


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
import signal
import time
import threading
import atexit
import webbrowser
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
SCROLL_UP = {curses.KEY_UP}
PAGE_DOWN = {curses.KEY_NPAGE, ord("l"), ord(" "), curses.KEY_RIGHT}
PAGE_UP = {curses.KEY_PPAGE, ord("h"), curses.KEY_LEFT}
CH_NEXT = {ord("n")}
CH_PREV = {ord("p")}
CH_HOME = {curses.KEY_HOME}
CH_END = {curses.KEY_END}
META = {ord("m")}
TOC = {9, ord("\t"), ord("t")}
FOLLOW = {10}
QUIT = {ord("q"), 3, 304}
HELP = {ord("?")}
BOOKMARKS = ord("b")
SAVE_BOOKMARK = ord("s")
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
CURRENT_SEARCH_TERM = None  # Store current search term for highlighting
WHOLE_BOOK_SEARCH_START = None  # Track starting chapter for whole-book search
WHOLE_BOOK_SEARCH_VISITED = []  # Track visited chapters during whole-book search
VWR = None
DEBUG_MODE = False  # Global debug flag
BOOKMARKSFILE = ""  # Global bookmarks file path
GLOBAL_BOOKMARKS = []  # List of global bookmarks
INITIAL_HELP_SHOWN = False  # Track if initial help message has been shown and dismissed

# Terminal resize handling
RESIZE_REQUESTED = False
RESIZE_TIMER = None
RESIZE_DELAY = 1.0  # Wait 1 second after last resize before re-rendering
LAST_TERMINAL_SIZE = (0, 0)  # Track last known terminal size

# Chapter loading animation
LOADING_IN_PROGRESS = False  # Track if chapter loading animation is active

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
        
        # Strip "Listing X.X" prefix if present for better language detection
        cleaned_code = code_text
        if re.match(r'^Listing\s+\d+\.?\d*\s+', code_text, re.IGNORECASE):
            # Remove the "Listing X.X Title" line for better language detection
            lines = code_text.split('\n')
            if len(lines) > 1:
                cleaned_code = '\n'.join(lines[1:])
        
        # Use heuristics first for common patterns (more reliable than guess_lexer)
        code_lower = cleaned_code.lower().strip()
        
        # Java heuristics - enhanced detection
        java_keywords = ['public class', 'private class', 'protected class', 
                         'public interface', 'private interface',
                         'public static', 'private static', 'protected static',
                         'public final', 'private final', 
                         'public synchronized', 'private synchronized',
                         'system.out.print', 'public void', 'private void',
                         'import java.', 'package ', '@override', '@autowired',
                         'new ', 'extends ', 'implements ', 'throws ',
                         'public enum', 'private enum']
        
        # Also check for common Java patterns (getter/setter, types)
        java_patterns = ['getid()', 'setid(', 'getname()', 'setname(',
                        'string ', 'integer ', 'boolean ', 'double ', 'float ',
                        'final ', 'static final', 'return id;', 'return name;',
                        'this.', '.equals(', '.hashcode(', '.tostring(']
            
        if any(keyword in code_lower for keyword in java_keywords) or \
           any(pattern in code_lower for pattern in java_patterns):
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
        
        # TypeScript heuristics (check before JavaScript since TS is superset)
        elif ('interface ' in code_lower or 'type ' in code_lower or 
              ': string' in code_lower or ': number' in code_lower or ': boolean' in code_lower or
              'public ' in code_lower or 'private ' in code_lower or 'protected ' in code_lower or
              ': void' in code_lower or 'readonly ' in code_lower or
              '?' in code_text or # Optional properties like email?: string
              ('class ' in code_lower and ('public' in code_lower or 'private' in code_lower))):
            try:
                return get_lexer_by_name('typescript')
            except ClassNotFound:
                # Fall back to JavaScript if TypeScript lexer not available
                try:
                    return get_lexer_by_name('javascript')
                except ClassNotFound:
                    pass
        
        # JavaScript heuristics
        elif ('function ' in code_lower or 'console.log' in code_lower or 'var ' in code_lower or 'let ' in code_lower or 'const ' in code_lower):
            try:
                return get_lexer_by_name('javascript')
            except ClassNotFound:
                pass
        
        # XML heuristics - prioritize XML detection
        elif ('<?xml' in code_lower or '<!doctype' in code_lower or 
              ('<!entity' in code_lower and '&' in code_lower and ';' in code_lower) or
              (code_lower.count('<') >= 2 and code_lower.count('>') >= 2)):
            try:
                return get_lexer_by_name('xml')
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
        
        # If heuristics didn't match, try guess_lexer as fallback
        try:
            lexer = guess_lexer(cleaned_code)
            # If guess_lexer returns TextLexer, reject it
            if lexer.__class__.__name__ == 'TextLexer':
                return TextLexer()
            return lexer
        except ClassNotFound:
            # Default to TextLexer if nothing matched
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
            
        except Exception:
            # Fall back to theme-appropriate colors if highlighting fails
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
            'Keyword': ((0, 150, 255), (0, 50, 200)),           # Blue: bright for dark, dark for light
            'Keyword.Constant': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Declaration': ((0, 150, 255), (0, 50, 200)), 
            'Keyword.Namespace': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Pseudo': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Reserved': ((0, 150, 255), (0, 50, 200)),
            'Keyword.Type': ((100, 200, 255), (0, 100, 180)),    # Type keywords
            
            'Name.Class': ((255, 255, 0), (180, 140, 0)),        # Yellow: bright for dark, dark gold for light
            'Name.Function': ((255, 255, 0), (180, 140, 0)),     # Functions
            'Name.Builtin': ((255, 100, 255), (150, 0, 150)),    # Magenta: bright for dark, dark purple for light
            'Name.Exception': ((255, 100, 0), (200, 50, 0)),     # Orange: bright for dark, dark orange for light
            
            'Literal.String': ((0, 255, 0), (0, 140, 0)),        # Green: bright for dark, dark green for light
            'Literal.String.Double': ((0, 255, 0), (0, 140, 0)),
            'Literal.String.Single': ((0, 255, 0), (0, 140, 0)),
            'Literal.Number': ((255, 165, 0), (180, 90, 0)),     # Orange numbers
            'Literal.Number.Integer': ((255, 165, 0), (180, 90, 0)),
            'Literal.Number.Float': ((255, 165, 0), (180, 90, 0)),
            
            'Comment': ((128, 128, 128), (100, 100, 100)),         # Gray for comments
            'Comment.Single': ((128, 128, 128), (100, 100, 100)),
            'Comment.Multiline': ((128, 128, 128), (100, 100, 100)),
            'Comment.Preproc': ((255, 255, 255), (50, 50, 50)),  # White/dark for preprocessor
            
            'Operator': ((255, 100, 255), (150, 0, 150)),       # Magenta for operators like =, +, -
            'Operator.Word': ((255, 100, 255), (150, 0, 150)),   # instanceof, typeof, etc.
            'Punctuation': ((255, 255, 0), (180, 140, 0)),      # Yellow for punctuation like {}, (), ;
            'Punctuation.Bracket': ((255, 255, 0), (180, 140, 0)), # Brackets specifically
            
            # XML-specific token types
            'Name.Tag': ((0, 150, 255), (0, 50, 200)),           # Blue for XML tags
            'Name.Attribute': ((255, 255, 0), (180, 140, 0)),     # Yellow for XML attributes  
            'Literal.String.Doc': ((0, 255, 0), (0, 140, 0)),   # Green for XML content
            'Generic.Emph': ((255, 255, 255), (50, 50, 50)),     # White/dark for emphasized
            'Generic.Strong': ((255, 255, 255), (50, 50, 50)),   # White/dark for strong
            'Text': ((255, 255, 255), (50, 50, 50)),             # White/dark for plain text
            'Text.Whitespace': ((255, 255, 255), (50, 50, 50)),  # White/dark for whitespace
            
            # TypeScript/JavaScript-specific tokens
            'Name.Other': ((0, 255, 255), (0, 150, 150)),        # Variable names and identifiers (cyan)
            'Name.Variable': ((0, 255, 255), (0, 150, 150)),     # Variable names (cyan)
            'Name.Property': ((255, 255, 0), (180, 140, 0)),     # Object properties (yellow)
            'Name.Constant': ((255, 165, 0), (180, 90, 0)),      # Constants
            'Name.Builtin.Pseudo': ((255, 100, 255), (150, 0, 150)), # this, super, etc.
            'Keyword.Declaration': ((0, 150, 255), (0, 50, 200)), # let, const, var, interface
            'Keyword.Reserved': ((0, 150, 255), (0, 50, 200)),   # Reserved keywords
            'Operator.Word': ((255, 100, 255), (150, 0, 150)),   # instanceof, typeof, etc.
            'Name.Decorator': ((255, 100, 255), (150, 0, 150)),  # Decorators (@override, etc.)
            'Literal.String.Backtick': ((0, 255, 0), (0, 140, 0)), # Template literals
            'Literal.String.Interpol': ((255, 255, 0), (180, 140, 0)), # String interpolation
            'Literal.Number.Bin': ((255, 165, 0), (180, 90, 0)), # Binary numbers
            'Literal.Number.Hex': ((255, 165, 0), (180, 90, 0)), # Hexadecimal numbers
            'Literal.Number.Oct': ((255, 165, 0), (180, 90, 0)), # Octal numbers
            
            # Special tokens
            'Error': ((255, 0, 0), (200, 0, 0)),                 # Error tokens (bright red)
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
        for pattern, color in token_colors_dual.items():
            if token_str.startswith(pattern):
                return color
        
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


def show_initial_help_message(stdscr, rows, cols):
    """Show initial help message at bottom of screen on startup - matches URL/images hint styling."""
    message = " ? for help "
    message_len = len(message)
    
    # Position at bottom center
    start_col = max(0, (cols - message_len) // 2)
    
    # Protect against resize/dimension issues - use same logic as show_persistent_hint
    try:
        # Verify we have valid dimensions before attempting to draw
        if rows <= 0 or cols <= 0 or start_col >= cols or start_col + message_len > cols:
            return
        
        # Determine current color scheme - same logic as show_persistent_hint
        current_bg_pair = curses.pair_number(stdscr.getbkgd())
        is_light_scheme = current_bg_pair == 3  # Light scheme is color pair 3
        
        # Show hint with appropriate colors - same as URL/images hints
        if COLORSUPPORT:
            try:
                if is_light_scheme:
                    # Light scheme: white text on darker background for better contrast
                    hint_pair = get_color_pair((255, 255, 255), (100, 100, 100))  # White on dark gray
                else:
                    # Dark scheme: light text on darker background  
                    hint_pair = get_color_pair((255, 255, 255), (64, 64, 64))  # White on dark gray
                    
                if hint_pair > 0:
                    stdscr.addstr(rows - 1, start_col, message, curses.color_pair(hint_pair))
                else:
                    # Fallback to reverse video
                    stdscr.addstr(rows - 1, start_col, message, curses.A_REVERSE)
            except:
                # Fallback to reverse video
                stdscr.addstr(rows - 1, start_col, message, curses.A_REVERSE)
        else:
            # No color support, use reverse video
            stdscr.addstr(rows - 1, start_col, message, curses.A_REVERSE)
    except:
        # Silently fail if screen dimensions are unstable during resize
        pass

def show_persistent_hint(stdscr, rows, cols, has_urls, has_images):
    """Show persistent hint at bottom of screen for URLs and/or images."""
    # Build message based on what's available
    if has_urls and has_images:
        message = " Press 'u' for URLs | Press 'i' for images "
    elif has_urls:
        message = " Press 'u' to access URLs "
    elif has_images:
        message = " Press 'i' to access images "
    else:
        return  # Nothing to show
    
    message_len = len(message)
    
    # Position at bottom center
    start_col = max(0, (cols - message_len) // 2)
    
    # Protect against resize/dimension issues - try to show hint but don't crash if screen is unstable
    try:
        # Verify we have valid dimensions before attempting to draw
        if rows <= 0 or cols <= 0 or start_col >= cols or start_col + message_len > cols:
            return
        
        # Determine current color scheme
        current_bg_pair = curses.pair_number(stdscr.getbkgd())
        is_light_scheme = current_bg_pair == 3  # Light scheme is color pair 3
        
        # Show hint with appropriate colors
        if COLORSUPPORT:
            if is_light_scheme:
                # Light scheme: use color pair 3 (designed for light backgrounds)
                stdscr.addstr(rows - 1, start_col, message, curses.color_pair(3))
            else:
                # Dark scheme: use default color pair 1 or 2
                stdscr.addstr(rows - 1, start_col, message, curses.color_pair(2))
        else:
            # No color support, use reverse video
            stdscr.addstr(rows - 1, start_col, message, curses.A_REVERSE)
    except:
        # Silently fail if screen dimensions are unstable during resize
        pass

def find_urls_in_text(text):
    """Find all URLs in text using whitelist-based pattern.
    Returns list of (url, start_pos, end_pos) tuples."""
    import re
    
    # Whitelist-based URL pattern: protocol + domain + optional port + optional path + optional query string
    # Includes query strings but excludes fragments
    # Path and query must end with alphanumeric or specific safe characters, not punctuation
    url_pattern = r'https?://[a-zA-Z0-9.-]+(?::[0-9]+)?(?:/[a-zA-Z0-9._/-]*)?(?:\?[a-zA-Z0-9._/\-~&=+%]*[a-zA-Z0-9_/\-~&=+%])?'
    
    urls = []
    for match in re.finditer(url_pattern, text):
        urls.append((match.group(), match.start(), match.end()))
    return urls

def check_urls_in_visible_area(src_lines, y, rows):
    """Check if there are URLs in the currently visible area."""
    # Check visible lines for URLs
    for line in src_lines[y:y+rows]:
        if find_urls_in_text(line):
            return True
    return False

def check_images_in_visible_area(src_lines, y, rows):
    """Check if there are images in the currently visible area."""
    import re
    
    # Check visible lines for images
    for line in src_lines[y:y+rows]:
        # Check for both unreplaced markers and rendered image lines
        if line.startswith("IMG_LINE:") or re.search(r'\[IMG:\d+\]', line):
            return True
    return False

def extract_figure_number(text):
    """Extract figure number from text like 'Figure 1.2', 'Fig 3', etc."""
    if not text:
        return None
    
    import re
    
    # Patterns for figure references - include hyphens and dots
    patterns = [
        r'(?:Figure|Fig\.?)\s+(\d+(?:[.\-]\d+)*)',  # Figure 1, Fig. 2.3, Fig 3-6, etc.
        r'(?:Listing|List\.?)\s+(\d+(?:[.\-]\d+)*)',  # Listing 1, List. 2.3, List 3-6, etc.  
        r'(?:Table|Tab\.?)\s+(\d+(?:[.\-]\d+)*)',   # Table 1, Tab. 2.3, Table 3-6, etc.
        r'(?:Diagram|Chart|Graph|Illustration)\s+(\d+(?:[.\-]\d+)*)',  # Other figure types
        r'(\d+(?:[.\-]\d+)*)\s*[:-]\s*',  # Leading number like "1.2: Title", "3-6: Title", etc.
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return None

def get_enhanced_image_label(img_path, img_idx, img_alts, src_lines, img_line_num=None):
    """Get enhanced label for image including figure number if available."""
    import re
    import os
    
    figure_number = None
    base_label = None
    
    # First search nearby lines for HTML captions (more reliable than alt text)
    if img_line_num is not None:
        # Search a larger range, especially after the image where captions often appear
        search_before = 10  # Check 10 lines before image  
        search_after = 20   # Check 20 lines after image (captions often come after)
        start = max(0, img_line_num - search_before)
        end = min(len(src_lines), img_line_num + search_after)
        
        # Look for captions first (more reliable) 
        for i in range(start, end):
            if i < len(src_lines):
                line = src_lines[i]
                
                # DEBUG: Log search process
                try:
                    with open('/tmp/termbook_debug.log', 'a') as f:
                        if i == img_line_num:
                            f.write(f"  Searching around IMG line {i}: '{line[:60]}...'\n")
                        elif 'Figure 1.7' in line:
                            f.write(f"  *** Found 'Figure 1.7' at line {i}: '{line[:60]}...'\n")
                except:
                    pass
                
                # Handle explicit CAPTION: prefix
                if line.startswith("CAPTION:"):
                    caption_text = line[8:]  # Remove "CAPTION:" prefix
                    figure_number = extract_figure_number(caption_text)
                    if figure_number:
                        base_label = caption_text.strip()
                        break
                
                # Also check for HTML figure captions (h5, h6, figcaption, etc.)
                import re
                if re.search(r'<(?:h[456]|figcaption)[^>]*>', line, re.IGNORECASE):
                    # Strip HTML tags to get clean text
                    clean_text = re.sub(r'<[^>]+>', ' ', line).strip()
                    figure_number = extract_figure_number(clean_text)
                    if figure_number:
                        base_label = clean_text
                        break
        
        # If still no figure number, check regular lines but prioritize lines closer to the image
        if not figure_number:
            # Check lines in order of proximity to image, preferring lines after the image
            distances = []
            for i in range(start, end):
                if i < len(src_lines):
                    distance = abs(i - img_line_num)
                    # Add slight preference for lines after the image (captions usually come after)
                    if i > img_line_num:
                        distance -= 0.5  # Make "after" lines slightly closer
                    distances.append((distance, i))
            distances.sort()  # Sort by distance from image (with after-image preference)
            
            for _, i in distances:
                line = src_lines[i]
                if not line.startswith("CAPTION:"):  # Skip captions (already checked)
                    figure_number = extract_figure_number(line)
                    if figure_number:
                        base_label = line.strip()
                        break
    
    # If still no figure number, try alt text as fallback
    if not figure_number and img_idx < len(img_alts) and img_alts[img_idx]:
        alt_text = img_alts[img_idx]
        figure_number = extract_figure_number(alt_text)
        if not base_label:  # Only use alt text if we don't have a better label
            base_label = alt_text
    
    # Final fallback: try to extract figure number from filename
    if not figure_number:
        filename = os.path.basename(img_path)
        figure_number = extract_figure_number(filename)
        if figure_number and not base_label:
            base_label = filename
    
    # Debug output for troubleshooting
    if os.getenv('TERMBOOK_DEBUG_FIGURES'):
        import sys
        print(f"DEBUG: Image {img_idx} ({os.path.basename(img_path)}) -> Figure: '{figure_number}' from '{base_label}' at line {img_line_num}", file=sys.stderr)
        if img_line_num is not None and img_line_num < len(src_lines):
            print(f'DEBUG: Context around line {img_line_num}:', file=sys.stderr)
            start = max(0, img_line_num - 2)
            end = min(len(src_lines), img_line_num + 8)
            for i in range(start, end):
                marker = '>>> ' if i == img_line_num else '    '
                line_content = src_lines[i][:80] + '...' if len(src_lines[i]) > 80 else src_lines[i]
                print(f'{marker}{i:3}: {line_content}', file=sys.stderr)
    
    # Fallback to filename
    if not base_label:
        base_label = os.path.basename(img_path)
    
    # Create enhanced label
    if figure_number:
        # Clean up the base label for display
        if base_label.startswith("CAPTION:"):
            base_label = base_label[8:].strip()
        
        # Try to get a short descriptive part after the figure number
        clean_label = re.sub(r'^(?:Figure|Fig\.?|Listing|List\.?|Table|Tab\.?|Diagram|Chart|Graph|Illustration)\s+\d+(?:[.\-]\d+)*\s*[:-]?\s*', '', base_label, flags=re.IGNORECASE)
        
        if clean_label and clean_label != base_label and len(clean_label.strip()) > 3:
            # Use figure number + shortened description
            short_desc = clean_label.strip()[:40]  # Increased limit for better context
            if len(clean_label.strip()) > 40:
                short_desc += "..."
            return f"Figure {figure_number}: {short_desc}"
        else:
            # Just figure number
            return f"Figure {figure_number}"
    
    # No figure number found, use original label logic
    if base_label and base_label != os.path.basename(img_path):
        # Use alt text, with more generous truncation
        if len(base_label) > 60:
            # Try to preserve important parts like figure numbers at the end
            if re.search(r'(?:Figure|Fig\.?|Table|Tab\.?)\s+\d+(?:[.\-]\d+)*', base_label, re.IGNORECASE):
                # If it contains figure/table numbers, be more generous with length
                return base_label[:80] + ("..." if len(base_label) > 80 else "")
            else:
                return base_label[:60] + ("..." if len(base_label) > 60 else "")
        else:
            return base_label
    else:
        # Use filename
        return os.path.basename(img_path)

def get_visible_images(src_lines, imgs, y, rows, image_line_map=None):
    """Get images that are visible or overlapping with the current viewport.
    Uses precise image line mapping if available."""
    import re
    
    # DEBUG: Log viewport parameters
    try:
        with open('/tmp/termbook_debug.log', 'a') as f:
            f.write(f"get_visible_images called with y={y}, rows={rows}, total_lines={len(src_lines)}\n")
    except:
        pass
    
    if not imgs:
        return []
    
    visible_images = []
    seen_indices = set()
    
    # Define viewport with small overlap 
    viewport_start = max(0, y - 2)
    viewport_end = min(len(src_lines), y + rows + 2)
    
    # DEBUG: Log viewport range
    try:
        with open('/tmp/termbook_debug.log', 'a') as f:
            f.write(f"  Viewport range: {viewport_start} to {viewport_end}\n")
    except:
        pass
    
    # Use precise image line mapping if available
    if image_line_map and len(image_line_map) == len(src_lines):
        # Scan visible lines and check image mapping
        for line_num in range(viewport_start, viewport_end):
            if line_num < len(image_line_map):
                img_idx = image_line_map[line_num]
                if img_idx is not None and img_idx < len(imgs) and img_idx not in seen_indices:
                    visible_images.append((imgs[img_idx], line_num, img_idx))
                    seen_indices.add(img_idx)
    else:
        # Fallback to old method - scan for image markers
        for line_num in range(viewport_start, viewport_end):
            if line_num >= len(src_lines):
                break
                
            line = src_lines[line_num]
            
            # Check for [IMG:n] markers (unrendered images)
            img_match = re.search(r'\[IMG:(\d+)\]', line)
            if img_match:
                img_idx = int(img_match.group(1))
                if img_idx < len(imgs) and img_idx not in seen_indices:
                    visible_images.append((imgs[img_idx], line_num, img_idx))
                    seen_indices.add(img_idx)
                    # DEBUG: Log found image
                    try:
                        with open('/tmp/termbook_debug.log', 'a') as f:
                            f.write(f"  Found image: {imgs[img_idx]} at line {line_num} (idx {img_idx})\n")
                            f.write(f"    Line content: '{line[:100]}...'\n")
                            f.write(f"    Regex match: '{img_match.group()}'\n")
                    except:
                        pass
            
            # Check for IMG_LINE: markers (rendered images)
            elif line.startswith("IMG_LINE:"):
                # Without mapping, we can't determine which specific image this is
                pass
        
        # If we found IMG_LINE markers but no [IMG:n] markers, return all images
        has_rendered_images = any(
            src_lines[i].startswith("IMG_LINE:") 
            for i in range(viewport_start, viewport_end) 
            if i < len(src_lines)
        )
        
        if has_rendered_images and not visible_images and imgs:
            for img_idx, img_path in enumerate(imgs):
                if img_idx not in seen_indices:
                    visible_images.append((img_path, y, img_idx))
    
    # Sort by line position
    visible_images.sort(key=lambda x: x[1])
    
    return visible_images

def open_image_in_system_viewer(ebook, chpath, img_path):
    """
    Extract image from EPUB and open it in the system's default image viewer
    
    Args:
        ebook: The EPUB file object
        chpath: Chapter path for resolving relative paths
        img_path: Path to the image within the EPUB
        
    Returns:
        bool: True if successful, False if failed
    """
    try:
        # Get correct image path using dots_path
        imgsrc = dots_path(chpath, img_path)
        
        # Extract and save the image to temp file
        img_data = ebook.file.read(imgsrc)
        
        # Determine file extension
        ext = os.path.splitext(img_path)[1] or '.png'
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(img_data)
            tmp_path = tmp.name
        
        # Open with system default viewer
        if os.name == 'posix':
            subprocess.run(['xdg-open', tmp_path], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL,
                         check=False)
        elif os.name == 'nt':
            subprocess.run(['start', tmp_path], shell=True, 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL,
                         check=False)
        else:
            # Fallback for other platforms (macOS, etc.)
            subprocess.run(['open', tmp_path], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL,
                         check=False)
        return True
    except Exception as e:
        return False

def loadstate():
    global STATE, STATEFILE, BOOKMARKSFILE
    if os.getenv("HOME") is not None:
        STATEFILE = os.path.join(os.getenv("HOME"), ".termbook")
        if os.path.isdir(os.path.join(os.getenv("HOME"), ".config")):
            configdir = os.path.join(os.getenv("HOME"), ".config", "termbook")
            os.makedirs(configdir, exist_ok=True)
            if os.path.isfile(STATEFILE):
                if os.path.isfile(os.path.join(configdir, "config")):
                    os.remove(os.path.join(configdir, "config"))
                shutil.move(STATEFILE, os.path.join(configdir, "config"))
            STATEFILE = os.path.join(configdir, "config")
            BOOKMARKSFILE = os.path.join(configdir, "bookmarks.json")
        else:
            BOOKMARKSFILE = os.path.join(os.getenv("HOME"), ".termbook_bookmarks.json")
    elif os.getenv("USERPROFILE") is not None:
        STATEFILE = os.path.join(os.getenv("USERPROFILE"), ".termbook")
        BOOKMARKSFILE = os.path.join(os.getenv("USERPROFILE"), ".termbook_bookmarks.json")
    else:
        STATEFILE = os.devnull
        BOOKMARKSFILE = os.devnull

    if os.path.exists(STATEFILE):
        with open(STATEFILE, "r") as f:
            STATE = json.load(f)
    
    # Load and clean up bookmarks
    load_bookmarks()
    
    # Note: URL hint is now persistent and shown whenever URLs are visible


def savestate(file, index, width, pos, pctg ):
    for i in STATE:
        STATE[i]["lastread"] = str(0)
    STATE[file]["lastread"] = str(1)
    STATE[file]["index"] = str(index)
    STATE[file]["width"] = str(width)
    STATE[file]["pos"] = str(pos)
    STATE[file]["pctg"] = str(pctg)
    
    # Note: URL hint is now persistent and shown whenever URLs are visible
    
    with open(STATEFILE, "w") as f:
        json.dump(STATE, f, indent=4)


def load_bookmarks():
    """Load global bookmarks from file and clean up missing books"""
    global GLOBAL_BOOKMARKS
    GLOBAL_BOOKMARKS = []
    
    if not os.path.exists(BOOKMARKSFILE):
        return
    
    try:
        with open(BOOKMARKSFILE, 'r', encoding='utf-8') as f:
            import json
            data = json.load(f)
            valid_bookmarks = []
            
            for bookmark in data:
                if 'path' in bookmark and os.path.exists(bookmark['path']):
                    valid_bookmarks.append(bookmark)
            
            GLOBAL_BOOKMARKS = valid_bookmarks
            
            # Save cleaned list back if we removed any
            if len(valid_bookmarks) < len(data):
                save_bookmarks()
    except:
        GLOBAL_BOOKMARKS = []

def save_bookmarks():
    """Save global bookmarks to file"""
    try:
        os.makedirs(os.path.dirname(BOOKMARKSFILE), exist_ok=True)
        with open(BOOKMARKSFILE, 'w', encoding='utf-8') as f:
            import json
            json.dump(GLOBAL_BOOKMARKS, f, indent=2, ensure_ascii=False)
    except:
        pass

# =============================================================================
# UNIFIED MODAL SYSTEM
# =============================================================================

class Modal:
    """Unified modal system for all dialogs"""
    _active_modal = None
    
    @classmethod
    def is_active(cls):
        return cls._active_modal is not None
    
    @classmethod
    def set_active(cls, modal_name):
        cls._active_modal = modal_name
    
    @classmethod
    def clear_active(cls):
        cls._active_modal = None
    
    @classmethod
    def handle_resize(cls):
        """Clear any active modal on resize and return to main reader"""
        if cls._active_modal:
            cls._active_modal = None
            return True
        return False
    
    @staticmethod
    def create_dialog(stdscr, width, height, title=""):
        """Create a centered dialog window"""
        rows, cols = stdscr.getmaxyx()
        start_y = (rows - height) // 2
        start_x = (cols - width) // 2
        
        dialog = curses.newwin(height, width, start_y, start_x)
        dialog.box()
        if title:
            dialog.addstr(0, 2, title)
        dialog.keypad(True)
        return dialog
    
    @staticmethod
    def get_immediate_key(dialog):
        """Get key input with immediate 'q' handling - no waiting for sequences"""
        # Set nodelay mode to avoid blocking on escape sequences
        dialog.nodelay(True)
        try:
            key = dialog.getch()
            if key == -1:  # No key pressed
                dialog.nodelay(False)
                key = dialog.getch()  # Wait for actual key
                
            # If we got 'q', return immediately without waiting for sequences
            if key == ord('q'):
                return key
                
            dialog.nodelay(False)
            return key
        except:
            dialog.nodelay(False)
            return dialog.getch()
    
    @staticmethod
    def destroy_dialog(stdscr, dialog):
        """Completely destroy dialog and refresh screen"""
        dialog.clear()
        dialog.refresh()
        del dialog
        stdscr.clear()
        stdscr.refresh()
        Modal.clear_active()
    
    @staticmethod
    def input_dialog(stdscr, width, height, title, prompt, max_length=50):
        """Generic input dialog - only 'q' and Enter are commands"""
        if Modal.is_active():
            return None
        
        Modal.set_active(f"input_{title}")
        
        dialog = Modal.create_dialog(stdscr, width, height, title)
        dialog.addstr(1, 2, prompt)
        dialog.refresh()
        
        curses.curs_set(1)
        curses.noecho()
        
        input_text = ""
        prompt_len = len(prompt) + 2
        
        while True:
            dialog.move(1, prompt_len + len(input_text))
            dialog.refresh()
            
            key = Modal.get_immediate_key(dialog)
            
            # ONLY 'q' and Enter are treated as commands
            if key == ord('q'):  # q - cancel
                curses.curs_set(0)
                curses.flushinp()
                Modal.destroy_dialog(stdscr, dialog)
                return None
            elif key in (10, 13):  # Enter - accept
                curses.curs_set(0)
                curses.flushinp()
                Modal.destroy_dialog(stdscr, dialog)
                return input_text if input_text else None
            elif key in (8, 127, curses.KEY_BACKSPACE):  # Backspace
                if input_text:
                    input_text = input_text[:-1]
                    dialog.move(1, prompt_len + len(input_text))
                    dialog.addch(' ')
            elif 32 <= key <= 126 and len(input_text) < max_length:  # All printable chars
                input_text += chr(key)
                dialog.addch(key)
    
    @staticmethod
    def message_dialog(stdscr, width, height, title, message):
        """Simple message dialog - only 'q' to close"""
        if Modal.is_active():
            return None
        
        Modal.set_active(f"message_{title}")
        
        dialog = Modal.create_dialog(stdscr, width, height, title)
        
        # Center and wrap the message if needed
        lines = []
        words = message.split()
        current_line = ""
        for word in words:
            if len(current_line) + len(word) + 1 <= width - 4:
                current_line = current_line + " " + word if current_line else word
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        
        # Display the message centered vertically
        start_y = max(1, (height - len(lines) - 2) // 2)
        for i, line in enumerate(lines):
            centered_line = line.center(width - 4)
            dialog.addstr(start_y + i, 2, centered_line)
        
        # Add help text at bottom
        help_text = "q: Close"
        format_help_text_with_colors(dialog, height - 2, 2, help_text, width - 4)
        
        dialog.refresh()
        
        while True:
            key = Modal.get_immediate_key(dialog)
            if key == ord('q'):  # q - close
                curses.flushinp()
                Modal.destroy_dialog(stdscr, dialog)
                return None
            elif key == curses.KEY_RESIZE:
                curses.flushinp()
                Modal.destroy_dialog(stdscr, dialog)
                return curses.KEY_RESIZE
    
    @staticmethod
    def list_dialog(stdscr, width, height, title, items, current=0, help_text=None):
        """Generic list selection dialog"""
        if Modal.is_active():
            return None
        
        Modal.set_active(f"list_{title}")
        
        while True:
            dialog = Modal.create_dialog(stdscr, width, height, title)
            
            # Display items
            display_height = height - 4
            start_idx = max(0, current - display_height // 2)
            end_idx = min(len(items), start_idx + display_height)
            
            for i in range(start_idx, end_idx):
                y = 2 + (i - start_idx)
                attr = curses.A_REVERSE if i == current else 0
                item_text = str(items[i])[:width-4]
                dialog.addstr(y, 2, item_text, attr)
            
            if help_text is None:
                help_text = "Enter: Select | q: Cancel"
            max_help_width = width - 4
            format_help_text_with_colors(dialog, height-2, 2, help_text, max_help_width)
            dialog.refresh()
            
            key = Modal.get_immediate_key(dialog)
                
            if key == ord('q'):  # q to exit
                Modal.destroy_dialog(stdscr, dialog)
                return None
            elif key in (10, 13):  # Enter
                selected = items[current] if current < len(items) else None
                Modal.destroy_dialog(stdscr, dialog)
                return selected
            elif key == curses.KEY_UP and current > 0:
                current -= 1
            elif key == curses.KEY_DOWN and current < len(items) - 1:
                current += 1
            elif key == curses.KEY_RESIZE:
                Modal.destroy_dialog(stdscr, dialog)
                return curses.KEY_RESIZE

def selection_dialog(stdscr, title, choices, help_text=None):
    """Selection dialog that returns the selected index instead of the item"""
    if Modal.is_active():
        return None
    
    # Calculate dialog size based on content including numbering
    if choices:
        max_choice_len = max(len(f"{i+1}. {str(choice)}") for i, choice in enumerate(choices))
    else:
        max_choice_len = 0
    max_width = max(len(title), max_choice_len) + 8  # +8 for borders and padding
    max_width = max(max_width, 60)  # Minimum width for usability
    max_width = min(max_width, curses.COLS - 4)  # Don't exceed screen
    
    height = min(len(choices) + 6, curses.LINES - 4)  # +6 for title, borders, help
    width = max_width
    
    current = 0
    
    Modal.set_active(f"selection_{title}")
    
    while True:
        dialog = Modal.create_dialog(stdscr, width, height, title)
        
        # Display choices with numbers
        display_height = height - 4
        start_idx = max(0, current - display_height // 2)
        end_idx = min(len(choices), start_idx + display_height)
        
        for i in range(start_idx, end_idx):
            y = 2 + (i - start_idx)
            attr = curses.A_REVERSE if i == current else 0
            choice_text = f"{i+1}. {str(choices[i])}"[:width-6]
            dialog.addstr(y, 2, choice_text, attr)
        
        if help_text is None:
            help_text = "Enter: Select | q: Cancel"
        max_help_width = width - 4
        format_help_text_with_colors(dialog, height-2, 2, help_text, max_help_width)
        dialog.refresh()
        
        key = Modal.get_immediate_key(dialog)
            
        if key == ord('q'):  # q to exit
            Modal.destroy_dialog(stdscr, dialog)
            Modal.set_active(None)
            return None
        elif key in (10, 13):  # Enter
            selected_index = current if current < len(choices) else None
            Modal.destroy_dialog(stdscr, dialog)
            Modal.set_active(None)
            return selected_index
        elif key == curses.KEY_UP and current > 0:
            current -= 1
        elif key == curses.KEY_DOWN and current < len(choices) - 1:
            current += 1
        elif key == curses.KEY_RESIZE:
            Modal.destroy_dialog(stdscr, dialog)
            Modal.set_active(None)
            return curses.KEY_RESIZE

def offer_whole_book_search(stdscr, search_term, ebook, current_index, current_y, width):
    """Ask user if they want to search the whole book"""
    if Modal.is_active():
        return None
    
    Modal.set_active("whole_book_search")
    
    dialog = Modal.create_dialog(stdscr, 60, 5, "")
    message = f"'{search_term}' not found in current chapter."
    prompt = "Search whole book? (y/n): "
    
    dialog.addstr(1, 2, message[:56])  # Truncate if too long
    dialog.addstr(3, 2, prompt)
    dialog.refresh()
    
    while True:
        key = Modal.get_immediate_key(dialog)
        if key in [ord('y'), ord('Y')]:
            # Perform whole-book search
            Modal.destroy_dialog(stdscr, dialog)
            return search_whole_book(stdscr, search_term, ebook, current_index, current_y, width)
        elif key in [ord('n'), ord('N'), ord('q'), 27]:  # n, N, q, or Esc
            Modal.destroy_dialog(stdscr, dialog)
            return None
        elif key == curses.KEY_RESIZE:
            Modal.destroy_dialog(stdscr, dialog)
            return curses.KEY_RESIZE

def apply_search_highlighting(pad, n, x, text, default_attr=0):
    """Apply search highlighting to text if CURRENT_SEARCH_TERM is set"""
    global CURRENT_SEARCH_TERM
    
    # DEBUG: Always log function calls
    try:
        with open('/tmp/search_debug.log', 'a') as f:
            f.write(f"FUNCTION_CALL: apply_search_highlighting called with text='{text[:30]}...', CURRENT_SEARCH_TERM='{CURRENT_SEARCH_TERM}'\n")
    except:
        pass
    
    if not CURRENT_SEARCH_TERM or not text:
        # No search term or no text - just render normally
        try:
            pad.addstr(n, x, text, default_attr)
        except:
            pass
        return
        
    # Apply search highlighting
    import re
    search_pattern = re.escape(CURRENT_SEARCH_TERM)
    last_pos = 0
    
    # DEBUG: Log that we're applying highlighting
    try:
        with open('/tmp/search_debug.log', 'a') as f:
            f.write(f"APPLY_HIGHLIGHT: Checking '{text[:30]}...' for '{CURRENT_SEARCH_TERM}'\n")
    except:
        pass
    
    for match in re.finditer(search_pattern, text, re.IGNORECASE):
        # Add text before match
        if match.start() > last_pos:
            before_text = text[last_pos:match.start()]
            try:
                pad.addstr(n, x + last_pos, before_text, default_attr)
            except:
                pass
        
        # Add highlighted match
        match_text = match.group()
        try:
            if COLORSUPPORT:
                # Create search highlight color
                search_color_pair = _SEARCH_PAIR_START
                try:
                    # Determine color scheme
                    current_bg_pair = curses.pair_number(pad.getbkgd())
                    is_light_scheme = current_bg_pair == 3
                    
                    if is_light_scheme:
                        # Light mode: black text on cyan background
                        curses.init_pair(search_color_pair, curses.COLOR_BLACK, curses.COLOR_CYAN)
                    else:
                        # Dark modes: black text on ultra bright fluorescent yellow background
                        curses.init_pair(search_color_pair, curses.COLOR_BLACK, curses.COLOR_YELLOW)
                    
                    pad.addstr(n, x + match.start(), match_text, curses.color_pair(search_color_pair))
                    
                    # DEBUG: Log successful highlight
                    try:
                        with open('/tmp/search_debug.log', 'a') as f:
                            f.write(f"HIGHLIGHT_SUCCESS: Highlighted '{match_text}' at position {match.start()}\n")
                    except:
                        pass
                except:
                    # Fallback to reverse video
                    pad.addstr(n, x + match.start(), match_text, curses.A_REVERSE | curses.A_BOLD)
            else:
                pad.addstr(n, x + match.start(), match_text, curses.A_REVERSE | curses.A_BOLD)
        except:
            pass
            
        last_pos = match.end()
    
    # Add remaining text after last match
    if last_pos < len(text):
        remaining_text = text[last_pos:]
        try:
            pad.addstr(n, x + last_pos, remaining_text, default_attr)
        except:
            pass

def search_whole_book(stdscr, search_term, ebook, current_index, current_y, width):
    """Search for term across all chapters, navigating chapter by chapter"""
    total_chapters = len(ebook.contents)
    
    # Start searching from the next chapter (wrapping around)
    next_chapter = (current_index + 1) % total_chapters
    
    # Navigate to the next chapter and search there
    # This will cause the reader to restart with the new chapter and search term set
    chapter_offset = next_chapter - current_index
    
    # Show a brief message
    rows, cols = stdscr.getmaxyx()
    stdscr.addstr(rows-1, 0, f" Searching chapter {next_chapter + 1}... ", curses.A_REVERSE)
    stdscr.refresh()
    curses.napms(500)  # Brief pause to show the message
    
    return (chapter_offset, width, 0, None)

def search_dialog(stdscr):
    """Specialized search dialog - Enter to search or exit if blank"""
    if Modal.is_active():
        return None
    
    Modal.set_active("search_input")
    
    dialog = Modal.create_dialog(stdscr, 60, 3, "")
    prompt = "Search: "
    dialog.addstr(1, 2, prompt)
    dialog.refresh()
    
    curses.curs_set(1)
    curses.noecho()
    
    input_text = ""
    prompt_len = len(prompt) + 2
    
    while True:
        dialog.move(1, prompt_len + len(input_text))
        dialog.refresh()
        
        key = Modal.get_immediate_key(dialog)
        
        if key in (10, 13):  # Enter - search if text, exit if blank
            curses.curs_set(0)
            curses.flushinp()
            Modal.destroy_dialog(stdscr, dialog)
            return input_text if input_text else None  # Return text or None to exit
        elif key in (8, 127, curses.KEY_BACKSPACE):  # Backspace
            if input_text:
                input_text = input_text[:-1]
                dialog.move(1, prompt_len + len(input_text))
                dialog.addch(' ')
        elif 32 <= key <= 126 and len(input_text) < 40:  # All printable chars
            input_text += chr(key)
            dialog.addch(chr(key))
        # Note: No 'q' handling - only Enter to search or exit

def add_bookmark(ebook, chapter_index, chapter_title, position, pctg):
    """Add a new global bookmark"""
    global GLOBAL_BOOKMARKS
    import datetime
    
    # Get book title, fallback to filename
    try:
        book_title = ebook.get_meta()[0][1] if ebook.get_meta() else os.path.basename(ebook.path)
        # Clean up title
        book_title = re.sub(r'<[^>]*>', '', book_title).strip()
        if not book_title:
            book_title = os.path.basename(ebook.path)
    except:
        book_title = os.path.basename(ebook.path)
    
    bookmark = {
        'path': ebook.path,
        'book_title': book_title,
        'chapter_index': chapter_index,
        'chapter_title': chapter_title,
        'position': position,
        'percentage': pctg,
        'created': datetime.datetime.now().isoformat()
    }
    
    GLOBAL_BOOKMARKS.append(bookmark)
    save_bookmarks()

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


def handle_terminal_resize():
    """Handle delayed terminal resize - called after resize timer expires"""
    global RESIZE_REQUESTED
    RESIZE_REQUESTED = True


def schedule_resize():
    """Schedule a terminal resize after a delay to avoid rapid re-renders"""
    global RESIZE_TIMER
    
    # Cancel any existing timer
    if RESIZE_TIMER:
        RESIZE_TIMER.cancel()
    
    # Schedule new timer
    RESIZE_TIMER = threading.Timer(RESIZE_DELAY, handle_terminal_resize)
    RESIZE_TIMER.start()


def check_for_resize():
    """Check if a resize has been requested and return True if so"""
    global RESIZE_REQUESTED
    if RESIZE_REQUESTED:
        RESIZE_REQUESTED = False
        return True
    return False


def check_terminal_size_changed(stdscr):
    """Check if terminal size has changed since last check"""
    global LAST_TERMINAL_SIZE
    
    try:
        current_size = stdscr.getmaxyx()
        if LAST_TERMINAL_SIZE == (0, 0):
            # First time - just record the size
            LAST_TERMINAL_SIZE = current_size
            return False
        
        if current_size != LAST_TERMINAL_SIZE:
            # Size changed - schedule delayed resize and update tracking
            LAST_TERMINAL_SIZE = current_size
            schedule_resize()
            return True
            
        return False
    except:
        return False


def cleanup_resize_timer():
    """Cancel any pending resize timer on exit"""
    global RESIZE_TIMER
    if RESIZE_TIMER:
        RESIZE_TIMER.cancel()


# Register cleanup function
atexit.register(cleanup_resize_timer)


def is_page_empty(src_lines, start_y, rows):
    """Check if a page/screen contains any visible content"""
    end_y = min(start_y + rows, len(src_lines))
    
    for i in range(start_y, end_y):
        if i < len(src_lines):
            line = src_lines[i].strip()
            # Skip various prefixed lines that are considered "content"
            if line and not line.startswith(('IMG_LINE:', 'SYNTAX_HL:|', 'HEADER:', 'CAPTION:')):
                # Check if it's just formatting or actually has readable content
                if any(c.isalnum() for c in line):
                    return False
    return True


def skip_empty_pages_forward(src_lines, y, rows, totlines, max_skips=10):
    """Skip forward through empty pages until content is found or limit reached"""
    original_y = y
    skips = 0
    
    while skips < max_skips and y < totlines - rows:
        if is_page_empty(src_lines, y, rows):
            y += rows
            skips += 1
        else:
            break
    
    # If we skipped and found content, return new position
    # If no content found after max_skips, return original position
    return y if skips > 0 and y < totlines - rows else original_y


def skip_empty_pages_backward(src_lines, y, rows, max_skips=10):
    """Skip backward through empty pages until content is found or limit reached"""
    original_y = y
    skips = 0
    
    while skips < max_skips and y > 0:
        if is_page_empty(src_lines, y, rows):
            y = max(0, y - rows)
            skips += 1
        else:
            break
    
    # If we skipped and found content, return new position
    # If no content found after max_skips, return original position
    return y if skips > 0 and y >= 0 else original_y


def bookmarks(stdscr):
    """Display and manage global bookmarks using unified modal system"""
    global GLOBAL_BOOKMARKS
    
    if not GLOBAL_BOOKMARKS:
        # No bookmarks - show simple message dialog
        return Modal.message_dialog(stdscr, 60, 5, "Bookmarks (0 saved)", 
                                   "No bookmarks saved. Press 's' while reading to save.")
    
    # Create list of formatted bookmark strings
    bookmark_items = []
    rows, cols = stdscr.getmaxyx()
    modal_width = min(cols - 4, 100)
    available_width = modal_width - 4  # Account for padding
    
    for i, bookmark in enumerate(GLOBAL_BOOKMARKS):
        # Format timestamp
        timestamp = ""
        if 'created' in bookmark:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(bookmark['created'])
                timestamp = dt.strftime("%m/%d %H:%M")
            except:
                timestamp = ""
        
        # Get position percentage
        position_pct = bookmark.get('percentage', 0.0)
        position_str = f"{position_pct:.0f}%"
        
        # Calculate space for fixed elements and proper spacing
        num_field = f"{i+1:2d}."
        
        # Check if container is narrow (less than 50 chars available)
        if available_width < 50 and timestamp:
            # Narrow container: show only timestamp and position
            display_line = f"{num_field} {timestamp} {position_str:>4s}"
        else:
            # Normal width: show all fields with proper spacing
            # Reserve space for position (right-aligned)
            position_space = 5  # "100%" is max 4 chars + 1 space
            
            # Reserve space for timestamp (left side after number)
            timestamp_space = 11 if timestamp else 0  # "12/31 23:59" + 1 space
            
            # Calculate remaining space for title and chapter with proper spacing
            # Leave extra space for safety to prevent overrun
            reserved_space = len(num_field) + 1 + timestamp_space + position_space + 2  # +2 for safety margin
            content_width = max(20, available_width - reserved_space)  # Ensure minimum content width
            
            # Split content space: 40% title, 60% chapter, with reasonable minimums
            title_space = max(8, min(20, int(content_width * 0.4)))  # Cap title at 20 chars
            chapter_space = max(8, content_width - title_space)
            
            # Truncate title and chapter to fit
            book_title = bookmark.get('book_title', 'Unknown')
            if len(book_title) > title_space:
                book_title = book_title[:title_space-1] + "…"
            else:
                book_title = book_title.ljust(title_space)
            
            chapter_title = bookmark.get('chapter_title', 'Chapter ?')
            if len(chapter_title) > chapter_space:
                chapter_title = chapter_title[:chapter_space-1] + "…"
            else:
                chapter_title = chapter_title.ljust(chapter_space)
            
            # Create spaced display line with proper alignment
            if timestamp:
                display_line = f"{num_field} {timestamp} {book_title} {chapter_title} {position_str:>4s}"
            else:
                # No timestamp, give more space to content  
                available_for_content = content_width + timestamp_space
                title_space = max(10, min(25, int(available_for_content * 0.4)))  # Cap title at 25 chars
                chapter_space = max(8, available_for_content - title_space)
                
                book_title = bookmark.get('book_title', 'Unknown')
                if len(book_title) > title_space:
                    book_title = book_title[:title_space-1] + "…"
                else:
                    book_title = book_title.ljust(title_space)
                
                chapter_title = bookmark.get('chapter_title', 'Chapter ?')
                if len(chapter_title) > chapter_space:
                    chapter_title = chapter_title[:chapter_space-1] + "…"
                else:
                    chapter_title = chapter_title.ljust(chapter_space)
                
                display_line = f"{num_field} {book_title} {chapter_title} {position_str:>4s}"
        
        bookmark_items.append((display_line, bookmark))  # Store display and actual bookmark
    
    # Use unified list dialog with custom delete handling
    width = modal_width  # Use the same width calculated above
    height = min(rows - 4, 25)
    current = 0
    
    while True:
        if Modal.is_active():
            return None
        
        Modal.set_active("bookmarks")
        
        dialog = Modal.create_dialog(stdscr, width, height, f"Bookmarks ({len(GLOBAL_BOOKMARKS)} saved)")
        
        # Display bookmarks
        display_height = height - 4
        start_idx = max(0, current - display_height // 2)
        end_idx = min(len(bookmark_items), start_idx + display_height)
        
        for i in range(start_idx, end_idx):
            y = 2 + (i - start_idx)
            attr = curses.A_REVERSE if i == current else 0
            item_text = bookmark_items[i][0]  # Already truncated to fit
            dialog.addstr(y, 2, item_text, attr)
        
        help_text = "Enter: Open | d: Delete | q: Cancel"
        # Use colored help text formatting
        max_help_width = width - 4  # Account for padding
        format_help_text_with_colors(dialog, height-2, 2, help_text, max_help_width)
        dialog.refresh()
        
        key = Modal.get_immediate_key(dialog)
            
        if key == ord('q'):  # q to exit
            Modal.destroy_dialog(stdscr, dialog)
            return None
        elif key in (10, 13):  # Enter - select bookmark
            if current < len(bookmark_items):
                selected = bookmark_items[current][1]  # Get actual bookmark object
                Modal.destroy_dialog(stdscr, dialog)
                return selected
            Modal.destroy_dialog(stdscr, dialog)
            return None
        elif key == ord('d'):  # Delete bookmark
            if current < len(GLOBAL_BOOKMARKS):
                del GLOBAL_BOOKMARKS[current]
                save_bookmarks()
                # Rebuild bookmark items list with same formatting logic
                bookmark_items = []
                for i, bookmark in enumerate(GLOBAL_BOOKMARKS):
                    # Format timestamp
                    timestamp = ""
                    if 'created' in bookmark:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(bookmark['created'])
                            timestamp = dt.strftime("%m/%d %H:%M")
                        except:
                            timestamp = ""
                    
                    # Get position percentage
                    position_pct = bookmark.get('percentage', 0.0)
                    position_str = f"{position_pct:.0f}%"
                    
                    # Calculate space for fixed elements and proper spacing
                    num_field = f"{i+1:2d}."
                    
                    # Check if container is narrow (less than 50 chars available)
                    if available_width < 50 and timestamp:
                        # Narrow container: show only timestamp and position
                        display_line = f"{num_field} {timestamp} {position_str:>4s}"
                    else:
                        # Normal width: show all fields with proper spacing
                        # Reserve space for position (right-aligned)
                        position_space = 5  # "100%" is max 4 chars + 1 space
                        
                        # Reserve space for timestamp (left side after number)
                        timestamp_space = 11 if timestamp else 0  # "12/31 23:59" + 1 space
                        
                        # Calculate remaining space for title and chapter with proper spacing
                        # Leave extra space for safety to prevent overrun
                        reserved_space = len(num_field) + 1 + timestamp_space + position_space + 2  # +2 for safety margin
                        content_width = max(20, available_width - reserved_space)  # Ensure minimum content width
                        
                        # Split content space: 40% title, 60% chapter, with reasonable minimums
                        title_space = max(8, min(20, int(content_width * 0.4)))  # Cap title at 20 chars
                        chapter_space = max(8, content_width - title_space)
                        
                        # Truncate title and chapter to fit
                        book_title = bookmark.get('book_title', 'Unknown')
                        if len(book_title) > title_space:
                            book_title = book_title[:title_space-1] + "…"
                        else:
                            book_title = book_title.ljust(title_space)
                        
                        chapter_title = bookmark.get('chapter_title', 'Chapter ?')
                        if len(chapter_title) > chapter_space:
                            chapter_title = chapter_title[:chapter_space-1] + "…"
                        else:
                            chapter_title = chapter_title.ljust(chapter_space)
                        
                        # Create spaced display line with proper alignment
                        if timestamp:
                            display_line = f"{num_field} {timestamp} {book_title} {chapter_title} {position_str:>4s}"
                        else:
                            # No timestamp, give more space to content
                            available_for_content = content_width + timestamp_space
                            title_space = max(10, min(25, int(available_for_content * 0.4)))  # Cap title at 25 chars
                            chapter_space = max(8, available_for_content - title_space)
                            
                            book_title = bookmark.get('book_title', 'Unknown')
                            if len(book_title) > title_space:
                                book_title = book_title[:title_space-1] + "…"
                            else:
                                book_title = book_title.ljust(title_space)
                            
                            chapter_title = bookmark.get('chapter_title', 'Chapter ?')
                            if len(chapter_title) > chapter_space:
                                chapter_title = chapter_title[:chapter_space-1] + "…"
                            else:
                                chapter_title = chapter_title.ljust(chapter_space)
                            
                            display_line = f"{num_field} {book_title} {chapter_title} {position_str:>4s}"
                    
                    bookmark_items.append((display_line, bookmark))
                
                if current >= len(bookmark_items) and current > 0:
                    current = len(bookmark_items) - 1
                if not bookmark_items:
                    Modal.destroy_dialog(stdscr, dialog)
                    return None
            Modal.destroy_dialog(stdscr, dialog)
            continue  # Restart dialog with updated list
        elif key == curses.KEY_UP and current > 0:
            current -= 1
        elif key == curses.KEY_DOWN and current < len(bookmark_items) - 1:
            current += 1
        elif key == curses.KEY_RESIZE:
            Modal.destroy_dialog(stdscr, dialog)
            return curses.KEY_RESIZE
        
        Modal.destroy_dialog(stdscr, dialog)

def toc(stdscr, src, index):
    """Table of Contents using unified modal system"""
    if Modal.is_active():
        return None
    
    # Create simple list from src for modal display
    toc_items = []
    for i, item in enumerate(src):
        prefix = ">> " if i == index else "   "
        toc_items.append(f"{prefix}{item}")
    
    # Use modal list dialog
    rows, cols = stdscr.getmaxyx()
    width = min(cols - 4, 80)
    height = min(rows - 4, 25)
    
    result = Modal.list_dialog(stdscr, width, height, "Table of Contents", toc_items, index)
    
    if result is None:
        return None
    elif result == curses.KEY_RESIZE:
        return curses.KEY_RESIZE
    else:
        # Find the index of the selected item
        for i, item in enumerate(toc_items):
            if item == result:
                return i
        return None


def meta(stdscr, ebook):
    """Metadata display using unified modal system"""
    if Modal.is_active():
        return None
    
    # Prepare metadata lines
    rows, cols = stdscr.getmaxyx()
    wrap_width = max(10, min(cols - 10, 70))  # Account for dialog borders and padding
    
    mdata = []
    for i in ebook.get_meta():
        data = re.sub("<[^>]*>", "", i[1])
        data = re.sub("\t", "", data)
        mdata += textwrap.wrap(i[0].upper() + ": " + data, wrap_width)
    
    if not mdata:
        mdata = ["No metadata available"]
    
    # Use modal list dialog (read-only)
    width = min(cols - 4, 80)
    height = min(rows - 4, 25)
    
    result = Modal.list_dialog(stdscr, width, height, "Metadata", mdata, 0, "q: Close")
    return result


def format_help_text_with_colors(dialog, y, x, text, width=None):
    """Display help text with highlighted key names"""
    import re
    
    # Pattern to match key names - more flexible matching
    key_pattern = r'(Enter|Space|Tab|Home|End|PgUp|PgDn|[↓↑←→]|[a-zA-Z?/])(?=\s*[-:])'
    
    if width and len(text) > width:
        text = text[:width-1] + "…"
    
    col = x
    last_end = 0
    
    for match in re.finditer(key_pattern, text):
        start, end = match.span()
        
        # Add normal text before the key
        if start > last_end:
            normal_text = text[last_end:start]
            try:
                dialog.addstr(y, col, normal_text)
                col += len(normal_text)
            except curses.error:
                break
        
        # Add highlighted key name
        key_text = text[start:end]
        try:
            # Use theme-appropriate highlighting for key names
            if COLORSUPPORT:
                # Determine current color scheme
                current_bg_pair = curses.pair_number(dialog.getbkgd())
                is_light_scheme = current_bg_pair == 3  # Light scheme is color pair 3
                
                if is_light_scheme:
                    # Light theme: use dark text with bold
                    dialog.addstr(y, col, key_text, curses.color_pair(1) | curses.A_BOLD)
                else:
                    # Dark theme: use bright text with bold  
                    dialog.addstr(y, col, key_text, curses.color_pair(2) | curses.A_BOLD)
            else:
                # No color support, just use bold
                dialog.addstr(y, col, key_text, curses.A_BOLD)
            col += len(key_text)
        except curses.error:
            break
        
        last_end = end
    
    # Add remaining normal text
    if last_end < len(text):
        remaining_text = text[last_end:]
        try:
            dialog.addstr(y, col, remaining_text)
        except curses.error:
            pass

def help(stdscr):
    """Simplified help dialog using unified modal system"""
    if Modal.is_active():
        return None
    
    # Create basic help content
    help_lines = [
        "Key Bindings:",
        "",
        "q          - Quit",
        "?          - Show this help",
        "↓/↑        - Scroll down/up",
        "Space/→    - Next page", 
        "←          - Previous page",
        "n          - Next chapter",
        "p          - Previous chapter",
        "Home       - Beginning of chapter",
        "End        - End of chapter",
        "i          - Open visible image",
        "u          - Show URLs",
        "/          - Search",
        "Tab/t      - Table of contents",
        "m          - Show metadata",
        "s          - Save bookmark",
        "b          - Show bookmarks",
        "c          - Cycle color schemes"
    ]
    
    return Modal.list_dialog(stdscr, 50, 20, "Help", help_lines)


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

            if ipt == ord('q'):  # 'q' to exit
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
                elif s == ord("p") and ch +1 == tot:
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
        elif s == ord("p"):
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
_image_cache = {}    # Cache processed images to avoid re-rendering on resize
_next_color_pair = 4  # Start after pre-defined pairs (1,2,3)  
_MAX_COLOR_PAIRS = 5000   # Safe range for image colors (pairs 4-5000)
_SEARCH_PAIR_START = 5001  # Reserved pairs 5001-5050 for search highlighting  
_SYNTAX_COLOR_PAIRS = {}  # Dedicated cache for syntax highlighting pairs
_SYNTAX_PAIR_START = 5051  # Reserve pairs 5051-5100 for syntax highlighting
_UI_PAIR_START = 5101     # Reserve pairs 5101+ for loading messages etc.

def get_ui_color_pair(purpose="loading"):
    """Get a dedicated color pair for UI elements like loading messages."""
    global _UI_PAIR_START
    try:
        if purpose == "loading":
            pair_id = _UI_PAIR_START
            # Use predefined color pairs to avoid conflicts
            return 1  # Default color pair (reliable)
        return 1  # Fallback to default
    except:
        return 1  # Safe fallback

def init_syntax_color_pairs():
    """Pre-allocate color pairs for syntax highlighting in reserved range."""
    global _SYNTAX_COLOR_PAIRS
    
    # Define syntax highlighting colors - work well on any background
    syntax_colors = [
        (255, 100, 100),  # Red for keywords
        (100, 255, 100),  # Green for strings  
        (200, 200, 200),  # Light gray for punctuation
        (150, 150, 150),  # Gray for comments
        (255, 255, 100),  # Yellow for classes
        (100, 200, 255),  # Blue for functions
        (200, 100, 255),  # Purple for builtins
        (255, 150, 100),  # Orange for numbers
    ]
    
    # Pre-allocate these colors in the reserved range (5001-5050)
    pair_id = _SYNTAX_PAIR_START
    for color in syntax_colors:
        if COLORSUPPORT and pair_id <= _SYNTAX_PAIR_START + 50:
            try:
                # Convert to color indices
                fg_color = find_closest_palette_color(color)
                
                fg_idx = rgb_to_color_index(*fg_color)
                bg_idx = -1  # Use default terminal background
                
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
    
    palette = []
    
    # Use a finer 8x8x8 RGB cube for better color matching
    # This gives us 512 color gradations instead of 216
    # More gradations = less "hickeldy pickley" color jumps
    for r in range(8):
        for g in range(8):
            for b in range(8):
                # Map 0-7 to 0-255 with better distribution
                red = int(r * 255 / 7)
                green = int(g * 255 / 7)
                blue = int(b * 255 / 7)
                palette.append((red, green, blue))
    
    # Add 24 grayscale colors (matching indices 232-255)
    for i in range(24):
        gray = 8 + i * 10  # Range from 8 to 238
        palette.append((gray, gray, gray))
    
    # Add the 16 basic ANSI colors (indices 0-15) for completeness
    basic_colors = [
        (0, 0, 0),       # 0 - Black
        (205, 0, 0),     # 1 - Red (adjusted for terminal)
        (0, 205, 0),     # 2 - Green
        (205, 205, 0),   # 3 - Yellow
        (0, 0, 238),     # 4 - Blue
        (205, 0, 205),   # 5 - Magenta
        (0, 205, 205),   # 6 - Cyan
        (229, 229, 229), # 7 - White
        (127, 127, 127), # 8 - Bright Black
        (255, 0, 0),     # 9 - Bright Red
        (0, 255, 0),     # 10 - Bright Green
        (255, 255, 0),   # 11 - Bright Yellow
        (92, 92, 255),   # 12 - Bright Blue
        (255, 0, 255),   # 13 - Bright Magenta
        (0, 255, 255),   # 14 - Bright Cyan
        (255, 255, 255)  # 15 - Bright White
    ]
    for color in basic_colors:
        if color not in palette:
            palette.append(color)
    
    _color_palette = palette

def find_closest_palette_color(target_rgb):
    """Find the closest color in our palette using more discerning matching."""
    if not _color_palette:
        init_smart_color_palette()
    
    target_r, target_g, target_b = target_rgb
    best_match = _color_palette[0]
    best_distance = float('inf')
    
    # Set a more lenient threshold - allow closer palette matches
    # to avoid returning original colors that won't work with curses
    max_acceptable_distance = 800  # More lenient to use palette colors more often
    
    for palette_rgb in _color_palette:
        # Use standard Euclidean distance for more consistent results
        r_diff = target_r - palette_rgb[0]
        g_diff = target_g - palette_rgb[1]
        b_diff = target_b - palette_rgb[2]
        distance = r_diff*r_diff + g_diff*g_diff + b_diff*b_diff
        
        if distance < best_distance:
            best_distance = distance
            best_match = palette_rgb
    
    # If the best match is still too far away, return the original color
    # This prevents very different colors from being forced into wrong palette entries
    if best_distance > max_acceptable_distance:
        return target_rgb
    
    return best_match

def rgb_to_color_index(r, g, b):
    """Convert RGB to 256-color palette index."""
    try:
        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        
        # Much stricter grayscale detection - only perfectly gray colors
        # Increased threshold to 35 to preserve subtle colors
        max_diff = max(abs(r - g), abs(g - b), abs(r - b))
        
        # Only consider it grayscale if VERY close in values AND low saturation
        # This preserves more colored pixels and prevents "hickeldy pickley" colors
        if max_diff < 35:
            # Check saturation - if there's any color bias, preserve it
            avg = (r + g + b) / 3
            color_bias = max(abs(r - avg), abs(g - avg), abs(b - avg))
            
            if color_bias < 18:  # Only truly neutral colors become grayscale
                gray = int((r + g + b) / 3)
                if gray < 8:
                    return 0  # Black
                elif gray > 248:  
                    return 15  # White
                else:
                    # Map to grayscale 232-255 (24 levels)
                    level = min(23, max(0, (gray - 8) * 23 // 240))
                    return 232 + level
        
        # For colored pixels, use better quantization to match our 8x8x8 palette
        # This provides smoother color gradations
        r_level = min(7, int(r * 8 / 256))
        g_level = min(7, int(g * 8 / 256))
        b_level = min(7, int(b * 8 / 256))
        # Map to appropriate color index in 256-color space
        # We still need to map to the standard 6x6x6 cube for terminal compatibility
        # So convert our 8-level to nearest 6-level
        r_level_6 = min(5, int(r_level * 6 / 8))
        g_level_6 = min(5, int(g_level * 6 / 8))
        b_level_6 = min(5, int(b_level * 6 / 8))
        return 16 + r_level_6 * 36 + g_level_6 * 6 + b_level_6
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

def get_syntax_color_pair(color, bg_color=None):
    """Get a pre-allocated color pair for syntax highlighting."""
    if not COLORSUPPORT:
        return 0
    
    # If no background specified, use black
    if bg_color is None:
        bg_color = (0, 0, 0)
    
    # Create a cache key that includes both fg and bg
    cache_key = (tuple(color) if isinstance(color, (list, tuple)) else color, 
                 tuple(bg_color) if isinstance(bg_color, (list, tuple)) else bg_color)
    
    # Try to find exact match in pre-allocated pairs
    if cache_key in _SYNTAX_COLOR_PAIRS:
        return _SYNTAX_COLOR_PAIRS[cache_key]
    
    # Fall back to regular color pair system with specified background
    pair = get_color_pair(color, bg_color)
    if pair > 0:
        _SYNTAX_COLOR_PAIRS[cache_key] = pair
    return pair

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
            
            # Use most vibrant/saturated color instead of average to preserve color richness
            # This prevents muddy averaged colors
            best_color = colors[0]
            max_saturation = 0
            for color in colors:
                r, g, b = color
                # Calculate saturation (how far from gray)
                avg = (r + g + b) / 3
                saturation = abs(r - avg) + abs(g - avg) + abs(b - avg)
                if saturation > max_saturation:
                    max_saturation = saturation
                    best_color = color
            
            # If all colors are very similar (low variance), then average them
            # Otherwise use the most saturated color
            color_variance = sum(abs(colors[i][j] - colors[0][j]) 
                                for i in range(1, len(colors)) 
                                for j in range(3))
            
            if color_variance < 30:  # All colors very similar
                avg_color = tuple(sum(c[i] for c in colors) // 4 for i in range(3))
                color_pair = get_color_pair(avg_color)
            else:
                color_pair = get_color_pair(best_color)
            
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



def detect_image_colorfulness(img, sample_size=100):
    """Detect if an image is roughly monochromatic (grayscale) or has significant color content.
    Returns (is_monochrome, avg_saturation) where is_monochrome is True for mostly gray/monochromatic images."""
    width, height = img.size
    
    # Sample pixels evenly across the image
    sample_points = []
    step_x = max(1, width // 10)
    step_y = max(1, height // 10)
    
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            if len(sample_points) >= sample_size:
                break
            r, g, b = img.getpixel((x, y))
            sample_points.append((r, g, b))
    
    # Calculate saturation statistics
    total_saturation = 0
    color_pixel_count = 0
    
    for r, g, b in sample_points:
        # Calculate saturation using HSV model
        max_val = max(r, g, b)
        min_val = min(r, g, b)
        
        if max_val == 0:
            saturation = 0
        else:
            saturation = (max_val - min_val) / max_val
        
        total_saturation += saturation
        
        # Count pixels that have noticeable color (not grayscale)
        # Use a threshold of 15 to detect color variation
        if abs(r - g) > 15 or abs(g - b) > 15 or abs(r - b) > 15:
            color_pixel_count += 1
    
    avg_saturation = total_saturation / len(sample_points) if sample_points else 0
    color_ratio = color_pixel_count / len(sample_points) if sample_points else 0
    
    # Consider image monochromatic if less than 20% of pixels have significant color
    # OR if average saturation is very low
    is_monochrome = color_ratio < 0.2 or avg_saturation < 0.15
    
    return is_monochrome, avg_saturation

def boost_color_saturation(r, g, b, boost_factor=1.5):
    """Selectively boost saturation ONLY for near-gray colors to prevent them being treated as gray.
    Already saturated colors are left unchanged."""
    
    # Calculate how "gray" this color is
    max_val = max(r, g, b)
    min_val = min(r, g, b)
    avg = (r + g + b) / 3
    
    # If it's truly grayscale, don't boost
    if max_val == min_val:
        return r, g, b
    
    # Calculate current saturation (0-1 scale)
    if max_val == 0:
        saturation = 0
    else:
        saturation = (max_val - min_val) / max_val
    
    # Calculate how much each channel deviates from gray
    max_deviation = max(abs(r - avg), abs(g - avg), abs(b - avg))
    
    # Only boost if the color is near-gray (low saturation, small deviation)
    # This prevents pale colors from being mapped to grayscale
    if saturation < 0.3 and max_deviation < 40:
        # This is a pale/near-gray color that needs boosting
        # Boost to ensure it stays colored, not gray
        actual_boost = boost_factor
    elif saturation < 0.15 and max_deviation < 20:
        # Very pale - needs stronger boost
        actual_boost = boost_factor * 1.5
    else:
        # Already has enough color - leave it alone
        actual_boost = 1.0
    
    # Apply selective boost
    new_r = avg + (r - avg) * actual_boost
    new_g = avg + (g - avg) * actual_boost
    new_b = avg + (b - avg) * actual_boost
    
    # Clamp to valid range
    new_r = max(0, min(255, int(new_r)))
    new_g = max(0, min(255, int(new_g)))
    new_b = max(0, min(255, int(new_b)))
    
    return new_r, new_g, new_b

def render_images_inline(ebook, chpath, src_lines, imgs, max_width):
    """Convert image placeholders to block-based representation inline with color info."""
    if not PIL_AVAILABLE or not imgs:
        # Create empty image tracking array for each line
        image_line_map = [None] * len(src_lines)
        return src_lines, [], image_line_map
    
    new_lines = []
    image_info = []
    image_line_map = []  # Track which image (if any) is associated with each line
    
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
                    
                    # Debug: show image dimensions in debug mode
                    if os.getenv('TERMBOOK_DEBUG'):
                        print(f"DEBUG: Image {impath} is {orig_width}x{orig_height} pixels", file=sys.stderr)
                    
                    # Enhanced decorative image filtering
                    is_decorative = False
                    
                    # Size-based filtering: expand threshold to catch more decorative images
                    if orig_width <= 120 and orig_height <= 120:
                        is_decorative = True
                    
                    # Also filter out very small images that are clearly decorative
                    if orig_width <= 50 or orig_height <= 50:
                        is_decorative = True
                    
                    # Area-based filtering: images with very small total area are decorative
                    total_area = orig_width * orig_height
                    if total_area <= 4000:  # Less than ~63x63 pixels
                        is_decorative = True
                    
                    # Check filename patterns that suggest decorative images
                    img_path_lower = impath.lower()
                    decorative_patterns = ['bullet', 'ornament', 'decoration', 'divider', 
                                         'separator', 'icon', 'mark', 'symbol', 'star', 'dot',
                                         'border', 'line', 'rule', 'flourish', 'accent', 'deco',
                                         'spacer', 'gap', 'filler']
                    if any(pattern in img_path_lower for pattern in decorative_patterns):
                        is_decorative = True
                    
                    # Aspect ratio filtering: very wide or very tall images are often decorative
                    if orig_aspect > 10 or orig_aspect < 0.1:  # 10:1 or 1:10 ratio
                        is_decorative = True
                    
                    # Check for simple/repetitive content - images with very few colors
                    try:
                        # Sample the image to check color variety
                        sample_img = img.resize((16, 16))  # Small sample for quick processing
                        colors = sample_img.getcolors(maxcolors=256)
                        if colors and len(colors) <= 6:  # Very few colors = likely decorative
                            is_decorative = True
                        
                        # For very small images, be even more aggressive
                        if total_area <= 2000 and colors and len(colors) <= 10:
                            is_decorative = True
                    except:
                        pass
                    
                    # Check for very thin images that span most of a line (borders, rules)
                    if (orig_width > 200 and orig_height < 30) or (orig_height > 200 and orig_width < 30):
                        is_decorative = True
                    
                    if is_decorative:
                        # Replace with minimal characters based on size and type
                        if orig_width <= 16 and orig_height <= 16:
                            # Very tiny - just use a dot
                            decorative_char = "·"  # Middle dot for very small images
                        elif orig_width <= 40 and orig_height <= 40:
                            # Small - use a simple bullet
                            decorative_char = "•"
                        elif orig_aspect > 5 or orig_aspect < 0.2:
                            # Thin/wide decorative - use a line
                            decorative_char = "―" if orig_aspect > 5 else "|"
                        else:
                            # Larger decorative - just skip it entirely
                            decorative_char = ""  # Remove completely for larger decorative images
                        new_lines.append(line.replace(f"[IMG:{img_idx}]", decorative_char))
                        continue
                    
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
                    
                    # Final bounds checking - adjust minimums based on original image size
                    if orig_width >= 100 and orig_height >= 100:
                        # Reasonably sized original, enforce decent minimums
                        char_width = max(12, min(char_width, max_width_by_screen))  # Minimum 12 chars wide
                        char_height = max(6, min(char_height, max_height_available))  # Minimum 6 chars tall
                    else:
                        # Small original image, use smaller minimums to preserve aspect ratio
                        min_width = max(6, orig_width // 4)  # Scale based on original
                        min_height = max(4, orig_height // 4)
                        char_width = max(min_width, min(char_width, max_width_by_screen))
                        char_height = max(min_height, min(char_height, max_height_available))
                    
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
                    
                    # Detect if image is monochromatic or has color before resizing
                    is_monochrome, avg_saturation = detect_image_colorfulness(img)
                    
                    # Determine saturation boost factor based on image colorfulness
                    if is_monochrome:
                        # Don't boost monochromatic images - preserve their grays
                        saturation_boost = 1.0
                    else:
                        # For colorful images, use a moderate boost value
                        # The boost_color_saturation function will selectively apply it
                        # only to near-gray colors, leaving saturated colors unchanged
                        saturation_boost = 1.5  # This value is only applied to pale colors
                    
                    # Keep original image for high-quality oversampling
                    orig_img = img.copy()
                    orig_w, orig_h = orig_img.size
                    
                    # Calculate target dimensions
                    target_width = target_pixel_width
                    target_height = target_pixel_height
                    
                    # Ensure height is even for proper half-block pairing
                    if target_height % 2 != 0:
                        target_height += 1
                    
                    # Calculate sampling regions for each output pixel
                    x_scale = orig_w / target_width
                    y_scale = orig_h / target_height
                    
                    # Unicode quarter-block characters for 2x2 pixel mapping
                    blocks = [' ', '▘', '▝', '▀', '▖', '▌', '▞', '▛', '▗', '▚', '▐', '▜', '▄', '▙', '▟', '█']
                    
                    # Store color and character info for each line with oversampling
                    for y in range(0, target_height, 2):  # Process 2 rows at a time (proper half-block technique)
                        line = ""
                        line_colors = []
                        
                        # Process each column of pixels with oversampling from original
                        for x in range(target_width):
                            # Calculate source region in original image for this output pixel
                            src_x_start = int(x * x_scale)
                            src_x_end = max(src_x_start + 1, int((x + 1) * x_scale))
                            src_y_top_start = int(y * y_scale)
                            src_y_top_end = max(src_y_top_start + 1, int((y + 1) * y_scale))
                            src_y_bot_start = int((y + 1) * y_scale)
                            src_y_bot_end = max(src_y_bot_start + 1, int(min((y + 2) * y_scale, orig_h)))
                            
                            # Scattergun sampling for much better performance
                            import random
                            top_samples = []
                            num_samples = 16  # Much fewer samples than 8x8=64, but randomly distributed
                            
                            # Set seed for consistent results per pixel coordinate
                            random.seed(x + y * 10000)
                            
                            for _ in range(num_samples):
                                # Random position within top half region
                                random_x = random.uniform(0, 1)
                                random_y = random.uniform(0, 1)
                                
                                precise_x = src_x_start + (random_x * (src_x_end - src_x_start))
                                precise_y = src_y_top_start + (random_y * (src_y_top_end - src_y_top_start))
                                
                                # Convert to integer for pixel access, with bounds checking
                                pixel_x = min(int(precise_x), orig_w - 1)
                                pixel_y = min(int(precise_y), orig_h - 1)
                                
                                if pixel_x >= 0 and pixel_y >= 0:
                                    top_samples.append(orig_img.getpixel((pixel_x, pixel_y)))
                            
                            # Scattergun sampling for bottom half region
                            bottom_samples = []
                            
                            for _ in range(num_samples):
                                # Random position within bottom half region  
                                random_x = random.uniform(0, 1)
                                random_y = random.uniform(0, 1)
                                
                                precise_x = src_x_start + (random_x * (src_x_end - src_x_start))
                                precise_y = src_y_bot_start + (random_y * (src_y_bot_end - src_y_bot_start))
                                
                                # Convert to integer for pixel access, with bounds checking
                                pixel_x = min(int(precise_x), orig_w - 1)
                                pixel_y = min(int(precise_y), orig_h - 1)
                                
                                if pixel_x >= 0 and pixel_y >= 0:
                                    bottom_samples.append(orig_img.getpixel((pixel_x, pixel_y)))
                            
                            # Average the samples for smoother colors
                            if top_samples:
                                top_pixel = tuple(sum(c[i] for c in top_samples) // len(top_samples) for i in range(3))
                            else:
                                top_pixel = (0, 0, 0)
                                
                            if bottom_samples:
                                bottom_pixel = tuple(sum(c[i] for c in bottom_samples) // len(bottom_samples) for i in range(3))
                            else:
                                bottom_pixel = (0, 0, 0)
                            
                            # Apply saturation boost if needed (only for colorful images)
                            if saturation_boost > 1.0:
                                top_pixel = boost_color_saturation(*top_pixel, saturation_boost)
                                bottom_pixel = boost_color_saturation(*bottom_pixel, saturation_boost)
                            
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
                        image_line_map.append(img_idx)  # Track which image this line belongs to
                    
                    new_lines.append("")  # Empty line after image
                    image_line_map.append(None)  # Empty line doesn't belong to any image
                    image_info.append([])  # Empty color info for empty line
                    
                except Exception as e:
                    # If image can't be processed, show error message
                    error_msg = f"[Error loading image: {imgs[img_idx]}]"
                    new_lines.append(" " * ((max_width - len(error_msg)) // 2) + error_msg)
                    image_info.append([])
                    image_line_map.append(img_idx)  # Error line still belongs to this image
            else:
                # Image index out of range
                new_lines.append(line)
                image_info.append([])
                image_line_map.append(None)  # No valid image association
        else:
            new_lines.append(line)
            image_info.append([])
            image_line_map.append(None)  # Regular text line, no image association
    
    return new_lines, image_info, image_line_map


def show_loading_animation(stdscr, message="Loading..."):
    """Display a centered loading animation with rolling spectrum effect."""
    rows, cols = stdscr.getmaxyx()
    
    # Center position
    center_row = rows // 2
    center_col = cols // 2
    
    # Determine current color scheme
    current_bg_pair = curses.pair_number(stdscr.getbkgd())
    is_light_scheme = current_bg_pair == 3  # Light scheme is color pair 3
    
    # Don't clear screen - preserve current background
    # Just clear the message area to avoid artifacts
    msg_len = len(message)
    start_col = center_col - msg_len // 2
    
    # Clear only the exact message area, no extra padding to avoid overwriting text
    try:
        # Only clear the space where the loading message will appear - exact length only
        if start_col >= 0 and start_col + msg_len <= cols:
            stdscr.addstr(center_row, start_col, " " * msg_len)
    except:
        pass
    
    # Create saturated two-color gradient with doubled sequence
    gradient_steps = 16  # Steps for one direction
    
    if is_light_scheme:
        # Darker colors for light theme - better visibility
        start_color = (0, 50, 150)     # Dark blue
        end_color = (0, 120, 100)      # Dark teal
    else:
        # Bright colors for dark theme
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
            
            # Create rolling spectrum animation with actual RGB colors
            if COLORSUPPORT:
                try:
                    # Get RGB color from spectrum
                    r, g, b = spectrum_colors[color_idx]
                    
                    # Convert RGB to terminal color index
                    color_idx_terminal = rgb_to_color_index(r, g, b)
                    
                    # Try to get or create color pair 
                    pair_id, _ = get_color_pair_with_reversal((r, g, b), (0, 0, 0), allow_reversal=False)
                    
                    if pair_id > 0:
                        # Use the spectrum color pair
                        stdscr.addstr(center_row, start_col + i, char, curses.color_pair(pair_id) | curses.A_BOLD)
                    else:
                        # Fallback: use terminal color directly if pair creation failed
                        stdscr.addstr(center_row, start_col + i, char, curses.color_pair(color_idx_terminal) | curses.A_BOLD)
                except:
                    # Final fallback to bold
                    stdscr.addstr(center_row, start_col + i, char, curses.A_BOLD)
            else:
                # No color support, just use bold
                stdscr.addstr(center_row, start_col + i, char, curses.A_BOLD)
        
        stdscr.refresh()
    except:
        pass  # Ignore any display errors

def reader(stdscr, ebook, index, width, y, pctg):
    global CURRENT_SEARCH_TERM, WHOLE_BOOK_SEARCH_START, WHOLE_BOOK_SEARCH_VISITED
    k = 0 if SEARCHPATTERN is None else ord("/")
    rows, cols = stdscr.getmaxyx()
    x = (cols - width) // 2

    contents = ebook.contents
    toc_src = ebook.toc_entries
    
    # Validate index is within bounds to prevent IndexError
    if not contents or len(contents) == 0 or index < 0 or index >= len(contents):
        index = 0
        y = 0
        pctg = 0
    
    # Additional safety check before accessing contents
    if not contents or len(contents) == 0:
        # Handle case where the book has no chapters/contents
        raise Exception(f"Book has no readable content: {ebook.path}")
    
    chpath = contents[index]
    content = ebook.file.open(chpath).read()
    content = content.decode("utf-8")

    parser = HTMLtoLines()
    try:
        parser.feed(content)
        parser.close()
    except:
        pass

    src_lines, imgs, img_alts = parser.get_lines(width)
    
    # Check if we're continuing a whole-book search
    if WHOLE_BOOK_SEARCH_START is not None and CURRENT_SEARCH_TERM:
        # Add current chapter to visited list
        if index not in WHOLE_BOOK_SEARCH_VISITED:
            WHOLE_BOOK_SEARCH_VISITED.append(index)
        
        # Search for term in this chapter
        found_in_chapter = False
        for i, line in enumerate(src_lines):
            if CURRENT_SEARCH_TERM.lower() in line.lower():
                # Found it! Reset search tracking and highlight
                WHOLE_BOOK_SEARCH_START = None
                WHOLE_BOOK_SEARCH_VISITED = []
                y = i
                found_in_chapter = True
                break
        
        if not found_in_chapter:
            # Not found in this chapter, check if we've searched all chapters
            if len(WHOLE_BOOK_SEARCH_VISITED) >= len(contents) or index == WHOLE_BOOK_SEARCH_START:
                # We've searched everything and returned to start, or visited all chapters
                rows, cols = stdscr.getmaxyx()
                stdscr.addstr(rows-1, 0, f" '{CURRENT_SEARCH_TERM}' not found in book ", curses.A_REVERSE)
                stdscr.refresh()
                curses.napms(2000)  # Show for 2 seconds
                WHOLE_BOOK_SEARCH_START = None
                WHOLE_BOOK_SEARCH_VISITED = []
                CURRENT_SEARCH_TERM = None
            else:
                # Continue to next chapter
                next_chapter = (index + 1) % len(contents)
                chapter_offset = next_chapter - index
                
                # Show searching message with cancel option
                rows, cols = stdscr.getmaxyx()
                stdscr.addstr(rows-1, 0, f" Searching chapter {next_chapter + 1}... (press 'q' to cancel) ", curses.A_REVERSE)
                stdscr.refresh()
                
                # Check for cancel key press
                stdscr.nodelay(True)  # Non-blocking input
                try:
                    cancel_key = stdscr.getch()
                    if cancel_key == ord('q'):
                        # User wants to cancel whole-book search
                        WHOLE_BOOK_SEARCH_START = None
                        WHOLE_BOOK_SEARCH_VISITED = []
                        CURRENT_SEARCH_TERM = None
                        stdscr.addstr(rows-1, 0, " Search cancelled ", curses.A_REVERSE)
                        stdscr.refresh()
                        curses.napms(1000)  # Show briefly
                        return 0, width, y, y/totlines if totlines > 0 else 0
                except:
                    pass  # No key pressed
                finally:
                    stdscr.nodelay(False)  # Restore blocking input
                curses.napms(300)  # Brief pause
                
                return (chapter_offset, width, 0, None)
    
    # Process images inline if PIL is available
    image_info = []
    image_line_map = []
    if PIL_AVAILABLE:
        src_lines, image_info, image_line_map = render_images_inline(ebook, chpath, src_lines, imgs, width)
    else:
        # Create empty image tracking array if not rendering images
        image_line_map = [None] * len(src_lines)
    
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
        # DEBUG: Log what type of line we're processing
        try:
            with open('/tmp/search_debug.log', 'a') as f:
                f.write(f"RENDER_LINE: n={n}, line_type='{line[:20]}...'\n")
        except:
            pass
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
                
                # Determine current theme
                current_bg_pair = curses.pair_number(pad.getbkgd())
                is_light_theme = current_bg_pair == 3  # Light theme is color pair 3
                
                # Skip background filling for now - just use normal text rendering
                if False:  # Disable complex background code
                        # Fill from text start to right edge of terminal with appropriate background
                        for bg_col in range(cols - x):
                            try:
                                pad.addstr(n, bg_col, " ", curses.color_pair(code_bg_pair))
                            except:
                                pass  # Ignore if we can't write at this position
                
                if "|" in content:
                    text_part, color_part = content.rsplit("|", 1)
                    try:
                        # Parse the color list
                        import ast
                        colors = ast.literal_eval(color_part)
                        # Make ALL syntax highlighted text BOLD for visibility testing
                        # Check if this line contains keywords
                        line_lower = text_part.lower()
                        is_keyword_line = any(keyword in line_lower for keyword in ['import', 'export', 'from', 'const', 'let', 'var', 'function'])
                        
                        # Apply syntax highlighting with colors - CRITICAL: Stay within screen bounds
                        for char_idx, char in enumerate(text_part):
                            if char_idx >= cols - x:  # STOP if we would go beyond screen width
                                break
                                
                            if char_idx < len(colors) and colors[char_idx]:
                                # Get color tuple - could be dual format ((dark_rgb), (light_rgb)) or single (r,g,b)
                                color_data = colors[char_idx]
                                
                                # Check if it's dual color format
                                if isinstance(color_data, (tuple, list)) and len(color_data) == 2:
                                    # Dual format: select based on theme
                                    dark_color, light_color = color_data
                                    color_tuple = light_color if is_light_theme else dark_color
                                elif isinstance(color_data, (tuple, list)) and len(color_data) == 3:
                                    # Single format (legacy): use as-is
                                    color_tuple = color_data
                                else:
                                    color_tuple = None
                                
                                if color_tuple and isinstance(color_tuple, (tuple, list)) and len(color_tuple) == 3:
                                    # Get or create color pair for this syntax color with appropriate background
                                    # Use light gray background for light theme, pure black for dark modes
                                    if is_light_theme:
                                        syntax_bg_color = (240, 240, 240)  # Light gray background for light theme
                                    else:
                                        syntax_bg_color = (0, 0, 0)  # Pure black background for dark themes
                                    
                                    color_pair = get_syntax_color_pair(color_tuple, syntax_bg_color)
                                    if color_pair > 0:
                                        try:
                                            pad.addstr(n, char_idx, char, curses.color_pair(color_pair))
                                        except:
                                            break  # Stop if we can't write anymore
                                    else:
                                        # Fallback to bold if color pair couldn't be created
                                        try:
                                            pad.addstr(n, char_idx, char, curses.A_BOLD)
                                        except:
                                            break  # Stop if we can't write anymore
                                else:
                                    # Invalid color format, use regular text
                                    try:
                                        pad.addstr(n, char_idx, char)
                                    except:
                                        break  # Stop if we can't write anymore
                            else:
                                # Regular text
                                try:
                                    pad.addstr(n, char_idx, char)
                                except:
                                    break  # Stop if we can't write anymore
                        
                        # Fill remaining line with background color for code blocks
                        text_end = min(len(text_part), cols - x)
                        if text_end < cols - x:
                            # Determine background color based on theme
                            if is_light_theme:
                                bg_color = (240, 240, 240)  # Light gray
                            else:
                                bg_color = (0, 0, 0)        # Pure black
                            
                            # Get or create background color pair
                            bg_pair = get_syntax_color_pair((128, 128, 128), bg_color)  # Gray text on background
                            if bg_pair > 0:
                                # Fill from end of text to right edge
                                for fill_col in range(text_end, cols - x):
                                    try:
                                        pad.addstr(n, fill_col, " ", curses.color_pair(bg_pair))
                                    except:
                                        break  # Stop if we can't write anymore
                        
                        # Highlight search results with inverse video bright
                        if CURRENT_SEARCH_TERM:
                            import re
                            search_pattern = re.escape(CURRENT_SEARCH_TERM)  # Escape special regex chars
                            # DEBUG: Write to a temp file to see if this code is reached
                            try:
                                with open('/tmp/search_debug.log', 'a') as f:
                                    f.write(f"SEARCH DEBUG: Looking for '{CURRENT_SEARCH_TERM}' in '{text_part[:50]}...'\n")
                            except:
                                pass
                            for match in re.finditer(search_pattern, text_part, re.IGNORECASE):
                                # DEBUG: Found a match
                                try:
                                    with open('/tmp/search_debug.log', 'a') as f:
                                        f.write(f"SEARCH DEBUG: Found match '{match.group()}' at {match.start()}-{match.end()}\n")
                                except:
                                    pass
                                start_pos = match.start()
                                end_pos = match.end()
                                match_text = match.group()
                                
                                # Apply custom search highlighting based on color scheme
                                for char_idx, char in enumerate(match_text):
                                    abs_char_pos = start_pos + char_idx
                                    if abs_char_pos < len(text_part) and n + n_relative < rows and abs_char_pos < width:
                                        try:
                                            if COLORSUPPORT:
                                                # Simple but effective approach: create custom search highlight color on demand
                                                try:
                                                    # Determine current color scheme
                                                    current_bg_pair = curses.pair_number(pad.getbkgd())
                                                    is_light_scheme = current_bg_pair == 3  # Light scheme is color pair 3
                                                    
                                                    # Try to create a search highlight color pair
                                                    search_color_pair = _SEARCH_PAIR_START  # Use high pair number to avoid conflicts
                                                    
                                                    if is_light_scheme:
                                                        # Light mode: bright white text on black background
                                                        curses.init_pair(search_color_pair, curses.COLOR_WHITE, curses.COLOR_BLACK)
                                                    else:
                                                        # Dark modes: black text on bright green background (closest to fluorescent yellow-green)
                                                        curses.init_pair(search_color_pair, curses.COLOR_BLACK, curses.COLOR_GREEN)
                                                    
                                                    pad.addstr(n, abs_char_pos, char, curses.color_pair(search_color_pair) | curses.A_BOLD)
                                                except:
                                                    # If color pair creation fails, fall back to reverse video
                                                    pad.addstr(n, abs_char_pos, char, curses.A_REVERSE | curses.A_BOLD)
                                            else:
                                                # Fallback to just inverse and bold
                                                pad.addstr(n, abs_char_pos, char, curses.A_REVERSE | curses.A_BOLD)
                                        except:
                                            break  # Stop if we can't write anymore
                        
                        # Now look for annotation patterns (#1, #2, #3, etc.) and highlight them
                        import re
                        annotation_pattern = r'#(\d+)'
                        for match in re.finditer(annotation_pattern, text_part):
                            start_pos = match.start()
                            end_pos = match.end()
                            annotation_text = match.group()
                            
                            # Apply yellow text on appropriate background for the annotation
                            if COLORSUPPORT:
                                if is_light_theme:
                                    # Dark yellow on light background for light theme
                                    annotation_color_pair = get_syntax_color_pair((180, 140, 0), (240, 240, 240))
                                else:
                                    # Bright yellow on dark background for dark themes
                                    annotation_color_pair = get_syntax_color_pair((255, 255, 0), (32, 32, 32))
                                if annotation_color_pair > 0:
                                    # Overwrite the annotation with yellow color - but ONLY if it's within bounds
                                    for i, char in enumerate(annotation_text):
                                        char_pos = start_pos + i
                                        if char_pos < cols - x:  # CRITICAL: Only render if within screen bounds
                                            try:
                                                pad.addstr(n, char_pos, char, curses.color_pair(annotation_color_pair))
                                            except:
                                                pass  # Ignore if we can't write at this position
                    except Exception as e:
                        # If color parsing fails, just display as regular text
                        apply_search_highlighting(pad, n, 0, text_part)
                else:
                    # No color info, but still a syntax highlighted line - add background
                    if COLORSUPPORT:
                        # The dark background was already filled above
                        pass
                    # Display the text (which might be empty for blank lines)
                    if content.strip():
                        apply_search_highlighting(pad, n, 0, content)
            elif line.startswith("URL_HL:"):
                # URL highlighted line
                import re
                content = line[7:]  # Remove "URL_HL:" prefix
                
                # Find all URLs in the line using central function
                url_data = find_urls_in_text(content)
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
                if urls:
                    current_pos = 0
                    for url_match in urls:
                        # Add text before URL
                        if url_match.start() > current_pos:
                            before_text = content[current_pos:url_match.start()]
                            pad.addstr(n, current_pos, before_text)
                        
                        # Add URL with scheme-appropriate color (no underline for better readability)
                        url_text = url_match.group()
                        
                        # Detect color scheme using pad background
                        current_bg_pair = curses.pair_number(pad.getbkgd())
                        is_light_scheme = current_bg_pair == 3  # Light scheme is color pair 3
                        
                        # Just use attributes without custom colors to avoid black background
                        # Use underline for all URLs regardless of theme
                        pad.addstr(n, url_match.start(), url_text, curses.A_UNDERLINE)
                        
                        current_pos = url_match.end()
                    
                    # Add any remaining text after the last URL
                    if current_pos < len(content):
                        remaining_text = content[current_pos:]
                        pad.addstr(n, current_pos, remaining_text)
                else:
                    # No URLs found, display as regular text
                    apply_search_highlighting(pad, n, 0, content)
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
                    # Check if content is already a URL_HL line
                    if content.startswith("URL_HL:"):
                        # Handle nested URL_HL within table background
                        url_content = content[7:]  # Remove "URL_HL:" prefix
                        # Find URLs in the content using central function
                        url_data = find_urls_in_text(url_content)
                    else:
                        # Check for URLs within regular table content and highlight them
                        import re
                        url_data = find_urls_in_text(content)
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
                    if urls:
                        # Handle URLs within table background
                        current_pos = 0
                        # Use the appropriate content for text positioning
                        text_content = url_content if content.startswith("URL_HL:") else content
                        for url_match in urls:
                            # Add text before URL
                            if url_match.start() > current_pos:
                                before_text = text_content[current_pos:url_match.start()]
                                pad.addstr(n, current_pos, before_text)
                            
                            # Add URL with same highlighting as regular URLs (just underline, no background)
                            url_text = url_match.group()
                            # Reset to normal colors and just add underline (no table background for URLs)
                            try:
                                # Clear the table background for this URL by using normal color pair
                                pad.addstr(n, url_match.start(), url_text, curses.color_pair(0) | curses.A_UNDERLINE)
                            except:
                                # Fallback to just underline if color reset fails
                                pad.addstr(n, url_match.start(), url_text, curses.A_UNDERLINE)
                            
                            current_pos = url_match.end()
                        
                        # Add remaining text
                        if current_pos < len(text_content):
                            remaining_text = text_content[current_pos:]
                            pad.addstr(n, current_pos, remaining_text)
                    else:
                        # No URLs, just add the text (use appropriate content)
                        display_content = url_content if content.startswith("URL_HL:") else content
                        apply_search_highlighting(pad, n, 0, display_content)
            elif line.startswith("HEADER:"):
                # Header line - add underline formatting only to the actual text
                content = line[7:]  # Remove "HEADER:" prefix
                if content.strip():
                    # Find the start and end of actual text (non-whitespace)
                    text_start = len(content) - len(content.lstrip())
                    text_end = len(content.rstrip())
                    
                    # Add leading whitespace without formatting
                    if text_start > 0:
                        apply_search_highlighting(pad, n, 0, content[:text_start])
                    
                    # Add the actual header text with underline + bold
                    header_text = content[text_start:text_end]
                    if header_text:
                        pad.addstr(n, text_start, header_text, curses.A_UNDERLINE | curses.A_BOLD)
                    
                    # Add trailing whitespace without formatting (if any)
                    if text_end < len(content):
                        apply_search_highlighting(pad, n, text_end, content[text_end:])
            elif line.startswith("CAPTION:"):
                # Caption line - format with italic style and centered
                content = line[8:]  # Remove "CAPTION:" prefix
                if content.strip():
                    # Center the caption text
                    centered_content = content.strip().center(cols)
                    try:
                        # Add italic formatting if supported, otherwise just dim
                        if hasattr(curses, 'A_ITALIC'):
                            pad.addstr(n, 0, centered_content, curses.A_ITALIC)
                        else:
                            pad.addstr(n, 0, centered_content, curses.A_DIM)
                    except:
                        # Fallback to normal text if formatting fails
                        apply_search_highlighting(pad, n, 0, centered_content)
            else:
                # Regular text line
                display_line = line[9:] if line.startswith("IMG_LINE:") else line
                apply_search_highlighting(pad, n, 0, str(display_line))
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

    global INITIAL_HELP_SHOWN
    
    countstring = ""
    svline = "dontsave"
    show_initial_help = not INITIAL_HELP_SHOWN  # Only show if not previously shown
    help_message_start_time = time.time()  # Track when help message was first shown
    while True:
        if countstring == "":
            count = 1
        else:
            count = int(countstring)
        if k in range(48, 58): # i.e., k is a numeral
            countstring = countstring + chr(k)
        else:
            if k in QUIT:
                if k == ord('q') and countstring != "":
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
            elif k in PAGE_UP:
                if y == 0 and index != 0:
                    return -1, width, -rows, None
                else:
                    new_y = pgup(y, rows, LINEPRSRV, count)
                    # Skip backward through empty pages if the new position is empty
                    if is_page_empty(src_lines, new_y, rows):
                        new_y = skip_empty_pages_backward(src_lines, new_y, rows)
                    y = new_y
            elif k in SCROLL_DOWN:
                if count > 1:
                    svline = y + rows - 1
                if y + count <= totlines - rows:
                    y += count
                elif y == totlines - rows and index != len(contents)-1:
                    return 1, width, 0, None
                else:
                    y = totlines - rows
            elif k in PAGE_DOWN:
                if totlines - y - LINEPRSRV > rows:
                    # y = pgdn(y, totlines, rows, LINEPRSRV, count)
                    new_y = y + rows - LINEPRSRV
                    # Skip forward through empty pages if the new position is empty
                    if is_page_empty(src_lines, new_y, rows):
                        new_y = skip_empty_pages_forward(src_lines, new_y, rows, totlines)
                    y = new_y
                elif index != len(contents)-1:
                    return 1, width, 0, None
            elif k in CH_NEXT:
                CURRENT_SEARCH_TERM = None  # Clear search when changing chapters
                if index + count < len(contents) - 1:
                    return count, width, 0, None
                if index + count >= len(contents) - 1:
                    return len(contents) - index - 1, width, 0, None
            elif k in CH_PREV:
                CURRENT_SEARCH_TERM = None  # Clear search when changing chapters
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
            elif k == BOOKMARKS:
                # Show bookmarks
                selected_bookmark = bookmarks(stdscr)
                if selected_bookmark == curses.KEY_RESIZE:
                    k = curses.KEY_RESIZE
                    continue
                elif selected_bookmark:
                    # User selected a bookmark - always return it (main loop will handle validation)
                    return selected_bookmark  # Return bookmark info to main loop
                
                # Refresh screen after dialog
                stdscr.clear()
                stdscr.refresh()
            elif k == SAVE_BOOKMARK:
                # Save current position as bookmark
                chapter_title = "Chapter ?"
                try:
                    if toc_src and index < len(toc_src):
                        chapter_title = toc_src[index]
                except:
                    pass
                position_pct = int((y/totlines) * 100) if totlines > 0 else 0
                add_bookmark(ebook, index, chapter_title, y, y/totlines)
                # Show brief confirmation
                stdscr.addstr(rows-1, 0, " Bookmark saved! ", curses.A_REVERSE)
                stdscr.refresh()
                curses.napms(1500)  # Show for 1.5 seconds
                
                # Refresh screen after dialog
                stdscr.clear()
                stdscr.refresh()
            # elif k == ord("0"):
            #     if width != 80 and cols - 2 >= 80:
            #         return 0, 80, 0, y/totlines
            #     else:
            #         return 0, cols - 2, 0, y/totlines
            elif k == ord("/"):
                # Use unified search dialog
                search_term = search_dialog(stdscr)
                if search_term:
                    CURRENT_SEARCH_TERM = search_term  # Store for highlighting
                    # DEBUG: Log that we set the search term
                    try:
                        with open('/tmp/search_debug.log', 'w') as f:
                            f.write(f"SEARCH DEBUG: Set CURRENT_SEARCH_TERM to '{search_term}'\n")
                    except:
                        pass
                    
                    # Initialize whole-book search tracking
                    WHOLE_BOOK_SEARCH_START = index
                    WHOLE_BOOK_SEARCH_VISITED = [index]
                    
                    # Find first occurrence of search term in current chapter
                    found_in_chapter = False
                    for i, line in enumerate(src_lines[y:], y):
                        if search_term.lower() in line.lower():
                            y = i
                            found_in_chapter = True
                            break
                    
                    if found_in_chapter:
                        # Found in current chapter - reset whole-book search and stay here
                        WHOLE_BOOK_SEARCH_START = None
                        WHOLE_BOOK_SEARCH_VISITED = []
                        return 0, width, y, y/totlines if totlines > 0 else 0
                    else:
                        # Not found in current chapter, offer whole-book search
                        whole_book_result = offer_whole_book_search(stdscr, search_term, ebook, index, y, width)
                        if whole_book_result:
                            return whole_book_result
                        else:
                            # User said no to whole-book search
                            WHOLE_BOOK_SEARCH_START = None
                            WHOLE_BOOK_SEARCH_VISITED = []
                else:
                    CURRENT_SEARCH_TERM = None  # Clear search term if cancelled
            elif k == ord("u"):  # Open URL
                # Find URLs in visible area
                import re
                import subprocess
                import webbrowser
                
                urls = []
                seen_urls = set()  # Track URLs we've already found to avoid duplicates
                seen_domains = {}  # Track domain->url mapping to prefer https over http
                
                for n, i in enumerate(src_lines[y:y+rows]):
                    # First, find complete URLs with schemes using central function
                    url_data = find_urls_in_text(i)
                    complete_matches = []
                    for url, start, end in url_data:
                        class MockMatch:
                            def __init__(self, text, start, end):
                                self._text = text
                                self._start = start
                                self._end = end
                            def group(self): return self._text
                            def start(self): return self._start
                            def end(self): return self._end
                        complete_matches.append(MockMatch(url, start, end))
                    
                    covered_ranges = []  # Track character ranges covered by complete URLs
                    
                    for match in complete_matches:
                        url = match.group()
                        if '.' in url and len(url) > 5:
                            # Extract domain (without scheme) for deduplication
                            if url.startswith('https://'):
                                domain_part = url[8:]  # Remove 'https://'
                                scheme = 'https'
                            elif url.startswith('http://'):
                                domain_part = url[7:]   # Remove 'http://'
                                scheme = 'http'
                            else:
                                domain_part = url
                                scheme = None
                            
                            # Check if we've seen this domain before
                            if domain_part in seen_domains:
                                existing_url, existing_line = seen_domains[domain_part]
                                existing_scheme = 'https' if existing_url.startswith('https://') else 'http'
                                
                                # Prefer https over http
                                if scheme == 'https' and existing_scheme == 'http':
                                    # Replace http version with https version
                                    urls = [(u, ln) for u, ln in urls if u != existing_url]
                                    urls.append((url, n))
                                    seen_domains[domain_part] = (url, n)
                                    seen_urls.add(url)
                                    seen_urls.discard(existing_url)
                                elif scheme == 'http' and existing_scheme == 'https':
                                    # Skip http version, we already have https
                                    pass
                                # If both same scheme or no clear preference, skip duplicate
                            else:
                                # New domain, add it
                                urls.append((url, n))
                                seen_urls.add(url)
                                seen_domains[domain_part] = (url, n)
                            
                            covered_ranges.append((match.start(), match.end()))
                    
                    # Then, find URL fragments that aren't part of complete URLs
                    fragment_pattern = r'[a-zA-Z0-9._/\-~?&=#+%]+\.[a-zA-Z]{2,}[a-zA-Z0-9._/\-~?&=#+%]*'
                    fragment_matches = re.finditer(fragment_pattern, i)
                    
                    for match in fragment_matches:
                        # Check if this fragment overlaps with any complete URL
                        fragment_start, fragment_end = match.start(), match.end()
                        overlaps = any(start <= fragment_start < end or start < fragment_end <= end 
                                     for start, end in covered_ranges)
                        
                        if not overlaps:
                            fragment = match.group()
                            if '.' in fragment and len(fragment) > 5:
                                url = 'https://' + fragment
                                # Check domain deduplication for fragments too
                                if fragment not in seen_domains:
                                    urls.append((url, n))
                                    seen_urls.add(url)
                                    seen_domains[fragment] = (url, n)
                
                if urls:
                    if len(urls) == 1:
                        # Single URL found, open it directly
                        url_to_open = urls[0][0]
                        try:
                            # Try to use xdg-open (Linux), open (macOS), or start (Windows)
                            # Redirect stdout and stderr to suppress debug output
                            if os.name == 'posix':
                                subprocess.run(['xdg-open', url_to_open], 
                                             stdout=subprocess.DEVNULL, 
                                             stderr=subprocess.DEVNULL,
                                             check=False)
                            elif os.name == 'nt':
                                subprocess.run(['start', url_to_open], shell=True, 
                                             stdout=subprocess.DEVNULL, 
                                             stderr=subprocess.DEVNULL,
                                             check=False)
                            else:
                                webbrowser.open(url_to_open)
                        except:
                            # Fallback to webbrowser module
                            webbrowser.open(url_to_open)
                    else:
                        # Multiple URLs found, deduplicate first
                        # First, deduplicate by cleaned display URL
                        unique_urls = []
                        seen_display_urls = set()
                        
                        for url, line_num in urls[:9]:  # Process up to 9 URLs
                            # Prefer https over http and clean up display
                            clean_url = url.replace('http://', 'https://', 1) if url.startswith('http://') else url
                            # Remove any trailing punctuation for display
                            clean_url = clean_url.rstrip('.,;:!?)]}>') 
                            
                            # Only add if we haven't seen this cleaned URL before
                            if clean_url not in seen_display_urls:
                                seen_display_urls.add(clean_url)
                                unique_urls.append((url, line_num, clean_url))
                        
                        # If deduplication resulted in only one unique URL, open it directly
                        if len(unique_urls) == 1:
                            url_to_open = unique_urls[0][0]
                            try:
                                # Try to use xdg-open (Linux), open (macOS), or start (Windows)
                                # Redirect stdout and stderr to suppress debug output
                                if os.name == 'posix':
                                    subprocess.run(['xdg-open', url_to_open], 
                                                 stdout=subprocess.DEVNULL, 
                                                 stderr=subprocess.DEVNULL,
                                                 check=False)
                                elif os.name == 'nt':
                                    subprocess.run(['start', url_to_open], shell=True, 
                                                 stdout=subprocess.DEVNULL, 
                                                 stderr=subprocess.DEVNULL,
                                                 check=False)
                                else:
                                    webbrowser.open(url_to_open)
                            except:
                                # Fallback to webbrowser module
                                webbrowser.open(url_to_open)
                        else:
                            # Multiple unique URLs, show selection menu
                            stdscr.clear()
                            stdscr.addstr(0, 0, "Multiple URLs found. Select one to open:")
                            for i, (original_url, line_num, clean_url) in enumerate(unique_urls):
                                # Truncate very long URLs for display
                                display_url = clean_url if len(clean_url) < 60 else clean_url[:57] + "..."
                                stdscr.addstr(i + 2, 0, f"{i+1}. {display_url}")
                            
                            # Update the urls list to use the unique ones for selection
                            urls = [(original_url, line_num) for original_url, line_num, _ in unique_urls]
                            stdscr.addstr(len(urls) + 3, 0, "Press 1-9 to open a URL, or any other key to cancel")
                            stdscr.refresh()
                            
                            choice = stdscr.getch()
                            
                            # Exit on resize - return to main reader immediately
                            if choice == curses.KEY_RESIZE:
                                # Don't clear screen, let main reader handle redraw
                                k = curses.KEY_RESIZE
                            elif ord('1') <= choice <= ord('9') and choice - ord('1') < len(urls):
                                url_to_open = urls[choice - ord('1')][0]
                                try:
                                    if os.name == 'posix':
                                        subprocess.run(['xdg-open', url_to_open], 
                                                     stdout=subprocess.DEVNULL, 
                                                     stderr=subprocess.DEVNULL,
                                                     check=False)
                                    else:
                                        webbrowser.open(url_to_open)
                                except:
                                    webbrowser.open(url_to_open)
                            # Clear screen and return to normal display
                            stdscr.clear()
                            stdscr.refresh()
                            # Force pad refresh to redraw the content
                            try:
                                pad.refresh(y,0, 0,x, rows-1,x+width)
                            except curses.error:
                                pass
            elif k == ord("i"):  # Open image
                # Find only images that are visible or overlapping with current viewport
                import re
                import subprocess
                import tempfile
                
                # Get visible/overlapping images instead of all chapter images
                visible_images = get_visible_images(src_lines, imgs, y, rows, image_line_map)
                
                if visible_images:
                    if len(visible_images) == 1:
                        # Single image found, open it directly
                        img_path = visible_images[0][0]
                        open_image_in_system_viewer(ebook, chpath, img_path)
                    else:
                        # Multiple images found, show simple text list to choose from
                        # Build list of image descriptions with filename, alt, caption, figure info
                        image_choices = []
                        for i, (img_path, line_num, img_idx) in enumerate(visible_images):
                            # Get enhanced label with all available info
                            label = get_enhanced_image_label(img_path, img_idx, img_alts, src_lines, line_num)
                            
                            # DEBUG: Log what's happening
                            try:
                                with open('/tmp/termbook_debug.log', 'a') as f:
                                    f.write(f"DEBUG: Image {i}: {img_path}\n")
                                    f.write(f"  line_num: {line_num}, img_idx: {img_idx}\n")
                                    f.write(f"  label: '{label}'\n")
                                    f.write(f"  img_alts[{img_idx}]: '{img_alts[img_idx] if img_idx < len(img_alts) else 'OUT_OF_RANGE'}'\n")
                            except:
                                pass
                            
                            # Add filename for clarity
                            filename = os.path.basename(img_path)
                            full_desc = f"{filename}"
                            if label and label != filename:
                                full_desc += f" - {label}"
                            
                            image_choices.append(full_desc)
                        
                        # Show selection dialog
                        selected = selection_dialog(stdscr, "Select Image to Open:", image_choices, 
                                                  help_text="Enter: Open | q: Cancel")
                        
                        if selected is not None and selected < len(visible_images):
                            # Open selected image
                            img_path = visible_images[selected][0]
                            open_image_in_system_viewer(ebook, chpath, img_path)
                else:
                    # No visible images found - show brief message
                    stdscr.addstr(rows - 1, 0, " No images visible on this screen ", curses.A_REVERSE)
                    stdscr.refresh()
                    curses.napms(1500)  # Show for 1.5 seconds
                    # Clear the message
                    stdscr.addstr(rows - 1, 0, " " * min(35, cols))
                    stdscr.refresh()
                    # Redraw content
                    try:
                        pad.refresh(y,0, 0,x, rows-1,x+width)
                    except curses.error:
                        pass
            elif k == COLORSWITCH and COLORSUPPORT:
                # Simple cycling: 1->2->3->1 (default->dark->light->default)
                current_color = curses.pair_number(stdscr.getbkgd())
                next_color = (current_color % 3) + 1
                stdscr.bkgd(curses.color_pair(next_color))
                return 0, width, y, None
            elif k == curses.KEY_RESIZE:
                # Clear any active modals on resize
                Modal.handle_resize()
                
                savestate(ebook.path, index, width, y, y/totlines)
                # Handle resize immediately - keep it simple
                if sys.platform == "win32":
                    curses.resize_term(rows, cols)
                    rows, cols = stdscr.getmaxyx()
                else:
                    rows, cols = stdscr.getmaxyx()
                    curses.resize_term(rows, cols)
                if cols < 22 or rows < 12:
                    sys.exit("ERR: Screen was too small (min 22cols x 12rows).")
                
                # Calculate new width - be more generous with expansion
                new_width = max(min(cols - 4, 120), 40)  # Between 40-120 chars, leave 4 char margin
                
                # Visual cue: show resize info briefly
                try:
                    stdscr.clear()
                    stdscr.addstr(0, 0, f"Resizing ({cols}x{rows}), please wait...")
                    stdscr.refresh()
                    time.sleep(0.5)  # Show briefly but long enough to read
                except:
                    pass
                
                # Always re-render on resize
                return 0, new_width, 0, y/totlines
            countstring = ""

        if svline != "dontsave":
            pad.chgat(svline, 0, width, curses.A_UNDERLINE)
        try:
            stdscr.clear()
            stdscr.addstr(0, 0, countstring)
            
            # Add debug info if --debug flag is used
            if DEBUG_MODE:
                # Handle None values safely
                pctg_str = f"{pctg:.1f}%" if pctg is not None else "0.0%"
                debug_info = f"DEBUG: Ch {index+1}/{len(contents)} | Pos {y}/{totlines} ({pctg_str}) | Built {__build_time__}"
                try:
                    stdscr.addstr(1, 0, debug_info[:cols-1], curses.A_DIM)  # Show on line 2, truncate if too long
                except:
                    pass  # Ignore if we can't fit the debug line
            
            stdscr.refresh()
            
            # Check if URLs or images are visible to reserve bottom line
            has_urls = check_urls_in_visible_area(src_lines, y, rows)
            has_images = check_images_in_visible_area(src_lines, y, rows)
            
            # Adjust pad positioning if debug mode is active (debug takes up one more line)
            pad_start_row = 2 if DEBUG_MODE else 1
            # Reserve bottom line for hint if URLs or images are present
            pad_end_row = rows - 2 if (has_urls or has_images) else rows - 1
            available_rows = pad_end_row - pad_start_row + 1
            
            if totlines - y < available_rows:
                pad.refresh(y,0, pad_start_row,x, totlines-y+pad_start_row-1,x+width)
            else:
                pad.refresh(y,0, pad_start_row,x, pad_end_row,x+width)
        except curses.error:
            pass
        
        # Show persistent hint AFTER pad refresh (post-reader) or initial help message
        if has_urls or has_images:
            show_persistent_hint(stdscr, rows, cols, has_urls, has_images)
            stdscr.refresh()
        elif show_initial_help:
            # Check if 5 seconds have passed since help message was shown
            if time.time() - help_message_start_time > 5.0:
                show_initial_help = False
                INITIAL_HELP_SHOWN = True  # Mark as dismissed globally
                # Clear the bottom line by refreshing the screen content
                stdscr.clear()
                stdscr.refresh()
                try:
                    if totlines - y < available_rows:
                        pad.refresh(y,0, pad_start_row,x, totlines-y+pad_start_row-1,x+width)
                    else:
                        pad.refresh(y,0, pad_start_row,x, pad_end_row,x+width)
                except curses.error:
                    pass
            else:
                # Still showing help message
                show_initial_help_message(stdscr, rows, cols)
                stdscr.refresh()
        
        # Use a timeout for getch so we can check the timer periodically
        pad.timeout(1000)  # 1 second timeout
        k = pad.getch()
        pad.timeout(-1)  # Reset to blocking
        
        # Handle timeout (no key pressed)
        if k == -1:  # Timeout occurred
            continue  # Go back to check timer and redraw
        
        # Clear initial help message on any actual key press
        if show_initial_help:
            show_initial_help = False
            INITIAL_HELP_SHOWN = True  # Mark as dismissed globally
            # Clear the bottom line by refreshing the screen content
            stdscr.clear()
            stdscr.refresh()
            try:
                if totlines - y < available_rows:
                    pad.refresh(y,0, pad_start_row,x, totlines-y+pad_start_row-1,x+width)
                else:
                    pad.refresh(y,0, pad_start_row,x, pad_end_row,x+width)
            except curses.error:
                pass
            
        if svline != "dontsave":
            pad.chgat(svline, 0, width, curses.A_NORMAL)
            svline = "dontsave"


def preread(stdscr, file):
    global COLORSUPPORT
    
    # Show loading message immediately
    try:
        stdscr.clear()
        stdscr.addstr(0, 0, "Loading...")
        stdscr.refresh()
    except:
        pass

    curses.start_color()  # Enable color support
    curses.use_default_colors()
    try:
        curses.init_pair(1, -1, -1)
        curses.init_pair(2, DARK[0], DARK[1])
        curses.init_pair(3, LIGHT[0], LIGHT[1])
        # Set initial color scheme to 1 (default)
        stdscr.bkgd(curses.color_pair(1))
        COLORSUPPORT = True
        
        # Initialize smart color palette for image rendering
        init_smart_color_palette()
        
        # Pre-allocate syntax highlighting color pairs
        init_syntax_color_pairs()
        
    except:
        COLORSUPPORT  = False

    stdscr.keypad(True)
    curses.curs_set(0)
    stdscr.clear()
    rows, cols = stdscr.getmaxyx()
    stdscr.refresh()

    # Show loading message for EPUB processing
    try:
        stdscr.clear()
        stdscr.addstr(0, 0, "Loading EPUB...")
        stdscr.refresh()
    except:
        pass

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
        result = reader(stdscr, epub, idx, width, y, pctg)
        
        # Check if result is a bookmark (dict) or normal navigation (tuple)
        if isinstance(result, dict):
            # User selected a bookmark - switch to that book
            bookmark = result
            bookmark_path = bookmark.get('path')
            
            
            # Always clear screen when returning from bookmark selection
            stdscr.clear()
            stdscr.refresh()
            
            if bookmark_path and os.path.exists(bookmark_path) and bookmark_path != epub.path:
                # Switch to the bookmarked book by restarting termbook
                try:
                    import sys
                    import json
                    
                    # Save the bookmark position to a temporary state
                    bookmark_idx = bookmark.get('chapter_index', 0)
                    bookmark_y = bookmark.get('position', 0)
                    bookmark_pctg = bookmark.get('percentage', 0.0)
                    
                    # Show switching message
                    rows, cols = stdscr.getmaxyx()
                    book_name = os.path.basename(bookmark_path)
                    stdscr.addstr(rows-1, 0, f" Opening: {book_name[:50]}... ", curses.A_REVERSE)
                    stdscr.refresh()
                    curses.napms(1000)  # Show for 1 second
                    
                    # Store the bookmark position in the book's state
                    if bookmark_path not in STATE:
                        STATE[bookmark_path] = {}
                    STATE[bookmark_path]["index"] = bookmark_idx
                    STATE[bookmark_path]["y"] = bookmark_y
                    STATE[bookmark_path]["pctg"] = bookmark_pctg
                    STATE[bookmark_path]["width"] = width
                    
                    # Save state
                    try:
                        with open(STATEFILE, 'w') as f:
                            json.dump(STATE, f, indent=2)
                    except:
                        pass
                    
                    # Exit and restart with the new book
                    os.execv(sys.executable, [sys.executable] + [sys.argv[0]] + [bookmark_path])
                    
                except Exception as e:
                    # Failed to restart, skip this bookmark
                    continue
            elif bookmark_path == epub.path:
                # Same book, jump to bookmarked position
                bookmark_idx = bookmark.get('chapter_index', 0)
                # Validate chapter index is within bounds
                idx = max(0, min(bookmark_idx, len(epub.contents) - 1)) if epub.contents else 0
                y = bookmark.get('position', 0)
                pctg = bookmark.get('percentage', 0.0)
                # No chapter change animation needed
                continue
            else:
                # Bookmark path doesn't exist, continue normally
                continue
        else:
            # Normal navigation result
            incr, width, y, pctg = result
        
        # Show loading animation for chapter transitions
        if incr != 0:  # Chapter navigation occurred
            import time
            global LOADING_IN_PROGRESS
            
            LOADING_IN_PROGRESS = True
            
            # Start loading animation with spectrum effect
            animation_data = show_loading_animation(stdscr, "Loading chapter...")
            message, start_col, center_row, spectrum_colors = animation_data
            
            # Animate rolling spectrum effect while changing chapter
            animation_step = 0
            for _ in range(12):  # Show animation for a brief moment  
                update_loading_animation(stdscr, message, start_col, center_row, spectrum_colors, animation_step)
                animation_step += 1
                time.sleep(0.08)  # Slightly longer delay for better color visibility
            
            LOADING_IN_PROGRESS = False
            
            # Clear the screen after loading animation to prepare for new content
            stdscr.clear()
            stdscr.refresh()
        
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
        print("Copyright (c) 2025", __author__)
        print(__url__)
        sys.exit()

    # Check for debug flag
    global DEBUG_MODE
    DEBUG_MODE = len({"--debug"} & set(args)) != 0

    if len({"--clean", "--reset"} & set(args)) != 0:
        # Clean up all saved state files
        cleaned_files = []
        
        # Check for state files in various locations
        state_locations = []
        bookmark_locations = []
        if os.getenv("HOME"):
            state_locations.append(os.path.join(os.getenv("HOME"), ".termbook"))
            state_locations.append(os.path.join(os.getenv("HOME"), ".config", "termbook", "config"))
            bookmark_locations.append(os.path.join(os.getenv("HOME"), ".termbook_bookmarks.json"))
            bookmark_locations.append(os.path.join(os.getenv("HOME"), ".config", "termbook", "bookmarks.json"))
        elif os.getenv("USERPROFILE"):
            state_locations.append(os.path.join(os.getenv("USERPROFILE"), ".termbook"))
            bookmark_locations.append(os.path.join(os.getenv("USERPROFILE"), ".termbook_bookmarks.json"))
        
        # Clean state files
        for state_file in state_locations:
            if os.path.exists(state_file):
                try:
                    os.remove(state_file)
                    cleaned_files.append(state_file)
                except Exception as e:
                    print(f"Warning: Could not remove {state_file}: {e}")
        
        # Clean bookmark files
        for bookmark_file in bookmark_locations:
            if os.path.exists(bookmark_file):
                try:
                    os.remove(bookmark_file)
                    cleaned_files.append(bookmark_file)
                except Exception as e:
                    print(f"Warning: Could not remove {bookmark_file}: {e}")
        
        if cleaned_files:
            print("Cleaned up the following state files:")
            for f in cleaned_files:
                print(f"  - {f}")
            print("\nTermbook has been reset to a fresh state.")
            print("All bookmarks and reading positions have been removed.")
        else:
            print("No state files found. Termbook is already in a fresh state.")
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
            parser = HTMLtoLines(dump_mode=True)
            try:
                parser.feed(content)
                parser.close()
            except:
                pass
            src_lines, imgs, img_alts = parser.get_lines()
            # sys.stdout.reconfigure(encoding="utf-8")  # Python>=3.7
            for j in src_lines:
                sys.stdout.buffer.write((j+"\n").encode("utf-8"))
        sys.exit()

    else:
        if termc < 22 or termr < 12:
            sys.exit("ERR: Screen was too small (min 22cols x 12rows).")
        curses.wrapper(preread, file)


if __name__ == "__main__":
    main()
