"""Manual test to debug the test framework."""

import pexpect
import time
import tempfile
import zipfile
import os


def create_simple_test_epub():
    """Create a very simple test EPUB."""
    test_dir = tempfile.mkdtemp()
    epub_path = os.path.join(test_dir, "simple_test.epub")
    
    with zipfile.ZipFile(epub_path, 'w') as epub:
        # Mimetype (uncompressed)
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        
        # Container
        container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>'''
        epub.writestr("META-INF/container.xml", container_xml)
        
        # Simple OPF
        content_opf = '''<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="book-id">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="book-id">test-book</dc:identifier>
        <dc:title>Simple Test Book</dc:title>
        <dc:creator>Test Author</dc:creator>
        <dc:language>en</dc:language>
    </metadata>
    <manifest>
        <item id="chapter1" href="chapter1.html" media-type="application/xhtml+xml"/>
    </manifest>
    <spine>
        <itemref idref="chapter1"/>
    </spine>
</package>'''
        epub.writestr("content.opf", content_opf)
        
        # Simple chapter
        chapter1 = '''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>Chapter 1</title>
</head>
<body>
    <h1>Test Chapter</h1>
    <p>This is a simple test paragraph.</p>
    <p>Another paragraph for testing.</p>
</body>
</html>'''
        epub.writestr("chapter1.html", chapter1)
    
    return epub_path


def test_termbook_manually():
    """Manual test to see what happens."""
    print("Creating test EPUB...")
    epub_path = create_simple_test_epub()
    print(f"Created: {epub_path}")
    
    print("Starting termbook...")
    try:
        proc = pexpect.spawn(f'termbook "{epub_path}"', timeout=10)
        proc.setwinsize(24, 80)
        
        # Wait a bit
        time.sleep(2)
        
        print("Process alive:", proc.isalive())
        
        # Try to get any output
        try:
            proc.expect('.+', timeout=3)
            print("Got output:", proc.before)
            print("After:", proc.after)
        except pexpect.TIMEOUT:
            print("No output received")
        except pexpect.EOF:
            print("Process ended")
            print("Before EOF:", proc.before)
        
        # Send quit
        if proc.isalive():
            proc.send('q')
            proc.expect(pexpect.EOF, timeout=5)
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Cleanup
        os.remove(epub_path)
        os.rmdir(os.path.dirname(epub_path))


if __name__ == "__main__":
    test_termbook_manually()