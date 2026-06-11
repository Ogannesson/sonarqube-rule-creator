from __future__ import annotations

from pathlib import Path
from typing import Callable

from .concurrency import bounded_parallel_map, client_read_workers, client_write_workers
from .models import (
    ActionResult,
    ItemStatus,
    ProfileExportResult,
    ProfileRuleRow,
    ProfileSyncMode,
    ProfileSyncPlan,
    ProfileSyncResult,
    ValidationIssue,
)
from .sonarqube import SonarQubeClient, SonarQubeError
from .spreadsheet import write_profile_rules
from .workflow import parse_bool


SYNC_ACTIONS = {"", "noop", "activate", "update", "deactivate", "skip", "skipped"}
ProgressCallback = Callable[[str, float | None], None]


class ProfileExportService:
    def __init__(self, client: SonarQubeClient) -> None:
        self.client = client

    def export_profile(
        self,
        language: str,
        quality_profile: str,
        output_dir: Path,
        target_profile: str = "",
        progress: ProgressCallback | None = None,
        include_activation_details: bool = False,
    ) -> ProfileExportResult:
        _notify(progress, "resolve", 0.05)
        profile = self.client.get_profile_by_name(language, quality_profile)
        if not profile:
            raise SonarQubeError(f"Profile '{quality_profile}' was not found for {language}.")
        profile_key = str(profile.get("key", ""))
        if not profile_key:
            raise SonarQubeError(f"Profile '{quality_profile}' has no API key.")
        source_name = str(profile.get("name", quality_profile))
        _notify(progress, "read", 0.15)
        rows = self._profile_rows(
            language,
            profile_key,
            source_name,
            target_profile or source_name,
            progress=progress,
            include_activation_details=include_activation_details,
        )
        _notify(progress, "write", 0.90)
        csv_path, xlsx_path = write_profile_rules(output_dir, rows, _export_name(language, source_name))
        _notify(progress, "done", 1.0)
        return ProfileExportResult(rows=rows, csv_path=csv_path, xlsx_path=xlsx_path)

    def _profile_rows(
        self,
        language: str,
        profile_key: str,
        source_profile: str,
        target_profile: str,
        progress: ProgressCallback | None = None,
        include_activation_details: bool = False,
    ) -> list[ProfileRuleRow]:
        rows: list[ProfileRuleRow] = []
        active_rules = self.client.search_active_rules(profile_key)
        total = len(active_rules)
        step = max(total // 20, 1)
        for index, rule in enumerate(active_rules, start=2):
            rule_key = str(rule.get("key", ""))
            activation = _activation_from_rule(rule, profile_key)
            if include_activation_details and rule_key and not activation:
                activation = self.client.show_rule_activation(rule_key, profile_key)
            rows.append(
                ProfileRuleRow(
                    source_row=index,
                    language=language,
                    target_profile=target_profile,
                    profile_key=profile_key,
                    source_profile=source_profile,
                    rule_key=rule_key,
                    rule_name=str(rule.get("name", "")),
                    active="true",
                    severity=str(activation.get("severity", rule.get("severity", ""))),
                    params=_activation_params(activation),
                    prioritizedRule=str(activation.get("prioritizedRule", "")).lower(),
                    inheritance=str(activation.get("inheritance", "")),
                    sync_action="",
                    note="",
                    raw=rule,
                )
            )
            done = index - 1
            if progress and (done == total or done % step == 0):
                _notify(progress, "prepare", 0.20 + (0.65 * done / max(total, 1)))
        return rows


class ProfileSyncService:
    def __init__(self, client: SonarQubeClient) -> None:
        self.client = client

    def precheck(self, rows: list[ProfileRuleRow], mode: ProfileSyncMode = ProfileSyncMode.PATCH) -> ProfileSyncPlan:
        issues: list[ValidationIssue] = []
        actions: list[ActionResult] = []
        profile_keys: dict[tuple[str, str], str] = {}
        row_keys: set[tuple[str, str, str]] = set()

        for row in rows:
            issues.extend(_validate_sync_row(row))
        valid_rows = [row for row in rows if not any(issue.source_row == row.source_row and issue.status == ItemStatus.ERROR for issue in issues)]

        profile_targets = sorted({(row.language, row.target_profile) for row in valid_rows})

        def load_profile(target: tuple[str, str]) -> tuple[str, str, dict | None, SonarQubeError | None]:
            language, target_profile = target
            try:
                return (language, target_profile, self.client.get_profile_by_name(language, target_profile), None)
            except SonarQubeError as exc:
                return (language, target_profile, None, exc)

        for language, target_profile, profile, exc in bounded_parallel_map(profile_targets, load_profile, client_read_workers(self.client, len(profile_targets))):
            if exc:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="api_error",
                        message=f"Profile lookup failed for {target_profile}: {exc}",
                        suggestion="Check SonarQube permissions and API availability.",
                    )
                )
                continue
            if not profile:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="missing_profile",
                        message=f"Target profile '{target_profile}' was not found for {language}.",
                        suggestion="Create the target profile first or fix the table.",
                    )
                )
                continue
            profile_keys[(language, target_profile)] = str(profile.get("key", ""))

        server_rules = self._server_rules(profile_keys, issues)
        rule_exists = self._rule_existence(valid_rows, issues, server_rules)
        for row in valid_rows:
            profile_key = profile_keys.get((row.language, row.target_profile), row.profile_key)
            marker = (row.language, row.target_profile, row.rule_key)
            row_keys.add(marker)
            if not profile_key:
                continue
            if not rule_exists.get(row.rule_key, False):
                continue
            current = server_rules.get(marker)
            desired_action = _desired_action(row, current)
            actions.append(_planned_action(row, desired_action, current))

        if mode == ProfileSyncMode.REPLACE:
            for (language, target_profile, rule_key), current in sorted(server_rules.items()):
                if (language, target_profile, rule_key) in row_keys:
                    continue
                actions.append(
                    ActionResult(
                        status=ItemStatus.WARNING,
                        action="deactivate",
                        message="Rule exists on server but is missing from replace table.",
                        language=language,
                        profile=target_profile,
                        rule_key=rule_key,
                        suggestion="Apply replace mode to deactivate this rule.",
                    )
                )

        return ProfileSyncPlan(mode=mode, rows=rows, actions=actions, issues=issues, profile_keys=profile_keys)

    def apply(self, plan: ProfileSyncPlan, backup_dir: Path | None = None) -> ProfileSyncResult:
        actions: list[ActionResult] = []
        if not plan.can_apply:
            actions.append(
                ActionResult(
                    status=ItemStatus.ERROR,
                    action="sync_blocked",
                    message="Sync precheck has errors.",
                    suggestion="Fix precheck errors before applying changes.",
                )
            )
            return ProfileSyncResult(plan=plan, actions=actions)

        backed_up: set[tuple[str, str]] = set()
        write_jobs: list[ActionResult] = []
        for planned in plan.actions:
            if planned.action in {"noop", "skipped"}:
                actions.append(planned)
                continue
            profile_key = plan.profile_keys.get((planned.language, planned.profile), "")
            if not profile_key:
                actions.append(
                    ActionResult(
                        status=ItemStatus.ERROR,
                        action=planned.action,
                        message="Target profile key is missing.",
                        language=planned.language,
                        profile=planned.profile,
                        rule_key=planned.rule_key,
                    )
                )
                continue
            if backup_dir and (planned.language, planned.profile) not in backed_up:
                actions.append(self._backup(planned.language, planned.profile, backup_dir))
                backed_up.add((planned.language, planned.profile))
            row = _row_for_action(plan.rows, planned)
            if not row and planned.action != "deactivate":
                actions.append(planned)
                continue
            write_jobs.append(planned)

        def apply_one(planned: ActionResult) -> ActionResult:
            profile_key = plan.profile_keys.get((planned.language, planned.profile), "")
            row = _row_for_action(plan.rows, planned)
            try:
                if planned.action in {"activate", "update"} and row:
                    self.client.activate_rule(
                        profile_key,
                        planned.rule_key,
                        severity=row.severity.upper(),
                        params=row.params,
                        prioritized_rule=row.prioritizedRule,
                    )
                    return _applied(planned)
                if planned.action == "deactivate":
                    self.client.deactivate_rule(profile_key, planned.rule_key)
                    return _applied(planned)
                return planned
            except SonarQubeError as exc:
                return _api_error(planned.action, exc, planned.language, planned.profile, planned.rule_key, planned.source_row)

        actions.extend(bounded_parallel_map(write_jobs, apply_one, client_write_workers(self.client, len(write_jobs))))
        return ProfileSyncResult(plan=plan, actions=actions)

    def _rule_existence(
        self,
        rows: list[ProfileRuleRow],
        issues: list[ValidationIssue],
        server_rules: dict[tuple[str, str, str], ProfileRuleRow] | None = None,
    ) -> dict[str, bool]:
        result: dict[str, bool] = {}
        active_rule_keys = {rule_key for _language, _profile, rule_key in (server_rules or {})}
        for rule_key in active_rule_keys:
            result[rule_key] = True
        rule_keys = sorted({row.rule_key for row in rows if row.rule_key and row.rule_key not in active_rule_keys})

        def lookup(rule_key: str) -> tuple[str, bool, SonarQubeError | None]:
            try:
                return (rule_key, bool(self.client.search_rule(rule_key)), None)
            except SonarQubeError as exc:
                return (rule_key, False, exc)

        for rule_key, exists, exc in bounded_parallel_map(rule_keys, lookup, client_read_workers(self.client, len(rule_keys))):
            result[rule_key] = exists
            if exc:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="api_error",
                        message=f"Rule lookup failed for {rule_key}: {exc}",
                        suggestion="Check the SonarQube URL, token, and API availability.",
                    )
                )
                continue
            if not exists:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="missing_rule",
                        message=f"Rule '{rule_key}' does not exist in this SonarQube instance.",
                        suggestion="Check the rule key or installed language plugin.",
                    )
                )
        return result

    def _server_rules(
        self,
        profile_keys: dict[tuple[str, str], str],
        issues: list[ValidationIssue],
    ) -> dict[tuple[str, str, str], ProfileRuleRow]:
        result: dict[tuple[str, str, str], ProfileRuleRow] = {}
        targets = list(profile_keys.items())

        def load(target: tuple[tuple[str, str], str]) -> tuple[str, str, list[ProfileRuleRow], SonarQubeError | None]:
            (language, profile_name), profile_key = target
            try:
                rows = ProfileExportService(self.client)._profile_rows(
                    language,
                    profile_key,
                    profile_name,
                    profile_name,
                    include_activation_details=True,
                )
                return (language, profile_name, rows, None)
            except SonarQubeError as exc:
                return (language, profile_name, [], exc)

        for language, profile_name, rows, exc in bounded_parallel_map(targets, load, client_read_workers(self.client, len(targets))):
            if exc:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="api_error",
                        message=f"Active rule lookup failed for {profile_name}: {exc}",
                        suggestion="Check SonarQube permissions and API availability.",
                    )
                )
                continue
            for row in rows:
                result[(language, profile_name, row.rule_key)] = row
        return result

    def _backup(self, language: str, quality_profile: str, backup_dir: Path) -> ActionResult:
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            content = self.client.backup_profile(language, quality_profile)
            backup_path = backup_dir / f"{language}_{_safe_name(quality_profile)}.xml"
            backup_path.write_bytes(content)
            return ActionResult(
                status=ItemStatus.OK,
                action="backup_exported",
                message=f"Backup exported: {backup_path}",
                language=language,
                profile=quality_profile,
            )
        except (OSError, SonarQubeError) as exc:
            return ActionResult(
                status=ItemStatus.WARNING,
                action="backup_export",
                message="Profile backup export failed.",
                language=language,
                profile=quality_profile,
                api_error=str(exc),
            )


def _validate_sync_row(row: ProfileRuleRow) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    missing = [name for name, value in (("language", row.language), ("target_profile", row.target_profile), ("rule_key", row.rule_key)) if not value]
    if missing:
        issues.append(
            ValidationIssue(
                status=ItemStatus.ERROR,
                category="required_field_missing",
                message=f"Missing required fields: {', '.join(missing)}.",
                source_row=row.source_row,
                raw=row.raw,
                suggestion="Fill required columns and run precheck again.",
            )
        )
    if row.sync_action.strip().casefold() not in SYNC_ACTIONS:
        issues.append(
            ValidationIssue(
                status=ItemStatus.ERROR,
                category="invalid_sync_action",
                message=f"Invalid sync_action '{row.sync_action}'.",
                source_row=row.source_row,
                raw=row.raw,
                suggestion="Use noop, activate, update, deactivate, or leave it blank.",
            )
        )
    return issues


def _desired_action(row: ProfileRuleRow, current: ProfileRuleRow | None) -> str:
    requested = row.sync_action.strip().casefold()
    if requested == "skip" or requested == "skipped":
        return "skipped"
    if requested in {"noop", "activate", "update", "deactivate"}:
        return requested
    if not parse_bool(row.active):
        return "deactivate" if current else "skipped"
    if not current:
        return "activate"
    if _normalized_rule_config(row) != _normalized_rule_config(current):
        return "update"
    return "noop"


def _planned_action(row: ProfileRuleRow, action: str, current: ProfileRuleRow | None) -> ActionResult:
    status = ItemStatus.OK
    message = "Rule change planned."
    if action == "noop":
        status = ItemStatus.SKIPPED
        message = "No change needed."
    elif action == "skipped":
        status = ItemStatus.SKIPPED
        message = "Rule skipped by table input."
    elif action == "deactivate" and not current:
        status = ItemStatus.SKIPPED
        message = "Rule is not active on the server."
        action = "skipped"
    return ActionResult(
        status=status,
        action=action,
        message=message,
        language=row.language,
        profile=row.target_profile,
        rule_key=row.rule_key,
        source_row=row.source_row,
    )


def _applied(planned: ActionResult) -> ActionResult:
    return ActionResult(
        status=ItemStatus.OK,
        action=planned.action,
        message=f"{planned.action} applied.",
        language=planned.language,
        profile=planned.profile,
        rule_key=planned.rule_key,
        source_row=planned.source_row,
    )


def _row_for_action(rows: list[ProfileRuleRow], action: ActionResult) -> ProfileRuleRow | None:
    for row in rows:
        if row.language == action.language and row.target_profile == action.profile and row.rule_key == action.rule_key:
            return row
    return None


def _api_error(action: str, exc: SonarQubeError, language: str, profile: str, rule_key: str, source_row: int | None) -> ActionResult:
    suggestion = "Check SonarQube permissions and input data."
    if exc.status_code in {401, 403}:
        suggestion = "Use a token with Administer Quality Profiles permission."
    return ActionResult(
        status=ItemStatus.ERROR,
        action=action,
        message=str(exc),
        language=language,
        profile=profile,
        rule_key=rule_key,
        source_row=source_row,
        api_error=str(exc),
        suggestion=suggestion,
    )


def _activation_params(activation: dict) -> str:
    params = activation.get("params", [])
    if isinstance(params, list):
        parts = []
        for item in params:
            if isinstance(item, dict) and item.get("key"):
                parts.append(f"{item.get('key')}={item.get('value', '')}")
        return ";".join(parts)
    return str(params or "")


def _activation_from_rule(rule: dict, profile_key: str) -> dict:
    actives = rule.get("actives", [])
    if isinstance(actives, list):
        for active in actives:
            if not isinstance(active, dict):
                continue
            active_profile_key = str(active.get("qProfile") or active.get("qProfileKey") or active.get("profileKey") or "")
            if not active_profile_key or active_profile_key == profile_key:
                return dict(active)
    active = rule.get("active")
    if isinstance(active, dict):
        return dict(active)
    return {}


def _notify(progress: ProgressCallback | None, stage: str, value: float | None) -> None:
    if progress:
        progress(stage, value)


def _normalized_rule_config(row: ProfileRuleRow) -> tuple[str, str, str]:
    prioritized = "true" if parse_bool(row.prioritizedRule) else "false"
    return (row.severity.upper(), row.params.strip(), prioritized)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_") or "profile"


def _export_name(language: str, profile: str) -> str:
    return f"{language}_{_safe_name(profile)}_rules"
