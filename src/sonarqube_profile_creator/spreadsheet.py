from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from .models import ALL_FIELDS, FieldMapping, REQUIRED_FIELDS, RuleRow, SpreadsheetData


HEADER_ALIASES: dict[str, set[str]] = {
    "language": {"language", "lang", "语言", "语言key", "语言 key", "sonar语言"},
    "target_profile": {
        "target_profile",
        "target profile",
        "quality_profile",
        "quality profile",
        "profile",
        "目标profile",
        "目标 profile",
        "目标质量配置",
        "质量配置",
    },
    "rule_key": {"rule_key", "rule key", "rule", "规则key", "规则 key", "规则", "sonar规则"},
    "parent_profile": {
        "parent_profile",
        "parent profile",
        "parent",
        "父profile",
        "父 profile",
        "继承profile",
        "继承 profile",
    },
    "strategy": {"strategy", "profile_strategy", "策略", "profile策略", "profile 策略"},
    "severity": {"severity", "严重级别", "严重性", "级别"},
    "params": {"params", "parameters", "rule_params", "规则参数", "参数"},
    "prioritizedRule": {
        "prioritizedRule",
        "prioritized_rule",
        "prioritized rule",
        "优先规则",
        "必须修复",
    },
    "project_key": {"project_key", "project key", "project", "项目key", "项目 key", "项目"},
    "set_default": {"set_default", "set default", "default", "设为默认", "默认profile"},
}


def normalize_header(value: object) -> str:
    text = str(value or "").strip()
    return " ".join(text.replace("-", "_").split()).lower()


def infer_mapping(headers: list[str]) -> FieldMapping:
    normalized = {normalize_header(header): header for header in headers}
    columns: dict[str, str] = {}
    for field in ALL_FIELDS:
        for alias in HEADER_ALIASES.get(field, set()):
            key = normalize_header(alias)
            if key in normalized:
                columns[field] = normalized[key]
                break
    return FieldMapping(columns=columns)


def read_spreadsheet(path: str | Path, preview_limit: int = 20) -> SpreadsheetData:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        headers, rows = _read_csv(file_path)
    elif suffix in {".xlsx", ".xlsm"}:
        headers, rows = _read_xlsx(file_path)
    else:
        raise ValueError("Supported file types are .csv, .xlsx, and .xlsm.")
    mapping = infer_mapping(headers)
    return SpreadsheetData(
        path=file_path,
        headers=headers,
        preview_rows=rows[:preview_limit],
        rows=rows,
        inferred_mapping=mapping,
    )


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [str(header or "").strip() for header in (reader.fieldnames or [])]
        rows = [
            {str(key or "").strip(): _clean_value(value) for key, value in row.items()}
            for row in reader
            if any(_clean_value(value) for value in row.values())
        ]
    return headers, rows


def _read_xlsx(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    sheet = workbook.active
    raw_rows = list(sheet.iter_rows(values_only=True))
    if not raw_rows:
        return [], []
    headers = [str(value or "").strip() for value in raw_rows[0]]
    rows: list[dict[str, Any]] = []
    for raw in raw_rows[1:]:
        values = [_clean_value(value) for value in raw]
        if not any(values):
            continue
        rows.append({headers[index]: values[index] if index < len(values) else "" for index in range(len(headers))})
    workbook.close()
    return headers, rows


def _clean_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def rows_from_mapping(rows: list[dict[str, Any]], mapping: FieldMapping) -> list[RuleRow]:
    mapped: list[RuleRow] = []
    for index, raw in enumerate(rows, start=2):
        payload = {}
        for field in ALL_FIELDS:
            source = mapping.source_for(field)
            payload[field] = _clean_value(raw.get(source, "")) if source else ""
        mapped.append(
            RuleRow(
                source_row=index,
                language=payload["language"],
                target_profile=payload["target_profile"],
                rule_key=payload["rule_key"],
                parent_profile=payload["parent_profile"],
                strategy=payload["strategy"],
                severity=payload["severity"],
                params=payload["params"],
                prioritizedRule=payload["prioritizedRule"],
                project_key=payload["project_key"],
                set_default=payload["set_default"],
                raw=raw,
            )
        )
    return mapped


def validate_required_mapping(mapping: FieldMapping) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not mapping.source_for(field)]


def write_example_templates(output_dir: str | Path) -> tuple[Path, Path]:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "language": "java",
            "target_profile": "PM Demo Java Profile",
            "rule_key": "java:S1144",
            "parent_profile": "",
            "strategy": "extend_default",
            "severity": "MAJOR",
            "params": "",
            "prioritizedRule": "false",
            "project_key": "",
            "set_default": "false",
        },
        {
            "language": "js",
            "target_profile": "PM Demo JS Profile",
            "rule_key": "javascript:S1128",
            "parent_profile": "",
            "strategy": "extend_default",
            "severity": "",
            "params": "",
            "prioritizedRule": "false",
            "project_key": "",
            "set_default": "false",
        },
    ]
    csv_path = path / "sonarqube_rules_template.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    xlsx_path = path / "sonarqube_rules_template.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Rules"
    sheet.append(list(rows[0].keys()))
    for row in rows:
        sheet.append(list(row.values()))
    for column_cells in sheet.columns:
        max_len = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 14), 36)
    workbook.save(xlsx_path)
    return csv_path, xlsx_path

