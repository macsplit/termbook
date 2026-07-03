"""The main reading UI: chapter rendering, the reader event loop, and startup.

This is the largest module -- it owns the curses event loop (reader()),
pre-read setup (preread()), and all of the small per-feature helpers (resize
handling, jump-to-page helpers, the image/URL visibility hints, media
launching) that only reader()/preread() use. Phase 4.2 (not done in this
pass) would further decompose reader()'s ~1000-line elif chain into a
keymap/dispatch table.
"""

import os
import re
import sys
import time
import json
import shutil
import curses
import textwrap
import threading
import atexit
import tempfile
import subprocess
import webbrowser
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    Image = None

from termbook import state
from termbook import __build_time__
from termbook.epub import Epub, dots_path
from termbook.text_render import HTMLtoLines, find_urls_in_text
from termbook.colors import (
    get_color_pair, get_color_pair_with_reversal, get_syntax_color_pair,
    rgb_to_color_index, find_closest_palette_color, init_smart_color_palette,
    init_syntax_color_pairs,
)
from termbook.image_render import (
    render_images_inline, render_image_with_fabulous, render_image_with_quarter_blocks,
    PIL_AVAILABLE,
)
from termbook.ui.dialogs import Modal, help as show_help_dialog
from termbook.ui.search import (
    apply_search_highlighting, check_urls_in_visible_area, offer_whole_book_search,
    search_dialog,
)
from termbook.ui.bookmarks import (
    add_bookmark, bookmarks, load_bookmarks, loadstate, savestate,
)

# key bindings (used throughout the reader loop)
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

# colorscheme: (fg, bg), -1 is default terminal fg/bg
DARK = (252, 235)
LIGHT = (239, 223)

LINEPRSRV = 0  # default = 2

# module-confined state (not shared with any other module -- see
# termbook/state.py's docstring for which globals ARE cross-module)
VWR = None
SEARCHPATTERN = None
INITIAL_HELP_SHOWN = False
RESIZE_REQUESTED = False
RESIZE_TIMER = None
RESIZE_DELAY = 1.0
LAST_TERMINAL_SIZE = (0, 0)
LOADING_IN_PROGRESS = False
WHOLE_BOOK_SEARCH_START = None
WHOLE_BOOK_SEARCH_VISITED = []
JUMPLIST = {}


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
        if state.COLORSUPPORT:
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
            except curses.error:
                # Fallback to reverse video
                stdscr.addstr(rows - 1, start_col, message, curses.A_REVERSE)
        else:
            # No color support, use reverse video
            stdscr.addstr(rows - 1, start_col, message, curses.A_REVERSE)
    except curses.error:
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
        if state.COLORSUPPORT:
            if is_light_scheme:
                # Light scheme: use color pair 3 (designed for light backgrounds)
                stdscr.addstr(rows - 1, start_col, message, curses.color_pair(3))
            else:
                # Dark scheme: use default color pair 1 or 2
                stdscr.addstr(rows - 1, start_col, message, curses.color_pair(2))
        else:
            # No color support, use reverse video
            stdscr.addstr(rows - 1, start_col, message, curses.A_REVERSE)
    except curses.error:
        # Silently fail if screen dimensions are unstable during resize
        pass


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

    if not imgs:
        return []

    visible_images = []
    seen_indices = set()

    # Define viewport with small overlap
    viewport_start = max(0, y - 2)
    viewport_end = min(len(src_lines), y + rows + 2)

    # Relax the length matching - allow small differences due to processing variations
    if image_line_map and abs(len(image_line_map) - len(src_lines)) <= 10:
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

            # Check for IMG_LINE: markers (rendered images)
            elif line.startswith("IMG_LINE:"):
                # Without mapping, we can't determine which specific image this is
                pass
        
        # If we found IMG_LINE markers but no [IMG:n] markers, try to identify which images
        # are visible by checking which IMG_LINE markers are in the viewport
        if not visible_images and imgs:
            # Look for IMG_LINE markers in viewport and try to determine which images they belong to
            for line_num in range(viewport_start, viewport_end):
                if line_num < len(src_lines) and src_lines[line_num].startswith("IMG_LINE:"):
                    # Without proper mapping, we can't determine which specific image this is
                    # So we don't add anything to avoid returning all chapter images
                    pass
    
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
            os.startfile(tmp_path)
        else:
            # Fallback for other platforms (macOS, etc.)
            subprocess.run(['open', tmp_path], 
                         stdout=subprocess.DEVNULL, 
                         stderr=subprocess.DEVNULL,
                         check=False)
        return True
    except Exception as e:
        if state.DEBUG_MODE:
            print(f"Could not open image in system viewer: {e}", file=sys.stderr)
        return False


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
    except curses.error:
        return False


def cleanup_resize_timer():
    """Cancel any pending resize timer on exit"""
    global RESIZE_TIMER
    if RESIZE_TIMER:
        RESIZE_TIMER.cancel()


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
            
            # Use Fabulous for improved image rendering, with fallback to quarter blocks
            color_lines = render_image_with_fabulous(img, max_width, max_height)
            if not color_lines:  # Fallback if Fabulous fails
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
            if state.DEBUG_MODE:
                print(f"Inline image display failed, falling back to external viewer: {e}", file=sys.stderr)
    
    # Fall back to external viewer for non-images or if display failed
    fd, path = tempfile.mkstemp(suffix=sfx)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(epub.file.read(src))
        if sys.platform == "win32":
            # "start" is a cmd.exe builtin, not a real executable; os.startfile
            # is the correct, injection-safe way to open it with the default handler.
            os.startfile(path)
        else:
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
        if state.COLORSUPPORT:
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
    except curses.error:
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
            if state.COLORSUPPORT:
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
                except curses.error:
                    # Final fallback to bold
                    stdscr.addstr(center_row, start_col + i, char, curses.A_BOLD)
            else:
                # No color support, just use bold
                stdscr.addstr(center_row, start_col + i, char, curses.A_BOLD)
        
        stdscr.refresh()
    except curses.error:
        pass  # Ignore any display errors


def reader(stdscr, ebook, index, width, y, pctg):
    global WHOLE_BOOK_SEARCH_START, WHOLE_BOOK_SEARCH_VISITED
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
    except Exception as e:
        if state.DEBUG_MODE:
            print(f"HTML parsing failed for {chpath}: {e}", file=sys.stderr)

    src_lines, imgs, img_alts = parser.get_lines(width)
    
    # Check if we're continuing a whole-book search
    if WHOLE_BOOK_SEARCH_START is not None and state.CURRENT_SEARCH_TERM:
        # Add current chapter to visited list
        if index not in WHOLE_BOOK_SEARCH_VISITED:
            WHOLE_BOOK_SEARCH_VISITED.append(index)
        
        # Search for term in this chapter
        found_in_chapter = False
        for i, line in enumerate(src_lines):
            if state.CURRENT_SEARCH_TERM.lower() in line.lower():
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
                stdscr.addstr(rows-1, 0, f" '{state.CURRENT_SEARCH_TERM}' not found in book ", curses.A_REVERSE)
                stdscr.refresh()
                curses.napms(2000)  # Show for 2 seconds
                WHOLE_BOOK_SEARCH_START = None
                WHOLE_BOOK_SEARCH_VISITED = []
                state.CURRENT_SEARCH_TERM = None
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
                        state.CURRENT_SEARCH_TERM = None
                        stdscr.addstr(rows-1, 0, " Search cancelled ", curses.A_REVERSE)
                        stdscr.refresh()
                        curses.napms(1000)  # Show briefly
                        return 0, width, y, y/totlines if totlines > 0 else 0
                except curses.error:
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

    if state.COLORSUPPORT:
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
                        if char != ' ' and state.COLORSUPPORT:
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
                            except curses.error:
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
                                        except curses.error:
                                            break  # Stop if we can't write anymore
                                    else:
                                        # Fallback to bold if color pair couldn't be created
                                        try:
                                            pad.addstr(n, char_idx, char, curses.A_BOLD)
                                        except curses.error:
                                            break  # Stop if we can't write anymore
                                else:
                                    # Invalid color format, use regular text
                                    try:
                                        pad.addstr(n, char_idx, char)
                                    except curses.error:
                                        break  # Stop if we can't write anymore
                            else:
                                # Regular text
                                try:
                                    pad.addstr(n, char_idx, char)
                                except curses.error:
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
                                    except curses.error:
                                        break  # Stop if we can't write anymore
                        
                        # Highlight search results with inverse video bright
                        if state.CURRENT_SEARCH_TERM:
                            import re
                            search_pattern = re.escape(state.CURRENT_SEARCH_TERM)  # Escape special regex chars
                            for match in re.finditer(search_pattern, text_part, re.IGNORECASE):
                                start_pos = match.start()
                                end_pos = match.end()
                                match_text = match.group()
                                
                                # Apply custom search highlighting based on color scheme
                                for char_idx, char in enumerate(match_text):
                                    abs_char_pos = start_pos + char_idx
                                    if abs_char_pos < len(text_part) and n + n_relative < rows and abs_char_pos < width:
                                        try:
                                            if state.COLORSUPPORT:
                                                # Simple but effective approach: create custom search highlight color on demand
                                                try:
                                                    # Determine current color scheme
                                                    current_bg_pair = curses.pair_number(pad.getbkgd())
                                                    is_light_scheme = current_bg_pair == 3  # Light scheme is color pair 3
                                                    
                                                    # Try to create a search highlight color pair
                                                    search_color_pair = 10  # Use a low reserved pair for search  # Use high pair number to avoid conflicts
                                                    
                                                    if is_light_scheme:
                                                        # Light mode: bright white text on black background
                                                        curses.init_pair(search_color_pair, curses.COLOR_WHITE, curses.COLOR_BLACK)
                                                    else:
                                                        # Dark modes: black text on bright green background (closest to fluorescent yellow-green)
                                                        curses.init_pair(search_color_pair, curses.COLOR_BLACK, curses.COLOR_GREEN)
                                                    
                                                    pad.addstr(n, abs_char_pos, char, curses.color_pair(search_color_pair) | curses.A_BOLD)
                                                except curses.error:
                                                    # If color pair creation fails, fall back to reverse video
                                                    pad.addstr(n, abs_char_pos, char, curses.A_REVERSE | curses.A_BOLD)
                                            else:
                                                # Fallback to just inverse and bold
                                                pad.addstr(n, abs_char_pos, char, curses.A_REVERSE | curses.A_BOLD)
                                        except curses.error:
                                            break  # Stop if we can't write anymore
                        
                        # Now look for annotation patterns (#1, #2, #3, etc.) and highlight them
                        import re
                        annotation_pattern = r'#(\d+)'
                        for match in re.finditer(annotation_pattern, text_part):
                            start_pos = match.start()
                            end_pos = match.end()
                            annotation_text = match.group()
                            
                            # Apply yellow text on appropriate background for the annotation
                            if state.COLORSUPPORT:
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
                                            except curses.error:
                                                pass  # Ignore if we can't write at this position
                    except Exception as e:
                        # If color parsing fails, just display as regular text
                        if state.DEBUG_MODE:
                            print(f"Syntax-highlight color parsing failed: {e}", file=sys.stderr)
                        apply_search_highlighting(pad, n, 0, text_part)
                else:
                    # No color info, but still a syntax highlighted line - add background
                    if state.COLORSUPPORT:
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
                if state.COLORSUPPORT:
                    # Create table background color pair (lighter than code blocks)
                    table_bg_pair = get_color_pair((220, 220, 220), (48, 48, 48))  # Light gray text on medium gray background
                    if table_bg_pair > 0:
                        # Fill from text start to right edge with table background
                        for bg_col in range(cols - x):
                            try:
                                pad.addstr(n, bg_col, " ", curses.color_pair(table_bg_pair))
                            except curses.error:
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
                            except curses.error:
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
                    except curses.error:
                        # Fallback to normal text if formatting fails
                        apply_search_highlighting(pad, n, 0, centered_content)
            else:
                # Regular text line
                display_line = line[9:] if line.startswith("IMG_LINE:") else line
                apply_search_highlighting(pad, n, 0, str(display_line))
        except curses.error:
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
                state.CURRENT_SEARCH_TERM = None  # Clear search when changing chapters
                if index + count < len(contents) - 1:
                    return count, width, 0, None
                if index + count >= len(contents) - 1:
                    return len(contents) - index - 1, width, 0, None
            elif k in CH_PREV:
                state.CURRENT_SEARCH_TERM = None  # Clear search when changing chapters
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
                k = show_help_dialog(stdscr)
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
                except (IndexError, TypeError):
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
                    state.CURRENT_SEARCH_TERM = search_term  # Store for highlighting

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
                    state.CURRENT_SEARCH_TERM = None  # Clear search term if cancelled
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
                                os.startfile(url_to_open)
                            else:
                                webbrowser.open(url_to_open)
                        except Exception as e:
                            # Fallback to webbrowser module
                            if state.DEBUG_MODE:
                                print(f"Could not open URL via system opener: {e}", file=sys.stderr)
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
                                    os.startfile(url_to_open)
                                else:
                                    webbrowser.open(url_to_open)
                            except Exception as e:
                                # Fallback to webbrowser module
                                if state.DEBUG_MODE:
                                    print(f"Could not open URL via system opener: {e}", file=sys.stderr)
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
                                except Exception as e:
                                    if state.DEBUG_MODE:
                                        print(f"Could not open URL via system opener: {e}", file=sys.stderr)
                                    webbrowser.open(url_to_open)
                            # Clear screen and return to normal display
                            stdscr.clear()
                            stdscr.refresh()
                            # Force pad refresh to redraw the content
                            try:
                                pad.refresh(y,0, 0,x, rows-1,x+width)
                            except curses.error:
                                pass
            elif k == ord("i"):
                visible_images = get_visible_images(src_lines, imgs, y, rows, image_line_map)

                if visible_images:
                    if len(visible_images) == 1:
                        # Single image, open directly
                        img_path = visible_images[0][0]
                        success = open_image_in_system_viewer(ebook, chpath, img_path)
                        if not success:
                            # Show error message
                            stdscr.addstr(rows-1, 0, " Could not open image ", curses.A_REVERSE)
                            stdscr.refresh()
                            curses.napms(1500)
                    else:
                        # Multiple images, show selection
                        stdscr.clear()
                        stdscr.addstr(0, 0, f"Found {len(visible_images)} images. Select one to open:")
                        for i, (img_path, line_num, img_idx) in enumerate(visible_images[:9]):
                            display_name = os.path.basename(img_path) if img_path else f"Image {img_idx + 1}"
                            if len(display_name) > 60:
                                display_name = display_name[:57] + "..."
                            stdscr.addstr(i + 2, 0, f"{i+1}. {display_name}")
                        
                        stdscr.addstr(len(visible_images) + 3, 0, "Press 1-9 to open an image, or any other key to cancel")
                        stdscr.refresh()
                        
                        choice = stdscr.getch()
                        if ord('1') <= choice <= ord('9') and choice - ord('1') < len(visible_images):
                            img_path = visible_images[choice - ord('1')][0]
                            success = open_image_in_system_viewer(ebook, chpath, img_path)
                            if not success:
                                stdscr.addstr(rows-1, 0, " Could not open image ", curses.A_REVERSE)
                                stdscr.refresh()
                                curses.napms(1500)
                    
                    # Redraw screen after external program closes
                    stdscr.clear()
                    stdscr.refresh()
                else:
                    # Show message to user
                    stdscr.addstr(rows-1, 0, " No images visible in current view ", curses.A_REVERSE)
                    stdscr.refresh()
                    curses.napms(1500)
            elif k == COLORSWITCH and state.COLORSUPPORT:
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
                except curses.error:
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
            if state.DEBUG_MODE:
                # Handle None values safely
                pctg_str = f"{pctg:.1f}%" if pctg is not None else "0.0%"
                debug_info = f"DEBUG: Ch {index+1}/{len(contents)} | Pos {y}/{totlines} ({pctg_str}) | Built {__build_time__}"
                try:
                    stdscr.addstr(1, 0, debug_info[:cols-1], curses.A_DIM)  # Show on line 2, truncate if too long
                except curses.error:
                    pass  # Ignore if we can't fit the debug line
            
            stdscr.refresh()
            
            # Check if URLs or images are visible to reserve bottom line
            has_urls = check_urls_in_visible_area(src_lines, y, rows)
            has_images = check_images_in_visible_area(src_lines, y, rows)
            
            # Adjust pad positioning if debug mode is active (debug takes up one more line)
            pad_start_row = 2 if state.DEBUG_MODE else 1
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
        
    # Show loading message immediately
    try:
        stdscr.clear()
        stdscr.addstr(0, 0, "Loading...")
        stdscr.refresh()
    except curses.error:
        pass

    curses.start_color()  # Enable color support
    curses.use_default_colors()
    try:
        curses.init_pair(1, -1, -1)
        curses.init_pair(2, DARK[0], DARK[1])
        curses.init_pair(3, LIGHT[0], LIGHT[1])
        # Set initial color scheme to 1 (default)
        stdscr.bkgd(curses.color_pair(1))
        state.COLORSUPPORT = True
        
        # Initialize smart color palette for image rendering
        init_smart_color_palette()
        
        # Pre-allocate syntax highlighting color pairs
        init_syntax_color_pairs()
        
    except Exception as e:
        if state.DEBUG_MODE:
            print(f"Color initialization failed: {e}", file=sys.stderr)
        state.COLORSUPPORT = False

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
    except curses.error:
        pass

    epub = Epub(file)

    # Calculate responsive width based on terminal size
    # Leave margin of 8 characters (4 on each side), cap at 100 columns
    margin = 8
    max_width = 100
    responsive_width = min(max_width, max(20, cols - margin))

    if epub.path in state.STATE:
        idx = int(state.STATE[epub.path]["index"])
        saved_width = int(state.STATE[epub.path]["width"])
        
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
            
        y = int(state.STATE[epub.path]["pos"])
        pctg = None
    else:
        state.STATE[epub.path] = {}
        idx = 0
        y = 0
        width = responsive_width
        pctg = None

    # Final adjustment if width is still too large for terminal
    if cols <= width + 4:
        width = cols - 4
        if "pctg" in state.STATE[epub.path]:
            pctg = float(state.STATE[epub.path]["pctg"])

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
                    if bookmark_path not in state.STATE:
                        state.STATE[bookmark_path] = {}
                    state.STATE[bookmark_path]["index"] = bookmark_idx
                    state.STATE[bookmark_path]["y"] = bookmark_y
                    state.STATE[bookmark_path]["pctg"] = bookmark_pctg
                    state.STATE[bookmark_path]["width"] = width
                    
                    # Save state
                    try:
                        with open(state.STATEFILE, 'w') as f:
                            json.dump(state.STATE, f, indent=2)
                    except OSError as e:
                        if state.DEBUG_MODE:
                            print(f"Could not save state before bookmark restart: {e}", file=sys.stderr)

                    # Exit and restart with the new book
                    os.execv(sys.executable, [sys.executable] + [sys.argv[0]] + [bookmark_path])

                except Exception as e:
                    # Failed to restart, skip this bookmark
                    if state.DEBUG_MODE:
                        print(f"Could not restart into bookmarked book {bookmark_path}: {e}", file=sys.stderr)
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
