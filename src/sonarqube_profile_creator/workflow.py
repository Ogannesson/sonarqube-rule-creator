from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .models import (
    ActionResult,
    ApplyResult,
    ItemStatus,
    PrecheckResult,
    ProfilePlan,
    ProfileStrategy,
    RuleRow,
    ValidationIssue,
)
from .sonarqube import SonarQubeClient, SonarQubeError


VALID_SEVERITIES = {"INFO", "MINOR", "MAJOR", "CRITICAL", "BLOCKER", ""}
TRUE_VALUES = {"true", "yes", "y", "1", "是", "是的"}
STRATEGY_ALIASES = {
    "": ProfileStrategy.EXTEND_DEFAULT,
    "extend_default": ProfileStrategy.EXTEND_DEFAULT,
    "default": ProfileStrategy.EXTEND_DEFAULT,
    "默认继承": ProfileStrategy.EXTEND_DEFAULT,
    "inherit_default": ProfileStrategy.EXTEND_DEFAULT,
    "extend_selected": ProfileStrategy.EXTEND_SELECTED,
    "extend": ProfileStrategy.EXTEND_SELECTED,
    "指定继承": ProfileStrategy.EXTEND_SELECTED,
    "copy": ProfileStrategy.COPY,
    "复制": ProfileStrategy.COPY,
    "复制 profile": ProfileStrategy.COPY,
    "independent": ProfileStrategy.INDEPENDENT,
    "standalone": ProfileStrategy.INDEPENDENT,
    "独立": ProfileStrategy.INDEPENDENT,
    "独立 profile": ProfileStrategy.INDEPENDENT,
}


class WorkflowService:
    def __init__(self, client: SonarQubeClient) -> None:
        self.client = client

    def precheck(
        self,
        rows: list[RuleRow],
        default_strategy: ProfileStrategy = ProfileStrategy.EXTEND_DEFAULT,
        selected_parent_by_language: dict[str, str] | None = None,
        copy_source_by_language: dict[str, str] | None = None,
    ) -> PrecheckResult:
        selected_parent_by_language = selected_parent_by_language or {}
        copy_source_by_language = copy_source_by_language or {}
        issues: list[ValidationIssue] = []
        normalized_rows: list[RuleRow] = []
        seen_rows: set[tuple[str, str, str]] = set()

        for row in rows:
            row_issues = self._validate_row(row)
            issues.extend(row_issues)
            if not row_issues:
                marker = (row.language.casefold(), row.target_profile.casefold(), row.rule_key.casefold())
                if marker in seen_rows:
                    issues.append(
                        ValidationIssue(
                            status=ItemStatus.WARNING,
                            category="duplicate_rule",
                            message="Duplicate rule row",
                            source_row=row.source_row,
                            raw=row.raw,
                            suggestion="The rule will be processed once.",
                        )
                    )
                seen_rows.add(marker)
                normalized_rows.append(row)

        default_profiles = self._safe_default_profiles(issues)
        languages = sorted({row.language for row in normalized_rows if row.language})
        all_profiles = self._load_profiles_by_language(languages, issues)
        existing_profiles: dict[tuple[str, str], dict] = {}
        for language, profiles in all_profiles.items():
            for profile in profiles:
                name = str(profile.get("name", ""))
                if name:
                    existing_profiles[(language, name.casefold())] = profile

        profile_plans = self._build_profile_plans(
            normalized_rows,
            default_strategy,
            default_profiles,
            selected_parent_by_language,
            copy_source_by_language,
            issues,
        )

        for plan in profile_plans:
            if plan.strategy == ProfileStrategy.EXTEND_DEFAULT:
                parent = default_profiles.get(plan.language, "")
                if not parent:
                    issues.append(
                        ValidationIssue(
                            status=ItemStatus.ERROR,
                            category="missing_parent",
                            message=f"No default profile found for {plan.language}.",
                            suggestion="Choose a parent profile or use independent mode.",
                        )
                    )
            if plan.strategy == ProfileStrategy.EXTEND_SELECTED:
                parent = plan.parent_profile
                if not parent or (plan.language, parent.casefold()) not in existing_profiles:
                    issues.append(
                        ValidationIssue(
                            status=ItemStatus.ERROR,
                            category="missing_parent",
                            message=f"Parent profile '{parent}' was not found for {plan.language}.",
                            suggestion="Choose an existing profile for this language.",
                        )
                    )
            if plan.strategy == ProfileStrategy.COPY:
                source = plan.source_profile
                if not source or (plan.language, source.casefold()) not in existing_profiles:
                    issues.append(
                        ValidationIssue(
                            status=ItemStatus.ERROR,
                            category="missing_parent",
                            message=f"Copy source profile '{source}' was not found for {plan.language}.",
                            suggestion="Choose an existing source profile for this language.",
                        )
                    )

        rule_status: dict[str, ItemStatus] = {}
        for rule_key in sorted({row.rule_key for row in normalized_rows if row.rule_key}):
            try:
                rule = self.client.search_rule(rule_key)
            except SonarQubeError as exc:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="api_error",
                        message=f"Rule lookup failed for {rule_key}: {exc}",
                        suggestion="Check the SonarQube URL, token, and API availability.",
                    )
                )
                rule_status[rule_key] = ItemStatus.ERROR
                continue
            if rule:
                rule_status[rule_key] = ItemStatus.OK
            else:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="missing_rule",
                        message=f"Rule '{rule_key}' does not exist in this SonarQube instance.",
                        suggestion="Check the rule key or installed language plugin.",
                    )
                )
                rule_status[rule_key] = ItemStatus.ERROR

        return PrecheckResult(
            rows=normalized_rows,
            profile_plans=profile_plans,
            issues=issues,
            rule_status=rule_status,
            existing_profiles=existing_profiles,
            default_profiles=default_profiles,
        )

    def apply(self, precheck: PrecheckResult, backup_dir: Path | None = None) -> ApplyResult:
        actions: list[ActionResult] = []
        if not precheck.can_apply:
            actions.append(
                ActionResult(
                    status=ItemStatus.ERROR,
                    action="apply_blocked",
                    message="Precheck has errors.",
                    suggestion="Fix precheck errors before applying changes.",
                )
            )
            return ApplyResult(precheck=precheck, actions=actions)

        profile_keys: dict[tuple[str, str], str] = {}
        for plan in precheck.profile_plans:
            existing = self.client.get_profile_by_name(plan.language, plan.target_profile)
            try:
                if existing:
                    profile_keys[plan.key] = str(existing.get("key", ""))
                    actions.append(
                        ActionResult(
                            status=ItemStatus.SKIPPED,
                            action="profile_exists",
                            message="Profile already exists.",
                            language=plan.language,
                            profile=plan.target_profile,
                        )
                    )
                elif plan.strategy == ProfileStrategy.COPY:
                    source_profile = self.client.get_profile_by_name(plan.language, plan.source_profile)
                    if not source_profile:
                        raise SonarQubeError(f"Source profile '{plan.source_profile}' was not found.")
                    created = self.client.copy_profile(str(source_profile.get("key", "")), plan.target_profile)
                    profile = created.get("profile", created)
                    profile_keys[plan.key] = str(profile.get("key", ""))
                    actions.append(
                        ActionResult(
                            status=ItemStatus.OK,
                            action="profile_copied",
                            message="Profile copied.",
                            language=plan.language,
                            profile=plan.target_profile,
                        )
                    )
                else:
                    created = self.client.create_profile(plan.language, plan.target_profile)
                    profile = created.get("profile", created)
                    profile_keys[plan.key] = str(profile.get("key", ""))
                    actions.append(
                        ActionResult(
                            status=ItemStatus.OK,
                            action="profile_created",
                            message="Profile created.",
                            language=plan.language,
                            profile=plan.target_profile,
                        )
                    )
            except SonarQubeError as exc:
                actions.append(self._api_error("profile_create", exc, plan.language, plan.target_profile))
                continue

            if plan.strategy in {ProfileStrategy.EXTEND_DEFAULT, ProfileStrategy.EXTEND_SELECTED}:
                parent = plan.parent_profile or precheck.default_profiles.get(plan.language, "")
                try:
                    self.client.change_parent(plan.language, plan.target_profile, parent)
                    actions.append(
                        ActionResult(
                            status=ItemStatus.OK,
                            action="parent_linked",
                            message="Parent profile linked.",
                            language=plan.language,
                            profile=plan.target_profile,
                        )
                    )
                except SonarQubeError as exc:
                    actions.append(self._api_error("change_parent", exc, plan.language, plan.target_profile))

            for project_key in plan.project_keys:
                try:
                    self.client.add_project(plan.language, project_key, plan.target_profile)
                    actions.append(
                        ActionResult(
                            status=ItemStatus.OK,
                            action="project_bound",
                            message="Project bound.",
                            language=plan.language,
                            profile=plan.target_profile,
                        )
                    )
                except SonarQubeError as exc:
                    actions.append(self._api_error("add_project", exc, plan.language, plan.target_profile))

            if plan.set_default:
                try:
                    self.client.set_default(plan.language, plan.target_profile)
                    actions.append(
                        ActionResult(
                            status=ItemStatus.OK,
                            action="set_default",
                            message="Default profile set.",
                            language=plan.language,
                            profile=plan.target_profile,
                        )
                    )
                except SonarQubeError as exc:
                    actions.append(self._api_error("set_default", exc, plan.language, plan.target_profile))

            if backup_dir:
                try:
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    content = self.client.backup_profile(plan.language, plan.target_profile)
                    backup_path = backup_dir / f"{plan.language}_{_safe_name(plan.target_profile)}.xml"
                    backup_path.write_bytes(content)
                    actions.append(
                        ActionResult(
                            status=ItemStatus.OK,
                            action="backup_exported",
                            message=f"Backup exported: {backup_path}",
                            language=plan.language,
                            profile=plan.target_profile,
                        )
                    )
                except (OSError, SonarQubeError) as exc:
                    actions.append(
                        ActionResult(
                            status=ItemStatus.WARNING,
                            action="backup_export",
                            message="Profile backup export failed.",
                            language=plan.language,
                            profile=plan.target_profile,
                            api_error=str(exc),
                        )
                    )

        for row in precheck.rows:
            profile_key = profile_keys.get((row.language, row.target_profile))
            if not profile_key:
                actions.append(
                    ActionResult(
                        status=ItemStatus.ERROR,
                        action="activate_rule",
                        message="Target profile key is missing.",
                        language=row.language,
                        profile=row.target_profile,
                        rule_key=row.rule_key,
                        source_row=row.source_row,
                        suggestion="Fix profile creation errors and run again.",
                    )
                )
                continue
            try:
                self.client.activate_rule(
                    profile_key,
                    row.rule_key,
                    severity=row.severity.upper(),
                    params=row.params,
                    prioritized_rule=row.prioritizedRule,
                )
                actions.append(
                    ActionResult(
                        status=ItemStatus.OK,
                        action="rule_activated",
                        message="Rule activated.",
                        language=row.language,
                        profile=row.target_profile,
                        rule_key=row.rule_key,
                        source_row=row.source_row,
                    )
                )
            except SonarQubeError as exc:
                message = str(exc)
                if "already" in message.casefold() or "active" in message.casefold():
                    actions.append(
                        ActionResult(
                            status=ItemStatus.SKIPPED,
                            action="rule_already_active",
                            message="Rule already active.",
                            language=row.language,
                            profile=row.target_profile,
                            rule_key=row.rule_key,
                            source_row=row.source_row,
                            api_error=message,
                        )
                    )
                else:
                    actions.append(
                        self._api_error(
                            "activate_rule",
                            exc,
                            row.language,
                            row.target_profile,
                            row.rule_key,
                            row.source_row,
                        )
                    )

        return ApplyResult(precheck=precheck, actions=actions)

    def _validate_row(self, row: RuleRow) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        missing = []
        if not row.language:
            missing.append("language")
        if not row.target_profile:
            missing.append("target_profile")
        if not row.rule_key:
            missing.append("rule_key")
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
        if row.severity.upper() not in VALID_SEVERITIES:
            issues.append(
                ValidationIssue(
                    status=ItemStatus.ERROR,
                    category="invalid_severity",
                    message=f"Invalid severity '{row.severity}'.",
                    source_row=row.source_row,
                    raw=row.raw,
                    suggestion="Use INFO, MINOR, MAJOR, CRITICAL, or BLOCKER.",
                )
            )
        if ":" not in row.rule_key and row.rule_key:
            issues.append(
                ValidationIssue(
                    status=ItemStatus.WARNING,
                    category="rule_key_format",
                    message=f"Rule key '{row.rule_key}' does not look like language:S1234.",
                    source_row=row.source_row,
                    raw=row.raw,
                    suggestion="Check the rule key format.",
                )
            )
        return issues

    def _safe_default_profiles(self, issues: list[ValidationIssue]) -> dict[str, str]:
        try:
            return self.client.get_default_profiles()
        except SonarQubeError as exc:
            issues.append(
                ValidationIssue(
                    status=ItemStatus.ERROR,
                    category="api_error",
                    message=f"Default profile lookup failed: {exc}",
                    suggestion="Check token permissions and SonarQube availability.",
                )
            )
            return {}

    def _load_profiles_by_language(
        self,
        languages: Iterable[str],
        issues: list[ValidationIssue],
    ) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for language in languages:
            try:
                result[language] = self.client.search_quality_profiles(language=language)
            except SonarQubeError as exc:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.ERROR,
                        category="api_error",
                        message=f"Profile lookup failed for {language}: {exc}",
                        suggestion="Check the language key and token permissions.",
                    )
                )
                result[language] = []
        return result

    def _build_profile_plans(
        self,
        rows: list[RuleRow],
        default_strategy: ProfileStrategy,
        default_profiles: dict[str, str],
        selected_parent_by_language: dict[str, str],
        copy_source_by_language: dict[str, str],
        issues: list[ValidationIssue],
    ) -> list[ProfilePlan]:
        grouped: dict[tuple[str, str], list[RuleRow]] = defaultdict(list)
        for row in rows:
            grouped[(row.language, row.target_profile)].append(row)

        plans: list[ProfilePlan] = []
        for (language, target_profile), group_rows in grouped.items():
            first = group_rows[0]
            strategy = parse_strategy(first.strategy, default_strategy)
            parent_profile = first.parent_profile
            source_profile = first.parent_profile
            if strategy == ProfileStrategy.EXTEND_DEFAULT:
                parent_profile = parent_profile or default_profiles.get(language, "")
            if strategy == ProfileStrategy.EXTEND_SELECTED:
                parent_profile = parent_profile or selected_parent_by_language.get(language, "")
            if strategy == ProfileStrategy.COPY:
                source_profile = source_profile or copy_source_by_language.get(language, "") or default_profiles.get(language, "")

            strategies = {parse_strategy(row.strategy, default_strategy) for row in group_rows}
            if len(strategies) > 1:
                issues.append(
                    ValidationIssue(
                        status=ItemStatus.WARNING,
                        category="mixed_strategy",
                        message=f"Profile '{target_profile}' has multiple strategy values. The first row is used.",
                        suggestion="Use one strategy per target profile.",
                    )
                )
            projects = tuple(sorted({row.project_key for row in group_rows if row.project_key}))
            set_default = any(parse_bool(row.set_default) for row in group_rows)
            plans.append(
                ProfilePlan(
                    language=language,
                    target_profile=target_profile,
                    strategy=strategy,
                    parent_profile=parent_profile,
                    source_profile=source_profile,
                    project_keys=projects,
                    set_default=set_default,
                )
            )
        return sorted(plans, key=lambda item: (item.language, item.target_profile))

    def _api_error(
        self,
        action: str,
        exc: SonarQubeError | Exception,
        language: str,
        profile: str,
        rule_key: str = "",
        source_row: int | None = None,
    ) -> ActionResult:
        message = str(exc)
        suggestion = "Check SonarQube permissions and input data."
        if isinstance(exc, SonarQubeError) and exc.status_code in {401, 403}:
            suggestion = "Use a token with Administer Quality Profiles permission."
        return ActionResult(
            status=ItemStatus.ERROR,
            action=action,
            message=message,
            language=language,
            profile=profile,
            rule_key=rule_key,
            source_row=source_row,
            api_error=message,
            suggestion=suggestion,
        )


def parse_strategy(value: str, default: ProfileStrategy = ProfileStrategy.EXTEND_DEFAULT) -> ProfileStrategy:
    text = (value or "").strip().casefold()
    return STRATEGY_ALIASES.get(text, default)


def parse_bool(value: str) -> bool:
    return (value or "").strip().casefold() in TRUE_VALUES


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value).strip("_") or "profile"

