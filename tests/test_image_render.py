"""Tests for bounded-palette inline image rendering."""

from io import BytesIO

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


def test_is_decorative_image_detects_wide_pale_banner():
    img = Image.new("RGB", (3200, 440), (255, 255, 255))
    for x in range(0, 3200, 120):
        img.putpixel((x, 200), (80, 80, 80))

    assert image_render._is_decorative_image(img, "chapter-banner.png") is True


def test_is_decorative_image_keeps_large_meaningful_diagram():
    img = Image.new("RGB", (1200, 800), (255, 255, 255))
    for y in range(80, 720):
        for x in range(120, 1080):
            if (x // 80 + y // 80) % 2 == 0:
                img.putpixel((x, y), (20, 90, 180))

    assert image_render._is_decorative_image(img, "model-diagram.png") is False


def test_render_images_inline_reports_progress(monkeypatch):
    img = Image.new("RGB", (8, 8), (50, 100, 150))
    payload = BytesIO()
    img.save(payload, format="PNG")

    class FakeFile:
        def read(self, _path):
            return payload.getvalue()

    class FakeBook:
        file = FakeFile()

    progress_calls = []

    monkeypatch.setattr(image_render, "dots_path", lambda _chpath, impath: impath)
    monkeypatch.setattr(image_render, "_is_decorative_image", lambda _img, _path="": False)
    monkeypatch.setattr(
        image_render,
        "render_image_with_quadrant_blocks",
        lambda _img, _width, _height: [("XX", [((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0))])],
    )

    lines, _, line_map = image_render.render_images_inline(
        FakeBook(),
        "chapter.xhtml",
        ["[IMG:0]", "after"],
        ["image.png"],
        40,
        progress_callback=lambda current, total: progress_calls.append((current, total)),
    )

    assert progress_calls == [(1, 1)]
    assert lines[0].startswith("IMG_LINE:")
    assert line_map[0] == 0
