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
from termbook.epub import dots_path


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
                    except Exception:
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
                    
                    # Try to use Fabulous for better image rendering
                    try:
                        # Use Fabulous to render the image
                        fabulous_lines = render_image_with_fabulous(img, char_width, char_height // 2)
                        
                        if fabulous_lines:
                            # Process Fabulous output
                            for fab_line in fabulous_lines:
                                # Convert escape sequences to displayable format and extract colors
                                processed_line, line_colors = process_fabulous_line(fab_line, max_width)
                                
                                # Add the line to output
                                new_lines.append("IMG_LINE:" + processed_line)
                                image_info.append(line_colors)
                                image_line_map.append(img_idx)  # Track which image this line belongs to
                        else:
                            raise Exception("Fabulous rendering failed")
                            
                    except Exception as e:
                        # Fallback to original pixel processing if Fabulous fails
                        if state.DEBUG_MODE:
                            print(f"Fabulous rendering failed for image {img_idx}, using pixel fallback: {e}", file=sys.stderr)
                        # Keep original image for high-quality oversampling
                        orig_img = img.copy()
                        orig_w, orig_h = orig_img.size
                        
                        # Calculate target dimensions
                        target_width = target_pixel_width
                        target_height = target_pixel_height
                        
                        # Ensure height is even for proper half-block pairing
                        if target_height % 2 != 0:
                            target_height += 1
                        
                        # Store color and character info for each line with simple fallback
                        for y in range(0, target_height, 2):  # Process 2 rows at a time
                            line = ""
                            line_colors = []
                            
                            # Simple fallback processing
                            for x in range(target_width):
                                # Simple pixel sampling
                                src_x = min(int(x * orig_w / target_width), orig_w - 1)
                                src_y_top = min(int(y * orig_h / target_height), orig_h - 1)
                                src_y_bot = min(int((y + 1) * orig_h / target_height), orig_h - 1)
                                
                                top_pixel = orig_img.getpixel((src_x, src_y_top))
                                bottom_pixel = orig_img.getpixel((src_x, src_y_bot))
                                
                                # Use upper slab character
                                line += '▀'
                                line_colors.append((top_pixel, bottom_pixel))
                            
                            # Add the line to output
                            padding = " " * ((max_width - len(line)) // 2)
                            centered_line = padding + line
                            padded_colors = [((0, 0, 0), (0, 0, 0))] * len(padding) + line_colors
                            
                            new_lines.append("IMG_LINE:" + centered_line)
                            image_info.append(padded_colors)
                            image_line_map.append(img_idx)  # Track which image this line belongs to
                    
                    new_lines.append("")  # Empty line after image
                    image_line_map.append(None)  # Empty line doesn't belong to any image
                    image_info.append([])  # Empty color info for empty line
                    
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
