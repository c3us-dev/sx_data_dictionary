from __future__ import annotations

import json
import re
from html import unescape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sx_data_dictionary.openmetadata_loader.models import ApplyResult, Plan
from sx_data_dictionary.openmetadata_loader.openmetadata import OpenMetadataClient
from sx_data_dictionary.openmetadata_loader.planner import (
    build_json_patch_operations,
    build_patch_payload,
    rows_by_table,
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def backup_tables(path: Path, tables: dict[str, dict[str, Any]]) -> None:
    write_json(
        path,
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables,
        },
    )


def apply_plan(
    *,
    client: OpenMetadataClient,
    plan: Plan,
    current_tables: dict[str, dict[str, Any]],
    continue_on_error: bool = False,
) -> list[ApplyResult]:
    results: list[ApplyResult] = []
    for table_fqn, table_rows in rows_by_table(plan).items():
        table = current_tables[table_fqn]
        payload = build_patch_payload(table, table_rows)
        if not payload:
            results.append(ApplyResult(table_fqn=table_fqn, success=True, patched=False))
            continue
        try:
            table_id = str(table["id"])
            response = client.patch_table_json_patch(
                table_id, build_json_patch_operations(table, table_rows)
            )
            verified = client.get_table(table_fqn)
            _verify_table(table_fqn, table_rows, verified)
            results.append(
                ApplyResult(
                    table_fqn=table_fqn,
                    success=True,
                    patched=True,
                    response=response,
                )
            )
        except Exception as exc:
            results.append(ApplyResult(table_fqn=table_fqn, success=False, error=str(exc)))
            if not continue_on_error:
                break
    return results


def _verify_table(table_fqn: str, rows: list[Any], table: dict[str, Any]) -> None:
    columns = {
        str(column.get("name", "")).lower(): column for column in table.get("columns", []) or []
    }
    for row in rows:
        if row.entity_type == "table":
            target = table
        else:
            target = columns.get(str(row.column_name or "").lower())
            if not target:
                raise RuntimeError(f"{table_fqn}.{row.column_name} was not found after patch")
        if row.display_name_action == "write" and target.get("displayName") != row.proposed_display_name:
            raise RuntimeError(f"{row.entity_type} displayName did not verify for {table_fqn}")
        if row.description_action == "write" and not _description_matches(
            target.get("description"), row.proposed_description
        ):
            raise RuntimeError(f"{row.entity_type} description did not verify for {table_fqn}")


def _description_matches(actual: str | None, expected: str | None) -> bool:
    if actual == expected:
        return True
    return _plain_text(actual) == _plain_text(expected)


def _plain_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*<p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()
