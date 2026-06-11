from sonarqube_profile_creator.config import AppConfig, ConfigStore


def test_config_roundtrip_target_profile_settings(tmp_path):
    store = ConfigStore(tmp_path)
    config = AppConfig(
        server_url="https://sonar.example",
        last_file="rules.xlsx",
        language="zh",
        default_strategy="copy",
        target_language="java",
        target_profile="PM Demo",
        project_key="project-one",
        set_default=True,
        last_mapping={"rule_key": "Rule Key"},
    )

    store.save(config)
    loaded = store.load()

    assert loaded.target_language == "java"
    assert loaded.target_profile == "PM Demo"
    assert loaded.project_key == "project-one"
    assert loaded.set_default is True
    assert loaded.last_mapping == {"rule_key": "Rule Key"}


def test_config_reads_string_false_as_false(tmp_path):
    store = ConfigStore(tmp_path)
    store.config_path.write_text('{"set_default": "false"}', encoding="utf-8")

    loaded = store.load()

    assert loaded.set_default is False
