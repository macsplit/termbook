"""Tests for external file opening helpers."""

import os

from termbook import reader


class _FakeFile:
    def __init__(self, payload):
        self._payload = payload

    def read(self, _path):
        return self._payload


class _FakeEpub:
    def __init__(self, payload):
        self.file = _FakeFile(payload)


def test_external_open_temp_dir_uses_cache_home_in_flatpak(monkeypatch, tmp_path):
    cache_home = tmp_path / "cache-home"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setattr(reader, "_is_flatpak_runtime", lambda: True)

    temp_dir = reader._external_open_temp_dir()

    assert temp_dir == os.path.join(
        str(cache_home), "termbook", "external-open"
    )
    assert os.path.isdir(temp_dir)


def test_external_open_temp_dir_is_none_outside_flatpak(monkeypatch):
    monkeypatch.setattr(reader, "_is_flatpak_runtime", lambda: False)

    assert reader._external_open_temp_dir() is None


def test_open_image_in_system_viewer_uses_shared_temp_dir_in_flatpak(
    monkeypatch, tmp_path
):
    shared_dir = tmp_path / "shared-cache"
    shared_dir.mkdir()
    launched = {}

    monkeypatch.setattr(reader, "_external_open_temp_dir", lambda: str(shared_dir))
    monkeypatch.setattr(reader.os, "name", "posix")
    monkeypatch.setattr(reader, "dots_path", lambda chpath, img_path: img_path)

    def fake_run(cmd, stdout=None, stderr=None, check=None):
        launched["cmd"] = cmd
        return None

    monkeypatch.setattr(reader.subprocess, "run", fake_run)

    ebook = _FakeEpub(b"fake-image-bytes")
    assert reader.open_image_in_system_viewer(ebook, "", "cover.png") is True

    opened_path = launched["cmd"][1]
    assert launched["cmd"][0] == "xdg-open"
    assert os.path.dirname(opened_path) == str(shared_dir)
    assert os.path.exists(opened_path)
