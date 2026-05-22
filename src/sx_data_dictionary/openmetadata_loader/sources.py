from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from sx_data_dictionary.openmetadata_loader.models import ColumnSource, TableSource
from sx_data_dictionary.openmetadata_loader.normalize import (
    clean_description,
    clean_table_label,
    clean_text,
    normalize_column_name,
    normalize_table_code,
    normalize_table_name,
)


def load_dictionary_source(path: Path, source_prefix: str = "csd_") -> dict[str, TableSource]:
    suffix = path.suffix.lower()
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return load_sqlite_source(path, source_prefix)
    if suffix == ".json":
        return load_annotations_json(path, source_prefix)
    if suffix == ".csv":
        return load_csv_source(path, source_prefix)
    raise ValueError(f"Unsupported dictionary input type: {path}")


def load_sqlite_source(path: Path, source_prefix: str = "csd_") -> dict[str, TableSource]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        tables: dict[str, TableSource] = {}
        for row in conn.execute(
            """
            SELECT table_id, table_name, table_title
            FROM tables
            ORDER BY table_id
            """
        ):
            table_code = normalize_table_code(row["table_id"], source_prefix)
            warehouse_table_name = normalize_table_name(table_code, source_prefix)
            tables[table_code] = TableSource(
                table_code=table_code,
                warehouse_table_name=warehouse_table_name,
                label=clean_table_label(table_code, row["table_title"]),
                description=None,
                source_ref=f"{path}:tables:{row['table_id']}",
            )

        for row in conn.execute(
            """
            SELECT table_id, field_name, label, description, content
            FROM fields
            ORDER BY table_id, id
            """
        ):
            table_code = normalize_table_code(row["table_id"], source_prefix)
            table = tables.get(table_code)
            if not table:
                continue
            column_name = normalize_column_name(row["field_name"], table_code)
            description = clean_description(row["description"]) or clean_description(
                row["content"]
            )
            table.columns[column_name] = ColumnSource(
                name=column_name,
                label=clean_text(row["label"]),
                description=description,
                source_ref=f"{path}:fields:{row['table_id']}.{row['field_name']}",
            )
        return tables
    finally:
        conn.close()


def load_annotations_json(path: Path, source_prefix: str = "csd_") -> dict[str, TableSource]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tables: dict[str, TableSource] = {}
    for module in data.get("modules", {}).values():
        for raw_table_name, table_data in module.get("tables", {}).items():
            table_code = normalize_table_code(raw_table_name, source_prefix)
            table = tables.setdefault(
                table_code,
                TableSource(
                    table_code=table_code,
                    warehouse_table_name=normalize_table_name(table_code, source_prefix),
                    label=clean_table_label(table_code, table_data.get("title")),
                    description=None,
                    source_ref=f"{path}:modules.{module.get('code', '')}.{raw_table_name}",
                ),
            )
            if not table.label:
                table.label = clean_table_label(table_code, table_data.get("title"))
            for raw_column_name, column_data in table_data.get("fields", {}).items():
                column_name = normalize_column_name(raw_column_name, table_code)
                table.columns[column_name] = ColumnSource(
                    name=column_name,
                    label=clean_text(column_data.get("label")),
                    description=clean_description(column_data.get("description"))
                    or clean_description(column_data.get("content")),
                    source_ref=(
                        f"{path}:modules.{module.get('code', '')}."
                        f"{raw_table_name}.{raw_column_name}"
                    ),
                )
    return tables


def load_csv_source(path: Path, source_prefix: str = "csd_") -> dict[str, TableSource]:
    tables: dict[str, TableSource] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            table_code = normalize_table_code(row.get("table_code", ""), source_prefix)
            if not table_code:
                continue
            table = tables.setdefault(
                table_code,
                TableSource(
                    table_code=table_code,
                    warehouse_table_name=normalize_table_name(table_code, source_prefix),
                    label=clean_text(row.get("table_label")),
                    description=clean_description(row.get("table_description")),
                    source_ref=f"{path}:{line_number}",
                ),
            )
            table.label = table.label or clean_text(row.get("table_label"))
            table.description = table.description or clean_description(
                row.get("table_description")
            )
            column_name = normalize_column_name(row.get("column_name", ""), table_code)
            if column_name:
                table.columns[column_name] = ColumnSource(
                    name=column_name,
                    label=clean_text(row.get("column_label")),
                    description=clean_description(row.get("column_description")),
                    source_ref=f"{path}:{line_number}",
                )
    return tables


def load_active_table_codes_from_config(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    marker = "csd_active_source_tables"
    marker_at = text.find(marker)
    if marker_at < 0:
        raise ValueError(f"{path} does not contain {marker}")
    open_at = text.find("[", marker_at)
    close_at = text.find("]", open_at)
    if open_at < 0 or close_at < 0:
        raise ValueError(f"{path} has an invalid {marker} list")
    return {
        normalize_table_code(item)
        for item in _quoted_items(text[open_at + 1 : close_at])
        if item.strip()
    }


def _quoted_items(text: str) -> Iterable[str]:
    reader = csv.reader([text.replace("\n", " ")])
    for row in reader:
        for item in row:
            cleaned = item.strip().strip('"').strip("'")
            if cleaned:
                yield cleaned

