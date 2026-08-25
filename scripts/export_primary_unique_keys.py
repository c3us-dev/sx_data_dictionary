#!/usr/bin/env python
"""Export primary unique keys and trans* columns from the rich annotation DB."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data/sqlite/annotations_rich_20260526_094055.db"
TRANS_PATTERN = "trans%"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export table primary+unique key columns and trans* column availability "
            "from an existing rich SX annotation SQLite DB."
        )
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Rich annotation SQLite DB. Defaults to {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Optional JSON output path. Defaults to stdout.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation. Defaults to 2.",
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help=(
            "Emit only table keys with pk_columns and transaction_columns lists. "
            "Omits metadata, index details, and helper maps."
        ),
    )
    return parser.parse_args()


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Input DB does not exist: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def clean_identifier(value: str) -> str:
    return value.strip().strip('"').strip("'").strip("[]").strip("`").strip().lower()


def normalize_field_name(field_name: str, table_name: str) -> str:
    field = clean_identifier(field_name)
    table = clean_identifier(table_name)
    table_prefix = f"{table}_"
    if field.startswith(table_prefix):
        return field[len(table_prefix) :]
    return field


def load_table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        row["table_name"]
        for row in conn.execute(
            """
            SELECT table_name
            FROM tables
            ORDER BY lower(table_name)
            """
        )
    ]


def load_primary_unique_indexes(
    conn: sqlite3.Connection,
) -> dict[str, list[dict[str, object]]]:
    indexes_by_table: dict[str, dict[str, list[str]]] = {}
    for row in conn.execute(
        """
        SELECT table_name, index_name, field_name
        FROM index_details
        WHERE is_primary = 1
          AND is_unique = 1
        ORDER BY lower(table_name), lower(index_name), field_sequence
        """
    ):
        table_indexes = indexes_by_table.setdefault(row["table_name"], {})
        table_indexes.setdefault(row["index_name"], []).append(row["field_name"])

    return {
        table_name: [
            {"index_name": index_name, "columns": columns}
            for index_name, columns in table_indexes.items()
        ]
        for table_name, table_indexes in indexes_by_table.items()
    }


def load_trans_columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    trans_columns_by_table: dict[str, list[str]] = {}
    for row in conn.execute(
        """
        SELECT t.table_name, f.field_name
        FROM fields f
        JOIN tables t ON f.table_id = t.table_id
        ORDER BY lower(t.table_name), f.id
        """
    ):
        column_name = normalize_field_name(row["field_name"], row["table_name"])
        if not column_name.startswith("trans"):
            continue
        columns = trans_columns_by_table.setdefault(row["table_name"], [])
        if column_name not in columns:
            columns.append(column_name)
    return trans_columns_by_table


def build_export(db_path: Path) -> dict[str, object]:
    table_names, primary_unique_indexes, trans_columns = load_annotation_data(db_path)

    primary_unique_key_columns = {
        table_name: (
            primary_unique_indexes[table_name][0]["columns"]
            if primary_unique_indexes.get(table_name)
            else []
        )
        for table_name in table_names
    }

    tables = {}
    for table_name in table_names:
        table_trans_columns = trans_columns.get(table_name, [])
        table_primary_unique_indexes = primary_unique_indexes.get(table_name, [])
        tables[table_name] = {
            "primary_unique_key_columns": primary_unique_key_columns[table_name],
            "primary_unique_indexes": table_primary_unique_indexes,
            "has_primary_unique_key": bool(table_primary_unique_indexes),
            "trans_columns": table_trans_columns,
            "has_transdttmz": any(
                column.lower() == "transdttmz" for column in table_trans_columns
            ),
        }

    return {
        "metadata": {
            "source_db": str(db_path),
            "tables_count": len(table_names),
            "tables_with_primary_unique_key_count": sum(
                1 for columns in primary_unique_key_columns.values() if columns
            ),
            "tables_with_trans_columns_count": sum(
                1 for columns in trans_columns.values() if columns
            ),
            "trans_pattern": TRANS_PATTERN,
        },
        "primary_unique_key_columns": primary_unique_key_columns,
        "primary_unique_indexes": {
            table_name: primary_unique_indexes.get(table_name, [])
            for table_name in table_names
        },
        "trans_columns": {
            table_name: {
                "columns": trans_columns.get(table_name, []),
                "has_transdttmz": any(
                    column.lower() == "transdttmz"
                    for column in trans_columns.get(table_name, [])
                ),
            }
            for table_name in table_names
        },
        "tables": tables,
    }


def load_annotation_data(
    db_path: Path,
) -> tuple[list[str], dict[str, list[dict[str, object]]], dict[str, list[str]]]:
    with connect(db_path) as conn:
        return (
            load_table_names(conn),
            load_primary_unique_indexes(conn),
            load_trans_columns(conn),
        )


def build_simple_export(db_path: Path) -> dict[str, dict[str, list[str]]]:
    table_names, primary_unique_indexes, trans_columns = load_annotation_data(db_path)
    return {
        table_name: {
            "pk_columns": (
                primary_unique_indexes[table_name][0]["columns"]
                if primary_unique_indexes.get(table_name)
                else []
            ),
            "transaction_columns": trans_columns.get(table_name, []),
        }
        for table_name in table_names
    }


def main() -> int:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    args = parse_args()
    export = build_simple_export(args.input) if args.simple else build_export(args.input)
    rendered = json.dumps(export, indent=args.indent) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        try:
            sys.stdout.write(rendered)
        except BrokenPipeError:
            sys.stdout = open(os.devnull, "w")
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
