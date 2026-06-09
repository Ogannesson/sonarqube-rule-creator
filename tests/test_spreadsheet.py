from pathlib import Path

from sonarqube_profile_creator.models import ProfileRuleRow, REQUIRED_FIELDS
from sonarqube_profile_creator.spreadsheet import (
    infer_mapping,
    profile_rule_rows_from_mapping,
    read_spreadsheet,
    rows_from_mapping,
    validate_required_mapping,
    write_profile_rules,
)


def test_infer_mapping_english_headers():
    mapping = infer_mapping(["language", "target profile", "rule key", "severity"])

    assert mapping.source_for("language") == "language"
    assert mapping.source_for("target_profile") == "target profile"
    assert mapping.source_for("rule_key") == "rule key"
    assert validate_required_mapping(mapping) == []


def test_infer_mapping_chinese_headers():
    mapping = infer_mapping(["语言", "目标Profile", "规则Key"])

    assert mapping.source_for("language") == "语言"
    assert mapping.source_for("target_profile") == "目标Profile"
    assert mapping.source_for("rule_key") == "规则Key"


def test_read_csv_and_map_rows(tmp_path: Path):
    csv_path = tmp_path / "rules.csv"
    csv_path.write_text(
        "语言,目标Profile,规则Key,严重级别\njava,Demo,java:S1144,MAJOR\n",
        encoding="utf-8-sig",
    )

    data = read_spreadsheet(csv_path)
    rows = rows_from_mapping(data.rows, data.inferred_mapping)

    assert data.headers == ["语言", "目标Profile", "规则Key", "严重级别"]
    assert rows[0].language == "java"
    assert rows[0].target_profile == "Demo"
    assert rows[0].rule_key == "java:S1144"
    assert rows[0].severity == "MAJOR"
    assert set(REQUIRED_FIELDS).issubset(data.inferred_mapping.columns)


def test_profile_rule_rows_roundtrip_csv_xlsx(tmp_path: Path):
    rows = [
        ProfileRuleRow(
            source_row=2,
            language="java",
            target_profile="Demo",
            profile_key="java-demo",
            source_profile="Base",
            rule_key="java:S1144",
            rule_name="Unused private methods",
            active="false",
            severity="MAJOR",
            params="",
            prioritizedRule="false",
            inheritance="NONE",
            sync_action="deactivate",
            note="PM disabled",
        )
    ]

    csv_path, xlsx_path = write_profile_rules(tmp_path, rows)
    csv_data = read_spreadsheet(csv_path)
    xlsx_data = read_spreadsheet(xlsx_path)
    csv_rows = profile_rule_rows_from_mapping(csv_data.rows, csv_data.inferred_mapping)
    xlsx_rows = profile_rule_rows_from_mapping(xlsx_data.rows, xlsx_data.inferred_mapping)

    assert csv_data.headers[:6] == ["source_row", "language", "target_profile", "profile_key", "source_profile", "rule_key"]
    assert csv_rows[0].active == "false"
    assert csv_rows[0].sync_action == "deactivate"
    assert xlsx_rows[0].rule_name == "Unused private methods"
