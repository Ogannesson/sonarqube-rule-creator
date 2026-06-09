from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import shutil
import struct
import sys
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import TextIO

import flet as ft

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sonarqube_profile_creator.config import APP_DISPLAY_NAME, local_cache_dir, user_config_dir
    from sonarqube_profile_creator.gui import app
    from sonarqube_profile_creator.spreadsheet import write_example_templates
else:
    from .config import APP_DISPLAY_NAME, local_cache_dir, user_config_dir
    from .gui import app
    from .spreadsheet import write_example_templates

_STDIO_HANDLES: list[TextIO] = []
INSTANCE_FILE = "instance.json"
RT_ICON = 3
RT_GROUP_ICON = 14


def _configure_logging() -> Path:
    log_dir = user_config_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "startup.log"
    _ensure_stdio(log_dir)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return log_path


def _ensure_stdio(log_dir: Path) -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream_path = log_dir / "stdio.log"
    handle = stream_path.open("a", encoding="utf-8", buffering=1)
    _STDIO_HANDLES.append(handle)
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle
    os.environ.setdefault("PYTHONUNBUFFERED", "1")


def main(argv: list[str] | None = None) -> int:
    log_path = _configure_logging()
    logging.info("Starting SonarQube Profile Creator")
    parser = argparse.ArgumentParser(description="SonarQube Profile Creator")
    parser.add_argument("--create-templates", type=Path, help="Create example CSV/XLSX templates in the target directory.")
    parser.add_argument("--web", action="store_true", help="Run in browser mode for development.")
    parser.add_argument("--desktop", action="store_true", help="Run with the Flet desktop client. This is the default.")
    args = parser.parse_args(argv)

    if args.create_templates:
        csv_path, xlsx_path = write_example_templates(args.create_templates)
        print(csv_path)
        print(xlsx_path)
        logging.info("Created templates: %s, %s", csv_path, xlsx_path)
        return 0

    view = _select_app_view(web=args.web)
    if view == ft.AppView.WEB_BROWSER and _open_existing_instance():
        logging.info("Opened existing instance")
        return 0
    logging.info("Using view=%s log=%s", view, log_path)
    os.environ["SONARQUBE_PROFILE_CREATOR_VIEW"] = "web" if view == ft.AppView.WEB_BROWSER else "desktop"
    app_view_url = _register_instance_later(view, time.time())
    _run_flet_app(view)
    _clear_instance(app_view_url)
    return 0


def _select_app_view(web: bool = False) -> ft.AppView:
    return ft.AppView.WEB_BROWSER if web else ft.AppView.FLET_APP


def _run_flet_app(view: ft.AppView) -> None:
    if view == ft.AppView.FLET_APP:
        _prepare_flet_desktop_runtime()
    ft.run(main=app, name=APP_DISPLAY_NAME, view=view, host="127.0.0.1", port=0, assets_dir=str(_assets_dir()))


def _assets_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "assets"
    return Path(__file__).resolve().parents[2] / "assets"


def _prepare_flet_desktop_runtime() -> Path | None:
    if os.environ.get("FLET_VIEW_PATH"):
        return None
    if os.name != "nt":
        return None

    version = _flet_desktop_version()
    cache_root = local_cache_dir() / "flet"
    icon_path = _assets_dir() / "app_icon.ico"
    icon_fingerprint = _file_fingerprint(icon_path)
    runtime_name = f"flet-desktop-full-{version}.{icon_fingerprint}"
    runtime_dir = cache_root / runtime_name
    marker_value = f"{version}:{icon_fingerprint}:patched"
    marker_path = runtime_dir / ".sonarqube-profile-creator-ready"
    flet_exe = runtime_dir / "flet" / "flet.exe"
    if marker_path.exists() and flet_exe.exists() and marker_path.read_text(encoding="utf-8") == marker_value:
        os.environ["FLET_VIEW_PATH"] = str(flet_exe.parent)
        return runtime_dir

    cache_root.mkdir(parents=True, exist_ok=True)
    _clear_stale_flet_runtime_dirs(cache_root, f"flet-desktop-full-{version}", keep=runtime_dir)
    shutil.rmtree(runtime_dir, ignore_errors=True)

    archive_path = _flet_desktop_archive_path()
    if not archive_path.exists():
        return None

    runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(runtime_dir)
        if _copy_icon_to_flet_view(flet_exe, icon_path):
            marker_path.write_text(marker_value, encoding="utf-8")
        else:
            marker_path.write_text(f"{version}:{icon_fingerprint}:unpatched", encoding="utf-8")
    except Exception:
        shutil.rmtree(runtime_dir, ignore_errors=True)
        raise

    os.environ["FLET_VIEW_PATH"] = str(flet_exe.parent)
    return runtime_dir


def _flet_desktop_version() -> str:
    import flet_desktop.version

    return str(flet_desktop.version.version)


def _flet_desktop_archive_path() -> Path:
    import flet_desktop

    return Path(flet_desktop.get_package_bin_dir()) / flet_desktop.get_artifact_filename()


def _clear_stale_flet_runtime_dirs(cache_root: Path, runtime_name: str, keep: Path | None = None) -> None:
    paths = [cache_root / runtime_name, *cache_root.glob(f"{runtime_name}.*")]
    keep_resolved = keep.resolve() if keep else None
    for path in paths:
        if path.is_dir() and (keep_resolved is None or path.resolve() != keep_resolved):
            shutil.rmtree(path, ignore_errors=True)


def _copy_icon_to_flet_view(flet_exe: Path, icon_path: Path) -> bool:
    if not flet_exe.exists() or not icon_path.exists():
        return False
    try:
        from win32ctypes.pywin32 import win32api

        group_data, icon_images = _read_ico_resource(icon_path)
        handle = win32api.BeginUpdateResource(str(flet_exe), 0)
        try:
            win32api.UpdateResource(handle, RT_GROUP_ICON, 1, group_data)
            for icon_id, image_data in icon_images:
                win32api.UpdateResource(handle, RT_ICON, icon_id, image_data)
            win32api.EndUpdateResource(handle, 0)
        except Exception:
            win32api.EndUpdateResource(handle, 1)
            raise
    except Exception:
        logging.exception("Failed to apply app icon to Flet desktop runtime")
        return False
    return True


def _read_ico_resource(icon_path: Path) -> tuple[bytes, list[tuple[int, bytes]]]:
    data = icon_path.read_bytes()
    reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)
    entries = []
    images = []
    group = [struct.pack("<HHH", reserved, icon_type, count)]
    for index in range(count):
        offset = 6 + index * 16
        width, height, color_count, reserved_byte, planes, bit_count, size, image_offset = struct.unpack_from("<BBBBHHII", data, offset)
        icon_id = index + 1
        entries.append((icon_id, data[image_offset : image_offset + size]))
        group.append(struct.pack("<BBBBHHIH", width, height, color_count, reserved_byte, planes, bit_count, size, icon_id))
    images.extend(entries)
    return b"".join(group), images


def _file_fingerprint(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _instance_path() -> Path:
    return user_config_dir() / INSTANCE_FILE


def _read_instance() -> dict[str, object]:
    path = _instance_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_instance(payload: dict[str, object]) -> None:
    path = _instance_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _open_existing_instance() -> bool:
    payload = _read_instance()
    url = str(payload.get("url", ""))
    pid = int(payload.get("pid", 0) or 0)
    if not url or not pid or not _is_pid_running(pid) or not _is_url_alive(url):
        _clear_stale_instance(pid)
        return False
    webbrowser.open(url)
    return True


def _register_instance_later(view: ft.AppView, started_at: float) -> str:
    placeholder_url = ""
    if view != ft.AppView.WEB_BROWSER:
        return placeholder_url

    def worker() -> None:
        deadline = time.time() + 15
        while time.time() < deadline:
            url = _extract_latest_app_url(started_at)
            if url:
                _write_instance({"pid": os.getpid(), "url": url, "started_at": time.time()})
                logging.info("Registered instance url=%s pid=%s", url, os.getpid())
                return
            time.sleep(0.25)

    import threading

    threading.Thread(target=worker, daemon=True).start()
    return placeholder_url


def _extract_latest_app_url(started_at: float) -> str:
    log_path = user_config_dir() / "startup.log"
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    start_marker = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at))
    for line in reversed(lines[-160:]):
        if line[:19] < start_marker:
            break
        marker = "App URL: "
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return ""


def _clear_instance(_expected_url: str = "") -> None:
    payload = _read_instance()
    if int(payload.get("pid", 0) or 0) == os.getpid():
        try:
            _instance_path().unlink(missing_ok=True)
        except OSError:
            pass


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _clear_stale_instance(pid: int) -> None:
    payload = _read_instance()
    if int(payload.get("pid", 0) or 0) == pid:
        try:
            _instance_path().unlink(missing_ok=True)
        except OSError:
            pass


def _is_url_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception:
        _configure_logging()
        logging.exception("Startup failed")
        raise
