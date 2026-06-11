from pathlib import Path

from sonarqube_profile_creator.models import ProfileRuleRow, REQUIRED_FIELDS
from sonarqube_profile_creator.spreadsheet import (
    infer_mapping,
    profile_rule_rows_from_mapping,
    read_spreadsheet,
    rows_from_mapping,
    validate_required_mapping,
    write_example_templates,
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


def test_read_csv_accepts_gb18030_chinese_files(tmp_path: Path):
    csv_path = tmp_path / "rules_gbk.csv"
    csv_path.write_bytes("规则Key;严重级别\njava:S1144;MAJOR\n".encode("gb18030"))

    data = read_spreadsheet(csv_path)
    rows = rows_from_mapping(data.rows, data.inferred_mapping)

    assert data.headers == ["规则Key", "严重级别"]
    assert rows[0].rule_key == "java:S1144"
    assert rows[0].severity == "MAJOR"


def test_read_csv_preserves_duplicate_headers(tmp_path: Path):
    csv_path = tmp_path / "duplicate_headers.csv"
    csv_path.write_text("rule_key,rule_key\njava:S1144,java:S112\n", encoding="utf-8-sig")

    data = read_spreadsheet(csv_path)

    assert data.headers == ["rule_key", "rule_key_2"]
    assert data.rows[0]["rule_key"] == "java:S1144"
    assert data.rows[0]["rule_key_2"] == "java:S112"


def test_example_template_keeps_profile_settings_out_of_table(tmp_path: Path):
    csv_path, xlsx_path = write_example_templates(tmp_path)

    data = read_spreadsheet(csv_path)
    xlsx_data = read_spreadsheet(xlsx_path)

    assert data.headers == ["rule_key", "active", "severity", "params", "prioritizedRule", "note"]
    assert "target_profile" not in data.headers
    assert "language" not in data.headers
    assert xlsx_data.headers == data.headers


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


def test_profile_rule_source_row_falls_back_when_pm_edits_row_number(tmp_path: Path):
    csv_path = tmp_path / "profile_rules.csv"
    csv_path.write_text(
        "source_row,language,target_profile,profile_key,source_profile,rule_key\nrow two,java,Demo,java-demo,Base,java:S1144\n",
        encoding="utf-8-sig",
    )

    data = read_spreadsheet(csv_path)
    rows = profile_rule_rows_from_mapping(data.rows, data.inferred_mapping)

    assert rows[0].source_row == 2
