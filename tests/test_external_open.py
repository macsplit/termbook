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
    monkeypatch.setattr(reader, "_is_flatpak_runtime", lambda: True)
    monkeypatch.setattr(
        reader.shutil, "which", lambda cmd: "/usr/bin/gio" if cmd == "gio" else None
    )

    def fake_popen(
        cmd,
        stdin=None,
        stdout=None,
        stderr=None,
        start_new_session=None,
        close_fds=None,
    ):
        launched["cmd"] = cmd
        launched["start_new_session"] = start_new_session
        return None

    monkeypatch.setattr(reader.subprocess, "Popen", fake_popen)

    ebook = _FakeEpub(b"fake-image-bytes")
    assert reader.open_image_in_system_viewer(ebook, "", "cover.png") is True

    opened_path = launched["cmd"][2]
    assert launched["cmd"][:2] == ["gio", "open"]
    assert launched["start_new_session"] is True
    assert os.path.dirname(opened_path) == str(shared_dir)
    assert os.path.exists(opened_path)


def test_launch_external_target_falls_back_to_xdg_open(monkeypatch):
    launched = {}

    monkeypatch.setattr(reader.os, "name", "posix")
    monkeypatch.setattr(reader.sys, "platform", "linux")
    monkeypatch.setattr(reader, "_is_flatpak_runtime", lambda: False)
    monkeypatch.setattr(
        reader.shutil, "which", lambda cmd: "/usr/bin/xdg-open" if cmd == "xdg-open" else None
    )

    def fake_popen(
        cmd,
        stdin=None,
        stdout=None,
        stderr=None,
        start_new_session=None,
        close_fds=None,
    ):
        launched["cmd"] = cmd
        launched["stdin"] = stdin
        launched["start_new_session"] = start_new_session
        launched["close_fds"] = close_fds
        return None

    monkeypatch.setattr(reader.subprocess, "Popen", fake_popen)

    assert reader._launch_external_target("/tmp/example.png") is True
    assert launched["cmd"] == ["xdg-open", "/tmp/example.png"]
    assert launched["stdin"] is reader.subprocess.DEVNULL
    assert launched["start_new_session"] is True
    assert launched["close_fds"] is True
