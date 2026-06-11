import asyncio

from sonarqube_profile_creator import gui
from sonarqube_profile_creator.gui import (
    apply_target_settings_to_profile_rule_rows,
    apply_target_settings_to_rule_rows,
    languages_from_rows,
    profile_export_dir,
    selected_profiles_by_language,
    target_profile_for_language,
    visible_reports_dir,
)
from sonarqube_profile_creator.models import (
    ActionResult,
    FieldMapping,
    ItemStatus,
    PrecheckResult,
    ProfileRuleRow,
    ProfileSyncMode,
    ProfileSyncPlan,
    RuleRow,
    ValidationIssue,
)


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


def test_apply_target_settings_to_rule_rows_uses_ui_values():
    rows = [
        RuleRow(
            source_row=2,
            language="old",
            target_profile="OldProfile",
            rule_key="java:S1144",
            strategy="copy",
            project_key="old-project",
            set_default="false",
            severity="MAJOR",
        )
    ]

    result = apply_target_settings_to_rule_rows(
        rows,
        language="java",
        target_profile="Demo",
        strategy="extend_default",
        project_key="project-one",
        set_default=True,
    )

    assert result[0].language == "java"
    assert result[0].target_profile == "Demo"
    assert result[0].strategy == "extend_default"
    assert result[0].project_key == "project-one"
    assert result[0].set_default == "true"
    assert result[0].severity == "MAJOR"


def test_apply_target_settings_to_profile_rule_rows_uses_ui_values():
    rows = [
        ProfileRuleRow(
            source_row=2,
            language="old",
            target_profile="OldProfile",
            profile_key="old-key",
            source_profile="Sonar way",
            rule_key="java:S1144",
            active="false",
        )
    ]

    result = apply_target_settings_to_profile_rule_rows(rows, language="java", target_profile="Demo")

    assert result[0].language == "java"
    assert result[0].target_profile == "Demo"
    assert result[0].rule_key == "java:S1144"
    assert result[0].active == "false"


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


def test_target_language_defaults_before_strategy_detail_renders():
    app = object.__new__(gui.ProfileCreatorApp)
    app.available_profiles = {"java": ["Sonar way"], "js": ["Sonar way"]}
    app.default_profiles = {"java": "Sonar way"}
    app.target_language = type("Dropdown", (), {"value": "", "options": []})()
    app.strategy_group = type("StrategyGroup", (), {"value": "copy"})()
    app.copy_dropdowns = {}
    app.client = object()
    app._export_busy = False
    app.t = lambda key, **_kwargs: key

    gui.ProfileCreatorApp._refresh_target_language_options(app)
    control = gui.ProfileCreatorApp._strategy_detail_control(app)

    assert app.target_language.value == "java"
    assert "java" in app.copy_dropdowns
    assert control is not None


def test_workspace_scroll_event_tracks_offset():
    app = object.__new__(gui.ProfileCreatorApp)
    app._workspace_scroll_offset = 0.0
    event = type("ScrollEvent", (), {"pixels": 420.5})()

    gui.ProfileCreatorApp._on_workspace_scroll(app, event)

    assert app._workspace_scroll_offset == 420.5


def test_render_reuses_page_shell_and_restores_scroll():
    calls = []

    class DummyPage:
        def clean(self):
            calls.append("clean")

        def add(self, _control):
            calls.append("add")

        def update(self):
            calls.append("update")

        def run_task(self, handler):
            calls.append(("run_task", handler.__name__))

    app = object.__new__(gui.ProfileCreatorApp)
    app.page = DummyPage()
    app.server_url = type("Field", (), {"label": ""})()
    app.token = type("Field", (), {"label": ""})()
    app.remember_token = type("Field", (), {"label": ""})()
    app.file_path = type("Field", (), {"label": ""})()
    app.export_language = type("Dropdown", (), {"label": ""})()
    app.export_profile = type("Dropdown", (), {"label": ""})()
    app.sync_mode = type("Dropdown", (), {"label": "", "options": []})()
    app.top_bar_host = type("Host", (), {"content": None})()
    app.sidebar_host = type("Host", (), {"content": None})()
    app.workspace_header_host = type("Host", (), {"content": None})()
    app.main_area = type("MainArea", (), {"controls": []})()
    app.workspace_panel_hosts = []
    app._page_built = False
    app._workspace_scroll_offset = 300.0
    app.t = lambda key, **_kwargs: key
    app._refresh_strategy_options = lambda: calls.append("strategy_options")
    app._refresh_export_controls = lambda: calls.append("export_controls")
    app._top_bar = lambda: "top"
    app._sidebar = lambda: "sidebar"
    app._workspace_header = lambda: "header"
    app._workspace_controls = lambda: ["content"]
    app._app_shell = lambda: "shell"

    gui.ProfileCreatorApp.render(app)
    gui.ProfileCreatorApp.render(app)

    assert calls.count("clean") == 1
    assert calls.count("add") == 1
    assert calls.count(("run_task", "_restore_workspace_scroll")) == 2
    assert len(app.main_area.controls) == 1
    assert app.main_area.controls[0].content == "content"


def test_workspace_panel_refresh_updates_mounted_host():
    calls = []

    class Host:
        def __init__(self):
            self.content = "old"

        def update(self):
            calls.append(("host_update", self.content))

    app = object.__new__(gui.ProfileCreatorApp)
    app._page_built = True
    app.workspace_panel_hosts = [Host()]
    app.page = type("Page", (), {"update": lambda _self: calls.append("page_update")})()

    gui.ProfileCreatorApp._refresh_workspace_panel(app, 0, "new")

    assert app.workspace_panel_hosts[0].content == "new"
    assert calls == [("host_update", "new")]


def test_export_progress_sets_busy_state_and_top_progress():
    calls = []

    app = object.__new__(gui.ProfileCreatorApp)
    app._exit_requested = False
    app._export_busy = False
    app.export_progress = type("Progress", (), {"visible": False, "value": 0})()
    app.export_status = type("Text", (), {"value": "", "color": ""})()
    app.progress = type("Progress", (), {"visible": False, "value": 0})()
    app.render = lambda: calls.append("render")

    gui.ProfileCreatorApp._set_export_progress(app, "Reading", 0.4, gui.PRIMARY)

    assert app._export_busy is True
    assert app.export_progress.visible is True
    assert app.export_progress.value == 0.4
    assert app.export_status.value == "Reading"
    assert app.progress.visible is True
    assert app.progress.value == 0.4
    assert calls == ["render"]


def test_export_progress_uses_sync_area_refresh_when_page_is_built():
    calls = []

    app = object.__new__(gui.ProfileCreatorApp)
    app._exit_requested = False
    app._export_busy = False
    app._page_built = True
    app.export_progress = type("Progress", (), {"visible": False, "value": 0})()
    app.export_status = type("Text", (), {"value": "", "color": ""})()
    app.progress = type("Progress", (), {"visible": False, "value": 0})()
    app.render = lambda: calls.append("render")
    app._refresh_sync_area = lambda include_dependent_panels=True: calls.append(("sync_area", include_dependent_panels))

    gui.ProfileCreatorApp._set_export_progress(app, "Reading", 0.4, gui.PRIMARY)

    assert calls == [("sync_area", False)]


def test_sync_precheck_worker_uses_local_refresh(monkeypatch):
    calls = []

    class FakeService:
        def __init__(self, client):
            self.client = client

        def precheck(self, rows, mode):
            calls.append(("precheck", mode.value))
            return ProfileSyncPlan(mode=mode, rows=[])

    app = object.__new__(gui.ProfileCreatorApp)
    app.client = object()
    app.spreadsheet = object()
    app.sync_mode = type("Mode", (), {"value": ProfileSyncMode.PATCH.value})()
    app.sync_status = type("Text", (), {"value": "", "color": ""})()
    app._exit_requested = False
    app._save_mapping = lambda: calls.append("save_mapping")
    app._save_target_settings = lambda: calls.append("save_target")
    app._target_settings_error = lambda: ""
    app._rule_key_source = lambda: "rule_key"
    app._set_busy = lambda busy: calls.append(("busy", busy))
    app._current_profile_rule_rows = lambda: []
    app._set_status = lambda control, value, color: calls.append(("status", value, color))
    app._refresh_sync_area = lambda include_dependent_panels=True: calls.append(("sync_area", include_dependent_panels))
    app.render = lambda: calls.append("render")
    monkeypatch.setattr(gui, "ProfileSyncService", FakeService)

    gui.ProfileCreatorApp._sync_precheck_worker(app)

    assert ("sync_area", True) in calls
    assert "render" not in calls


def test_connect_worker_marks_connected_before_catalog_load(monkeypatch):
    calls = []
    background_workers = []

    class FakeClient:
        def __init__(self, server_url, token):
            calls.append(("client", server_url, token))
            self.server_url = server_url

        def test_connection(self):
            calls.append(("test_connection", None))
            return gui.ConnectionInfo(
                server_url="https://sonar.example",
                version="10.4",
                status="UP",
                auth_mode="Bearer token",
                capabilities={},
            )

    class DummyStore:
        def save(self, config):
            calls.append(("save", config.server_url))

        def save_token(self, server_url, token):
            calls.append(("save_token", server_url, token))

    app = object.__new__(gui.ProfileCreatorApp)
    app.server_url = type("Field", (), {"value": "https://sonar.example"})()
    app.token = type("Field", (), {"value": "secret"})()
    app.remember_token = type("Remember", (), {"value": True})()
    app.connection_status = type("Text", (), {"value": "", "color": ""})()
    app.config = type("Config", (), {"server_url": "", "language": ""})()
    app.translator = type("Translator", (), {"language": "zh"})()
    app.store = DummyStore()
    app.available_profiles = {"java": ["Old"]}
    app.default_profiles = {"java": "Old"}
    app.profile_catalog_loading = False
    app.profile_catalog_error = ""
    app.api_capability_loading = False
    app.api_capability_error = ""
    app._connection_catalog_token = 0
    app._exit_requested = False
    app._set_busy = lambda busy: calls.append(("busy", busy))
    app._set_status = lambda control, value, color: calls.append(("status", value, color))
    app.render = lambda: calls.append(("render", app.profile_catalog_loading, bool(app.available_profiles)))
    app._run_background = lambda worker: background_workers.append(worker)
    app.t = lambda key, **_kwargs: key
    monkeypatch.setattr(gui, "SonarQubeClient", FakeClient)

    gui.ProfileCreatorApp._connect_worker(app)

    assert app.client is not None
    assert app.connection_info.status == "UP"
    assert app.available_profiles == {}
    assert app.profile_catalog_loading is True
    assert app.api_capability_loading is True
    assert len(background_workers) == 1
    assert calls.index(("status", "connected", gui.SUCCESS)) < calls.index(("render", True, False))
    assert ("save_token", "https://sonar.example", "secret") in calls


def test_connection_catalog_worker_updates_profiles_before_capabilities():
    calls = []

    class FakeClient:
        def search_quality_profiles(self):
            calls.append("profiles")
            return [
                {"language": "java", "name": "Sonar way", "isDefault": True},
                {"language": "java", "name": "Custom", "isDefault": False},
            ]

        def get_default_profiles(self):
            calls.append("defaults")
            return {"java": "Sonar way"}

        def load_capabilities(self):
            calls.append(("before_capabilities", dict(app.available_profiles), app.profile_catalog_loading))
            return {"create_profile": True}

    app = object.__new__(gui.ProfileCreatorApp)
    client = FakeClient()
    app.client = client
    app.connection_info = gui.ConnectionInfo("https://sonar.example", "10.4", "UP", "Bearer token", {})
    app.available_profiles = {}
    app.default_profiles = {}
    app.profile_catalog_loading = True
    app.profile_catalog_error = ""
    app.api_capability_loading = True
    app.api_capability_error = ""
    app._connection_catalog_token = 3
    app._exit_requested = False
    app._refresh_target_language_options = lambda: calls.append("target_options")
    app._refresh_profile_dropdowns = lambda: calls.append("profile_dropdowns")
    app.render = lambda: calls.append(("render", app.profile_catalog_loading, app.api_capability_loading))

    gui.ProfileCreatorApp._connection_catalog_worker(app, client, 3)

    assert app.available_profiles == {"java": ["Custom", "Sonar way"]}
    assert app.default_profiles == {"java": "Sonar way"}
    assert app.connection_info.capabilities == {"create_profile": True}
    assert ("before_capabilities", {"java": ["Custom", "Sonar way"]}, False) in calls
    assert calls[-1] == ("render", False, False)


def test_profile_export_dir_uses_exe_directory_when_frozen(monkeypatch, tmp_path):
    exe_path = tmp_path / "SonarQubeProfileCreator.exe"
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(exe_path))

    assert profile_export_dir() == tmp_path


def test_visible_reports_dir_uses_exe_directory_when_frozen(monkeypatch, tmp_path):
    exe_path = tmp_path / "SonarQubeProfileCreator.exe"
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(exe_path))

    assert visible_reports_dir() == tmp_path / "reports"


def test_default_profiles_are_derived_from_loaded_profiles():
    app = object.__new__(gui.ProfileCreatorApp)
    profiles = [
        {"language": "java", "name": "Sonar way", "isDefault": True},
        {"language": "java", "name": "Custom", "isDefault": False},
        {"language": "js", "name": "Sonar way", "default": "true"},
    ]

    assert gui.ProfileCreatorApp._default_profiles_from_profiles(app, profiles) == {
        "java": "Sonar way",
        "js": "Sonar way",
    }


def test_issue_sort_key_prioritizes_errors():
    issues = [
        ValidationIssue(status=ItemStatus.OK, category="ok", message="ok", source_row=2),
        ValidationIssue(status=ItemStatus.WARNING, category="warning", message="warning", source_row=4),
        ValidationIssue(status=ItemStatus.ERROR, category="error", message="error", source_row=8),
        ValidationIssue(status=ItemStatus.ERROR, category="early_error", message="early", source_row=3),
    ]

    ordered = sorted(issues, key=gui._issue_sort_key)

    assert [issue.category for issue in ordered] == ["early_error", "error", "warning", "ok"]


def test_sync_display_items_include_issues_before_actions():
    plan = ProfileSyncPlan(
        mode=ProfileSyncMode.PATCH,
        rows=[],
        issues=[
            ValidationIssue(status=ItemStatus.WARNING, category="warn", message="warning", source_row=5),
            ValidationIssue(status=ItemStatus.ERROR, category="missing_rule", message="missing", source_row=9),
        ],
        actions=[
            ActionResult(status=ItemStatus.OK, action="activate", message="planned", language="java", profile="Demo", rule_key="java:S1", source_row=2),
        ],
    )

    items = gui._sync_display_items(plan)

    assert [(item.status, item.action) for item in items] == [
        (ItemStatus.ERROR, "missing_rule"),
        (ItemStatus.WARNING, "warn"),
        (ItemStatus.OK, "activate"),
    ]


def test_sync_display_filter_by_error_returns_issue_rows():
    plan = ProfileSyncPlan(
        mode=ProfileSyncMode.PATCH,
        rows=[],
        issues=[ValidationIssue(status=ItemStatus.ERROR, category="missing_rule", message="missing", source_row=9)],
        actions=[ActionResult(status=ItemStatus.OK, action="activate", message="planned", rule_key="java:S1")],
    )

    items = gui._filter_sync_display_items(gui._sync_display_items(plan), gui.SYNC_FILTER_ERROR)

    assert len(items) == 1
    assert items[0].action == "missing_rule"
    assert items[0].status == ItemStatus.ERROR


def test_precheck_and_sync_precheck_states_are_mutually_exclusive():
    app = object.__new__(gui.ProfileCreatorApp)
    app.sync_action_filter = "activate"
    app.precheck_result = None
    app.apply_result = "old_apply"
    app.sync_plan = ProfileSyncPlan(mode=ProfileSyncMode.PATCH, rows=[])
    app.sync_result = "old_sync"
    precheck = PrecheckResult(rows=[], profile_plans=[])

    gui.ProfileCreatorApp._set_precheck_result(app, precheck)

    assert app.precheck_result is precheck
    assert app.apply_result is None
    assert app.sync_plan is None
    assert app.sync_result is None
    assert app.sync_action_filter == gui.SYNC_FILTER_ALL

    sync_plan = ProfileSyncPlan(mode=ProfileSyncMode.PATCH, rows=[])
    app.precheck_result = precheck
    app.apply_result = "old_apply"

    gui.ProfileCreatorApp._set_sync_plan(app, sync_plan)

    assert app.sync_plan is sync_plan
    assert app.sync_result is None
    assert app.precheck_result is None
    assert app.apply_result is None
    assert app.sync_action_filter == gui.SYNC_FILTER_ALL


def test_current_step_index_includes_profile_sync_step():
    app = object.__new__(gui.ProfileCreatorApp)
    app.apply_result = None
    app.sync_result = None
    app.precheck_result = None
    app.sync_plan = None
    app.spreadsheet = object()
    app.connection_info = object()

    assert gui.ProfileCreatorApp._current_step_index(app) == 2

    app.sync_plan = ProfileSyncPlan(mode=ProfileSyncMode.PATCH, rows=[])
    assert gui.ProfileCreatorApp._current_step_index(app) == 5

    app.sync_result = type("SyncResult", (), {})()
    assert gui.ProfileCreatorApp._current_step_index(app) == 6


def test_step_titles_include_profile_sync():
    app = object.__new__(gui.ProfileCreatorApp)
    app.t = lambda key, **_kwargs: key

    assert gui.ProfileCreatorApp._step_titles(app) == [
        "step_connect",
        "step_import",
        "strategy",
        "profile_sync",
        "step_precheck",
        "step_apply",
        "step_report",
    ]


def test_mapping_dropdown_options_are_independent_instances():
    app = object.__new__(gui.ProfileCreatorApp)
    app.spreadsheet = type(
        "Spreadsheet",
        (),
        {"headers": ["rule_key", "severity", "params"], "inferred_mapping": None},
    )()
    app.mapping_controls = {}
    app.config = type("Config", (), {"last_mapping": {}})()

    gui.ProfileCreatorApp._ensure_mapping_controls(app, reset=True)

    rule_options = app.mapping_controls["rule_key"].options
    severity_options = app.mapping_controls["severity"].options
    assert rule_options is not severity_options
    assert rule_options[0] is not severity_options[0]
    assert [option.key for option in rule_options] == ["", "rule_key", "severity", "params"]


def test_mapping_controls_are_created_for_display_fields_only():
    app = object.__new__(gui.ProfileCreatorApp)
    app.spreadsheet = type(
        "Spreadsheet",
        (),
        {"headers": ["rule_key", "severity", "params"], "inferred_mapping": None},
    )()
    app.mapping_controls = {}
    app.config = type("Config", (), {"last_mapping": {}})()

    gui.ProfileCreatorApp._ensure_mapping_controls(app, reset=True)

    assert set(app.mapping_controls) == set(gui.DISPLAY_MAPPING_FIELDS)
    assert "source_row" in app.mapping_controls
    assert "target_profile" not in app.mapping_controls


def test_saved_empty_mapping_does_not_override_inferred_rule_key():
    app = object.__new__(gui.ProfileCreatorApp)
    app.spreadsheet = type(
        "Spreadsheet",
        (),
        {
            "headers": ["rule_key", "severity"],
            "inferred_mapping": FieldMapping({"rule_key": "rule_key", "severity": "severity"}),
        },
    )()
    app.mapping_controls = {}
    app.config = type("Config", (), {"last_mapping": {"rule_key": ""}})()

    gui.ProfileCreatorApp._ensure_mapping_controls(app, reset=True)

    assert app.mapping_controls["rule_key"].value == "rule_key"


def test_save_mapping_initializes_controls_from_inferred_mapping():
    calls = []
    app = object.__new__(gui.ProfileCreatorApp)
    app.spreadsheet = type(
        "Spreadsheet",
        (),
        {
            "headers": ["rule_key", "severity"],
            "inferred_mapping": FieldMapping({"rule_key": "rule_key", "severity": "severity"}),
        },
    )()
    app.mapping_controls = {}
    app.config = type("Config", (), {"last_mapping": {}})()
    app.store = type("Store", (), {"save": lambda _self, _config: calls.append("save")})()

    gui.ProfileCreatorApp._save_mapping(app)

    assert app.mapping.source_for("rule_key") == "rule_key"
    assert app.config.last_mapping["rule_key"] == "rule_key"
    assert calls == ["save"]


def test_mapping_groups_do_not_repeat_fields():
    grouped = [field for _title, fields in gui.DISPLAY_MAPPING_GROUPS for field in fields]

    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(gui.DISPLAY_MAPPING_FIELDS)
    assert "rule_key" in gui.DISPLAY_MAPPING_FIELDS
    assert "target_profile" not in gui.DISPLAY_MAPPING_FIELDS
    assert "project_key" not in gui.DISPLAY_MAPPING_FIELDS


def test_duplicate_mapping_sources_are_reported():
    app = object.__new__(gui.ProfileCreatorApp)
    app.mapping_controls = {
        "active": type("Dropdown", (), {"value": "规则Key"})(),
        "rule_key": type("Dropdown", (), {"value": "规则Key"})(),
        "severity": type("Dropdown", (), {"value": ""})(),
    }

    duplicates = gui.ProfileCreatorApp._duplicate_mapping_sources(app)

    assert duplicates == {"规则Key": ["active", "rule_key"]}


def test_save_mapping_and_refresh_persists_then_renders():
    calls = []

    app = object.__new__(gui.ProfileCreatorApp)
    app.mapping_controls = {
        "language": type("Dropdown", (), {"value": "语言"})(),
        "rule_key": type("Dropdown", (), {"value": "规则Key"})(),
    }
    app.config = type("Config", (), {"last_mapping": {}})()
    app.store = type("Store", (), {"save": lambda _self, _config: calls.append("save")})()
    app.render = lambda: calls.append("render")

    gui.ProfileCreatorApp._save_mapping_and_refresh(app)

    assert app.config.last_mapping == {"language": "语言", "rule_key": "规则Key"}
    assert calls == ["save", "render"]


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


def test_open_report_folder_reports_os_error(monkeypatch, tmp_path):
    calls = []
    report = tmp_path / "report.xlsx"
    report.write_text("", encoding="utf-8")

    app = object.__new__(gui.ProfileCreatorApp)
    app.apply_result = type("ApplyResult", (), {"report_xlsx": report})()
    app.sync_result = None
    app._show_message = lambda message: calls.append(message)

    def fail_open(_folder):
        raise OSError("cannot open folder")

    monkeypatch.setattr(gui.os, "startfile", fail_open, raising=False)

    gui.ProfileCreatorApp._open_report_folder(app, None)

    assert calls == ["cannot open folder"]
