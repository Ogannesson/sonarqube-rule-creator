from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ProfileStrategy(str, Enum):
    EXTEND_DEFAULT = "extend_default"
    EXTEND_SELECTED = "extend_selected"
    COPY = "copy"
    INDEPENDENT = "independent"


class ProfileSyncMode(str, Enum):
    PATCH = "patch"
    REPLACE = "replace"


class ItemStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    WARNING = "warning"
    ERROR = "error"


REQUIRED_FIELDS = ("language", "target_profile", "rule_key")
OPTIONAL_FIELDS = (
    "parent_profile",
    "strategy",
    "active",
    "sync_action",
    "source_profile",
    "profile_key",
    "rule_name",
    "inheritance",
    "severity",
    "params",
    "prioritizedRule",
    "project_key",
    "set_default",
    "note",
)
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS


@dataclass(frozen=True)
class RuleRow:
    source_row: int
    language: str
    target_profile: str
    rule_key: str
    parent_profile: str = ""
    strategy: str = ""
    active: str = ""
    sync_action: str = ""
    source_profile: str = ""
    profile_key: str = ""
    rule_name: str = ""
    inheritance: str = ""
    severity: str = ""
    params: str = ""
    prioritizedRule: str = ""
    project_key: str = ""
    set_default: str = ""
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileRuleRow:
    source_row: int
    language: str
    target_profile: str
    profile_key: str
    source_profile: str
    rule_key: str
    rule_name: str = ""
    active: str = "true"
    severity: str = ""
    params: str = ""
    prioritizedRule: str = ""
    inheritance: str = ""
    sync_action: str = ""
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldMapping:
    columns: dict[str, str]

    def source_for(self, canonical_field: str) -> str:
        return self.columns.get(canonical_field, "")


@dataclass
class SpreadsheetData:
    path: Path
    headers: list[str]
    preview_rows: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    inferred_mapping: FieldMapping


@dataclass(frozen=True)
class ProfilePlan:
    language: str
    target_profile: str
    strategy: ProfileStrategy
    parent_profile: str = ""
    source_profile: str = ""
    project_keys: tuple[str, ...] = ()
    set_default: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.language, self.target_profile)


@dataclass
class ValidationIssue:
    status: ItemStatus
    category: str
    message: str
    source_row: int | None = None
    raw: dict[str, Any] | None = None
    suggestion: str = ""


@dataclass
class ActionResult:
    status: ItemStatus
    action: str
    message: str
    language: str = ""
    profile: str = ""
    rule_key: str = ""
    source_row: int | None = None
    api_error: str = ""
    suggestion: str = ""


@dataclass
class PrecheckResult:
    rows: list[RuleRow]
    profile_plans: list[ProfilePlan]
    issues: list[ValidationIssue] = field(default_factory=list)
    rule_status: dict[str, ItemStatus] = field(default_factory=dict)
    existing_profiles: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    default_profiles: dict[str, str] = field(default_factory=dict)

    @property
    def can_apply(self) -> bool:
        return all(issue.status != ItemStatus.ERROR for issue in self.issues)

    def count(self, status: ItemStatus) -> int:
        return sum(1 for issue in self.issues if issue.status == status)


@dataclass
class ApplyResult:
    precheck: PrecheckResult
    actions: list[ActionResult] = field(default_factory=list)
    report_json: Path | None = None
    report_xlsx: Path | None = None

    @property
    def has_errors(self) -> bool:
        return any(action.status == ItemStatus.ERROR for action in self.actions)


@dataclass
class ProfileExportResult:
    rows: list[ProfileRuleRow]
    csv_path: Path | None = None
    xlsx_path: Path | None = None


@dataclass
class ProfileSyncPlan:
    mode: ProfileSyncMode
    rows: list[ProfileRuleRow]
    actions: list[ActionResult] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    profile_keys: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def can_apply(self) -> bool:
        return all(issue.status != ItemStatus.ERROR for issue in self.issues)


@dataclass
class ProfileSyncResult:
    plan: ProfileSyncPlan
    actions: list[ActionResult] = field(default_factory=list)
    report_json: Path | None = None
    report_xlsx: Path | None = None

    @property
    def has_errors(self) -> bool:
        return any(action.status == ItemStatus.ERROR for action in self.actions)
