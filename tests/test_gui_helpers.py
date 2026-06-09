import asyncio

from sonarqube_profile_creator import gui
from sonarqube_profile_creator.gui import languages_from_rows, selected_profiles_by_language, target_profile_for_language
from sonarqube_profile_creator.models import RuleRow


def test_languages_from_rows_returns_sorted_non_empty_languages():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144"),
        RuleRow(source_row=3, language="js", target_profile="Demo", rule_key="javascript:S1128"),
        RuleRow(source_row=4, language="", target_profile="Demo", rule_key="java:S1144"),
        RuleRow(source_row=5, language="java", target_profile="Demo", rule_key="java:S1144"),
    ]

    assert languages_from_rows(rows) == ["java", "js"]


def test_selected_profiles_by_language_filters_to_input_languages():
    values = {"java": "Base Java", "js": "Base JS", "py": "Base Py", "cpp": ""}

    result = selected_profiles_by_language(values, ["java", "js", "cpp"])

    assert result == {"java": "Base Java", "js": "Base JS"}


def test_target_profile_for_language_returns_single_target():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144"),
        RuleRow(source_row=3, language="java", target_profile="Demo", rule_key="java:S112"),
        RuleRow(source_row=4, language="js", target_profile="JsDemo", rule_key="javascript:S1128"),
    ]

    assert target_profile_for_language(rows, "java") == "Demo"
    assert target_profile_for_language(rows, "js") == "JsDemo"


def test_target_profile_for_language_returns_empty_for_multiple_targets():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144"),
        RuleRow(source_row=3, language="java", target_profile="Other", rule_key="java:S112"),
    ]

    assert target_profile_for_language(rows, "java") == ""


def test_desktop_disconnect_does_not_start_exit_thread(monkeypatch):
    started = []

    class DummyThread:
        def __init__(self, target, daemon):
            started.append((target, daemon))

        def start(self):
            started.append("started")

    app = object.__new__(gui.ProfileCreatorApp)
    monkeypatch.setenv("SONARQUBE_PROFILE_CREATOR_VIEW", "desktop")
    monkeypatch.setattr(gui.threading, "Thread", DummyThread)

    gui.ProfileCreatorApp._on_disconnect(app, None)

    assert started == []


def test_web_disconnect_starts_exit_thread(monkeypatch):
    started = []

    class DummyThread:
        def __init__(self, target, daemon):
            started.append((target, daemon))

        def start(self):
            started.append("started")

    app = object.__new__(gui.ProfileCreatorApp)
    app._disconnect_token = 0
    app._connected = True
    monkeypatch.setenv("SONARQUBE_PROFILE_CREATOR_VIEW", "web")
    monkeypatch.setattr(gui.threading, "Thread", DummyThread)

    gui.ProfileCreatorApp._on_disconnect(app, None)

    assert app._disconnect_token == 1
    assert app._connected is False
    assert len(started) == 2
    assert started[0][1] is True


def test_shutdown_app_exits_once_and_clears_busy(monkeypatch):
    calls = []

    class DummyPage:
        def pop_dialog(self):
            calls.append("pop")

        def update(self):
            calls.append("update")

    app = object.__new__(gui.ProfileCreatorApp)
    app.page = DummyPage()
    app.progress = type("Progress", (), {"visible": True, "value": None})()
    app._exit_requested = False
    monkeypatch.setattr(gui, "_request_process_exit", lambda: calls.append("exit"))

    gui.ProfileCreatorApp._shutdown_app(app)
    gui.ProfileCreatorApp._shutdown_app(app)

    assert calls == ["pop", "update", "exit"]
    assert app._exit_requested is True
    assert app.progress.visible is False
    assert app.progress.value == 0


def test_exit_requested_suppresses_ui_updates():
    calls = []

    class DummyPage:
        def update(self):
            calls.append("update")

        def show_dialog(self, _dialog):
            calls.append("dialog")

    app = object.__new__(gui.ProfileCreatorApp)
    app.page = DummyPage()
    app.progress = type("Progress", (), {"visible": False, "value": 0})()
    app._exit_requested = True
    text = type("Text", (), {"value": "", "color": ""})()

    gui.ProfileCreatorApp._set_busy(app, True)
    gui.ProfileCreatorApp._set_status(app, text, "value", "color")
    gui.ProfileCreatorApp._show_message(app, "message")

    assert calls == []
    assert app.progress.visible is False
    assert text.value == ""


def test_apply_picked_files_updates_path_and_config():
    calls = []

    class DummyStore:
        def save(self, config):
            calls.append(("save", config.last_file))

    class DummyPage:
        def update(self):
            calls.append(("update", None))

    app = object.__new__(gui.ProfileCreatorApp)
    app.file_path = type("TextField", (), {"value": ""})()
    app.config = type("Config", (), {"last_file": ""})()
    app.store = DummyStore()
    app.page = DummyPage()
    picked = type("PickedFile", (), {"path": r"C:\tmp\rules.xlsx"})()

    gui.ProfileCreatorApp._apply_picked_files(app, [picked])

    assert app.file_path.value == r"C:\tmp\rules.xlsx"
    assert app.config.last_file == r"C:\tmp\rules.xlsx"
    assert calls == [("save", r"C:\tmp\rules.xlsx"), ("update", None)]


def test_choose_file_awaits_flet_file_picker():
    calls = []
    picked = type("PickedFile", (), {"path": r"C:\tmp\rules.csv"})()

    class DummyPicker:
        async def pick_files(self, **kwargs):
            calls.append(("pick", kwargs))
            return [picked]

    app = object.__new__(gui.ProfileCreatorApp)
    app.file_picker = DummyPicker()
    app.t = lambda key: key
    app._apply_picked_files = lambda files: calls.append(("apply", list(files)))

    asyncio.run(gui.ProfileCreatorApp._choose_file(app, None))

    assert calls[0][0] == "pick"
    assert calls[0][1]["allow_multiple"] is False
    assert calls[0][1]["allowed_extensions"] == ["csv", "xlsx", "xlsm"]
    assert calls[1] == ("apply", [picked])


def test_strategy_change_updates_detail_without_full_render():
    calls = []

    class DummyStore:
        def save(self, config):
            calls.append(("save", config.default_strategy))

    class DummyDetail:
        def update(self):
            calls.append(("detail_update", None))

    app = object.__new__(gui.ProfileCreatorApp)
    app.strategy_group = type("StrategyGroup", (), {"value": "copy"})()
    app.config = type("Config", (), {"default_strategy": ""})()
    app.store = DummyStore()
    app.strategy_detail = DummyDetail()
    app._refresh_strategy_detail = lambda: calls.append(("refresh_detail", None))
    app.render = lambda: calls.append(("render", None))

    gui.ProfileCreatorApp._on_strategy_change(app, None)

    assert app.config.default_strategy == "copy"
    assert calls == [
        ("save", "copy"),
        ("refresh_detail", None),
        ("detail_update", None),
    ]


def test_open_report_folder_uses_sync_result(monkeypatch, tmp_path):
    calls = []
    report = tmp_path / "sync_report.xlsx"
    report.write_text("", encoding="utf-8")

    app = object.__new__(gui.ProfileCreatorApp)
    app.apply_result = None
    app.sync_result = type("SyncResult", (), {"report_xlsx": report})()
    monkeypatch.setattr(gui.os, "startfile", lambda folder: calls.append(folder), raising=False)

    gui.ProfileCreatorApp._open_report_folder(app, None)

    assert calls == [str(tmp_path)]
