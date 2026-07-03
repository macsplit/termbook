"""Curses dialog widgets: the Modal system, selection lists, and help screen."""

import curses

from termbook import state


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
        except curses.error:
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
            if state.COLORSUPPORT:
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
        "n          - Next chapter (search: next)",
        "p          - Previous chapter (search: prev)",
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

