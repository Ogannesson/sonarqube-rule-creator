from sonarqube_profile_creator.models import ItemStatus, ProfileStrategy, RuleRow
from sonarqube_profile_creator.sonarqube import SonarQubeError
from sonarqube_profile_creator.workflow import WorkflowService, parse_bool, parse_strategy


class FakeClient:
    def __init__(self):
        self.profiles = {
            "java": [
                {"name": "Sonar way", "key": "java-default", "language": "java"},
                {"name": "Base", "key": "java-base", "language": "java"},
            ]
        }
        self.calls = []

    def get_default_profiles(self):
        return {"java": "Sonar way"}

    def search_quality_profiles(self, language="", quality_profile="", defaults=False):
        if defaults:
            return [{"name": "Sonar way", "key": "java-default", "language": "java"}]
        profiles = self.profiles.get(language, [])
        if quality_profile:
            return [profile for profile in profiles if profile["name"] == quality_profile]
        return profiles

    def search_rule(self, rule_key):
        if rule_key == "java:S1144":
            return {"key": rule_key}
        return None

    def get_profile_by_name(self, language, quality_profile):
        profiles = self.search_quality_profiles(language=language, quality_profile=quality_profile)
        return profiles[0] if profiles else None

    def create_profile(self, language, name):
        self.calls.append(("create_profile", language, name))
        profile = {"name": name, "key": f"{language}-{name}", "language": language}
        self.profiles.setdefault(language, []).append(profile)
        return {"profile": profile}

    def copy_profile(self, from_key, to_name):
        self.calls.append(("copy_profile", from_key, to_name))
        profile = {"name": to_name, "key": f"java-{to_name}", "language": "java"}
        self.profiles.setdefault("java", []).append(profile)
        return {"profile": profile}

    def change_parent(self, language, quality_profile, parent_quality_profile):
        self.calls.append(("change_parent", language, quality_profile, parent_quality_profile))
        return {}

    def add_project(self, language, project_key, quality_profile):
        self.calls.append(("add_project", language, project_key, quality_profile))
        return {}

    def set_default(self, language, quality_profile):
        self.calls.append(("set_default", language, quality_profile))
        return {}

    def backup_profile(self, language, quality_profile):
        self.calls.append(("backup_profile", language, quality_profile))
        return b"<profile />"

    def activate_rule(self, profile_key, rule_key, severity="", params="", prioritized_rule=""):
        self.calls.append(("activate_rule", profile_key, rule_key, severity, params, prioritized_rule))
        return {}


class ParentFailureClient(FakeClient):
    def change_parent(self, language, quality_profile, parent_quality_profile):
        self.calls.append(("change_parent", language, quality_profile, parent_quality_profile))
        raise SonarQubeError("parent link failed")


def test_parse_strategy_aliases():
    assert parse_strategy("默认继承") == ProfileStrategy.EXTEND_DEFAULT
    assert parse_strategy("copy") == ProfileStrategy.COPY
    assert parse_strategy("", ProfileStrategy.INDEPENDENT) == ProfileStrategy.INDEPENDENT


def test_parse_bool_aliases():
    assert parse_bool("yes")
    assert parse_bool("是")
    assert not parse_bool("false")


def test_precheck_extend_default_success():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144"),
    ]

    result = WorkflowService(FakeClient()).precheck(rows)

    assert result.can_apply
    assert result.profile_plans[0].strategy == ProfileStrategy.EXTEND_DEFAULT
    assert result.profile_plans[0].parent_profile == "Sonar way"
    assert result.rule_status["java:S1144"] == ItemStatus.OK


def test_precheck_missing_rule_blocks_apply():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S9999"),
    ]

    result = WorkflowService(FakeClient()).precheck(rows)

    assert not result.can_apply
    assert any(issue.category == "missing_rule" for issue in result.issues)


def test_precheck_empty_row_strategy_uses_default_strategy():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144", strategy=""),
    ]

    result = WorkflowService(FakeClient()).precheck(rows, default_strategy=ProfileStrategy.INDEPENDENT)

    assert result.can_apply
    assert result.profile_plans[0].strategy == ProfileStrategy.INDEPENDENT


def test_precheck_copy_default_strategy_uses_selected_copy_source():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144", strategy=""),
    ]

    result = WorkflowService(FakeClient()).precheck(
        rows,
        default_strategy=ProfileStrategy.COPY,
        copy_source_by_language={"java": "Base"},
    )

    assert result.can_apply
    assert result.profile_plans[0].strategy == ProfileStrategy.COPY
    assert result.profile_plans[0].source_profile == "Base"


def test_precheck_copy_strategy_uses_source_profile_column():
    rows = [
        RuleRow(
            source_row=2,
            language="java",
            target_profile="Demo",
            rule_key="java:S1144",
            strategy="copy",
            source_profile="Base",
        ),
    ]

    result = WorkflowService(FakeClient()).precheck(rows)

    assert result.can_apply
    assert result.profile_plans[0].strategy == ProfileStrategy.COPY
    assert result.profile_plans[0].source_profile == "Base"


def test_precheck_duplicate_rule_processed_once_with_warning():
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144"),
        RuleRow(source_row=3, language="java", target_profile="Demo", rule_key="java:S1144"),
    ]

    result = WorkflowService(FakeClient()).precheck(rows)

    assert result.can_apply
    assert len(result.rows) == 1
    assert result.rows[0].source_row == 2
    assert any(issue.category == "duplicate_rule" and issue.source_row == 3 for issue in result.issues)


def test_apply_parent_failure_skips_profile_followups_and_rule_activation(tmp_path):
    client = ParentFailureClient()
    rows = [
        RuleRow(
            source_row=2,
            language="java",
            target_profile="Demo",
            rule_key="java:S1144",
            project_key="project-one",
            set_default="true",
        ),
    ]
    precheck = WorkflowService(client).precheck(rows)

    result = WorkflowService(client).apply(precheck, backup_dir=tmp_path)

    assert any(action.action == "change_parent" and action.status == ItemStatus.ERROR for action in result.actions)
    assert any(action.action == "profile_followup_skipped" and action.status == ItemStatus.SKIPPED for action in result.actions)
    assert any(action.action == "rule_activation_skipped" and action.status == ItemStatus.SKIPPED for action in result.actions)
    call_names = [call[0] for call in client.calls]
    assert "add_project" not in call_names
    assert "set_default" not in call_names
    assert "backup_profile" not in call_names
    assert "activate_rule" not in call_names


def test_apply_active_false_skips_rule_activation(tmp_path):
    client = FakeClient()
    rows = [
        RuleRow(source_row=2, language="java", target_profile="Demo", rule_key="java:S1144", active="false"),
    ]
    precheck = WorkflowService(client).precheck(rows, default_strategy=ProfileStrategy.INDEPENDENT)

    result = WorkflowService(client).apply(precheck, backup_dir=tmp_path)

    assert any(action.action == "rule_activation_skipped" and action.status == ItemStatus.SKIPPED for action in result.actions)
    assert "java:S1144" not in precheck.rule_status
    assert "activate_rule" not in [call[0] for call in client.calls]
