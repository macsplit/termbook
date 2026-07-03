"""Search: in-text highlighting, the search prompt dialog, and whole-book search."""

import re
import curses

from termbook import state
from termbook.text_render import find_urls_in_text
from termbook.ui.dialogs import Modal


def check_urls_in_visible_area(src_lines, y, rows):
    """Check if there are URLs in the currently visible area."""
    # Check visible lines for URLs
    for line in src_lines[y:y+rows]:
        if find_urls_in_text(line):
            return True
    return False



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
    
    if not state.CURRENT_SEARCH_TERM or not text:
        # No search term or no text - just render normally
        try:
            pad.addstr(n, x, text, default_attr)
        except curses.error:
            pass
        return
        
    # Apply search highlighting
    import re
    search_pattern = re.escape(state.CURRENT_SEARCH_TERM)
    last_pos = 0

    for match in re.finditer(search_pattern, text, re.IGNORECASE):
        # Add text before match
        if match.start() > last_pos:
            before_text = text[last_pos:match.start()]
            try:
                pad.addstr(n, x + last_pos, before_text, default_attr)
            except curses.error:
                pass
        
        # Add highlighted match
        match_text = match.group()
        try:
            if state.COLORSUPPORT:
                # Create search highlight color
                search_color_pair = 10  # Use a low reserved pair for search
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
                except curses.error:
                    # Fallback to reverse video
                    pad.addstr(n, x + match.start(), match_text, curses.A_REVERSE | curses.A_BOLD)
            else:
                pad.addstr(n, x + match.start(), match_text, curses.A_REVERSE | curses.A_BOLD)
        except curses.error:
            pass
            
        last_pos = match.end()
    
    # Add remaining text after last match
    if last_pos < len(text):
        remaining_text = text[last_pos:]
        try:
            pad.addstr(n, x + last_pos, remaining_text, default_attr)
        except curses.error:
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

