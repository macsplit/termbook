"""Unit tests for termbook.text_render.find_urls_in_text and MockMatch
(Phase 4.4 backfill) -- the shared URL-detection helper and re.Match-alike
that Phase 4.3 unified from four duplicated call sites."""

from termbook.text_render import find_urls_in_text, MockMatch


class TestFindUrlsInText:
    def test_finds_single_url(self):
        text = "Check out https://example.com for more info."
        urls = find_urls_in_text(text)
        assert len(urls) == 1
        url, start, end = urls[0]
        assert url == "https://example.com"
        assert text[start:end] == url

    def test_finds_multiple_urls(self):
        text = "See https://a.com and http://b.com/path?x=1 for details."
        urls = [u for u, _, _ in find_urls_in_text(text)]
        assert urls == ["https://a.com", "http://b.com/path?x=1"]

    def test_no_urls_returns_empty_list(self):
        assert find_urls_in_text("Just plain prose, no links here.") == []

    def test_url_does_not_capture_trailing_punctuation(self):
        text = "Visit https://example.com/page, then continue."
        url, _, _ = find_urls_in_text(text)[0]
        assert url == "https://example.com/page"

    def test_excludes_fragment_but_keeps_query(self):
        text = "https://example.com/page?a=1#section"
        url, _, _ = find_urls_in_text(text)[0]
        assert url == "https://example.com/page?a=1"


class TestMockMatch:
    def test_group_start_end(self):
        m = MockMatch("https://example.com", 5, 24)
        assert m.group() == "https://example.com"
        assert m.start() == 5
        assert m.end() == 24
