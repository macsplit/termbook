"""Test the dump mode (-d) functionality."""

import subprocess
import tempfile
import os
import zipfile


def create_epub_with_formatting():
    """Create an EPUB with various formatting that should be stripped in dump mode."""
    test_dir = tempfile.mkdtemp()
    epub_path = os.path.join(test_dir, "test_dump.epub")
    
    with zipfile.ZipFile(epub_path, 'w') as epub:
        # Add mimetype
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        
        # Add META-INF/container.xml
        container_xml = '''<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''
        epub.writestr("META-INF/container.xml", container_xml)
        
        # Add OEBPS/content.opf
        content_opf = '''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book for Dump Mode</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:identifier id="BookId">test-dump-book-id</dc:identifier>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter1"/>
  </spine>
</package>'''
        epub.writestr("OEBPS/content.opf", content_opf)
        
        # Add OEBPS/chapter1.xhtml with various formatting
        chapter1 = '''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>Chapter 1</title>
</head>
<body>
    <h1>Test Chapter Title</h1>
    <p>This is a <strong>regular</strong> paragraph with <em>emphasis</em>.</p>
    <pre><code class="python">
def hello_world():
    print('Hello, World!')
    return 42
    </code></pre>
    <p>Here is a link: <a href="https://example.com">Example Site</a></p>
    <img src="test.png" alt="Test Image"/>
    <ul>
        <li>First item</li>
        <li>Second item</li>
    </ul>
    <p>Final paragraph with normal text.</p>
</body>
</html>'''
        epub.writestr("OEBPS/chapter1.xhtml", chapter1)
        
        # Add OEBPS/toc.ncx
        toc_ncx = '''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="test-dump-book-id"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>Test Book for Dump Mode</text>
  </docTitle>
  <navMap>
    <navPoint id="navpoint-1" playOrder="1">
      <navLabel>
        <text>Chapter 1: Test Chapter</text>
      </navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>'''
        epub.writestr("OEBPS/toc.ncx", toc_ncx)
    
    return epub_path, test_dir


def test_dump_mode_plain_text():
    """Test that dump mode outputs plain text without formatting."""
    epub_path, test_dir = create_epub_with_formatting()
    
    try:
        # Run termbook with -d flag
        result = subprocess.run(
            ['python3', 'termbook.py', '-d', epub_path],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        
        output = result.stdout
        
        # Check that the output contains expected text
        assert "Test Chapter Title" in output
        assert "This is a regular paragraph with emphasis." in output
        assert "def hello_world():" in output
        assert "print('Hello, World!')" in output
        assert "return 42" in output
        assert "Here is a link: Example Site" in output
        assert "First item" in output
        assert "Second item" in output
        assert "Final paragraph with normal text." in output
        
        # Check that formatting markers are NOT in the output
        assert "SYNTAX_HL:" not in output
        assert "HEADER:" not in output
        assert "CAPTION:" not in output
        assert "[IMG:" not in output
        assert "https://example.com" not in output  # URL should not be extracted
        
        # Check that ANSI escape codes are not present
        assert "\033[" not in output
        assert "\x1b[" not in output
        
    finally:
        # Cleanup
        import shutil
        shutil.rmtree(test_dir)


if __name__ == "__main__":
    test_dump_mode_plain_text()
    print("Dump mode test passed!")