from pathlib import Path

from sonarqube_profile_creator.models import ItemStatus, ProfileRuleRow, ProfileSyncMode
from sonarqube_profile_creator.profile_sync import ProfileExportService, ProfileSyncService
from sonarqube_profile_creator.sonarqube import SonarQubeClient


class FakeSyncClient:
    def __init__(self):
        self.calls = []
        self.profiles = {
            ("java", "Demo"): {"name": "Demo", "key": "java-demo", "language": "java"},
        }
        self.active_rules = [
            {"key": "java:S1144", "name": "Unused private methods", "severity": "MAJOR"},
            {"key": "java:S112", "name": "Generic exceptions", "severity": "MINOR"},
        ]
        self.activations = {
            "java:S1144": {"qProfile": "java-demo", "severity": "MAJOR", "params": [], "prioritizedRule": False, "inheritance": "NONE"},
            "java:S112": {"qProfile": "java-demo", "severity": "MINOR", "params": [], "prioritizedRule": False, "inheritance": "NONE"},
        }

    def get_profile_by_name(self, language, quality_profile):
        return self.profiles.get((language, quality_profile))

    def search_active_rules(self, profile_key):
        assert profile_key == "java-demo"
        return self.active_rules

    def show_rule_activation(self, rule_key, profile_key):
        self.calls.append(("show_rule_activation", rule_key, profile_key))
        return self.activations.get(rule_key, {})

    def search_rule(self, rule_key):
        return {"key": rule_key} if rule_key.startswith("java:S") else None

    def backup_profile(self, language, quality_profile):
        self.calls.append(("backup_profile", language, quality_profile))
        return b"<profile />"

    def activate_rule(self, profile_key, rule_key, severity="", params="", prioritized_rule=""):
        self.calls.append(("activate_rule", profile_key, rule_key, severity, params, prioritized_rule))
        return {}

    def deactivate_rule(self, profile_key, rule_key):
        self.calls.append(("deactivate_rule", profile_key, rule_key))
        return {}


def test_export_profile_writes_rows(tmp_path: Path):
    result = ProfileExportService(FakeSyncClient()).export_profile("java", "Demo", tmp_path)

    assert len(result.rows) == 2
    assert result.rows[0].target_profile == "Demo"
    assert result.rows[0].profile_key == "java-demo"
    assert result.csv_path and result.csv_path.exists()
    assert result.xlsx_path and result.xlsx_path.exists()


def test_export_profile_uses_search_activation_without_per_rule_detail(tmp_path: Path):
    client = FakeSyncClient()
    client.active_rules = [
        {
            "key": "java:S1144",
            "name": "Unused private methods",
            "severity": "MAJOR",
            "actives": [{"qProfile": "java-demo", "severity": "CRITICAL", "params": [{"key": "x", "value": "1"}], "prioritizedRule": True}],
        }
    ]
    progress = []

    result = ProfileExportService(client).export_profile("java", "Demo", tmp_path, progress=lambda stage, value: progress.append((stage, value)))

    assert result.rows[0].severity == "CRITICAL"
    assert result.rows[0].params == "x=1"
    assert result.rows[0].prioritizedRule == "true"
    assert not any(call[0] == "show_rule_activation" for call in client.calls)
    assert progress[0] == ("resolve", 0.05)
    assert progress[-1] == ("done", 1.0)


def test_export_profile_can_include_activation_detail_fallback(tmp_path: Path):
    client = FakeSyncClient()

    ProfileExportService(client).export_profile("java", "Demo", tmp_path, include_activation_details=True)

    assert ("show_rule_activation", "java:S1144", "java-demo") in client.calls


def test_sync_patch_plans_activate_update_deactivate_and_noop():
    rows = [
        ProfileRuleRow(2, "java", "Demo", "java-demo", "", "java:S1144", active="true", severity="MAJOR"),
        ProfileRuleRow(3, "java", "Demo", "java-demo", "", "java:S112", active="false", severity="MINOR"),
        ProfileRuleRow(4, "java", "Demo", "java-demo", "", "java:S999", active="true", severity="CRITICAL"),
        ProfileRuleRow(5, "java", "Demo", "java-demo", "", "java:S113", active="true", severity="BLOCKER", sync_action="update"),
    ]

    plan = ProfileSyncService(FakeSyncClient()).precheck(rows, ProfileSyncMode.PATCH)

    actions = {(action.rule_key, action.action): action.status for action in plan.actions}
    assert actions[("java:S1144", "noop")] == ItemStatus.SKIPPED
    assert actions[("java:S112", "deactivate")] == ItemStatus.OK
    assert actions[("java:S999", "activate")] == ItemStatus.OK
    assert actions[("java:S113", "update")] == ItemStatus.OK


def test_sync_replace_plans_missing_server_rule_deactivate():
    rows = [
        ProfileRuleRow(2, "java", "Demo", "java-demo", "", "java:S1144", active="true", severity="MAJOR"),
    ]

    plan = ProfileSyncService(FakeSyncClient()).precheck(rows, ProfileSyncMode.REPLACE)

    assert any(action.rule_key == "java:S112" and action.action == "deactivate" for action in plan.actions)


def test_sync_apply_calls_activate_and_deactivate_once_per_profile(tmp_path: Path):
    client = FakeSyncClient()
    rows = [
        ProfileRuleRow(2, "java", "Demo", "java-demo", "", "java:S112", active="false", severity="MINOR"),
        ProfileRuleRow(3, "java", "Demo", "java-demo", "", "java:S999", active="true", severity="CRITICAL"),
    ]
    service = ProfileSyncService(client)
    plan = service.precheck(rows, ProfileSyncMode.PATCH)

    result = service.apply(plan, backup_dir=tmp_path)

    assert not result.has_errors
    assert client.calls.count(("backup_profile", "java", "Demo")) == 1
    assert ("deactivate_rule", "java-demo", "java:S112") in client.calls
    assert ("activate_rule", "java-demo", "java:S999", "CRITICAL", "", "") in client.calls


def test_sync_precheck_missing_rule_blocks_apply():
    class MissingRuleClient(FakeSyncClient):
        def search_rule(self, rule_key):
            return None if rule_key == "java:S404" else {"key": rule_key}

    rows = [
        ProfileRuleRow(2, "java", "Demo", "java-demo", "", "java:S404", active="true", severity="MAJOR"),
    ]

    plan = ProfileSyncService(MissingRuleClient()).precheck(rows, ProfileSyncMode.PATCH)

    assert not plan.can_apply
    assert any(issue.category == "missing_rule" for issue in plan.issues)


def test_sync_precheck_reuses_active_rule_lookup_for_existing_rules():
    class CountingClient(FakeSyncClient):
        def search_rule(self, rule_key):
            self.calls.append(("search_rule", rule_key))
            return super().search_rule(rule_key)

    client = CountingClient()
    rows = [
        ProfileRuleRow(2, "java", "Demo", "java-demo", "", "java:S1144", active="true", severity="MAJOR"),
        ProfileRuleRow(3, "java", "Demo", "java-demo", "", "java:S999", active="true", severity="CRITICAL"),
    ]

    plan = ProfileSyncService(client).precheck(rows, ProfileSyncMode.PATCH)

    assert plan.can_apply
    assert ("search_rule", "java:S1144") not in client.calls
    assert ("search_rule", "java:S999") in client.calls


def test_sonarqube_client_active_rule_pagination_and_deactivate():
    client = SonarQubeClient("https://sonar.example", "token")
    calls = []

    def fake_get(endpoint, params=None):
        calls.append(("get", endpoint, params))
        if params["p"] == 1:
            return {"rules": [{"key": "java:S1"}], "paging": {"total": 2}}
        return {"rules": [{"key": "java:S2"}], "paging": {"total": 2}}

    def fake_post(endpoint, data=None):
        calls.append(("post", endpoint, data))
        return {}

    client.get = fake_get
    client.post = fake_post

    rules = client.search_active_rules("java-demo")
    client.deactivate_rule("java-demo", "java:S1")

    assert [rule["key"] for rule in rules] == ["java:S1", "java:S2"]
    assert calls[0][2]["f"] == "actives,name,severity,params"
    assert calls[-1] == ("post", "api/qualityprofiles/deactivate_rule", {"key": "java-demo", "rule": "java:S1"})


def test_sonarqube_client_caches_rule_and_profile_lookups():
    client = SonarQubeClient("https://sonar.example", "token")
    calls = []

    def fake_get(endpoint, params=None):
        calls.append((endpoint, dict(params or {})))
        if endpoint == "api/rules/search":
            return {"rules": [{"key": params["rule_key"]}]}
        if endpoint == "api/qualityprofiles/search":
            return {"profiles": [{"name": "Demo", "key": "java-demo", "language": "java"}]}
        return {}

    client.get = fake_get

    assert client.search_rule("java:S1") == {"key": "java:S1"}
    assert client.search_rule("java:S1") == {"key": "java:S1"}
    assert client.get_profile_by_name("java", "Demo")["key"] == "java-demo"
    assert client.get_profile_by_name("java", "Demo")["key"] == "java-demo"

    assert [call[0] for call in calls].count("api/rules/search") == 1
    assert [call[0] for call in calls].count("api/qualityprofiles/search") == 1


def test_sonarqube_client_active_rule_pages_are_cached():
    client = SonarQubeClient("https://sonar.example", "token", max_read_workers=2)
    calls = []

    def fake_get(endpoint, params=None):
        calls.append(("get", endpoint, params))
        return {
            "rules": [{"key": f"java:S{params['p']}"}],
            "paging": {"total": 2, "pageSize": 1},
        }

    client.get = fake_get

    first = client.search_active_rules("java-demo")
    second = client.search_active_rules("java-demo")

    assert [rule["key"] for rule in first] == ["java:S1", "java:S2"]
    assert second == first
    assert len(calls) == 2


def test_sonarqube_client_show_rule_activation_matches_profile_key_variants():
    client = SonarQubeClient("https://sonar.example", "token")

    def fake_show_rule(rule_key, actives=False):
        assert actives is True
        return {
            "actives": [
                {"qProfileKey": "other", "severity": "MINOR"},
                {"qProfileKey": "java-demo", "severity": "BLOCKER"},
            ]
        }

    client.show_rule = fake_show_rule

    assert client.show_rule_activation("java:S1", "java-demo")["severity"] == "BLOCKER"


def test_sonarqube_client_fast_connection_skips_capability_catalog():
    client = SonarQubeClient("https://sonar.example", "token", max_read_workers=1)
    calls = []

    def fake_get(endpoint, params=None):
        calls.append(("get", endpoint, params))
        assert endpoint == "api/system/status"
        return {"status": "UP"}

    def fake_get_text(endpoint, params=None):
        calls.append(("text", endpoint, params))
        assert endpoint == "api/server/version"
        return "10.4"

    client.get = fake_get
    client.get_text = fake_get_text

    info = client.test_connection()

    assert info.status == "UP"
    assert info.version == "10.4"
    assert info.capabilities == {}
    assert "api/webservices/list" not in [call[1] for call in calls]


def test_sonarqube_client_load_capabilities_uses_webservices_catalog():
    client = SonarQubeClient("https://sonar.example", "token")
    calls = []

    def fake_get(endpoint, params=None):
        calls.append((endpoint, dict(params or {})))
        return {
            "webServices": [
                {
                    "path": "api/qualityprofiles",
                    "actions": [{"key": "create"}, {"key": "activate_rule"}],
                }
            ]
        }

    client.get = fake_get

    capabilities = client.load_capabilities()

    assert calls == [("api/webservices/list", {"include_internals": "false"})]
    assert capabilities["create_profile"] is True
    assert capabilities["activate_rule"] is True
    assert capabilities["copy_profile"] is False
