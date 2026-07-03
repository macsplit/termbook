"""Unit tests for termbook.epub.Epub and dots_path (Phase 4.4 backfill)."""

import os

from termbook.epub import Epub, dots_path


class TestEpub:
    def test_init_parses_container_and_rootfile(self, test_epub):
        book = Epub(test_epub)
        assert book.version == "2.0"
        assert book.rootfile == "OEBPS/content.opf"
        assert book.rootdir == "OEBPS/"
        assert book.path == os.path.abspath(test_epub)

    def test_get_meta_returns_dc_metadata(self, test_epub):
        book = Epub(test_epub)
        meta = dict(book.get_meta())
        assert meta["title"] == "Test Book"
        assert meta["creator"] == "Test Author"
        assert meta["language"] == "en"

    def test_initialize_populates_contents_and_toc(self, test_epub):
        book = Epub(test_epub)
        book.initialize()
        assert book.contents == ["OEBPS/chapter1.xhtml"]
        assert book.toc_entries == ["Chapter 1: Test Chapter"]


class TestDotsPath:
    def test_same_directory(self):
        assert dots_path("OEBPS/chapter1.xhtml", "image.png") == "OEBPS/image.png"

    def test_parent_directory_reference(self):
        assert dots_path("OEBPS/text/chapter1.xhtml", "../images/fig1.png") == "OEBPS/images/fig1.png"

    def test_no_subdirectory(self):
        assert dots_path("chapter1.xhtml", "image.png") == "image.png"
