from sonarqube_profile_creator.models import ItemStatus, ProfileStrategy, RuleRow
from sonarqube_profile_creator.workflow import WorkflowService, parse_bool, parse_strategy


class FakeClient:
    def __init__(self):
        self.profiles = {
            "java": [
                {"name": "Sonar way", "key": "java-default", "language": "java"},
                {"name": "Base", "key": "java-base", "language": "java"},
            ]
        }

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


def test_parse_strategy_aliases():
    assert parse_strategy("默认继承") == ProfileStrategy.EXTEND_DEFAULT
    assert parse_strategy("copy") == ProfileStrategy.COPY
    assert parse_strategy("", ProfileStrategy.INDEPENDENT) == ProfileStrategy.EXTEND_DEFAULT


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

