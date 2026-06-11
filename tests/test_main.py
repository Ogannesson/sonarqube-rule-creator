import os
import zipfile

import flet as ft

from sonarqube_profile_creator.config import APP_DISPLAY_NAME
from sonarqube_profile_creator.main import (
    _assets_dir,
    _file_fingerprint,
    _flet_app_name,
    _prepare_flet_desktop_runtime,
    _register_instance_later,
    _run_flet_app,
    _select_app_view,
)


def test_default_view_is_flet_desktop():
    assert _select_app_view() == ft.AppView.FLET_APP


def test_web_flag_selects_browser_view():
    assert _select_app_view(web=True) == ft.AppView.WEB_BROWSER


def test_web_view_uses_root_mount_name():
    assert _flet_app_name(ft.AppView.WEB_BROWSER) == ""
    assert _flet_app_name(ft.AppView.FLET_APP) == APP_DISPLAY_NAME


def test_desktop_view_does_not_register_browser_instance():
    assert _register_instance_later(ft.AppView.FLET_APP, 0) == ""


def test_desktop_window_uses_display_name(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("sonarqube_profile_creator.main.ft.run", fake_run)
    monkeypatch.setattr("sonarqube_profile_creator.main._prepare_flet_desktop_runtime", lambda: None)

    _run_flet_app(ft.AppView.FLET_APP)

    assert calls[0]["name"] == APP_DISPLAY_NAME
    assert calls[0]["view"] == ft.AppView.FLET_APP
    assert calls[0]["assets_dir"].endswith("assets")


def test_web_window_uses_root_mount_name(monkeypatch):
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("sonarqube_profile_creator.main.ft.run", fake_run)

    _run_flet_app(ft.AppView.WEB_BROWSER)

    assert calls[0]["name"] == ""
    assert calls[0]["view"] == ft.AppView.WEB_BROWSER


def test_assets_dir_points_to_project_assets():
    assert _assets_dir().name == "assets"


def test_prepare_flet_runtime_uses_local_cache(monkeypatch, tmp_path):
    archive_path = tmp_path / "flet-windows.zip"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("flet/flet.exe", "")

    monkeypatch.delenv("FLET_VIEW_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr("sonarqube_profile_creator.main._is_windows", lambda: True)
    monkeypatch.setattr("sonarqube_profile_creator.main._flet_desktop_version", lambda: "0.85.2")
    monkeypatch.setattr("sonarqube_profile_creator.main._flet_desktop_archive_path", lambda: archive_path)
    monkeypatch.setattr("sonarqube_profile_creator.main._file_fingerprint", lambda _path: "iconhash")
    monkeypatch.setattr("sonarqube_profile_creator.main._copy_icon_to_flet_view", lambda _exe, _icon: True)

    runtime_dir = _prepare_flet_desktop_runtime()

    assert runtime_dir is not None
    assert runtime_dir.name == "flet-desktop-full-0.85.2.iconhash"
    assert runtime_dir.joinpath("flet", "flet.exe").exists()
    assert runtime_dir.joinpath(".sonarqube-profile-creator-ready").exists()
    assert runtime_dir.parent.parent == tmp_path / "local" / "SonarQubeProfileCreator"
    assert os.environ["FLET_VIEW_PATH"] == str(runtime_dir / "flet")


def test_file_fingerprint_changes_with_content(tmp_path):
    path = tmp_path / "icon.ico"
    path.write_bytes(b"one")
    first = _file_fingerprint(path)
    path.write_bytes(b"two")
    assert _file_fingerprint(path) != first
