"""Image rendering backends: convert EPUB images into curses-drawable text.

Four rendering strategies feed into render_images_inline() (the entry point
called from reader.py): Fabulous (if installed), a plain quarter/half-block
fallback, and colorfulness/decorative-image heuristics used to skip tiny
decorative images. detect_and_convert_escape_sequences, boost_color_saturation,
and render_image_curses were removed here as dead code (zero call sites) --
see REMEDIATION_PLAN.md Phase 4.1.
"""

import os
import re
import sys
from io import BytesIO

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from fabulous import image as fabulous_image
    FABULOUS_AVAILABLE = True
except ImportError:
    FABULOUS_AVAILABLE = False

from termbook import state
from termbook.colors import get_available_color_pair_budget
from termbook.epub import dots_path


INLINE_IMAGE_PALETTE_SIZE = 64
_QUADRANT_CHARS = {
    0: " ",
    1: "▘",
    2: "▝",
    3: "▀",
    4: "▖",
    5: "▌",
    6: "▞",
    7: "▛",
    8: "▗",
    9: "▚",
    10: "▐",
    11: "▜",
    12: "▄",
    13: "▙",
    14: "▟",
    15: "█",
}


def quantize_image_for_inline(img, palette_size=INLINE_IMAGE_PALETTE_SIZE):
    """Reduce an image to a bounded palette so curses pair allocation stays sane."""
    if img.mode != "RGB":
        img = img.convert("RGB")

    palette_size = max(2, min(palette_size, 256))
    quantized = img.quantize(colors=palette_size, method=Image.Quantize.MEDIANCUT)
    return quantized.convert("RGB")


def get_inline_palette_size():
    """Choose an inline palette size from the available pair budget."""
    override = os.getenv("TERMBOOK_INLINE_PALETTE")
    if override:
        try:
            return max(8, min(128, int(override)))
        except ValueError:
            pass

    pair_budget = get_available_color_pair_budget()
    if pair_budget >= 12000:
        return 96
    if pair_budget >= 6000:
        return 64
    if pair_budget >= 2500:
        return 48
    return 32


def _is_decorative_image(img, img_path=""):
    """Heuristics for skipping decorative EPUB images inline."""
    if img.mode != "RGB":
        img = img.convert("RGB")

    orig_width, orig_height = img.size
    if orig_width <= 0 or orig_height <= 0:
        return True

    orig_aspect = orig_width / orig_height
    total_area = orig_width * orig_height
    img_path_lower = img_path.lower()

    if orig_width <= 120 and orig_height <= 120:
        return True

    if orig_width <= 50 or orig_height <= 50:
        return True

    if total_area <= 4000:
        return True

    decorative_patterns = [
        "bullet", "ornament", "decoration", "divider", "separator", "icon",
        "mark", "symbol", "star", "dot", "border", "line", "rule",
        "flourish", "accent", "deco", "spacer", "gap", "filler",
    ]
    if any(pattern in img_path_lower for pattern in decorative_patterns):
        return True

    if orig_aspect > 10 or orig_aspect < 0.1:
        return True

    try:
        sample_img = img.resize((16, 16))
        colors = sample_img.getcolors(maxcolors=256)
        if colors and len(colors) <= 6:
            return True
        if total_area <= 2000 and colors and len(colors) <= 10:
            return True

        # Wide, pale chapter-opening banners can consume the whole first
        # screen on short terminals while conveying almost no information.
        light_pixels = sum(
            count for count, color in colors
            if (color[0] + color[1] + color[2]) / 3 >= 245
        ) if colors else 0
        light_ratio = light_pixels / 256 if colors else 0
        if orig_aspect >= 5 and orig_height <= 600 and light_ratio >= 0.6:
            return True
    except Exception:
        pass

    if (orig_width > 200 and orig_height < 30) or (orig_height > 200 and orig_width < 30):
        return True

    return False


def _color_distance(a, b):
    r_diff = a[0] - b[0]
    g_diff = a[1] - b[1]
    b_diff = a[2] - b[2]
    return r_diff * r_diff + g_diff * g_diff + b_diff * b_diff


def _choose_two_block_colors(samples):
    """Choose up to two representative colors for a 2x2 character cell."""
    unique = []
    for color in samples:
        if color not in unique:
            unique.append(color)
        if len(unique) == 4:
            break

    if len(unique) <= 2:
        if len(unique) == 1:
            return unique[0], unique[0]
        return unique[0], unique[1]

    best_pair = (unique[0], unique[1])
    best_score = float("inf")

    for i, fg_color in enumerate(unique):
        for bg_color in unique[i + 1:]:
            score = 0
            for sample in samples:
                score += min(_color_distance(sample, fg_color), _color_distance(sample, bg_color))
            if score < best_score:
                best_score = score
                best_pair = (fg_color, bg_color)

    return best_pair


def render_image_with_quadrant_blocks(img, max_width, max_height):
    """Render an image using 2x2 Unicode quadrant glyphs with bounded colors."""
    if img.mode != "RGB":
        img = img.convert("RGB")

    orig_width, orig_height = img.size
    if orig_width <= 0 or orig_height <= 0:
        return []

    aspect_ratio = orig_width / orig_height
    terminal_char_aspect = 2.0
    display_area_aspect = max_width / max_height * terminal_char_aspect

    if aspect_ratio > display_area_aspect:
        target_width_chars = max_width
        target_height_chars = int(target_width_chars / aspect_ratio / terminal_char_aspect)
    else:
        target_height_chars = max_height
        target_width_chars = int(target_height_chars * aspect_ratio * terminal_char_aspect)

    target_width_chars = max(1, min(max_width, target_width_chars))
    target_height_chars = max(1, min(max_height, target_height_chars))

    target_pixel_width = max(2, target_width_chars * 2)
    target_pixel_height = max(2, target_height_chars * 2)
    if target_pixel_width % 2:
        target_pixel_width += 1
    if target_pixel_height % 2:
        target_pixel_height += 1

    resized = img.resize((target_pixel_width, target_pixel_height), Image.Resampling.LANCZOS)
    quantized = quantize_image_for_inline(resized, palette_size=get_inline_palette_size())
    width, height = quantized.size
    pixels = quantized.load()

    rendered_lines = []

    for y in range(0, height, 2):
        line_chars = []
        line_colors = []

        for x in range(0, width, 2):
            tl = pixels[x, y]
            tr = pixels[min(x + 1, width - 1), y]
            bl = pixels[x, min(y + 1, height - 1)]
            br = pixels[min(x + 1, width - 1), min(y + 1, height - 1)]
            samples = [tl, tr, bl, br]

            fg_color, bg_color = _choose_two_block_colors(samples)
            mask = 0
            for bit, sample in enumerate(samples):
                if _color_distance(sample, fg_color) <= _color_distance(sample, bg_color):
                    mask |= 1 << bit

            char = _QUADRANT_CHARS[mask]
            line_chars.append(char)
            line_colors.append((fg_color, bg_color))

        rendered_lines.append(("".join(line_chars), line_colors))

    return rendered_lines


def render_image_with_fabulous(img_data, max_width, max_height):
    """Render image using Fabulous library for improved color handling."""
    if not FABULOUS_AVAILABLE:
        return []
    
    try:
        # Save image data to temporary file since Fabulous requires a file path
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            # Convert PIL image to bytes if needed
            if hasattr(img_data, 'save'):
                img_data.save(tmp.name, format='PNG')
            else:
                # img_data is already bytes
                tmp.write(img_data)
            temp_path = tmp.name
        
        # Use Fabulous to render the image
        fab_img = fabulous_image.Image(temp_path, max_width)
        
        # Convert to string and split into lines
        img_str = str(fab_img)
        color_lines = img_str.split('\n')
        
        # Return the raw lines - process_fabulous_line will handle them
        processed_lines = [line for line in color_lines if line.strip()]
        
        # Clean up temporary file
        os.unlink(temp_path)
        
        return processed_lines
        
    except Exception as e:
        # Fall back to quarter blocks if Fabulous fails
        if state.DEBUG_MODE:
            print(f"Fabulous image rendering failed: {e}", file=sys.stderr)
        if hasattr(img_data, 'save'):
            return render_image_with_quarter_blocks(img_data, max_width, max_height)
        else:
            # Convert bytes to PIL Image for fallback
            try:
                from PIL import Image
                img = Image.open(BytesIO(img_data))
                return render_image_with_quarter_blocks(img, max_width, max_height)
            except Exception as e2:
                if state.DEBUG_MODE:
                    print(f"Quarter-block fallback also failed: {e2}", file=sys.stderr)
                return []



def process_fabulous_line(fab_line, max_width):
    """Process a line from Fabulous output, extracting colors and preparing for display."""
    import re
    
    # Fabulous uses spaces with background colors to create blocks
    # We need to convert these to block characters with proper colors
    
    line_chars = []
    line_colors = []
    current_fg = (255, 255, 255)  # Default white
    current_bg = (0, 0, 0)        # Default black
    
    # Parse the raw Fabulous output directly
    i = 0
    while i < len(fab_line):
        if fab_line[i:i+1] == '\033' or fab_line[i:i+1] == '[':
            # Find the end of the escape sequence
            if fab_line[i:i+1] == '\033':
                start = i + 1
            else:
                start = i
            
            end = fab_line.find('m', start)
            if end != -1:
                # Extract the escape sequence
                if fab_line[i:i+1] == '\033':
                    seq = fab_line[i+2:end]  # Skip '\033['
                else:
                    seq = fab_line[i+1:end] if fab_line[i:i+1] == '[' else fab_line[start:end]
                
                # Parse color codes
                if '48;5;' in seq:  # Background color
                    parts = seq.split(';')
                    if len(parts) >= 3:
                        try:
                            color_index = int(parts[2])
                            current_bg = ansi_256_to_rgb(color_index)
                        except (ValueError, IndexError):
                            pass
                elif '38;5;' in seq:  # Foreground color
                    parts = seq.split(';')
                    if len(parts) >= 3:
                        try:
                            color_index = int(parts[2])
                            current_fg = ansi_256_to_rgb(color_index)
                        except (ValueError, IndexError):
                            pass
                elif seq in ['49', '0']:  # Reset background or all
                    current_bg = (0, 0, 0)
                    if seq == '0':
                        current_fg = (255, 255, 255)
                
                i = end + 1
            else:
                i += 1
        else:
            # Regular character - if it's a space with bg color, convert to block
            char = fab_line[i]
            if char == ' ' and current_bg != (0, 0, 0):
                # Use a full block character instead of space for visibility
                line_chars.append('█')
                line_colors.append((current_bg, current_bg))  # Both fg and bg same color for solid block
            elif char == ' ':
                line_chars.append(' ')
                line_colors.append((current_fg, current_bg))
            else:
                line_chars.append(char)
                line_colors.append((current_fg, current_bg))
            i += 1
    
    # Create the final display line
    display_line = ''.join(line_chars)
    
    # Center the line and pad colors
    padding_needed = max(0, (max_width - len(display_line)) // 2)
    padded_line = " " * padding_needed + display_line
    padded_colors = [((0, 0, 0), (0, 0, 0))] * padding_needed + line_colors
    
    return padded_line, padded_colors


def ansi_256_to_rgb(color_index):
    """Convert ANSI 256 color index to RGB tuple."""
    # Standard 16 colors
    if color_index < 16:
        standard_colors = [
            (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
            (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
            (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
            (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)
        ]
        return standard_colors[color_index]
    
    # 216 color cube (colors 16-231)
    elif color_index < 232:
        color_index -= 16
        r = (color_index // 36) * 51
        g = ((color_index % 36) // 6) * 51
        b = (color_index % 6) * 51
        return (r, g, b)
    
    # Grayscale (colors 232-255)
    else:
        gray = (color_index - 232) * 10 + 8
        return (gray, gray, gray)



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


def _placeholder_text_for_image(img_idx, total_images):
    if total_images <= 1:
        return "[Loading image]"
    return f"[Loading image {img_idx + 1}/{total_images}]"


def _decorative_omission_text():
    return "[Decorative image omitted]"


def prepare_image_placeholders(src_lines, imgs):
    """Replace image markers with cheap one-line placeholders."""
    new_lines = []
    image_info = []
    image_line_map = []
    total_images = len(imgs)

    for line in src_lines:
        img_match = re.search(r"\[IMG:([0-9]+)\]", line)
        if img_match:
            img_idx = int(img_match.group(1))
            if img_idx < total_images:
                placeholder = _placeholder_text_for_image(img_idx, total_images)
                new_lines.append(line.replace(f"[IMG:{img_idx}]", placeholder))
                image_info.append([])
                image_line_map.append(img_idx)
                continue

        new_lines.append(line)
        image_info.append([])
        image_line_map.append(None)

    return new_lines, image_info, image_line_map


def render_single_image_inline(ebook, chpath, impath, img_idx, max_width):
    """Render one EPUB image into inline text rows plus color metadata."""
    imgsrc = dots_path(chpath, impath)
    img_data = ebook.file.read(imgsrc)
    img = Image.open(BytesIO(img_data))

    if img.mode != 'RGB':
        img = img.convert('RGB')

    orig_width, orig_height = img.size
    orig_aspect = orig_width / orig_height

    if os.getenv('TERMBOOK_DEBUG'):
        print(f"DEBUG: Image {impath} is {orig_width}x{orig_height} pixels", file=sys.stderr)

    if _is_decorative_image(img, impath):
        omitted = _decorative_omission_text().center(max_width)
        return [omitted], [[]], [None]

    max_chars_available = max_width - 8
    terminal_char_aspect = 2.0
    max_width_by_screen = min(max_chars_available - 4, 80)
    max_height_available = 30
    natural_char_width = min(orig_width, max_width_by_screen)
    width_percentage = natural_char_width / max_width_by_screen

    if width_percentage < 0.50:
        target_char_width = int(max_width_by_screen * 0.75)
        target_char_height = int(target_char_width / orig_aspect / terminal_char_aspect)

        if target_char_height > max_height_available:
            target_char_height = max_height_available
            target_char_width = int(target_char_height * orig_aspect * terminal_char_aspect)
            target_char_width = min(target_char_width, max_width_by_screen)

        char_width = target_char_width
        char_height = target_char_height
    else:
        if natural_char_width <= max_width_by_screen:
            char_width = natural_char_width
            char_height = int(char_width / orig_aspect / terminal_char_aspect)
        else:
            char_width = max_width_by_screen
            char_height = int(char_width / orig_aspect / terminal_char_aspect)

        if char_height > max_height_available:
            char_height = max_height_available
            char_width = int(char_height * orig_aspect * terminal_char_aspect)

    if orig_width >= 100 and orig_height >= 100:
        char_width = max(12, min(char_width, max_width_by_screen))
        char_height = max(6, min(char_height, max_height_available))
    else:
        min_width = max(6, orig_width // 4)
        min_height = max(4, orig_height // 4)
        char_width = max(min_width, min(char_width, max_width_by_screen))
        char_height = max(min_height, min(char_height, max_height_available))

    rendered_lines = render_image_with_quadrant_blocks(img, char_width, char_height)
    output_lines = []
    image_info = []
    image_line_map = []

    for rendered_line, line_colors in rendered_lines:
        padding = " " * ((max_width - len(rendered_line)) // 2)
        centered_line = padding + rendered_line
        padded_colors = [((0, 0, 0), (0, 0, 0))] * len(padding) + line_colors

        output_lines.append("IMG_LINE:" + centered_line)
        image_info.append(padded_colors)
        image_line_map.append(img_idx)

    output_lines.append("")
    image_info.append([])
    image_line_map.append(None)
    return output_lines, image_info, image_line_map


def render_images_inline(ebook, chpath, src_lines, imgs, max_width, progress_callback=None):
    """Convert image placeholders to block-based representation inline with color info."""
    if not PIL_AVAILABLE or not imgs:
        # Create empty image tracking array for each line
        image_line_map = [None] * len(src_lines)
        return src_lines, [], image_line_map
    
    new_lines = []
    image_info = []
    image_line_map = []  # Track which image (if any) is associated with each line
    processed_images = 0
    total_images = len(imgs)
    
    for line in src_lines:
        # Check if line contains an image placeholder
        img_match = re.search(r"\[IMG:([0-9]+)\]", line)
        if img_match:
            img_idx = int(img_match.group(1))
            if img_idx < len(imgs):
                try:
                    processed_images += 1
                    if progress_callback is not None:
                        progress_callback(processed_images, total_images)

                    impath = imgs[img_idx]
                    rendered_lines, rendered_info, rendered_map = render_single_image_inline(
                        ebook, chpath, impath, img_idx, max_width
                    )
                    new_lines.extend(rendered_lines)
                    image_info.extend(rendered_info)
                    image_line_map.extend(rendered_map)
                except Exception as e:
                    # If image can't be processed, show error message
                    if state.DEBUG_MODE:
                        print(f"Could not render image {imgs[img_idx]}: {e}", file=sys.stderr)
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
