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


class _FakeVariantType:
    @staticmethod
    def new(value):
        return value


class _FakeVariant:
    def __init__(self, signature, value):
        self.signature = signature
        self.value = value


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

    class FakeUnixFDList:
        def __init__(self):
            self.fds = []

        @classmethod
        def new(cls):
            return cls()

        def append(self, fd):
            self.fds.append(fd)
            return len(self.fds) - 1

    class FakeConnection:
        def call_with_unix_fd_list_sync(
            self,
            bus_name,
            object_path,
            interface_name,
            method_name,
            parameters,
            reply_type,
            flags,
            timeout_msec,
            fd_list,
            cancellable,
        ):
            launched["bus_name"] = bus_name
            launched["object_path"] = object_path
            launched["interface_name"] = interface_name
            launched["method_name"] = method_name
            launched["parameters"] = parameters
            launched["reply_type"] = reply_type
            launched["fd_count"] = len(fd_list.fds)
            return (_FakeVariant("(o)", ("/fake/request",)), None)

    class FakeGio:
        class BusType:
            SESSION = "session"

        class DBusCallFlags:
            NONE = 0

        UnixFDList = FakeUnixFDList

        @staticmethod
        def bus_get_sync(bus_type, cancellable):
            launched["bus_type"] = bus_type
            return FakeConnection()

    class FakeGLib:
        Variant = _FakeVariant
        VariantType = _FakeVariantType

    monkeypatch.setattr(reader, "Gio", FakeGio)
    monkeypatch.setattr(reader, "GLib", FakeGLib)

    ebook = _FakeEpub(b"fake-image-bytes")
    assert reader.open_image_in_system_viewer(ebook, "", "cover.png") is True

    portal_args = launched["parameters"].value
    created_files = list(shared_dir.iterdir())
    assert launched["bus_type"] == "session"
    assert launched["bus_name"] == "org.freedesktop.portal.Desktop"
    assert launched["object_path"] == "/org/freedesktop/portal/desktop"
    assert launched["interface_name"] == "org.freedesktop.portal.OpenURI"
    assert launched["method_name"] == "OpenFile"
    assert portal_args[0] == ""
    assert portal_args[1] == 0
    assert launched["reply_type"] == "(o)"
    assert launched["fd_count"] == 1
    assert len(created_files) == 1
    assert os.path.dirname(str(created_files[0])) == str(shared_dir)
    assert os.path.exists(created_files[0])


def test_open_file_via_portal_returns_false_without_gi(monkeypatch):
    monkeypatch.setattr(reader, "Gio", None)
    monkeypatch.setattr(reader, "GLib", None)

    assert reader._open_file_via_portal("/tmp/example.png") is False


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


def test_apply_cached_image_renders_replaces_placeholders():
    src_lines = ["before", "[Loading image 1/2]", "after"]
    image_info = [[], [], []]
    image_line_map = [None, 0, None]
    cached_images = {
        0: (
            ["IMG_LINE:rendered", ""],
            [[((0, 0, 0), (0, 0, 0))], []],
            [0, None],
        )
    }

    new_lines, new_info, new_map = reader._apply_cached_image_renders(
        src_lines, image_info, image_line_map, cached_images
    )

    assert new_lines == ["before", "IMG_LINE:rendered", "", "after"]
    assert new_map == [None, 0, None, None]
    assert new_info[1]


def test_choose_next_pending_image_prefers_viewport_then_ahead():
    src_lines = [
        "[Loading image 1/3]",
        "text",
        "[Loading image 2/3]",
        "text",
        "[Loading image 3/3]",
    ]
    image_line_map = [0, None, 1, None, 2]

    assert reader._choose_next_pending_image(src_lines, image_line_map, {0}, 1, 2) == (2, 1)
    assert reader._choose_next_pending_image(src_lines, image_line_map, {0, 1}, 1, 2) == (4, 2)
