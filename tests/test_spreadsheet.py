from pathlib import Path

from sonarqube_profile_creator.models import REQUIRED_FIELDS
from sonarqube_profile_creator.spreadsheet import infer_mapping, read_spreadsheet, rows_from_mapping, validate_required_mapping


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

