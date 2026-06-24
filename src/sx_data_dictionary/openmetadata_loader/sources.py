from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Iterable

from sx_data_dictionary.openmetadata_loader.models import (
    ColumnSource,
    TableIndexFieldSource,
    TableIndexSource,
    TableSource,
)
from sx_data_dictionary.openmetadata_loader.normalize import (
    clean_description,
    clean_identifier,
    clean_table_label,
    clean_text,
    format_legacy_entries,
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
        _load_sqlite_indexes(conn, tables, source_prefix)

        for row in conn.execute(
            """
            SELECT *
            FROM fields
            ORDER BY table_id, id
            """
        ):
            table_code = normalize_table_code(row["table_id"], source_prefix)
            table = tables.get(table_code)
            if not table:
                continue
            column_name = normalize_column_name(row["field_name"], table_code)
            description = format_legacy_entries(_column_legacy_sections(row))
            table.columns[column_name] = ColumnSource(
                name=column_name,
                label=clean_text(row["label"]),
                description=description,
                help=clean_text(_row_value(row, "help")),
                content=clean_description(_row_value(row, "content")),
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
                    description=_format_table_description(
                        _parse_json_indexes(table_data.get("indexes", []), table_code)
                    ),
                    indexes=_parse_json_indexes(table_data.get("indexes", []), table_code),
                    source_ref=f"{path}:modules.{module.get('code', '')}.{raw_table_name}",
                ),
            )
            if not table.label:
                table.label = clean_table_label(table_code, table_data.get("title"))
            for raw_column_name, column_data in table_data.get("fields", {}).items():
                column_name = normalize_column_name(raw_column_name, table_code)
                description = format_legacy_entries(
                    [
                        ("Description", column_data.get("description")),
                        ("Help", column_data.get("help")),
                        ("Content", column_data.get("content")),
                        ("Type", column_data.get("type")),
                        ("Format", column_data.get("format")),
                        ("Decimals", column_data.get("decimals")),
                        ("Initial", column_data.get("initial")),
                        ("Extent", column_data.get("extent")),
                        ("Mandatory", column_data.get("mandatory")),
                        ("Val Exp", column_data.get("val_exp")),
                        ("Val Msg", column_data.get("val_msg")),
                        ("Trigger", column_data.get("trigger")),
                        ("Indexes", _format_index_list(column_data.get("indexes"))),
                    ]
                )
                table.columns[column_name] = ColumnSource(
                    name=column_name,
                    label=clean_text(column_data.get("label")),
                    description=description,
                    help=clean_text(column_data.get("help")),
                    content=clean_description(column_data.get("content")),
                    source_ref=(
                        f"{path}:modules.{module.get('code', '')}."
                        f"{raw_table_name}.{raw_column_name}"
                    ),
                )
    return tables


def _column_legacy_sections(row: sqlite3.Row) -> list[tuple[str, str | None]]:
    return [
        ("Description", _row_value(row, "description")),
        ("Help", _row_value(row, "help")),
        ("Content", _row_value(row, "content")),
        ("Type", _row_value(row, "field_type")),
        ("Format", _row_value(row, "format")),
        ("Decimals", _row_value(row, "decimals")),
        ("Initial", _row_value(row, "initial")),
        ("Extent", _row_value(row, "extent")),
        ("Mandatory", _row_value(row, "mandatory")),
        ("Val Exp", _row_value(row, "val_exp")),
        ("Val Msg", _row_value(row, "val_msg")),
        ("Trigger", _row_value(row, "trigger")),
        ("Indexes", _format_index_list(_row_value(row, "indexes"))),
    ]


def _row_value(row: sqlite3.Row, key: str) -> str | None:
    if key not in row.keys():
        return None
    return row[key]


def _format_index_list(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip()) or None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, list):
        return ", ".join(str(item).strip() for item in parsed if str(item).strip()) or None
    return text


def _load_sqlite_indexes(
    conn: sqlite3.Connection,
    tables: dict[str, TableSource],
    source_prefix: str,
) -> None:
    if not _sqlite_table_exists(conn, "table_indexes"):
        return
    indexes_by_id: dict[int, TableIndexSource] = {}
    for row in conn.execute(
        """
        SELECT id, table_id, index_name, is_primary, is_unique, is_word
        FROM table_indexes
        ORDER BY table_id, index_name
        """
    ):
        table_code = normalize_table_code(row["table_id"], source_prefix)
        table = tables.get(table_code)
        if not table:
            continue
        index_source = TableIndexSource(
            name=clean_identifier(row["index_name"]),
            primary=_sqlite_bool(row["is_primary"]),
            unique=_sqlite_bool(row["is_unique"]),
            word=_sqlite_bool(row["is_word"]),
        )
        indexes_by_id[int(row["id"])] = index_source
        table.indexes.append(index_source)
    if _sqlite_table_exists(conn, "table_index_fields"):
        for row in conn.execute(
            """
            SELECT index_id, field_sequence, field_name, field_order, abbreviated
            FROM table_index_fields
            ORDER BY index_id, field_sequence
            """
        ):
            index_source = indexes_by_id.get(int(row["index_id"]))
            if not index_source:
                continue
            index_source.fields.append(
                TableIndexFieldSource(
                    sequence=row["field_sequence"],
                    name=clean_identifier(row["field_name"]),
                    order=clean_text(row["field_order"]),
                    abbreviated=clean_text(row["abbreviated"]),
                )
            )
    for table in tables.values():
        table.description = _format_table_description(table.indexes)


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _sqlite_bool(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _format_table_description(indexes: list[TableIndexSource]) -> str | None:
    primary_indexes = [index for index in indexes if index.primary]
    sections: list[tuple[str, str | None]] = []
    if primary_indexes:
        primary_lines = []
        for index in primary_indexes:
            columns = ", ".join(field.name for field in index.fields) or "unknown"
            primary_lines.append(f"{index.name}: {columns}")
        sections.append(("Primary Key", "\n".join(primary_lines)))
    if indexes:
        index_lines = []
        for index in indexes:
            traits = []
            if index.primary:
                traits.append("primary")
            if index.unique:
                traits.append("unique")
            if index.word:
                traits.append("word")
            columns = ", ".join(field.name for field in index.fields) or "unknown"
            suffix = f" ({', '.join(traits)})" if traits else ""
            index_lines.append(f"{index.name}{suffix}: {columns}")
        sections.append(("Indexes", "\n".join(index_lines)))
    return format_legacy_entries(sections)


def _parse_json_indexes(raw_indexes: object, table_code: str) -> list[TableIndexSource]:
    if not isinstance(raw_indexes, list):
        return []
    indexes: list[TableIndexSource] = []
    for raw_index in raw_indexes:
        if isinstance(raw_index, dict):
            fields = [
                TableIndexFieldSource(
                    sequence=field.get("sequence"),
                    name=clean_identifier(field.get("name")),
                    order=clean_text(field.get("order")),
                    abbreviated=clean_text(field.get("abbreviated")),
                )
                for field in raw_index.get("fields", [])
                if isinstance(field, dict) and field.get("name")
            ]
            indexes.append(
                TableIndexSource(
                    name=clean_identifier(raw_index.get("name")),
                    primary=raw_index.get("primary"),
                    unique=raw_index.get("unique"),
                    word=raw_index.get("word"),
                    fields=fields,
                )
            )
        elif isinstance(raw_index, (list, tuple)) and raw_index:
            name = Path(str(raw_index[0])).stem
            table_prefix = f"{table_code}_"
            if name.startswith(table_prefix):
                name = name[len(table_prefix) :]
            indexes.append(TableIndexSource(name=clean_identifier(name)))
    return indexes


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
