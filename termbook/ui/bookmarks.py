"""Bookmark and reading-state persistence, and the bookmarks dialog."""

import os
import re
import sys
import json
import shutil
import curses
import datetime

from termbook import state
from termbook.ui.dialogs import Modal, format_help_text_with_colors

BOOKMARKSFILE = ""  # path to the global bookmarks JSON file
GLOBAL_BOOKMARKS = []  # list of global bookmarks


def loadstate():
    global BOOKMARKSFILE
    if os.getenv("HOME") is not None:
        state.STATEFILE = os.path.join(os.getenv("HOME"), ".termbook")
        if os.path.isdir(os.path.join(os.getenv("HOME"), ".config")):
            configdir = os.path.join(os.getenv("HOME"), ".config", "termbook")
            os.makedirs(configdir, exist_ok=True)
            if os.path.isfile(state.STATEFILE):
                if os.path.isfile(os.path.join(configdir, "config")):
                    os.remove(os.path.join(configdir, "config"))
                shutil.move(state.STATEFILE, os.path.join(configdir, "config"))
            state.STATEFILE = os.path.join(configdir, "config")
            BOOKMARKSFILE = os.path.join(configdir, "bookmarks.json")
        else:
            BOOKMARKSFILE = os.path.join(os.getenv("HOME"), ".termbook_bookmarks.json")
    elif os.getenv("USERPROFILE") is not None:
        state.STATEFILE = os.path.join(os.getenv("USERPROFILE"), ".termbook")
        BOOKMARKSFILE = os.path.join(os.getenv("USERPROFILE"), ".termbook_bookmarks.json")
    else:
        state.STATEFILE = os.devnull
        BOOKMARKSFILE = os.devnull

    if os.path.exists(state.STATEFILE):
        with open(state.STATEFILE, "r") as f:
            state.STATE = json.load(f)
    
    # Load and clean up bookmarks
    load_bookmarks()
    
    # Note: URL hint is now persistent and shown whenever URLs are visible


def savestate(file, index, width, pos, pctg ):
    for i in state.STATE:
        state.STATE[i]["lastread"] = str(0)
    state.STATE[file]["lastread"] = str(1)
    state.STATE[file]["index"] = str(index)
    state.STATE[file]["width"] = str(width)
    state.STATE[file]["pos"] = str(pos)
    state.STATE[file]["pctg"] = str(pctg)
    
    # Note: URL hint is now persistent and shown whenever URLs are visible
    
    with open(state.STATEFILE, "w") as f:
        json.dump(state.STATE, f, indent=4)


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
    except Exception as e:
        if state.DEBUG_MODE:
            print(f"Could not load bookmarks from {BOOKMARKSFILE}: {e}", file=sys.stderr)
        GLOBAL_BOOKMARKS = []

def save_bookmarks():
    """Save global bookmarks to file"""
    try:
        os.makedirs(os.path.dirname(BOOKMARKSFILE), exist_ok=True)
        with open(BOOKMARKSFILE, 'w', encoding='utf-8') as f:
            import json
            json.dump(GLOBAL_BOOKMARKS, f, indent=2, ensure_ascii=False)
    except Exception as e:
        if state.DEBUG_MODE:
            print(f"Could not save bookmarks to {BOOKMARKSFILE}: {e}", file=sys.stderr)


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
    except (IndexError, TypeError, AttributeError):
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
            except (ValueError, TypeError):
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
                        except (ValueError, TypeError):
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

