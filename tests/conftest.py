"""Test configuration and fixtures for termbook tests."""

import pytest
import pexpect
import os
import tempfile
import shutil
from pathlib import Path


@pytest.fixture(scope="session")
def test_epub():
    """Create a minimal test EPUB file for testing."""
    test_dir = tempfile.mkdtemp()
    epub_path = os.path.join(test_dir, "test.epub")
    
    # Create a minimal EPUB structure
    import zipfile
    
    with zipfile.ZipFile(epub_path, 'w') as epub:
        # Add mimetype
        epub.writestr("mimetype", "application/epub+zip")
        
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
    <dc:title>Test Book</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:identifier id="BookId">test-book-id</dc:identifier>
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
        
        # Add OEBPS/chapter1.xhtml
        chapter1 = '''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>Chapter 1</title>
</head>
<body>
    <h1>Chapter 1: Test Chapter</h1>
    <p>This is a test paragraph for automated testing.</p>
    <p>Second paragraph with some content.</p>
    <p>Third paragraph for scrolling tests.</p>
</body>
</html>'''
        epub.writestr("OEBPS/chapter1.xhtml", chapter1)
        
        # Add OEBPS/toc.ncx
        toc_ncx = '''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="test-book-id"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>Test Book</text>
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
    
    yield epub_path
    
    # Cleanup
    shutil.rmtree(test_dir)


@pytest.fixture
def termbook_process(test_epub):
    """Start a termbook process with a test EPUB file."""
    # Start termbook with the test EPUB
    proc = pexpect.spawn(f'termbook "{test_epub}"', timeout=10)
    proc.setwinsize(24, 80)  # Set standard terminal size
    
    # Wait for initial load
    proc.expect(r'.*', timeout=3)
    
    yield proc
    
    # Cleanup
    if proc.isalive():
        proc.terminate()


@pytest.fixture
def clean_termbook_state():
    """Clean termbook state before and after tests."""
    # Clean before test
    config_dir = os.path.expanduser("~/.config/termbook")
    old_config = os.path.expanduser("~/.config/epr")
    
    if os.path.exists(config_dir):
        shutil.rmtree(config_dir)
    if os.path.exists(old_config):
        shutil.rmtree(old_config)
    
    yield
    
    # Clean after test (optional, depends on test isolation needs)
    if os.path.exists(config_dir):
        shutil.rmtree(config_dir)
    if os.path.exists(old_config):
        shutil.rmtree(old_config)