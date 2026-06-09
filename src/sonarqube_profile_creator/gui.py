from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import flet as ft

from .config import AppConfig, ConfigStore, reports_dir, user_config_dir
from .i18n import Translator, detect_language
from .models import ALL_FIELDS, FieldMapping, ItemStatus, ProfileStrategy, SpreadsheetData
from .reports import export_apply_report, export_precheck_report
from .sonarqube import ConnectionInfo, SonarQubeClient, SonarQubeError
from .spreadsheet import read_spreadsheet, rows_from_mapping, validate_required_mapping
from .workflow import WorkflowService, parse_strategy


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
        self.available_profiles: dict[str, list[str]] = {}
        self.default_profiles: dict[str, str] = {}

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
        self.strategy_group = ft.RadioGroup(
            value=self.config.default_strategy or ProfileStrategy.EXTEND_DEFAULT.value,
            content=ft.Column([]),
            on_change=lambda _e: self.render(),
        )
        self.parent_dropdown = ft.Dropdown(label=self.t("parent_profile"), width=420, border_color=LINE, focused_border_color=PRIMARY)
        self.copy_dropdown = ft.Dropdown(label=self.t("source_profile"), width=420, border_color=LINE, focused_border_color=PRIMARY)
        self.precheck_status = ft.Text("", color=TEXT_MUTED)
        self.progress = ft.ProgressBar(visible=False, color=PRIMARY, bgcolor=PRIMARY_SOFT)
        self.main_area = ft.Column([], expand=True, scroll=ft.ScrollMode.AUTO)
        self._disconnect_token = 0
        self._connected = True

    def run(self) -> None:
        self.page.title = self.t("app_title")
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.window.width = 1320
        self.page.window.height = 860
        self.page.padding = 0
        self.page.bgcolor = APP_BG
        self.page.on_connect = self._on_connect
        self.page.on_disconnect = self._on_disconnect
        self.page.services.append(self.file_picker)
        self.render()

    def t(self, key: str, **kwargs: object) -> str:
        return self.translator.t(key, **kwargs)

    def render(self) -> None:
        self.page.clean()
        self.page.title = self.t("app_title")
        self.page.bgcolor = APP_BG
        self.page.padding = 0
        self.server_url.label = self.t("server_url")
        self.token.label = self.t("token")
        self.remember_token.label = self.t("remember_token")
        self.file_path.label = self.t("selected_file")
        self.parent_dropdown.label = self.t("parent_profile")
        self.copy_dropdown.label = self.t("source_profile")
        self._refresh_strategy_options()

        self.page.add(
            ft.Container(
                content=ft.Column(
                    [
                        self._top_bar(),
                        self.progress,
                        ft.Row(
                            [
                                self._sidebar(),
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
                expand=True,
            )
        )
        self.page.update()

    def _top_bar(self) -> ft.Control:
        lang_label = "English" if self.translator.language == "zh" else "中文"
        return ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.Container(
                                content=ft.Icon(ft.Icons.ADMIN_PANEL_SETTINGS, color=ft.Colors.WHITE, size=22),
                                width=42,
                                height=42,
                                bgcolor=PRIMARY,
                                border_radius=8,
                                alignment=ft.Alignment(0, 0),
                            ),
                            ft.Column(
                                [
                                    ft.Text(self.t("app_title"), size=20, weight=ft.FontWeight.BOLD, color=TEXT),
                                    ft.Text(self.t("app_subtitle"), size=13, color=TEXT_MUTED),
                                ],
                                spacing=1,
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True,
                    ),
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
                    ft.OutlinedButton(
                        lang_label,
                        icon=ft.Icons.LANGUAGE,
                        on_click=self._toggle_language,
                        height=42,
                    ),
                    ft.OutlinedButton(
                        self.t("exit_app"),
                        icon=ft.Icons.LOGOUT,
                        on_click=self._confirm_exit,
                        height=42,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=78,
            padding=_padding_symmetric(horizontal=24, vertical=10),
            bgcolor=SURFACE,
            border=ft.Border(bottom=ft.BorderSide(width=1, color=LINE_SOFT)),
        )

    def _sidebar(self) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(self.t("workflow"), size=12, color=SIDEBAR_MUTED, weight=ft.FontWeight.BOLD),
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
                    self._workspace_header(),
                    ft.Container(
                        content=ft.Column(
                            [
                                self._connect_panel(),
                                self._import_panel(),
                                self._strategy_panel(),
                                self._precheck_panel(),
                                self._apply_panel(),
                                self._report_panel(),
                            ],
                            spacing=16,
                            expand=True,
                            scroll=ft.ScrollMode.AUTO,
                        ),
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

    def _workspace_header(self) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(self._current_step_title(), size=26, weight=ft.FontWeight.BOLD, color=TEXT),
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
            self.t("step_precheck"),
            self.t("step_apply"),
            self.t("step_report"),
        ]

    def _step_descriptions(self) -> list[str]:
        return [
            self.t("desc_connect"),
            self.t("desc_import"),
            self.t("desc_strategy"),
            self.t("desc_precheck"),
            self.t("desc_apply"),
            self.t("desc_report"),
        ]

    def _current_step_title(self) -> str:
        return self._step_titles()[min(self._current_step_index(), len(self._step_titles()) - 1)]

    def _current_step_description(self) -> str:
        return self._step_descriptions()[min(self._current_step_index(), len(self._step_descriptions()) - 1)]

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
                            ft.Text(title, size=14, weight=ft.FontWeight.BOLD if current else ft.FontWeight.W_500, color=ft.Colors.WHITE if current else "#D1D5DB"),
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
                    ft.Text(self.t("operator_note"), size=13, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
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
        if self.apply_result:
            return ft.FilledButton(self.t("open_report_folder"), icon=ft.Icons.FOLDER_OPEN, on_click=self._open_report_folder, height=44)
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
                    self._mapping_panel(),
                    self._preview_table(),
                ],
                spacing=12,
            ),
        )

    def _strategy_panel(self) -> ft.Control:
        parent_controls: list[ft.Control] = []
        if self.strategy_group.value == ProfileStrategy.EXTEND_SELECTED.value:
            parent_controls.append(self.parent_dropdown)
        if self.strategy_group.value == ProfileStrategy.COPY.value:
            parent_controls.append(self.copy_dropdown)
        return self._section(
            self.t("strategy"),
            self.t("desc_strategy"),
            ft.Icons.ACCOUNT_TREE,
            ft.Column(
                [
                    self.strategy_group,
                    ft.Row(parent_controls, wrap=True) if parent_controls else self._inline_status(self._strategy_help_text(), TEXT_MUTED, ft.Icons.INFO),
                ],
                spacing=8,
            ),
        )

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
                                    ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color=TEXT),
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
        caps = ", ".join(name for name, enabled in self.connection_info.capabilities.items() if enabled)
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
                    ft.Text(f"{self.t('api_capability')}: {caps or '-'}", color=TEXT_MUTED, size=13),
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
                self._metric(self.t("field_mapping"), f"{self._mapped_field_count()}/{len(ALL_FIELDS)}", SUCCESS, ft.Icons.SWAP_HORIZ),
                self._metric(self.t("preview"), str(min(20, len(self.spreadsheet.preview_rows))), WARNING, ft.Icons.VISIBILITY),
            ],
            wrap=True,
            spacing=10,
        )

    def _mapping_panel(self) -> ft.Control:
        if not self.spreadsheet:
            return ft.Text("")
        self._ensure_mapping_controls()
        rows = []
        for field in ALL_FIELDS:
            label_key = f"field_{field}"
            rows.append(self._field_mapping_item(field, self.t(label_key)))
        missing = validate_required_mapping(self._current_mapping())
        missing_text = f"{self.t('required_field_missing')}: {', '.join(missing)}" if missing else self.t("mapping_ready")
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(self.t("field_mapping"), weight=ft.FontWeight.BOLD, color=TEXT),
                        self._status_chip(
                            missing_text,
                            ft.Icons.ERROR_OUTLINE if missing else ft.Icons.CHECK_CIRCLE,
                            DANGER_SOFT if missing else SUCCESS_SOFT,
                            DANGER if missing else SUCCESS,
                        ),
                    ],
                    spacing=10,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.ResponsiveRow(rows, columns=12),
            ],
            spacing=10,
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
                columns=[ft.DataColumn(ft.Text(header, size=12, weight=ft.FontWeight.BOLD, color=TEXT)) for header in headers],
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

    def _metric(self, label: str, value: str, color: str, icon: str = ft.Icons.INSIGHTS) -> ft.Control:
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
                            ft.Text(value, size=22, weight=ft.FontWeight.BOLD, color=TEXT),
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
            border=_border_all(LINE_SOFT),
            border_radius=8,
            bgcolor=SURFACE_MUTED,
        )

    def _issues_table(self) -> ft.Control:
        if not self.precheck_result or not self.precheck_result.issues:
            if self.precheck_result:
                return self._empty_state(self.t("issues_empty"), ft.Icons.CHECK_CIRCLE)
            return ft.Text("")
        rows = []
        for issue in self.precheck_result.issues[:80]:
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
                    ft.DataColumn(ft.Text("status", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("category", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("row", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("message", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("suggestion", size=12, weight=ft.FontWeight.BOLD)),
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
                    ft.DataColumn(ft.Text("status", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("action", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("language", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("profile", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("rule", size=12, weight=ft.FontWeight.BOLD)),
                    ft.DataColumn(ft.Text("message", size=12, weight=ft.FontWeight.BOLD)),
                ],
                rows=rows,
                heading_row_color=SURFACE_MUTED,
                column_spacing=16,
                divider_thickness=1,
            ),
        )

    def _field_mapping_item(self, field: str, label: str) -> ft.Control:
        required = field in ("language", "target_profile", "rule_key")
        control = self.mapping_controls[field]
        control.width = 280
        control.border_radius = 6
        control.border_color = LINE
        control.focused_border_color = PRIMARY
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(label, size=13, weight=ft.FontWeight.BOLD, color=TEXT),
                            self._mini_badge(self.t("required") if required else self.t("optional"), DANGER if required else TEXT_MUTED, DANGER_SOFT if required else NEUTRAL_SOFT),
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

    def _table_block(self, title: str, table: ft.Control) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, weight=ft.FontWeight.BOLD, color=TEXT),
                    ft.Container(
                        content=ft.Row([table], scroll=ft.ScrollMode.AUTO),
                        border=_border_all(LINE_SOFT),
                        border_radius=8,
                        bgcolor=SURFACE,
                    ),
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
                    ft.Text(text, size=12, color=color, weight=ft.FontWeight.BOLD, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
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
            content=ft.Text(text, size=11, color=color, weight=ft.FontWeight.BOLD),
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

    def _mapped_field_count(self) -> int:
        if not self.mapping_controls:
            return 0
        return sum(1 for control in self.mapping_controls.values() if control.value)

    def _current_step_index(self) -> int:
        if self.apply_result:
            return 5
        if self.precheck_result:
            if self.precheck_result.can_apply:
                return 4
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

    def _choose_file(self, _event: Any) -> None:
        files = self.file_picker.pick_files(
            allow_multiple=False,
            allowed_extensions=["csv", "xlsx", "xlsm"],
            dialog_title=self.t("choose_file"),
        )
        if files:
            self.file_path.value = files[0].path
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
        self._set_busy(True)
        try:
            client = SonarQubeClient(server_url, token)
            info = client.test_connection()
            self.client = client
            self.connection_info = info
            self.default_profiles = client.get_default_profiles()
            self.available_profiles = self._load_available_profiles(client)
            self._refresh_profile_dropdowns()
            self.config.server_url = server_url
            self.config.language = self.translator.language
            self.store.save(self.config)
            if self.remember_token.value:
                self.store.save_token(server_url, token)
            self._set_status(self.connection_status, self.t("connected"), SUCCESS)
        except SonarQubeError as exc:
            self._set_status(self.connection_status, f"{self.t('connection_failed')}: {exc}", DANGER)
        finally:
            self._set_busy(False)
            self.render()

    def _load_available_profiles(self, client: SonarQubeClient) -> dict[str, list[str]]:
        profiles_by_language: dict[str, list[str]] = {}
        try:
            profiles = client.search_quality_profiles()
        except SonarQubeError:
            return profiles_by_language
        for profile in profiles:
            language = str(profile.get("language", ""))
            name = str(profile.get("name", ""))
            if language and name:
                profiles_by_language.setdefault(language, []).append(name)
        return {language: sorted(set(names)) for language, names in profiles_by_language.items()}

    def _refresh_profile_dropdowns(self) -> None:
        names = sorted({name for names in self.available_profiles.values() for name in names})
        options = [ft.DropdownOption(key=name, text=name) for name in names]
        self.parent_dropdown.options = options
        self.copy_dropdown.options = options
        if not self.parent_dropdown.value and names:
            self.parent_dropdown.value = names[0]
        if not self.copy_dropdown.value and names:
            self.copy_dropdown.value = names[0]

    def _load_file(self, _event: Any) -> None:
        path = self.file_path.value.strip()
        if not path:
            self._show_message(self.t("no_file"))
            return
        try:
            self.spreadsheet = read_spreadsheet(path)
            self.mapping = self.spreadsheet.inferred_mapping
            self._ensure_mapping_controls(reset=True)
            self.config.last_file = path
            self.store.save(self.config)
            self.precheck_result = None
            self.apply_result = None
            self.render()
        except Exception as exc:
            self._show_message(str(exc))

    def _ensure_mapping_controls(self, reset: bool = False) -> None:
        if not self.spreadsheet:
            return
        headers = [""] + self.spreadsheet.headers
        options = [ft.DropdownOption(key=header, text=header or "-") for header in headers]
        if reset:
            self.mapping_controls = {}
        for field in ALL_FIELDS:
            if field in self.mapping_controls:
                self.mapping_controls[field].options = options
                continue
            inferred = ""
            if self.spreadsheet.inferred_mapping:
                inferred = self.spreadsheet.inferred_mapping.source_for(field)
            saved = self.config.last_mapping.get(field, "")
            value = saved if saved in headers else inferred
            self.mapping_controls[field] = ft.Dropdown(
                value=value,
                options=options,
                width=260,
                enable_search=True,
                border_color=LINE,
                focused_border_color=PRIMARY,
                border_radius=6,
                on_select=lambda _e: self._save_mapping(),
            )

    def _current_mapping(self) -> FieldMapping:
        return FieldMapping(columns={field: control.value or "" for field, control in self.mapping_controls.items()})

    def _save_mapping(self) -> None:
        self.mapping = self._current_mapping()
        self.config.last_mapping = self.mapping.columns
        self.store.save(self.config)

    def _run_precheck(self, _event: Any) -> None:
        self._run_background(self._precheck_worker)

    def _precheck_worker(self) -> None:
        if not self.client:
            self._show_message(self.t("no_connection"))
            return
        if not self.spreadsheet:
            self._show_message(self.t("no_file"))
            return
        self._save_mapping()
        missing = validate_required_mapping(self.mapping or FieldMapping({}))
        if missing:
            self._show_message(f"{self.t('required_field_missing')}: {', '.join(missing)}")
            return
        self._set_busy(True)
        try:
            rows = rows_from_mapping(self.spreadsheet.rows, self.mapping or self.spreadsheet.inferred_mapping)
            strategy = parse_strategy(str(self.strategy_group.value or ""), ProfileStrategy.EXTEND_DEFAULT)
            service = WorkflowService(self.client)
            parent_by_language = self._selected_profile_by_language(self.parent_dropdown.value or "")
            copy_by_language = self._selected_profile_by_language(self.copy_dropdown.value or "")
            self.precheck_result = service.precheck(
                rows,
                default_strategy=strategy,
                selected_parent_by_language=parent_by_language,
                copy_source_by_language=copy_by_language,
            )
            self.apply_result = None
            text = self.t("precheck_passed") if self.precheck_result.can_apply else self.t("precheck_blocked")
            color = SUCCESS if self.precheck_result.can_apply else DANGER
            self._set_status(self.precheck_status, text, color)
        except Exception as exc:
            self._set_status(self.precheck_status, str(exc), DANGER)
        finally:
            self._set_busy(False)
            self.render()

    def _selected_profile_by_language(self, profile_name: str) -> dict[str, str]:
        if not profile_name:
            return {}
        result = {}
        for language, profiles in self.available_profiles.items():
            if profile_name in profiles:
                result[language] = profile_name
        return result

    def _apply_changes(self, _event: Any) -> None:
        self._run_background(self._apply_worker)

    def _apply_worker(self) -> None:
        if not self.client or not self.precheck_result:
            return
        self._set_busy(True)
        try:
            service = WorkflowService(self.client)
            backup_path = reports_dir() / "profile_backups"
            result = service.apply(self.precheck_result, backup_dir=backup_path)
            self.apply_result = export_apply_report(result)
            self._show_message(self.t("report_created"))
        except Exception as exc:
            self._show_message(str(exc))
        finally:
            self._set_busy(False)
            self.render()

    def _export_precheck(self, _event: Any) -> None:
        if not self.precheck_result:
            return
        json_path, xlsx_path = export_precheck_report(self.precheck_result)
        self._show_message(f"{self.t('report_created')}: {xlsx_path}")

    def _open_report_folder(self, _event: Any) -> None:
        if not self.apply_result or not self.apply_result.report_xlsx:
            return
        folder = str(self.apply_result.report_xlsx.parent)
        try:
            os.startfile(folder)
        except AttributeError:
            subprocess.Popen(["open", folder])

    def _toggle_language(self, _event: Any) -> None:
        self.translator.set_language("en" if self.translator.language == "zh" else "zh")
        self.config.language = self.translator.language
        self.store.save(self.config)
        self.render()

    def _confirm_exit(self, _event: Any) -> None:
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(self.t("exit_app"), weight=ft.FontWeight.BOLD, color=TEXT),
                content=ft.Text(self.t("exit_confirm"), color=TEXT_MUTED),
                actions=[
                    ft.TextButton(self.t("cancel"), on_click=lambda _e: self.page.pop_dialog()),
                    ft.FilledButton(self.t("exit_app"), icon=ft.Icons.LOGOUT, on_click=self._shutdown_app),
                ],
                modal=True,
            )
        )
        self.page.update()

    def _on_connect(self, _event: Any) -> None:
        self._disconnect_token += 1
        self._connected = True

    def _on_disconnect(self, _event: Any) -> None:
        self._disconnect_token += 1
        self._connected = False
        token = self._disconnect_token

        def delayed_exit() -> None:
            time.sleep(EXIT_DELAY_SECONDS)
            if token == self._disconnect_token and not self._connected:
                _request_process_exit()

        threading.Thread(target=delayed_exit, daemon=True).start()

    def _shutdown_app(self, _event: Any | None = None) -> None:
        try:
            self.page.pop_dialog()
        except Exception:
            pass
        threading.Thread(target=_request_process_exit, daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self.progress.visible = busy
        self.progress.value = None if busy else 0
        self.page.update()

    def _set_status(self, control: ft.Text, value: str, color: str) -> None:
        control.value = value
        control.color = color
        self.page.update()

    def _show_message(self, message: str) -> None:
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(self.t("app_title"), weight=ft.FontWeight.BOLD, color=TEXT),
                content=ft.Text(message, color=TEXT_MUTED),
                actions=[ft.TextButton("OK", on_click=lambda _e: self.page.pop_dialog())],
                modal=False,
            )
        )
        self.page.update()

    def _run_background(self, worker) -> None:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()


def app(page: ft.Page) -> None:
    ProfileCreatorApp(page).run()


def _request_process_exit() -> None:
    time.sleep(0.2)
    _clear_instance_file()
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
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
