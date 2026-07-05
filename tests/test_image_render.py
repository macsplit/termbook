"""Tests for bounded-palette inline image rendering."""

from PIL import Image

from termbook import image_render


def test_quantize_image_for_inline_caps_palette():
    img = Image.new("RGB", (8, 8))
    for y in range(8):
        for x in range(8):
            img.putpixel((x, y), (x * 30, y * 30, (x + y) * 16))

    quantized = image_render.quantize_image_for_inline(img, palette_size=8)
    colors = quantized.getcolors(maxcolors=256)

    assert colors is not None
    assert len(colors) <= 8


def test_render_image_with_quadrant_blocks_returns_cells():
    img = Image.new("RGB", (4, 4), (0, 0, 0))
    img.putpixel((0, 0), (255, 255, 255))
    img.putpixel((1, 0), (255, 255, 255))
    img.putpixel((0, 1), (255, 255, 255))

    rendered = image_render.render_image_with_quadrant_blocks(img, 4, 4)

    assert rendered
    line_text, line_colors = rendered[0]
    assert len(line_text) == len(line_colors)
    assert any(char != " " for char in line_text)


def test_render_image_with_quadrant_blocks_preserves_display_width():
    img = Image.new("RGB", (200, 100), (120, 180, 220))

    rendered = image_render.render_image_with_quadrant_blocks(img, 40, 20)

    assert rendered
    assert len(rendered[0][0]) == 40


def test_render_image_with_quadrant_blocks_uses_bounded_palette(monkeypatch):
    monkeypatch.setenv("TERMBOOK_INLINE_PALETTE", "40")

    img = Image.new("RGB", (12, 12))
    for y in range(12):
        for x in range(12):
            img.putpixel((x, y), ((x * 20) % 256, (y * 20) % 256, ((x + y) * 11) % 256))

    rendered = image_render.render_image_with_quadrant_blocks(img, 8, 6)
    used_colors = set()
    for _, line_colors in rendered:
        for fg_color, bg_color in line_colors:
            used_colors.add(fg_color)
            used_colors.add(bg_color)

    assert len(used_colors) <= image_render.get_inline_palette_size()


def test_get_inline_palette_size_override(monkeypatch):
    monkeypatch.setenv("TERMBOOK_INLINE_PALETTE", "72")

    assert image_render.get_inline_palette_size() == 72
