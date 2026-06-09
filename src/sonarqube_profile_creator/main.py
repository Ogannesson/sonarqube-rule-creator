from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import TextIO

import flet as ft

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sonarqube_profile_creator.config import user_config_dir
    from sonarqube_profile_creator.gui import app
    from sonarqube_profile_creator.spreadsheet import write_example_templates
else:
    from .config import user_config_dir
    from .gui import app
    from .spreadsheet import write_example_templates

_STDIO_HANDLES: list[TextIO] = []
INSTANCE_FILE = "instance.json"


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
    parser.add_argument("--web", action="store_true", help="Run Flet in web mode for development.")
    parser.add_argument("--desktop", action="store_true", help="Run with the Flet desktop client.")
    args = parser.parse_args(argv)

    if args.create_templates:
        csv_path, xlsx_path = write_example_templates(args.create_templates)
        print(csv_path)
        print(xlsx_path)
        logging.info("Created templates: %s, %s", csv_path, xlsx_path)
        return 0

    view = ft.AppView.FLET_APP if args.desktop else ft.AppView.WEB_BROWSER
    if view == ft.AppView.WEB_BROWSER and _open_existing_instance():
        logging.info("Opened existing instance")
        return 0
    logging.info("Using view=%s log=%s", view, log_path)
    app_view_url = _register_instance_later(view, time.time())
    ft.run(main=app, view=view, host="127.0.0.1", port=0)
    _clear_instance(app_view_url)
    return 0


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
