"""Curses color-pair allocation: RGB-to-256-color mapping and pair caching.

Shared by image rendering and text/search/syntax-highlight drawing, all of
which need to turn an RGB color into a curses color pair.
"""

import curses
import os

from termbook import state

# Smart color palette system
_color_palette = []  # Pre-computed palette of color indices
_terminal_palette = []  # Indexed xterm-256 RGB palette
_color_pairs = {}    # Cache of created color pairs  
_image_cache = {}    # Cache processed images to avoid re-rendering on resize
_next_color_pair = 6  # Start after pre-defined reserved pairs (1-5)
_MAX_COLOR_PAIRS = 32000   # Safe limit - most terminals support 32768 or 65536
_SEARCH_PAIR_START = 32001  # Reserved pairs for search highlighting  
# Syntax highlighting pairs are now allocated dynamically
# All pairs are now allocated dynamically from the same pool


def reset_dynamic_color_pairs():
    """Reset the dynamic pair cache at safe full-redraw boundaries."""
    global _color_pairs, _next_color_pair
    _color_pairs = {}
    _next_color_pair = 6


def get_available_color_pair_budget():
    """Return the practical pair budget for dynamic color-pair allocation."""
    override = os.getenv("TERMBOOK_MAX_COLOR_PAIRS")
    if override:
        try:
            return max(16, int(override))
        except ValueError:
            pass

    terminal_pairs = getattr(curses, "COLOR_PAIRS", 0) or 0
    if terminal_pairs > 16:
        return max(16, terminal_pairs - 256)

    return _MAX_COLOR_PAIRS

def get_ui_color_pair(purpose="loading"):
    """Get a dedicated color pair for UI elements like loading messages."""
    return 1  # Default color pair (reliable)

def init_syntax_color_pairs():
    """Pre-allocate color pairs for syntax highlighting."""
    # Syntax highlighting pairs are now allocated dynamically as needed
    # No pre-allocation required
    pass

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


def _build_terminal_palette():
    """Build the RGB lookup table for the terminal's 256-color palette."""
    global _terminal_palette
    if _terminal_palette:
        return

    basic_colors = [
        (0, 0, 0),
        (128, 0, 0),
        (0, 128, 0),
        (128, 128, 0),
        (0, 0, 128),
        (128, 0, 128),
        (0, 128, 128),
        (192, 192, 192),
        (128, 128, 128),
        (255, 0, 0),
        (0, 255, 0),
        (255, 255, 0),
        (0, 0, 255),
        (255, 0, 255),
        (0, 255, 255),
        (255, 255, 255),
    ]
    palette = list(basic_colors)

    cube_levels = [0, 95, 135, 175, 215, 255]
    for r in cube_levels:
        for g in cube_levels:
            for b in cube_levels:
                palette.append((r, g, b))

    for level in range(24):
        gray = 8 + level * 10
        palette.append((gray, gray, gray))

    _terminal_palette = palette


def _rgb_distance(a, b):
    return (
        (a[0] - b[0]) * (a[0] - b[0])
        + (a[1] - b[1]) * (a[1] - b[1])
        + (a[2] - b[2]) * (a[2] - b[2])
    )

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
        _build_terminal_palette()

        target = (r, g, b)
        best_index = 0
        best_distance = float("inf")

        for idx, palette_rgb in enumerate(_terminal_palette):
            distance = _rgb_distance(target, palette_rgb)
            if distance < best_distance:
                best_distance = distance
                best_index = idx

        return best_index
    except (TypeError, ValueError):
        return 7  # Default white

def get_color_pair_with_reversal(fg_color, bg_color, allow_reversal=True):
    """Get color pair, potentially reversing colors to reuse existing pairs."""
    global _next_color_pair
    
    if not state.COLORSUPPORT:
        return 0, False  # No color support, no reversal

    # Convert to color indices
    fg_idx = rgb_to_color_index(*fg_color) if fg_color else -1
    bg_idx = rgb_to_color_index(*bg_color) if bg_color else -1
    
    # Validate and adjust indices
    if fg_idx < -1 or fg_idx > 255: fg_idx = 7  # Default to white
    if bg_idx < -1 or bg_idx > 255: bg_idx = 0  # Default to black
    
    # Check if we already have this pair
    key = (fg_idx, bg_idx)
    if key in _color_pairs:
        return _color_pairs[key], False
    
    # Check if we have the reversed pair (and reversal is allowed)
    reversed_key = (bg_idx, fg_idx)
    if allow_reversal and reversed_key in _color_pairs:
        return _color_pairs[reversed_key], True  # Use reversed pair
    
    # Create new pair if we have room
    if _next_color_pair < get_available_color_pair_budget():
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
    if not state.COLORSUPPORT:
        return 0
    
    # If no background specified, use black
    if bg_color is None:
        bg_color = (0, 0, 0)
    
    # Create a cache key that includes both fg and bg
    cache_key = (tuple(color) if isinstance(color, (list, tuple)) else color, 
                 tuple(bg_color) if isinstance(bg_color, (list, tuple)) else bg_color)
    
    # Try to find exact match in pre-allocated pairs
    # Just get a color pair dynamically
    pair = get_color_pair(color, bg_color)
    return pair

def get_color_pair(fg_color, bg_color=None):
    """Legacy interface that uses the new smart color system."""
    if bg_color is None:
        bg_color = (0, 0, 0)  # Default black background
    
    color_pair, _ = get_color_pair_with_reversal(fg_color, bg_color, allow_reversal=False)
    return color_pair
