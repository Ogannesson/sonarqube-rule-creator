from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

import flet as ft

from .config import APP_DISPLAY_NAME, AppConfig, ConfigStore, reports_dir, user_config_dir
from .i18n import Translator, detect_language
from .models import (
    ALL_FIELDS,
    ActionResult,
    FieldMapping,
    ItemStatus,
    ProfileRuleRow,
    ProfileSyncMode,
    ProfileSyncPlan,
    ProfileSyncResult,
    ProfileStrategy,
    REQUIRED_FIELDS,
    RuleRow,
    SpreadsheetData,
    ValidationIssue,
)
from .profile_sync import ProfileExportService, ProfileSyncService
from .reports import export_apply_report, export_precheck_report, export_sync_precheck_report, export_sync_report
from .sonarqube import ConnectionInfo, SonarQubeClient, SonarQubeError, _profile_is_default
from .spreadsheet import profile_rule_rows_from_mapping, read_spreadsheet, rows_from_mapping, validate_required_mapping
from .workflow import WorkflowService, parse_strategy


RULE_MAPPING_FIELDS = (
    "rule_key",
    "active",
    "severity",
    "params",
    "prioritizedRule",
    "sync_action",
    "note",
)
SYNC_INFO_MAPPING_FIELDS = (
    "source_row",
    "profile_key",
    "source_profile",
    "rule_name",
    "inheritance",
)
DISPLAY_MAPPING_FIELDS = tuple(dict.fromkeys((*RULE_MAPPING_FIELDS, *SYNC_INFO_MAPPING_FIELDS)))
DISPLAY_MAPPING_GROUPS = (
    ("mapping_group_rules", RULE_MAPPING_FIELDS),
    ("mapping_group_sync", SYNC_INFO_MAPPING_FIELDS),
)

APP_BG = "#F5F7FA"
SURFACE = "#FFFFFF"
SURFACE_MUTED = "#F8FAFC"
LINE = "#D9E1EA"
LINE_SOFT = "#E8EEF5"
TEXT = "#172033"
TEXT_MUTED = "#5D697C"
TEXT_SUBTLE = "#8A95A5"
PRIMARY = "#2563EB"
PRIMARY_DARK = "#1E40AF"
PRIMARY_SOFT = "#EAF1FF"
SUCCESS = "#0F766E"
SUCCESS_SOFT = "#E6F4F1"
WARNING = "#B45309"
WARNING_SOFT = "#FFF4E5"
DANGER = "#B42318"
DANGER_SOFT = "#FDECEC"
NEUTRAL_SOFT = "#EEF2F6"
SIDEBAR = "#111827"
SIDEBAR_MUTED = "#9CA3AF"
EXIT_DELAY_SECONDS = 15
TITLE_BAR_HEIGHT = 58
RESIZE_HANDLE_SIZE = 8
FONT_FAMILY = "Microsoft YaHei"
WEIGHT_REGULAR = ft.FontWeight.W_400
WEIGHT_MEDIUM = ft.FontWeight.W_500
WEIGHT_SEMIBOLD = ft.FontWeight.W_600
SYNC_FILTER_ALL = "all"
SYNC_FILTER_ERROR = "error"
SYNC_FILTER_WARNING = "warning"
SYNC_ACTION_TABLE_LIMIT = 80
SYNC_ACTION_TABLE_HEIGHT = 320
WORKSPACE_PROFILE_SYNC_INDEX = 2
WORKSPACE_PRECHECK_INDEX = 4
WORKSPACE_APPLY_INDEX = 5
WORKSPACE_REPORT_INDEX = 6


@dataclass(frozen=True)
class SyncDisplayItem:
    status: ItemStatus
    action: str
    message: str
    language: str = ""
    profile: str = ""
    rule_key: str = ""
    source_row: int | None = None
    suggestion: str = ""


class ProfileCreatorApp:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.store = ConfigStore()
        self.config = self.store.load()
        self.translator = Translator(self.config.language or detect_language())
        self.client: SonarQubeClient | None = None
        self.connection_info: ConnectionInfo | None = None
        self.spreadsheet: SpreadsheetData | None = None
        self.mapping: FieldMapping | None = None
        self.precheck_result = None
        self.apply_result = None
        self.sync_plan = None
        self.sync_result = None
        self.sync_action_filter = SYNC_FILTER_ALL
        self.available_profiles: dict[str, list[str]] = {}
        self.default_profiles: dict[str, str] = {}
        self.profile_catalog_loading = False
        self.profile_catalog_error = ""
        self.api_capability_loading = False
        self.api_capability_error = ""
        self._connection_catalog_token = 0

        self.server_url = ft.TextField(
            label=self.t("server_url"),
            value=self.config.server_url,
            width=430,
            height=56,
            border_radius=6,
            border_color=LINE,
            focused_border_color=PRIMARY,
            filled=True,
            fill_color=SURFACE,
        )
        self.token = ft.TextField(
            label=self.t("token"),
            password=True,
            can_reveal_password=True,
            width=430,
            height=56,
            border_radius=6,
            border_color=LINE,
            focused_border_color=PRIMARY,
            filled=True,
            fill_color=SURFACE,
        )
        if self.config.server_url:
            self.token.value = self.store.load_token(self.config.server_url)
        self.remember_token = ft.Checkbox(label=self.t("remember_token"), value=True)
        self.connection_status = ft.Text(self.t("status_ready"), color=TEXT_MUTED)

        self.file_path = ft.TextField(
            label=self.t("selected_file"),
            value=self.config.last_file,
            read_only=False,
            width=650,
            height=56,
            border_radius=6,
            border_color=LINE,
            focused_border_color=PRIMARY,
            filled=True,
            fill_color=SURFACE,
        )
        self.file_picker = ft.FilePicker()

        self.mapping_controls: dict[str, ft.Dropdown] = {}
        self.target_language = ft.Dropdown(
            label=self.t("target_language"),
            value=self.config.target_language,
            width=180,
            enable_search=True,
            editable=True,
            border_color=LINE,
            focused_border_color=PRIMARY,
            border_radius=6,
            on_select=self._on_target_language_change,
            on_blur=self._on_target_language_change,
        )
        self.target_profile = ft.TextField(
            label=self.t("target_profile"),
            value=self.config.target_profile,
            width=360,
            height=56,
            border_radius=6,
            border_color=LINE,
            focused_border_color=PRIMARY,
            filled=True,
            fill_color=SURFACE,
        )
        self.project_key = ft.TextField(
            label=self.t("project_key"),
            value=self.config.project_key,
            width=300,
            height=56,
            border_radius=6,
            border_color=LINE,
            focused_border_color=PRIMARY,
            filled=True,
            fill_color=SURFACE,
        )
        self.set_default_profile = ft.Checkbox(label=self.t("set_default_profile"), value=self.config.set_default)
        self.strategy_group = ft.RadioGroup(
            value=self.config.default_strategy or ProfileStrategy.EXTEND_DEFAULT.value,
            content=ft.Column([]),
            on_change=self._on_strategy_change,
        )
        self.strategy_detail = ft.Column([], spacing=8)
        self.parent_dropdowns: dict[str, ft.Dropdown] = {}
        self.copy_dropdowns: dict[str, ft.Dropdown] = {}
        self.export_language = ft.Dropdown(label=self.t("export_language"), width=180, enable_search=True, border_color=LINE, focused_border_color=PRIMARY, border_radius=6)
        self.export_profile = ft.Dropdown(label=self.t("export_profile"), width=360, enable_search=True, border_color=LINE, focused_border_color=PRIMARY, border_radius=6)
        self.sync_mode = ft.Dropdown(
            label=self.t("sync_mode"),
            value=ProfileSyncMode.PATCH.value,
            width=180,
            options=[
                ft.DropdownOption(key=ProfileSyncMode.PATCH.value, text=self.t("sync_mode_patch")),
                ft.DropdownOption(key=ProfileSyncMode.REPLACE.value, text=self.t("sync_mode_replace")),
            ],
            border_color=LINE,
            focused_border_color=PRIMARY,
            border_radius=6,
        )
        self.sync_status = ft.Text("", color=TEXT_MUTED)
        self.export_status = ft.Text("", color=TEXT_MUTED)
        self.export_progress = ft.ProgressBar(visible=False, value=0, color=PRIMARY, bgcolor=PRIMARY_SOFT)
        self._export_busy = False
        self.precheck_status = ft.Text("", color=TEXT_MUTED)
        self.progress = ft.ProgressBar(visible=False, color=PRIMARY, bgcolor=PRIMARY_SOFT)
        self.main_area = ft.Column(
            [],
            spacing=16,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            scroll_interval=80,
            on_scroll=self._on_workspace_scroll,
        )
        self.top_bar_host = ft.Container()
        self.sidebar_host = ft.Container()
        self.workspace_header_host = ft.Container()
        self.workspace_panel_hosts: list[ft.Container] = []
        self._page_built = False
        self._workspace_scroll_offset = 0.0
        self._disconnect_token = 0
        self._connected = True
        self._exit_requested = False

    def run(self) -> None:
        self.page.title = APP_DISPLAY_NAME
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.theme = ft.Theme(font_family=FONT_FAMILY)
        self.page.window.width = 1320
        self.page.window.height = 860
        self.page.window.min_width = 980
        self.page.window.min_height = 680
        self.page.window.movable = True
        self.page.window.resizable = True
        self.page.window.minimizable = True
        self.page.window.maximizable = True
        self.page.window.frameless = True
        self.page.window.title_bar_hidden = True
        self.page.window.title_bar_buttons_hidden = True
        self.page.window.shadow = True
        self.page.padding = 0
        self.page.bgcolor = APP_BG
        self.page.window.on_event = self._on_window_event
        self.page.on_connect = self._on_connect
        self.page.on_disconnect = self._on_disconnect
        self.page.services.append(self.file_picker)
        self.render()

    def t(self, key: str, **kwargs: object) -> str:
        return self.translator.t(key, **kwargs)

    def render(self) -> None:
        self.page.title = APP_DISPLAY_NAME
        self.page.bgcolor = APP_BG
        self.page.padding = 0
        self.server_url.label = self.t("server_url")
        self.token.label = self.t("token")
        self.remember_token.label = self.t("remember_token")
        self.file_path.label = self.t("selected_file")
        self.export_language.label = self.t("export_language")
        self.export_profile.label = self.t("export_profile")
        self.sync_mode.label = self.t("sync_mode")
        self.sync_mode.options = [
            ft.DropdownOption(key=ProfileSyncMode.PATCH.value, text=self.t("sync_mode_patch")),
            ft.DropdownOption(key=ProfileSyncMode.REPLACE.value, text=self.t("sync_mode_replace")),
        ]
        self._refresh_strategy_options()
        self._refresh_export_controls()

        self.top_bar_host.content = self._top_bar()
        self.sidebar_host.content = self._sidebar()
        self.workspace_header_host.content = self._workspace_header()
        if not self._page_built:
            self.workspace_panel_hosts = [ft.Container(content=control) for control in self._workspace_controls()]
            self.main_area.controls = self.workspace_panel_hosts
            self.page.clean()
            self.page.add(self._app_shell())
            self._page_built = True
        else:
            self._set_workspace_controls(self._workspace_controls())
        self.page.update()
        self._schedule_workspace_scroll_restore()

    def _app_shell(self) -> ft.Control:
        return ft.Stack(
            controls=[
                ft.Container(
                    content=ft.Column(
                        [
                            self.top_bar_host,
                            self.progress,
                            ft.Row(
                                [
                                    self.sidebar_host,
                                    self._workspace(),
                                ],
                                spacing=0,
                                expand=True,
                                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                            ),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    border=_border_all(LINE_SOFT),
                    expand=True,
                ),
                *self._resize_handles(),
            ],
            fit=ft.StackFit.EXPAND,
            expand=True,
        )

    def _top_bar(self) -> ft.Control:
        lang_label = "English" if self.translator.language == "zh" else "中文"
        return ft.Container(
            content=ft.Row(
                [
                    ft.WindowDragArea(
                        content=ft.Container(
                            content=ft.Row(
                                [
                                    ft.Container(
                                        content=ft.Image(src="app_icon.png", fit=ft.BoxFit.COVER, border_radius=7),
                                        width=34,
                                        height=34,
                                        border_radius=7,
                                        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                                        alignment=ft.Alignment(0, 0),
                                    ),
                                    ft.Column(
                                        [
                                            ft.Text(self.t("app_title"), size=15, weight=WEIGHT_SEMIBOLD, color=TEXT),
                                            ft.Text(self.t("app_subtitle"), size=11, color=TEXT_MUTED),
                                        ],
                                        spacing=0,
                                        alignment=ft.MainAxisAlignment.CENTER,
                                    ),
                                ],
                                spacing=10,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            padding=_padding_symmetric(horizontal=16, vertical=0),
                            height=TITLE_BAR_HEIGHT,
                            expand=True,
                        ),
                        maximizable=True,
                        expand=True,
                    ),
                    ft.Row(
                        [
                            self._status_chip(
                                self._connection_label(),
                                ft.Icons.CHECK_CIRCLE if self.connection_info else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                SUCCESS_SOFT if self.connection_info else NEUTRAL_SOFT,
                                SUCCESS if self.connection_info else TEXT_MUTED,
                            ),
                            self._status_chip(
                                self._file_label(),
                                ft.Icons.DESCRIPTION if self.spreadsheet else ft.Icons.INSERT_DRIVE_FILE,
                                PRIMARY_SOFT if self.spreadsheet else NEUTRAL_SOFT,
                                PRIMARY if self.spreadsheet else TEXT_MUTED,
                            ),
                            self._window_button(
                                ft.Icons.LANGUAGE,
                                lang_label,
                                self._toggle_language,
                            ),
                            self._window_button(
                                ft.Icons.MINIMIZE,
                                self.t("minimize_window"),
                                self._minimize_window,
                            ),
                            self._window_button(
                                ft.Icons.CROP_SQUARE,
                                self.t("maximize_window"),
                                self._toggle_maximize_window,
                            ),
                            self._window_button(
                                ft.Icons.CLOSE,
                                self.t("close_window"),
                                self._confirm_exit,
                                danger=True,
                            ),
                        ],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=TITLE_BAR_HEIGHT,
            padding=ft.Padding(left=0, top=0, right=8, bottom=0),
            bgcolor=SURFACE,
            border=ft.Border(bottom=ft.BorderSide(width=1, color=LINE_SOFT)),
        )

    def _window_button(self, icon: str, tooltip: str, on_click: Any, danger: bool = False) -> ft.Control:
        return ft.IconButton(
            icon=icon,
            tooltip=tooltip,
            on_click=on_click,
            width=36,
            height=36,
            icon_size=18,
            icon_color=DANGER if danger else TEXT_MUTED,
            hover_color=DANGER_SOFT if danger else SURFACE_MUTED,
            splash_radius=18,
        )

    def _resize_handles(self) -> list[ft.Control]:
        side = RESIZE_HANDLE_SIZE
        return [
            self._resize_handle(ft.WindowResizeEdge.TOP_LEFT, ft.MouseCursor.RESIZE_UP_LEFT, left=0, top=0, width=side, height=side),
            self._resize_handle(ft.WindowResizeEdge.TOP_RIGHT, ft.MouseCursor.RESIZE_UP_RIGHT, right=0, top=0, width=side, height=side),
            self._resize_handle(ft.WindowResizeEdge.BOTTOM_LEFT, ft.MouseCursor.RESIZE_DOWN_LEFT, left=0, bottom=0, width=side, height=side),
            self._resize_handle(ft.WindowResizeEdge.BOTTOM_RIGHT, ft.MouseCursor.RESIZE_DOWN_RIGHT, right=0, bottom=0, width=side, height=side),
            self._resize_handle(ft.WindowResizeEdge.TOP, ft.MouseCursor.RESIZE_UP_DOWN, left=side, right=side, top=0, height=side),
            self._resize_handle(ft.WindowResizeEdge.BOTTOM, ft.MouseCursor.RESIZE_UP_DOWN, left=side, right=side, bottom=0, height=side),
            self._resize_handle(ft.WindowResizeEdge.LEFT, ft.MouseCursor.RESIZE_LEFT_RIGHT, left=0, top=side, bottom=side, width=side),
            self._resize_handle(ft.WindowResizeEdge.RIGHT, ft.MouseCursor.RESIZE_LEFT_RIGHT, right=0, top=side, bottom=side, width=side),
        ]

    def _resize_handle(self, edge: ft.WindowResizeEdge, cursor: ft.MouseCursor, **position: float) -> ft.Control:
        return ft.GestureDetector(
            content=ft.Container(bgcolor=ft.Colors.TRANSPARENT),
            mouse_cursor=cursor,
            on_pan_down=lambda _event, resize_edge=edge: self._start_window_resize(resize_edge),
            drag_interval=1,
            **position,
        )

    def _start_window_resize(self, edge: ft.WindowResizeEdge) -> None:
        self.page.run_task(self.page.window.start_resizing, edge)

    def _minimize_window(self, _event: Any) -> None:
        self.page.window.minimized = True
        self.page.update()

    def _toggle_maximize_window(self, _event: Any) -> None:
        self.page.window.maximized = not bool(self.page.window.maximized)
        self.page.update()

    def _sidebar(self) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(self.t("workflow"), size=12, color=SIDEBAR_MUTED, weight=WEIGHT_SEMIBOLD),
                    ft.Column([self._step_nav_item(index, title) for index, title in enumerate(self._step_titles())], spacing=8),
                    ft.Divider(color="#293241"),
                    self._sidebar_hint(),
                ],
                spacing=16,
            ),
            width=280,
            padding=_padding_all(20),
            bgcolor=SIDEBAR,
        )

    def _workspace(self) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    self.workspace_header_host,
                    ft.Container(
                        content=self.main_area,
                        expand=True,
                    ),
                ],
                spacing=18,
                expand=True,
            ),
            padding=_padding_all(24),
            expand=True,
            bgcolor=APP_BG,
        )

    def _workspace_controls(self) -> list[ft.Control]:
        return [
            self._connect_panel(),
            self._import_panel(),
            self._profile_sync_panel(),
            self._strategy_panel(),
            self._precheck_panel(),
            self._apply_panel(),
            self._report_panel(),
        ]

    def _set_workspace_controls(self, controls: list[ft.Control]) -> None:
        if len(self.workspace_panel_hosts) != len(controls):
            self.workspace_panel_hosts = [ft.Container(content=control) for control in controls]
            self.main_area.controls = self.workspace_panel_hosts
            return
        for host, control in zip(self.workspace_panel_hosts, controls):
            host.content = control

    def _refresh_workspace_panel(self, index: int, control: ft.Control) -> None:
        if not getattr(self, "_page_built", False):
            return
        if index >= len(self.workspace_panel_hosts):
            return
        host = self.workspace_panel_hosts[index]
        host.content = control
        try:
            host.update()
        except Exception:
            self.page.update()

    def _refresh_progress_only(self) -> None:
        if not getattr(self, "_page_built", False):
            self.page.update()
            return
        try:
            self.progress.update()
        except Exception:
            self.page.update()

    def _refresh_step_shell(self) -> None:
        if not getattr(self, "_page_built", False):
            self.page.update()
            return
        self.sidebar_host.content = self._sidebar()
        self.workspace_header_host.content = self._workspace_header()
        try:
            self.sidebar_host.update()
            self.workspace_header_host.update()
        except Exception:
            self.page.update()

    def _refresh_sync_area(self, include_dependent_panels: bool = True) -> None:
        if not getattr(self, "_page_built", False):
            self.render()
            return
        self._refresh_workspace_panel(WORKSPACE_PROFILE_SYNC_INDEX, self._profile_sync_panel())
        if include_dependent_panels:
            self._refresh_workspace_panel(WORKSPACE_PRECHECK_INDEX, self._precheck_panel())
            self._refresh_workspace_panel(WORKSPACE_APPLY_INDEX, self._apply_panel())
            self._refresh_workspace_panel(WORKSPACE_REPORT_INDEX, self._report_panel())
            self._refresh_step_shell()
        self._refresh_progress_only()

    def _workspace_header(self) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(self._current_step_title(), size=26, weight=WEIGHT_SEMIBOLD, color=TEXT),
                            ft.Text(self._current_step_description(), size=14, color=TEXT_MUTED),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                    self._quick_action_button(),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=_padding_symmetric(horizontal=4, vertical=2),
        )

    def _step_titles(self) -> list[str]:
        return [
            self.t("step_connect"),
            self.t("step_import"),
            self.t("strategy"),
            self.t("profile_sync"),
            self.t("step_precheck"),
            self.t("step_apply"),
            self.t("step_report"),
        ]

    def _step_descriptions(self) -> list[str]:
        return [
            self.t("desc_connect"),
            self.t("desc_import"),
            self.t("desc_strategy"),
            self.t("desc_profile_sync"),
            self.t("desc_precheck"),
            self.t("desc_apply"),
            self.t("desc_report"),
        ]

    def _current_step_title(self) -> str:
        return self._step_titles()[min(self._current_step_index(), len(self._step_titles()) - 1)]

    def _current_step_description(self) -> str:
        return self._step_descriptions()[min(self._current_step_index(), len(self._step_descriptions()) - 1)]

    def _on_workspace_scroll(self, event: Any) -> None:
        pixels = getattr(event, "pixels", None)
        if pixels is None:
            return
        try:
            self._workspace_scroll_offset = max(float(pixels), 0.0)
        except (TypeError, ValueError):
            return

    def _schedule_workspace_scroll_restore(self) -> None:
        if self._workspace_scroll_offset <= 0:
            return
        try:
            self.page.run_task(self._restore_workspace_scroll)
        except Exception:
            pass

    async def _restore_workspace_scroll(self) -> None:
        await self.main_area.scroll_to(offset=self._workspace_scroll_offset, duration=0)

    def _step_nav_item(self, index: int, title: str) -> ft.Control:
        current = index == self._current_step_index()
        done = index < self._current_step_index()
        icon = ft.Icons.CHECK if done else self._step_icon(index)
        color = ft.Colors.WHITE if current else ("#D1FAE5" if done else SIDEBAR_MUTED)
        bg = "#1D4ED8" if current else ("#064E3B" if done else "#1F2937")
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(icon, size=16, color=color),
                        width=30,
                        height=30,
                        border_radius=6,
                        bgcolor=bg,
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Column(
                        [
                            ft.Text(title, size=14, weight=WEIGHT_SEMIBOLD if current else WEIGHT_REGULAR, color=ft.Colors.WHITE if current else "#D1D5DB"),
                            ft.Text(self._step_state_label(index), size=12, color="#BFDBFE" if current else SIDEBAR_MUTED),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=_padding_symmetric(horizontal=10, vertical=9),
            border_radius=8,
            bgcolor="#172554" if current else SIDEBAR,
            border=_border_all("#1E3A8A" if current else "#1F2937"),
        )

    def _step_icon(self, index: int) -> str:
        return [
            ft.Icons.LINK,
            ft.Icons.UPLOAD_FILE,
            ft.Icons.ACCOUNT_TREE,
            ft.Icons.SYNC_ALT,
            ft.Icons.FACT_CHECK,
            ft.Icons.PLAY_ARROW,
            ft.Icons.ARTICLE,
        ][index]

    def _step_state_label(self, index: int) -> str:
        if index < self._current_step_index():
            return self.t("state_done")
        if index == self._current_step_index():
            return self.t("state_current")
        return self.t("state_pending")

    def _sidebar_hint(self) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(self.t("operator_note"), size=13, color=ft.Colors.WHITE, weight=WEIGHT_SEMIBOLD),
                    ft.Text(self.t("operator_note_body"), size=12, color=SIDEBAR_MUTED),
                ],
                spacing=6,
            ),
            padding=_padding_all(12),
            bgcolor="#0B1220",
            border_radius=8,
            border=_border_all("#263244"),
        )

    def _quick_action_button(self) -> ft.Control:
        if self.apply_result or self.sync_result:
            return ft.FilledButton(self.t("open_report_folder"), icon=ft.Icons.FOLDER_OPEN, on_click=self._open_report_folder, height=44)
        if self.sync_plan and self.sync_plan.can_apply:
            return ft.FilledButton(self.t("apply_sync"), icon=ft.Icons.SYNC, on_click=self._apply_sync, height=44)
        if self.precheck_result and self.precheck_result.can_apply:
            return ft.FilledButton(self.t("apply_changes"), icon=ft.Icons.PLAY_ARROW, on_click=self._apply_changes, height=44)
        if self.spreadsheet:
            return ft.FilledButton(self.t("run_precheck"), icon=ft.Icons.FACT_CHECK, on_click=self._run_precheck, height=44)
        return ft.FilledButton(self.t("connect"), icon=ft.Icons.LINK, on_click=self._connect, height=44)

    def _connection_label(self) -> str:
        if self.connection_info:
            return self.t("state_connected")
        return self.t("state_disconnected")

    def _file_label(self) -> str:
        if self.spreadsheet:
            return self.t("state_file_loaded")
        return self.t("state_no_file")

    def _connect_panel(self) -> ft.Control:
        return self._section(
            self.t("step_connect"),
            self.t("desc_connect"),
            ft.Icons.LINK,
            ft.Column(
                [
                    ft.Row(
                        [
                            self.server_url,
                            self.token,
                        ],
                        wrap=True,
                        spacing=12,
                    ),
                    ft.Row(
                        [
                            self.remember_token,
                            ft.Container(expand=True),
                            ft.FilledButton(self.t("connect"), icon=ft.Icons.LINK, on_click=self._connect, height=44),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._inline_status(self.connection_status.value or self.t("status_ready"), self.connection_status.color or TEXT_MUTED),
                    self._connection_summary(),
                ],
                spacing=14,
            ),
        )

    def _import_panel(self) -> ft.Control:
        return self._section(
            self.t("step_import"),
            self.t("desc_import"),
            ft.Icons.UPLOAD_FILE,
            ft.Column(
                [
                    ft.Row(
                        [
                            self.file_path,
                            ft.OutlinedButton(self.t("browse"), icon=ft.Icons.FOLDER_OPEN, on_click=self._choose_file, height=44),
                            ft.FilledButton(self.t("load_file"), icon=ft.Icons.UPLOAD_FILE, on_click=self._load_file, height=44),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                    self._file_summary(),
                    self._basic_import_status(),
                    self._preview_table(),
                    self._advanced_mapping_panel(),
                ],
                spacing=12,
            ),
        )

    def _strategy_panel(self) -> ft.Control:
        self._refresh_target_language_options()
        self._refresh_strategy_detail()
        return self._section(
            self.t("strategy"),
            self.t("desc_strategy"),
            ft.Icons.ACCOUNT_TREE,
            ft.Column(
                [
                    self._target_profile_controls(),
                    self.strategy_group,
                    self.strategy_detail,
                ],
                spacing=12,
            ),
        )

    def _target_profile_controls(self) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self.target_language,
                            self.target_profile,
                            self.project_key,
                            self.set_default_profile,
                        ],
                        wrap=True,
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._inline_status(self.t("target_profile_help"), TEXT_MUTED, ft.Icons.INFO_OUTLINE),
                ],
                spacing=8,
            ),
            padding=_padding_all(12),
            bgcolor=SURFACE_MUTED,
            border=_border_all(LINE_SOFT),
            border_radius=8,
        )

    def _profile_sync_panel(self) -> ft.Control:
        disabled = not self.client
        profile_catalog_loading = getattr(self, "profile_catalog_loading", False)
        profile_catalog_error = getattr(self, "profile_catalog_error", "")
        export_disabled = disabled or self._export_busy or profile_catalog_loading or not self.available_profiles
        apply_disabled = not (self.sync_plan and self.sync_plan.can_apply)
        export_status = self.export_status.value or self.t("export_waiting")
        export_color = self.export_status.color or TEXT_MUTED
        if profile_catalog_loading:
            export_status = self.t("profiles_loading")
            export_color = PRIMARY
        elif profile_catalog_error:
            export_status = self.t("profiles_load_failed")
            export_color = WARNING
        return self._section(
            self.t("profile_sync"),
            self.t("desc_profile_sync"),
            ft.Icons.SYNC_ALT,
            ft.Column(
                [
                    ft.Row(
                        [
                            self.export_language,
                            self.export_profile,
                            ft.OutlinedButton(
                                self.t("export_profile_rules"),
                                icon=ft.Icons.DOWNLOAD,
                                disabled=export_disabled,
                                on_click=self._export_selected_profile,
                                height=44,
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._export_progress_control(export_status, export_color),
                    ft.Row(
                        [
                            self.sync_mode,
                            ft.OutlinedButton(self.t("run_sync_precheck"), icon=ft.Icons.FACT_CHECK, disabled=disabled or not self.spreadsheet, on_click=self._run_sync_precheck, height=44),
                            ft.FilledButton(self.t("apply_sync"), icon=ft.Icons.SYNC, disabled=apply_disabled, on_click=self._apply_sync, height=44),
                            ft.OutlinedButton(self.t("export_report"), icon=ft.Icons.SIM_CARD_DOWNLOAD, disabled=self.sync_plan is None, on_click=self._export_sync_precheck, height=44),
                        ],
                        wrap=True,
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._inline_status(self.sync_status.value or self.t("sync_waiting"), self.sync_status.color or TEXT_MUTED),
                    self._sync_summary(),
                    self._sync_actions_table(),
                ],
                spacing=10,
            ),
        )

    def _strategy_detail_control(self) -> ft.Control:
        parent_controls: list[ft.Control] = []
        if self.strategy_group.value == ProfileStrategy.EXTEND_SELECTED.value:
            parent_controls.append(self._language_profile_selectors(self.parent_dropdowns, "parent_profile"))
        if self.strategy_group.value == ProfileStrategy.COPY.value:
            parent_controls.append(self._language_profile_selectors(self.copy_dropdowns, "source_profile"))
        if parent_controls:
            return ft.Column(parent_controls, spacing=8)
        return self._inline_status(self._strategy_help_text(), TEXT_MUTED, ft.Icons.INFO)

    def _refresh_strategy_detail(self) -> None:
        self.strategy_detail.controls = [self._strategy_detail_control()]

    def _precheck_panel(self) -> ft.Control:
        return self._section(
            self.t("step_precheck"),
            self.t("desc_precheck"),
            ft.Icons.FACT_CHECK,
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.FilledButton(self.t("run_precheck"), icon=ft.Icons.FACT_CHECK, on_click=self._run_precheck, height=44),
                            self._inline_status(self.precheck_status.value or self.t("precheck_waiting"), self.precheck_status.color or TEXT_MUTED),
                        ],
                        spacing=12,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self._precheck_summary(),
                    self._issues_table(),
                ],
                spacing=10,
            ),
        )

    def _export_progress_control(self, message: str | None = None, color: str | None = None) -> ft.Control:
        return ft.Column(
            [
                self.export_progress,
                self._inline_status(message or self.export_status.value or self.t("export_waiting"), color or self.export_status.color or TEXT_MUTED, ft.Icons.DOWNLOAD),
            ],
            spacing=6,
        )

    def _apply_panel(self) -> ft.Control:
        disabled = not (self.precheck_result and self.precheck_result.can_apply)
        return self._section(
            self.t("step_apply"),
            self.t("desc_apply"),
            ft.Icons.PLAY_ARROW,
            ft.Column(
                [
                    ft.Row(
                        [
                            ft.FilledButton(
                                self.t("apply_changes"),
                                icon=ft.Icons.PLAY_ARROW,
                                disabled=disabled,
                                on_click=self._apply_changes,
                                height=44,
                            ),
                            ft.OutlinedButton(
                                self.t("export_report"),
                                icon=ft.Icons.SIM_CARD_DOWNLOAD,
                                disabled=self.precheck_result is None,
                                on_click=self._export_precheck,
                                height=44,
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                    self._actions_table(),
                ],
                spacing=10,
            ),
        )

    def _report_panel(self) -> ft.Control:
        report_text = ""
        if self.apply_result and self.apply_result.report_xlsx:
            report_text = str(self.apply_result.report_xlsx)
        elif self.sync_result and self.sync_result.report_xlsx:
            report_text = str(self.sync_result.report_xlsx)
        return self._section(
            self.t("step_report"),
            self.t("desc_report"),
            ft.Icons.ARTICLE,
            ft.Column(
                [
                    self._inline_status(f"{self.t('report_path')}: {report_text}" if report_text else self.t("report_waiting"), SUCCESS if report_text else TEXT_MUTED, ft.Icons.ARTICLE),
                    ft.Row(
                        [
                            ft.OutlinedButton(
                                self.t("open_report_folder"),
                                icon=ft.Icons.FOLDER,
                                disabled=not report_text,
                                on_click=self._open_report_folder,
                                height=44,
                            )
                        ]
                    ),
                ],
                spacing=8,
            ),
        )

    def _section(self, title: str, subtitle: str, icon: str, content: ft.Control) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Icon(icon, size=18, color=PRIMARY),
                                width=36,
                                height=36,
                                bgcolor=PRIMARY_SOFT,
                                border_radius=8,
                                alignment=ft.Alignment(0, 0),
                            ),
                            ft.Column(
                                [
                                    ft.Text(title, size=18, weight=WEIGHT_SEMIBOLD, color=TEXT),
                                    ft.Text(subtitle, size=13, color=TEXT_MUTED),
                                ],
                                spacing=2,
                                expand=True,
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(color=LINE_SOFT, height=1),
                    content,
                ],
                spacing=14,
            ),
            padding=_padding_all(18),
            border=_border_all(LINE_SOFT),
            border_radius=8,
            bgcolor=SURFACE,
            shadow=ft.BoxShadow(blur_radius=14, spread_radius=0, color="#14000000", offset=ft.Offset(0, 3)),
        )

    def _connection_summary(self) -> ft.Control:
        if not self.connection_info:
            return self._empty_state(self.t("connection_empty"), ft.Icons.LINK_OFF)
        if self.connection_info.capabilities:
            caps = ", ".join(name for name, enabled in self.connection_info.capabilities.items() if enabled) or "-"
        elif getattr(self, "api_capability_loading", False):
            caps = self.t("api_capability_loading")
        elif getattr(self, "api_capability_error", ""):
            caps = self.t("api_capability_load_failed")
        else:
            caps = "-"
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self._status_chip(f"Version {self.connection_info.version}", ft.Icons.VERIFIED, SUCCESS_SOFT, SUCCESS),
                            self._status_chip(self.connection_info.status, ft.Icons.MONITOR_HEART, PRIMARY_SOFT, PRIMARY),
                        ],
                        wrap=True,
                        spacing=8,
                    ),
                    ft.Text(f"{self.t('api_capability')}: {caps}", color=TEXT_MUTED, size=13),
                ],
                spacing=8,
            ),
            padding=_padding_all(12),
            bgcolor=SURFACE_MUTED,
            border_radius=8,
            border=_border_all(LINE_SOFT),
        )

    def _file_summary(self) -> ft.Control:
        if not self.spreadsheet:
            return self._empty_state(self.t("file_empty"), ft.Icons.INSERT_DRIVE_FILE)
        return ft.Row(
            [
                self._metric(self.t("table_rows"), str(len(self.spreadsheet.rows)), PRIMARY, ft.Icons.TABLE_ROWS),
                self._metric(
                    self.t("rule_column"),
                    self._rule_column_label(),
                    SUCCESS,
                    ft.Icons.KEY,
                ),
                self._metric(self.t("preview"), str(min(20, len(self.spreadsheet.preview_rows))), WARNING, ft.Icons.VISIBILITY),
            ],
            wrap=True,
            spacing=10,
        )

    def _rule_column_label(self) -> str:
        if not self.spreadsheet:
            return "-"
        mapping = self.mapping or self.spreadsheet.inferred_mapping
        return mapping.source_for("rule_key") or self.t("not_detected")

    def _basic_import_status(self) -> ft.Control:
        if not self.spreadsheet:
            return ft.Text("")
        mapping = self.mapping or self.spreadsheet.inferred_mapping
        rule_column = mapping.source_for("rule_key")
        if rule_column:
            return self._inline_status(self.t("rule_column_detected", column=rule_column), SUCCESS, ft.Icons.CHECK_CIRCLE)
        return self._inline_status(self.t("rule_column_missing"), DANGER, ft.Icons.ERROR_OUTLINE)

    def _advanced_mapping_panel(self) -> ft.Control:
        if not self.spreadsheet:
            return ft.Text("")
        return ft.ExpansionTile(
            title=ft.Text(self.t("advanced_mapping"), size=13, weight=WEIGHT_SEMIBOLD, color=TEXT),
            subtitle=ft.Text(self.t("advanced_mapping_hint"), size=12, color=TEXT_MUTED),
            controls=[self._mapping_panel()],
            expanded=False,
        )

    def _mapping_panel(self) -> ft.Control:
        if not self.spreadsheet:
            return ft.Text("")
        self._ensure_mapping_controls()
        missing = validate_required_mapping(self._current_mapping(), required_fields=("rule_key",))
        duplicate_sources = self._duplicate_mapping_sources()
        status_text, status_icon, status_bg, status_color = self._mapping_status(missing, duplicate_sources)
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(self.t("field_mapping"), weight=WEIGHT_SEMIBOLD, color=TEXT),
                        self._status_chip(
                            status_text,
                            status_icon,
                            status_bg,
                            status_color,
                        ),
                    ],
                    spacing=10,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._inline_status(self.t("mapping_help"), TEXT_MUTED, ft.Icons.INFO_OUTLINE),
                *[self._mapping_group(title_key, fields) for title_key, fields in DISPLAY_MAPPING_GROUPS],
            ],
            spacing=12,
        )

    def _mapping_status(self, missing: list[str], duplicate_sources: dict[str, list[str]]) -> tuple[str, str, str, str]:
        if missing:
            return (
                f"{self.t('required_field_missing')}: {', '.join(missing)}",
                ft.Icons.ERROR_OUTLINE,
                DANGER_SOFT,
                DANGER,
            )
        if duplicate_sources:
            return (
                self.t("mapping_duplicate_columns", count=len(duplicate_sources)),
                ft.Icons.WARNING_AMBER,
                WARNING_SOFT,
                WARNING,
            )
        return self.t("mapping_ready"), ft.Icons.CHECK_CIRCLE, SUCCESS_SOFT, SUCCESS

    def _mapping_group(self, title_key: str, fields: tuple[str, ...]) -> ft.Control:
        rows = [self._field_mapping_item(field, self.t(f"field_{field}")) for field in fields]
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(self.t(title_key), size=13, weight=WEIGHT_SEMIBOLD, color=TEXT),
                        self._mini_badge(f"{self._mapped_field_count(fields)}/{len(fields)}", PRIMARY, PRIMARY_SOFT),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.ResponsiveRow(rows, columns=12),
            ],
            spacing=8,
        )

    def _preview_table(self) -> ft.Control:
        if not self.spreadsheet:
            return ft.Text("")
        headers = self.spreadsheet.headers[:8]
        if not headers:
            return ft.Text("")
        rows = []
        for raw in self.spreadsheet.preview_rows[:20]:
            rows.append(ft.DataRow(cells=[ft.DataCell(ft.Text(str(raw.get(header, ""))[:80], size=12, color=TEXT)) for header in headers]))
        return self._table_block(
            self.t("preview"),
            ft.DataTable(
                columns=[ft.DataColumn(ft.Text(header, size=12, weight=WEIGHT_SEMIBOLD, color=TEXT)) for header in headers],
                rows=rows,
                heading_row_color=SURFACE_MUTED,
                column_spacing=18,
                divider_thickness=1,
            ),
        )

    def _precheck_summary(self) -> ft.Control:
        if not self.precheck_result:
            return self._empty_state(self.t("precheck_empty"), ft.Icons.FACT_CHECK)
        errors = self.precheck_result.count(ItemStatus.ERROR)
        warnings = self.precheck_result.count(ItemStatus.WARNING)
        profiles = len(self.precheck_result.profile_plans)
        rules = len(self.precheck_result.rule_status)
        return ft.Row(
            [
                self._metric(self.t("table_rows"), str(len(self.precheck_result.rows)), PRIMARY, ft.Icons.TABLE_ROWS),
                self._metric(self.t("profiles_to_create"), str(profiles), PRIMARY_DARK, ft.Icons.ACCOUNT_TREE),
                self._metric(self.t("rules_to_activate"), str(rules), SUCCESS, ft.Icons.RULE),
                self._metric(self.t("errors"), str(errors), DANGER, ft.Icons.ERROR_OUTLINE),
                self._metric(self.t("warnings"), str(warnings), WARNING, ft.Icons.WARNING_AMBER),
            ],
            wrap=True,
            spacing=10,
        )

    def _sync_summary(self) -> ft.Control:
        if not self.sync_plan:
            return self._empty_state(self.t("sync_empty"), ft.Icons.SYNC_ALT)
        items = self._current_sync_display_items()
        errors = sum(1 for item in items if item.status == ItemStatus.ERROR)
        warnings = sum(1 for item in items if item.status == ItemStatus.WARNING)
        activate = sum(1 for item in items if item.action == "activate")
        update = sum(1 for item in items if item.action == "update")
        deactivate = sum(1 for item in items if item.action == "deactivate")
        return ft.Row(
            [
                self._metric(
                    self.t("sync_all"),
                    str(len(items)),
                    PRIMARY,
                    ft.Icons.TABLE_ROWS,
                    on_click=lambda _e: self._set_sync_action_filter(SYNC_FILTER_ALL),
                    selected=self.sync_action_filter == SYNC_FILTER_ALL,
                ),
                self._metric(
                    self.t("sync_activate"),
                    str(activate),
                    SUCCESS,
                    ft.Icons.ADD_CIRCLE,
                    on_click=lambda _e: self._set_sync_action_filter("activate"),
                    selected=self.sync_action_filter == "activate",
                ),
                self._metric(
                    self.t("sync_update"),
                    str(update),
                    PRIMARY_DARK,
                    ft.Icons.UPDATE,
                    on_click=lambda _e: self._set_sync_action_filter("update"),
                    selected=self.sync_action_filter == "update",
                ),
                self._metric(
                    self.t("sync_deactivate"),
                    str(deactivate),
                    WARNING,
                    ft.Icons.REMOVE_CIRCLE,
                    on_click=lambda _e: self._set_sync_action_filter("deactivate"),
                    selected=self.sync_action_filter == "deactivate",
                ),
                self._metric(
                    self.t("errors"),
                    str(errors),
                    DANGER,
                    ft.Icons.ERROR_OUTLINE,
                    on_click=lambda _e: self._set_sync_action_filter(SYNC_FILTER_ERROR),
                    selected=self.sync_action_filter == SYNC_FILTER_ERROR,
                ),
                self._metric(
                    self.t("warnings"),
                    str(warnings),
                    WARNING,
                    ft.Icons.WARNING_AMBER,
                    on_click=lambda _e: self._set_sync_action_filter(SYNC_FILTER_WARNING),
                    selected=self.sync_action_filter == SYNC_FILTER_WARNING,
                ),
            ],
            wrap=True,
            spacing=10,
        )

    def _metric(
        self,
        label: str,
        value: str,
        color: str,
        icon: str = ft.Icons.INSIGHTS,
        on_click: Callable[[Any], None] | None = None,
        selected: bool = False,
    ) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(icon, color=color, size=18),
                        width=34,
                        height=34,
                        bgcolor=_soft_for_color(color),
                        border_radius=8,
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Column(
                        [
                            ft.Text(value, size=22, weight=WEIGHT_SEMIBOLD, color=TEXT),
                            ft.Text(label, size=12, color=TEXT_MUTED),
                        ],
                        spacing=0,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            width=172,
            padding=_padding_all(12),
            border=_border_all(color if selected else LINE_SOFT),
            border_radius=8,
            bgcolor=SURFACE if selected else SURFACE_MUTED,
            on_click=on_click,
            ink=on_click is not None,
        )

    def _set_sync_action_filter(self, value: str) -> None:
        self.sync_action_filter = value
        self._refresh_sync_area(include_dependent_panels=False)

    def _current_sync_display_items(self) -> list[SyncDisplayItem]:
        return _sync_display_items(self.sync_plan, self.sync_result)

    def _current_filtered_sync_display_items(self) -> list[SyncDisplayItem]:
        return _filter_sync_display_items(self._current_sync_display_items(), self.sync_action_filter)

    def _issues_table(self) -> ft.Control:
        if not self.precheck_result or not self.precheck_result.issues:
            if self.precheck_result:
                return self._empty_state(self.t("issues_empty"), ft.Icons.CHECK_CIRCLE)
            return ft.Text("")
        rows = []
        issues = sorted(self.precheck_result.issues, key=_issue_sort_key)
        for issue in issues[:120]:
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(self._status_badge(issue.status.value)),
                        ft.DataCell(ft.Text(issue.category, size=12, color=TEXT)),
                        ft.DataCell(ft.Text("" if issue.source_row is None else str(issue.source_row), size=12, color=TEXT_MUTED)),
                        ft.DataCell(ft.Text(issue.message, size=12, color=TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                        ft.DataCell(ft.Text(issue.suggestion, size=12, color=TEXT_MUTED, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                    ]
                )
            )
        return self._table_block(
            self.t("details"),
            ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text("status", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("category", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("row", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("message", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("suggestion", size=12, weight=WEIGHT_SEMIBOLD)),
                ],
                rows=rows,
                heading_row_color=SURFACE_MUTED,
                column_spacing=16,
                divider_thickness=1,
            ),
        )

    def _actions_table(self) -> ft.Control:
        if not self.apply_result or not self.apply_result.actions:
            return self._empty_state(self.t("apply_empty"), ft.Icons.PLAYLIST_ADD_CHECK)
        rows = []
        for action in self.apply_result.actions[:100]:
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(self._status_badge(action.status.value)),
                        ft.DataCell(ft.Text(action.action, size=12, color=TEXT)),
                        ft.DataCell(ft.Text(action.language, size=12, color=TEXT_MUTED)),
                        ft.DataCell(ft.Text(action.profile, size=12, color=TEXT)),
                        ft.DataCell(ft.Text(action.rule_key, size=12, color=TEXT_MUTED)),
                        ft.DataCell(ft.Text(action.message[:160], size=12, color=TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                    ]
                )
            )
        return self._table_block(
            self.t("details"),
            ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text("status", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("action", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("language", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("profile", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("rule", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("message", size=12, weight=WEIGHT_SEMIBOLD)),
                ],
                rows=rows,
                heading_row_color=SURFACE_MUTED,
                column_spacing=16,
                divider_thickness=1,
            ),
        )

    def _sync_actions_table(self) -> ft.Control:
        if not self.sync_plan:
            return ft.Text("")
        items = self._current_filtered_sync_display_items()
        if not items:
            return self._empty_state(self.t("sync_actions_empty"), ft.Icons.FILTER_ALT_OFF)
        rows = []
        visible_items = items[:SYNC_ACTION_TABLE_LIMIT]
        for item in visible_items:
            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(self._status_badge(item.status.value)),
                        ft.DataCell(ft.Text(item.action, size=12, color=TEXT)),
                        ft.DataCell(ft.Text("" if item.source_row is None else str(item.source_row), size=12, color=TEXT_MUTED)),
                        ft.DataCell(ft.Text(item.language, size=12, color=TEXT_MUTED)),
                        ft.DataCell(ft.Text(item.profile, size=12, color=TEXT)),
                        ft.DataCell(ft.Text(item.rule_key, size=12, color=TEXT_MUTED)),
                        ft.DataCell(ft.Text(_sync_item_message(item), size=12, color=TEXT, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)),
                    ]
                )
            )
        return self._table_block(
            f"{self.t('sync_actions')} ({len(visible_items)}/{len(items)})",
            ft.DataTable(
                columns=[
                    ft.DataColumn(ft.Text("status", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("action", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("row", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("language", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("profile", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("rule", size=12, weight=WEIGHT_SEMIBOLD)),
                    ft.DataColumn(ft.Text("message", size=12, weight=WEIGHT_SEMIBOLD)),
                ],
                rows=rows,
                heading_row_color=SURFACE_MUTED,
                column_spacing=16,
                divider_thickness=1,
            ),
            height=SYNC_ACTION_TABLE_HEIGHT,
        )

    def _field_mapping_item(self, field: str, label: str) -> ft.Control:
        required = field in REQUIRED_FIELDS
        control = self.mapping_controls[field]
        control.width = 280
        control.border_radius = 6
        control.border_color = LINE
        control.focused_border_color = PRIMARY
        duplicate_sources = self._duplicate_mapping_sources()
        duplicate = bool(control.value and control.value in duplicate_sources)
        badge_text = self.t("required") if required else self.t("optional")
        badge_color = DANGER if required else TEXT_MUTED
        badge_bg = DANGER_SOFT if required else NEUTRAL_SOFT
        if duplicate:
            badge_text = self.t("mapping_duplicate")
            badge_color = WARNING
            badge_bg = WARNING_SOFT
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(label, size=13, weight=WEIGHT_SEMIBOLD, color=TEXT),
                            self._mini_badge(badge_text, badge_color, badge_bg),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    control,
                ],
                spacing=6,
            ),
            col={"xs": 12, "sm": 6, "md": 4, "lg": 4},
            padding=_padding_all(10),
            bgcolor=SURFACE_MUTED,
            border=_border_all(LINE_SOFT),
            border_radius=8,
        )

    def _table_block(self, title: str, table: ft.Control, height: int | None = None) -> ft.Control:
        table_body: ft.Control
        if height is None:
            table_body = ft.Container(
                content=ft.Row([table], scroll=ft.ScrollMode.AUTO),
                border=_border_all(LINE_SOFT),
                border_radius=8,
                bgcolor=SURFACE,
            )
        else:
            table_body = ft.Container(
                content=ft.Column([ft.Row([table], scroll=ft.ScrollMode.AUTO)], scroll=ft.ScrollMode.AUTO),
                height=height,
                border=_border_all(LINE_SOFT),
                border_radius=8,
                bgcolor=SURFACE,
            )
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, weight=WEIGHT_SEMIBOLD, color=TEXT),
                    table_body,
                ],
                spacing=8,
            ),
        )

    def _inline_status(self, text: str, color: str, icon: str = ft.Icons.INFO) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=17, color=color),
                    ft.Text(text, size=13, color=color, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=_padding_symmetric(horizontal=12, vertical=9),
            bgcolor=_soft_for_color(color),
            border_radius=8,
            border=_border_all(LINE_SOFT),
        )

    def _empty_state(self, text: str, icon: str) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, color=TEXT_SUBTLE, size=18),
                    ft.Text(text, color=TEXT_MUTED, size=13),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=_padding_all(12),
            bgcolor=SURFACE_MUTED,
            border_radius=8,
            border=_border_all(LINE_SOFT),
        )

    def _status_chip(self, text: str, icon: str, bgcolor: str, color: str) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, color=color, size=16),
                    ft.Text(text, size=12, color=color, weight=WEIGHT_SEMIBOLD, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=_padding_symmetric(horizontal=10, vertical=7),
            bgcolor=bgcolor,
            border_radius=999,
        )

    def _mini_badge(self, text: str, color: str, bgcolor: str) -> ft.Control:
        return ft.Container(
            content=ft.Text(text, size=11, color=color, weight=WEIGHT_SEMIBOLD),
            padding=_padding_symmetric(horizontal=7, vertical=3),
            bgcolor=bgcolor,
            border_radius=999,
        )

    def _status_badge(self, value: str) -> ft.Control:
        color = {
            "ok": SUCCESS,
            "warning": WARNING,
            "error": DANGER,
            "skipped": TEXT_MUTED,
        }.get(value, TEXT_MUTED)
        return self._mini_badge(value, color, _soft_for_color(color))

    def _mapped_field_count(self, fields: Iterable[str] = ALL_FIELDS) -> int:
        if not self.mapping_controls:
            return 0
        return sum(1 for field in fields if self.mapping_controls.get(field) and self.mapping_controls[field].value)

    def _duplicate_mapping_sources(self) -> dict[str, list[str]]:
        by_source: dict[str, list[str]] = {}
        for field, control in self.mapping_controls.items():
            source = str(control.value or "").strip()
            if not source:
                continue
            by_source.setdefault(source, []).append(field)
        return {source: fields for source, fields in by_source.items() if len(fields) > 1}

    def _current_step_index(self) -> int:
        if self.apply_result or self.sync_result:
            return 6
        if self.precheck_result:
            if self.precheck_result.can_apply:
                return 5
            return 4
        if self.sync_plan:
            if self.sync_plan.can_apply:
                return 5
            return 3
        if self.spreadsheet:
            return 2
        if self.connection_info:
            return 1
        return 0

    def _refresh_strategy_options(self) -> None:
        self.strategy_group.content = ft.ResponsiveRow(
            [
                ft.Radio(value=ProfileStrategy.EXTEND_DEFAULT.value, label=self.t("strategy_extend_default"), col={"xs": 12, "sm": 6, "md": 3}),
                ft.Radio(value=ProfileStrategy.EXTEND_SELECTED.value, label=self.t("strategy_extend_selected"), col={"xs": 12, "sm": 6, "md": 3}),
                ft.Radio(value=ProfileStrategy.COPY.value, label=self.t("strategy_copy"), col={"xs": 12, "sm": 6, "md": 3}),
                ft.Radio(value=ProfileStrategy.INDEPENDENT.value, label=self.t("strategy_independent"), col={"xs": 12, "sm": 6, "md": 3}),
            ],
            columns=12,
        )

    def _on_strategy_change(self, _event: Any) -> None:
        self.config.default_strategy = str(self.strategy_group.value or ProfileStrategy.EXTEND_DEFAULT.value)
        self.store.save(self.config)
        self._refresh_strategy_detail()
        self.strategy_detail.update()

    def _strategy_help_text(self) -> str:
        if self.strategy_group.value == ProfileStrategy.EXTEND_DEFAULT.value:
            return self.t("strategy_help_extend_default")
        if self.strategy_group.value == ProfileStrategy.INDEPENDENT.value:
            return self.t("strategy_help_independent")
        if self.strategy_group.value == ProfileStrategy.EXTEND_SELECTED.value:
            return self.t("strategy_help_extend_selected")
        if self.strategy_group.value == ProfileStrategy.COPY.value:
            return self.t("strategy_help_copy")
        return self.t("strategy_help_extend_default")

    async def _choose_file(self, _event: Any) -> None:
        files = await self.file_picker.pick_files(
            allow_multiple=False,
            allowed_extensions=["csv", "xlsx", "xlsm"],
            dialog_title=self.t("choose_file"),
        )
        self._apply_picked_files(files or [])

    def _apply_picked_files(self, files: Iterable[Any]) -> None:
        file_list = list(files)
        if not file_list:
            return
        self.file_path.value = str(file_list[0].path or "")
        self.config.last_file = self.file_path.value
        self.store.save(self.config)
        self.page.update()

    def _connect(self, _event: Any) -> None:
        self._run_background(self._connect_worker)

    def _connect_worker(self) -> None:
        server_url = self.server_url.value.strip()
        token = self.token.value.strip()
        if not server_url:
            self._set_status(self.connection_status, self.t("server_url"), DANGER)
            return
        self._connection_catalog_token += 1
        catalog_token = self._connection_catalog_token
        self._set_busy(True)
        try:
            client = SonarQubeClient(server_url, token)
            info = client.test_connection()
            self.client = client
            self.connection_info = info
            self.available_profiles = {}
            self.default_profiles = {}
            self.profile_catalog_error = ""
            self.api_capability_error = ""
            self.config.server_url = server_url
            self.config.language = self.translator.language
            self.store.save(self.config)
            if self.remember_token.value:
                self.store.save_token(server_url, token)
            self._set_status(self.connection_status, self.t("connected"), SUCCESS)
            self.profile_catalog_loading = True
            self.api_capability_loading = True
            if not self._exit_requested:
                self.render()
            self._run_background(lambda: self._connection_catalog_worker(client, catalog_token))
        except SonarQubeError as exc:
            self.profile_catalog_loading = False
            self.api_capability_loading = False
            self._set_status(self.connection_status, f"{self.t('connection_failed')}: {exc}", DANGER)
        finally:
            self._set_busy(False)
            if not self._exit_requested:
                self.render()

    def _connection_catalog_worker(self, client: SonarQubeClient, catalog_token: int) -> None:
        try:
            profiles = client.search_quality_profiles()
        except SonarQubeError as exc:
            self._finish_profile_catalog_load(client, catalog_token, [], str(exc))
        else:
            self._finish_profile_catalog_load(client, catalog_token, profiles, "")

        try:
            capabilities = client.load_capabilities()
        except SonarQubeError as exc:
            self._finish_capability_load(client, catalog_token, {}, str(exc))
        else:
            self._finish_capability_load(client, catalog_token, capabilities, "")

    def _finish_profile_catalog_load(self, client: SonarQubeClient, catalog_token: int, profiles: list[dict[str, Any]], error: str) -> None:
        if catalog_token != self._connection_catalog_token or client is not self.client:
            return
        if profiles:
            self.available_profiles = self._available_profiles_from_profiles(profiles)
            self.default_profiles = self._default_profiles_from_profiles(profiles) or client.get_default_profiles()
            self._refresh_target_language_options()
            self._refresh_profile_dropdowns()
        self.profile_catalog_error = error
        self.profile_catalog_loading = False
        if not self._exit_requested:
            self.render()

    def _finish_capability_load(self, client: SonarQubeClient, catalog_token: int, capabilities: dict[str, bool], error: str) -> None:
        if catalog_token != self._connection_catalog_token or client is not self.client:
            return
        if capabilities and self.connection_info:
            self.connection_info = replace(self.connection_info, capabilities=capabilities)
        self.api_capability_error = error
        self.api_capability_loading = False
        if not self._exit_requested:
            self.render()

    def _load_quality_profiles(self, client: SonarQubeClient) -> list[dict[str, Any]]:
        try:
            return client.search_quality_profiles()
        except SonarQubeError:
            return []

    def _available_profiles_from_profiles(self, profiles: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
        profiles_by_language: dict[str, list[str]] = {}
        for profile in profiles:
            language = str(profile.get("language", ""))
            name = str(profile.get("name", ""))
            if language and name:
                profiles_by_language.setdefault(language, []).append(name)
        return {language: sorted(set(names)) for language, names in profiles_by_language.items()}

    def _default_profiles_from_profiles(self, profiles: Iterable[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for profile in profiles:
            if not _profile_is_default(profile):
                continue
            language = str(profile.get("language", ""))
            name = str(profile.get("name", ""))
            if language and name:
                result[language] = name
        return result

    def _refresh_profile_dropdowns(self) -> None:
        for controls in (self.parent_dropdowns, self.copy_dropdowns):
            for language, dropdown in controls.items():
                self._configure_language_dropdown(language, dropdown)

    def _language_profile_selectors(self, controls: dict[str, ft.Dropdown], label_key: str) -> ft.Control:
        languages = self._input_languages()
        if not languages:
            if getattr(self, "profile_catalog_loading", False):
                return self._inline_status(self.t("profiles_loading"), PRIMARY, ft.Icons.CLOUD_SYNC)
            if getattr(self, "profile_catalog_error", ""):
                return self._inline_status(self.t("profiles_load_failed"), WARNING, ft.Icons.WARNING_AMBER)
            return self._inline_status(self.t("target_profile_required"), DANGER, ft.Icons.ERROR_OUTLINE)
        rows = []
        for language in languages:
            dropdown = controls.get(language)
            if dropdown is None:
                dropdown = ft.Dropdown(width=420, enable_search=True, border_color=LINE, focused_border_color=PRIMARY, border_radius=6)
                controls[language] = dropdown
            dropdown.label = self.t(label_key)
            self._configure_language_dropdown(language, dropdown)
            rows.append(
                ft.Container(
                    content=ft.Row(
                        [
                            self._mini_badge(language, PRIMARY, PRIMARY_SOFT),
                            dropdown,
                            ft.OutlinedButton(
                                self.t("export_profile_rules"),
                                icon=ft.Icons.DOWNLOAD,
                                disabled=not self.client or not dropdown.value or self._export_busy or getattr(self, "profile_catalog_loading", False),
                                on_click=lambda _e, lang=language, control=dropdown: self._export_profile_for_language(lang, str(control.value or "")),
                                height=44,
                            ),
                        ],
                        spacing=10,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=_padding_all(10),
                    bgcolor=SURFACE_MUTED,
                    border=_border_all(LINE_SOFT),
                    border_radius=8,
                )
            )
        return ft.Column(
            [
                self._inline_status(self.t("language_profile_hint"), TEXT_MUTED, ft.Icons.INFO),
                ft.Column(rows, spacing=8),
            ],
            spacing=8,
        )

    def _configure_language_dropdown(self, language: str, dropdown: ft.Dropdown) -> None:
        names = self.available_profiles.get(language, [])
        dropdown.options = _dropdown_options(names)
        dropdown.disabled = not names
        if dropdown.value not in names:
            dropdown.value = self.default_profiles.get(language, "") if self.default_profiles.get(language, "") in names else (names[0] if names else "")

    def _refresh_target_language_options(self) -> None:
        languages = sorted(self.available_profiles)
        current = self._target_language_value()
        if current and current not in languages:
            languages.append(current)
            languages.sort()
        self.target_language.options = _dropdown_options(languages)
        if current:
            self.target_language.value = current
        elif languages:
            self.target_language.value = languages[0]

    def _on_target_language_change(self, _event: Any) -> None:
        self._refresh_strategy_detail()
        self._save_target_settings()
        self.render()

    def _refresh_export_controls(self) -> None:
        languages = sorted(self.available_profiles)
        self.export_language.options = [ft.DropdownOption(key=language, text=language) for language in languages]
        if self.export_language.value not in languages:
            self.export_language.value = languages[0] if languages else ""
        profiles = self.available_profiles.get(str(self.export_language.value or ""), [])
        self.export_profile.options = [ft.DropdownOption(key=name, text=name) for name in profiles]
        if self.export_profile.value not in profiles:
            default_profile = self.default_profiles.get(str(self.export_language.value or ""), "")
            self.export_profile.value = default_profile if default_profile in profiles else (profiles[0] if profiles else "")
        self.export_language.on_change = lambda _e: self._on_export_language_change()

    def _on_export_language_change(self) -> None:
        self._refresh_export_controls()
        self.page.update()

    def _input_languages(self) -> list[str]:
        language = self._target_language_value()
        return [language] if language else []

    def _load_file(self, _event: Any) -> None:
        path = self.file_path.value.strip()
        if not path:
            self._show_message(self.t("no_file"))
            return
        try:
            self.spreadsheet = read_spreadsheet(path)
            self.mapping = self.spreadsheet.inferred_mapping
            self._ensure_mapping_controls(reset=True)
            self.mapping = self._current_mapping()
            self.config.last_file = path
            self.store.save(self.config)
            self.precheck_result = None
            self.apply_result = None
            self.sync_plan = None
            self.sync_result = None
            self.sync_action_filter = SYNC_FILTER_ALL
            self.precheck_status.value = ""
            self.sync_status.value = ""
            self.render()
        except Exception as exc:
            self._show_message(str(exc))

    def _ensure_mapping_controls(self, reset: bool = False) -> None:
        if not self.spreadsheet:
            return
        headers = [""] + self.spreadsheet.headers
        if reset:
            self.mapping_controls = {}
        for field in DISPLAY_MAPPING_FIELDS:
            if field in self.mapping_controls:
                self.mapping_controls[field].options = _dropdown_options(headers)
                continue
            inferred = ""
            if self.spreadsheet.inferred_mapping:
                inferred = self.spreadsheet.inferred_mapping.source_for(field)
            saved = self.config.last_mapping.get(field, "")
            value = saved if saved and saved in headers else inferred
            self.mapping_controls[field] = ft.Dropdown(
                value=value,
                options=_dropdown_options(headers),
                width=260,
                enable_search=True,
                border_color=LINE,
                focused_border_color=PRIMARY,
                border_radius=6,
                on_select=lambda _e: self._save_mapping_and_refresh(),
                on_blur=lambda _e: self._save_mapping_and_refresh(),
            )

    def _current_mapping(self) -> FieldMapping:
        return FieldMapping(columns={field: control.value or "" for field, control in self.mapping_controls.items()})

    def _save_mapping(self) -> None:
        if getattr(self, "spreadsheet", None):
            self._ensure_mapping_controls()
        self.mapping = self._current_mapping()
        self.config.last_mapping = self.mapping.columns
        self.store.save(self.config)

    def _save_mapping_and_refresh(self) -> None:
        self._save_mapping()
        self.render()

    def _target_language_value(self) -> str:
        return str(self.target_language.value or "").strip()

    def _target_profile_value(self) -> str:
        return str(self.target_profile.value or "").strip()

    def _project_key_value(self) -> str:
        return str(self.project_key.value or "").strip()

    def _target_settings_error(self) -> str:
        missing = []
        if not self._target_language_value():
            missing.append(self.t("target_language"))
        if not self._target_profile_value():
            missing.append(self.t("target_profile"))
        return f"{self.t('required_field_missing')}: {', '.join(missing)}" if missing else ""

    def _save_target_settings(self) -> None:
        self.config.target_language = self._target_language_value()
        self.config.target_profile = self._target_profile_value()
        self.config.project_key = self._project_key_value()
        self.config.set_default = bool(self.set_default_profile.value)
        self.store.save(self.config)

    def _rule_key_source(self) -> str:
        mapping = self.mapping or (self.spreadsheet.inferred_mapping if self.spreadsheet else FieldMapping({}))
        return mapping.source_for("rule_key")

    def _current_rule_rows(self) -> list[RuleRow]:
        if not self.spreadsheet:
            return []
        rows = rows_from_mapping(self.spreadsheet.rows, self.mapping or self.spreadsheet.inferred_mapping)
        return apply_target_settings_to_rule_rows(
            rows,
            language=self._target_language_value(),
            target_profile=self._target_profile_value(),
            strategy=str(self.strategy_group.value or ""),
            project_key=self._project_key_value(),
            set_default=bool(self.set_default_profile.value),
        )

    def _current_profile_rule_rows(self) -> list[ProfileRuleRow]:
        if not self.spreadsheet:
            return []
        rows = profile_rule_rows_from_mapping(self.spreadsheet.rows, self.mapping or self.spreadsheet.inferred_mapping)
        return apply_target_settings_to_profile_rule_rows(
            rows,
            language=self._target_language_value(),
            target_profile=self._target_profile_value(),
        )

    def _run_precheck(self, _event: Any) -> None:
        self._run_background(self._precheck_worker)

    def _set_precheck_result(self, result: Any) -> None:
        self.precheck_result = result
        self.apply_result = None
        self.sync_plan = None
        self.sync_result = None
        self.sync_action_filter = SYNC_FILTER_ALL

    def _set_sync_plan(self, plan: ProfileSyncPlan) -> None:
        self.sync_plan = plan
        self.sync_result = None
        self.precheck_result = None
        self.apply_result = None
        self.sync_action_filter = SYNC_FILTER_ALL

    def _precheck_worker(self) -> None:
        if not self.client:
            self._show_message(self.t("no_connection"))
            return
        if not self.spreadsheet:
            self._show_message(self.t("no_file"))
            return
        self._save_mapping()
        self._save_target_settings()
        settings_error = self._target_settings_error()
        if settings_error:
            self._show_message(settings_error)
            return
        if not self._rule_key_source():
            self._show_message(self.t("rule_column_missing"))
            return
        self._set_busy(True)
        try:
            rows = self._current_rule_rows()
            strategy = parse_strategy(str(self.strategy_group.value or ""), ProfileStrategy.EXTEND_DEFAULT)
            service = WorkflowService(self.client)
            languages = languages_from_rows(rows)
            parent_by_language = selected_profiles_by_language(self._dropdown_values(self.parent_dropdowns), languages)
            copy_by_language = selected_profiles_by_language(self._dropdown_values(self.copy_dropdowns), languages)
            self._set_precheck_result(
                service.precheck(
                    rows,
                    default_strategy=strategy,
                    selected_parent_by_language=parent_by_language,
                    copy_source_by_language=copy_by_language,
                )
            )
            text = self.t("precheck_passed") if self.precheck_result.can_apply else self.t("precheck_blocked")
            color = SUCCESS if self.precheck_result.can_apply else DANGER
            self._set_status(self.precheck_status, text, color)
        except Exception as exc:
            self._set_status(self.precheck_status, str(exc), DANGER)
        finally:
            self._set_busy(False)
            if not self._exit_requested:
                self.render()

    def _dropdown_values(self, controls: dict[str, ft.Dropdown]) -> dict[str, str]:
        return {language: str(dropdown.value or "") for language, dropdown in controls.items()}

    def _export_selected_profile(self, _event: Any) -> None:
        language = str(self.export_language.value or "")
        profile = str(self.export_profile.value or "")
        self._export_profile_for_language(language, profile)

    def _export_profile_for_language(self, language: str, profile: str) -> None:
        if not self.client:
            self._show_message(self.t("no_connection"))
            return
        if self._export_busy:
            return
        if not language or not profile:
            self._show_message(self.t("missing_profile"))
            return
        self._set_export_progress(self.t("export_starting"), 0.02, PRIMARY)
        self._run_background(lambda: self._export_profile_worker(language, profile))

    def _export_profile_worker(self, language: str, profile: str) -> None:
        if not self.client:
            return
        exported = False
        try:
            target_profile = self._target_profile_value() if self._target_language_value() == language else ""
            result = ProfileExportService(self.client).export_profile(
                language,
                profile,
                profile_export_dir(),
                target_profile=target_profile,
                progress=self._on_export_progress,
            )
            exported = True
            self._show_message(f"{self.t('profile_exported')}: {result.xlsx_path}")
        except Exception as exc:
            self._set_export_progress(str(exc), 0, DANGER, busy=False)
            self._show_message(str(exc))
        finally:
            if exported:
                self._set_export_progress(self.t("export_done"), 1.0, SUCCESS, busy=False)

    def _on_export_progress(self, stage: str, value: float | None) -> None:
        self._set_export_progress(self._export_stage_text(stage), value, PRIMARY)

    def _export_stage_text(self, stage: str) -> str:
        return self.t(f"export_stage_{stage}")

    def _set_export_progress(self, message: str, value: float | None, color: str, busy: bool = True) -> None:
        if self._exit_requested:
            return
        self._export_busy = busy
        self.export_progress.visible = busy or value not in (None, 0)
        self.export_progress.value = value
        self.export_status.value = message
        self.export_status.color = color
        self.progress.visible = busy
        self.progress.value = value if value is not None else None
        self._refresh_sync_area(include_dependent_panels=False)

    def _run_sync_precheck(self, _event: Any) -> None:
        self._run_background(self._sync_precheck_worker)

    def _sync_precheck_worker(self) -> None:
        if not self.client:
            self._show_message(self.t("no_connection"))
            return
        if not self.spreadsheet:
            self._show_message(self.t("no_file"))
            return
        self._save_mapping()
        self._save_target_settings()
        settings_error = self._target_settings_error()
        if settings_error:
            self._show_message(settings_error)
            return
        if not self._rule_key_source():
            self._show_message(self.t("rule_column_missing"))
            return
        self._set_busy(True)
        try:
            rows = self._current_profile_rule_rows()
            mode = ProfileSyncMode(str(self.sync_mode.value or ProfileSyncMode.PATCH.value))
            self._set_sync_plan(ProfileSyncService(self.client).precheck(rows, mode))
            text = self.t("sync_precheck_passed") if self.sync_plan.can_apply else self.t("sync_precheck_blocked")
            color = SUCCESS if self.sync_plan.can_apply else DANGER
            self._set_status(self.sync_status, text, color)
        except Exception as exc:
            self._set_status(self.sync_status, str(exc), DANGER)
        finally:
            self._set_busy(False)
            if not self._exit_requested:
                self._refresh_sync_area()

    def _apply_sync(self, _event: Any) -> None:
        if self.sync_plan and self.sync_plan.mode == ProfileSyncMode.REPLACE:
            self.page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text(self.t("confirm_replace_sync"), weight=WEIGHT_SEMIBOLD, color=TEXT),
                    content=ft.Text(self.t("confirm_replace_sync_body"), color=TEXT_MUTED),
                    actions=[
                        ft.TextButton(self.t("cancel"), on_click=lambda _e: self.page.pop_dialog()),
                        ft.FilledButton(self.t("apply_sync"), icon=ft.Icons.SYNC, on_click=self._confirm_apply_sync),
                    ],
                    modal=True,
                )
            )
            self.page.update()
            return
        self._run_background(self._apply_sync_worker)

    def _confirm_apply_sync(self, _event: Any) -> None:
        self.page.pop_dialog()
        self._run_background(self._apply_sync_worker)

    def _apply_sync_worker(self) -> None:
        if not self.client or not self.sync_plan:
            return
        self._set_busy(True)
        try:
            result = ProfileSyncService(self.client).apply(self.sync_plan, backup_dir=visible_reports_dir() / "profile_backups")
            self.sync_result = export_sync_report(result, output_dir=visible_report_run_dir())
            self._show_message(self.t("report_created"))
        except Exception as exc:
            self._show_message(str(exc))
        finally:
            self._set_busy(False)
            if not self._exit_requested:
                self._refresh_sync_area()

    def _export_sync_precheck(self, _event: Any) -> None:
        if not self.sync_plan:
            return
        _json_path, xlsx_path = export_sync_precheck_report(self.sync_plan, output_dir=visible_report_run_dir())
        self._show_message(f"{self.t('report_created')}: {xlsx_path}")

    def _current_rows_for_helpers(self) -> list[RuleRow]:
        return self._current_rule_rows()

    def _apply_changes(self, _event: Any) -> None:
        self._run_background(self._apply_worker)

    def _apply_worker(self) -> None:
        if not self.client or not self.precheck_result:
            return
        self._set_busy(True)
        try:
            service = WorkflowService(self.client)
            backup_path = visible_reports_dir() / "profile_backups"
            result = service.apply(self.precheck_result, backup_dir=backup_path)
            self.apply_result = export_apply_report(result, output_dir=visible_report_run_dir())
            self._show_message(self.t("report_created"))
        except Exception as exc:
            self._show_message(str(exc))
        finally:
            self._set_busy(False)
            if not self._exit_requested:
                self.render()

    def _export_precheck(self, _event: Any) -> None:
        if not self.precheck_result:
            return
        json_path, xlsx_path = export_precheck_report(self.precheck_result, output_dir=visible_report_run_dir())
        self._show_message(f"{self.t('report_created')}: {xlsx_path}")

    def _open_report_folder(self, _event: Any) -> None:
        report_xlsx = None
        if self.apply_result and self.apply_result.report_xlsx:
            report_xlsx = self.apply_result.report_xlsx
        elif self.sync_result and self.sync_result.report_xlsx:
            report_xlsx = self.sync_result.report_xlsx
        if not report_xlsx:
            return
        folder = str(report_xlsx.parent)
        try:
            os.startfile(folder)
        except AttributeError:
            subprocess.Popen(["open", folder])
        except OSError as exc:
            self._show_message(str(exc))

    def _toggle_language(self, _event: Any) -> None:
        self.translator.set_language("en" if self.translator.language == "zh" else "zh")
        self.config.language = self.translator.language
        self.store.save(self.config)
        self.render()

    def _confirm_exit(self, _event: Any) -> None:
        if self._exit_requested:
            return
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(self.t("exit_app"), weight=WEIGHT_SEMIBOLD, color=TEXT),
                content=ft.Text(self.t("exit_confirm"), color=TEXT_MUTED),
                actions=[
                    ft.TextButton(self.t("cancel"), on_click=lambda _e: self.page.pop_dialog()),
                    ft.FilledButton(self.t("exit_app"), icon=ft.Icons.LOGOUT, on_click=self._shutdown_app),
                ],
                modal=True,
            )
        )
        self.page.update()

    def _on_window_event(self, event: ft.WindowEvent) -> None:
        if event.type == ft.WindowEventType.CLOSE:
            self._shutdown_app()

    def _on_connect(self, _event: Any) -> None:
        self._disconnect_token += 1
        self._connected = True

    def _on_disconnect(self, _event: Any) -> None:
        if os.environ.get("SONARQUBE_PROFILE_CREATOR_VIEW") != "web":
            return
        self._disconnect_token += 1
        self._connected = False
        token = self._disconnect_token

        def delayed_exit() -> None:
            time.sleep(EXIT_DELAY_SECONDS)
            if token == self._disconnect_token and not self._connected:
                _request_process_exit()

        threading.Thread(target=delayed_exit, daemon=True).start()

    def _shutdown_app(self, _event: Any | None = None) -> None:
        if self._exit_requested:
            return
        self._exit_requested = True
        try:
            self.page.pop_dialog()
        except Exception:
            pass
        self.progress.visible = False
        self.progress.value = 0
        try:
            self.page.update()
        except Exception:
            pass
        _request_process_exit()

    def _set_busy(self, busy: bool) -> None:
        if self._exit_requested:
            return
        self.progress.visible = busy
        self.progress.value = None if busy else 0
        self._refresh_progress_only()

    def _set_status(self, control: ft.Text, value: str, color: str) -> None:
        if self._exit_requested:
            return
        control.value = value
        control.color = color
        self._safe_page_update()

    def _show_message(self, message: str) -> None:
        if self._exit_requested:
            return
        try:
            self.page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text(self.t("app_title"), weight=WEIGHT_SEMIBOLD, color=TEXT),
                    content=ft.Text(message, color=TEXT_MUTED, selectable=True),
                    actions=[ft.TextButton("OK", on_click=lambda _e: self.page.pop_dialog())],
                    modal=False,
                )
            )
            self._safe_page_update()
        except Exception:
            return

    def _run_background(self, worker) -> None:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _safe_page_update(self) -> None:
        try:
            self.page.update()
        except Exception:
            return


def app(page: ft.Page) -> None:
    ProfileCreatorApp(page).run()


def languages_from_rows(rows: Iterable[RuleRow]) -> list[str]:
    return sorted({row.language for row in rows if row.language})


def selected_profiles_by_language(values: dict[str, str], languages: Iterable[str]) -> dict[str, str]:
    language_set = set(languages)
    return {language: profile for language, profile in values.items() if language in language_set and profile}


def target_profile_for_language(rows: Iterable[RuleRow], language: str) -> str:
    profiles = sorted({row.target_profile for row in rows if row.language == language and row.target_profile})
    return profiles[0] if len(profiles) == 1 else ""


def visible_reports_dir() -> Path:
    path = profile_export_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def visible_report_run_dir() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = visible_reports_dir() / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def apply_target_settings_to_rule_rows(
    rows: Iterable[RuleRow],
    language: str,
    target_profile: str,
    strategy: str = "",
    project_key: str = "",
    set_default: bool = False,
) -> list[RuleRow]:
    default_value = "true" if set_default else ""
    return [
        RuleRow(
            source_row=row.source_row,
            language=language,
            target_profile=target_profile,
            rule_key=row.rule_key,
            parent_profile="",
            strategy=strategy,
            active=row.active,
            sync_action=row.sync_action,
            source_profile="",
            profile_key="",
            rule_name=row.rule_name,
            inheritance=row.inheritance,
            severity=row.severity,
            params=row.params,
            prioritizedRule=row.prioritizedRule,
            project_key=project_key,
            set_default=default_value,
            note=row.note,
            raw=row.raw,
        )
        for row in rows
    ]


def apply_target_settings_to_profile_rule_rows(
    rows: Iterable[ProfileRuleRow],
    language: str,
    target_profile: str,
) -> list[ProfileRuleRow]:
    return [
        ProfileRuleRow(
            source_row=row.source_row,
            language=language,
            target_profile=target_profile,
            profile_key=row.profile_key,
            source_profile=row.source_profile,
            rule_key=row.rule_key,
            rule_name=row.rule_name,
            active=row.active,
            severity=row.severity,
            params=row.params,
            prioritizedRule=row.prioritizedRule,
            inheritance=row.inheritance,
            sync_action=row.sync_action,
            note=row.note,
            raw=row.raw,
        )
        for row in rows
    ]


def profile_export_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _issue_sort_key(issue) -> tuple[int, int, str]:
    status_rank = {
        ItemStatus.ERROR: 0,
        ItemStatus.WARNING: 1,
        ItemStatus.SKIPPED: 2,
        ItemStatus.OK: 3,
    }.get(issue.status, 4)
    row = issue.source_row if issue.source_row is not None else 10**9
    return (status_rank, row, issue.category)


def _sync_display_items(plan: ProfileSyncPlan | None, result: ProfileSyncResult | None = None) -> list[SyncDisplayItem]:
    if not plan:
        return []
    items = [_sync_item_from_issue(issue) for issue in plan.issues]
    action_source = result.actions if result and result.actions else plan.actions
    items.extend(_sync_item_from_action(action) for action in action_source)
    return sorted(items, key=_sync_display_sort_key)


def _filter_sync_display_items(items: Iterable[SyncDisplayItem], filter_value: str) -> list[SyncDisplayItem]:
    if filter_value == SYNC_FILTER_ALL:
        return list(items)
    if filter_value == SYNC_FILTER_ERROR:
        return [item for item in items if item.status == ItemStatus.ERROR]
    if filter_value == SYNC_FILTER_WARNING:
        return [item for item in items if item.status == ItemStatus.WARNING]
    return [item for item in items if item.action == filter_value]


def _sync_item_from_issue(issue: ValidationIssue) -> SyncDisplayItem:
    return SyncDisplayItem(
        status=issue.status,
        action=issue.category,
        message=issue.message,
        source_row=issue.source_row,
        suggestion=issue.suggestion,
    )


def _sync_item_from_action(action: ActionResult) -> SyncDisplayItem:
    return SyncDisplayItem(
        status=action.status,
        action=action.action,
        message=action.message,
        language=action.language,
        profile=action.profile,
        rule_key=action.rule_key,
        source_row=action.source_row,
        suggestion=action.suggestion,
    )


def _sync_display_sort_key(item: SyncDisplayItem) -> tuple[int, int, str, str]:
    status_rank = {
        ItemStatus.ERROR: 0,
        ItemStatus.WARNING: 1,
        ItemStatus.SKIPPED: 2,
        ItemStatus.OK: 3,
    }.get(item.status, 4)
    row = item.source_row if item.source_row is not None else 10**9
    return (status_rank, row, item.action, item.rule_key)


def _sync_item_message(item: SyncDisplayItem) -> str:
    if item.suggestion:
        return f"{item.message} {item.suggestion}"[:180]
    return item.message[:180]


def _request_process_exit() -> None:
    _clear_instance_file()
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(os.getpid()), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
    os._exit(0)


def _clear_instance_file() -> None:
    path = user_config_dir() / "instance.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _border_all(color: str) -> ft.Border:
    side = ft.BorderSide(width=1, color=color)
    return ft.Border(left=side, right=side, top=side, bottom=side)


def _padding_all(value: int | float) -> ft.Padding:
    return ft.Padding(left=value, top=value, right=value, bottom=value)


def _padding_symmetric(horizontal: int | float = 0, vertical: int | float = 0) -> ft.Padding:
    return ft.Padding(left=horizontal, top=vertical, right=horizontal, bottom=vertical)


def _soft_for_color(color: str) -> str:
    if color == SUCCESS:
        return SUCCESS_SOFT
    if color == DANGER:
        return DANGER_SOFT
    if color == WARNING:
        return WARNING_SOFT
    if color in (PRIMARY, PRIMARY_DARK):
        return PRIMARY_SOFT
    return NEUTRAL_SOFT


def _dropdown_options(values: Iterable[str]) -> list[ft.DropdownOption]:
    return [ft.DropdownOption(key=value, text=value or "-") for value in values]

