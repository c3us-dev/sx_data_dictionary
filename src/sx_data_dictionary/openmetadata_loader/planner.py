from __future__ import annotations

from copy import deepcopy
from typing import Any

from sx_data_dictionary.openmetadata_loader.models import (
    CONFIRM_OVERWRITE_PHRASE,
    DescriptionMode,
    DisplayNameMode,
    LoaderOptions,
    MetadataAction,
    Plan,
    PlanRow,
    TableSource,
)
from sx_data_dictionary.openmetadata_loader.normalize import (
    build_table_fqn,
    clean_multiline_text,
    clean_text,
    description_state,
    is_blank,
    is_default_column_display_name,
    is_default_table_display_name,
    normalize_table_code,
)

LEGACY_DESCRIPTION_START = "<!-- legacy-data-dictionary-import:start -->"
LEGACY_DESCRIPTION_END = "<!-- legacy-data-dictionary-import:end -->"


def validate_overwrite_options(options: LoaderOptions) -> None:
    wants_display_overwrite = (
        options.display_name_mode == DisplayNameMode.OVERWRITE
        or options.allow_overwrite_display_name
    )
    wants_description_overwrite = (
        options.description_mode == DescriptionMode.OVERWRITE
        or options.allow_overwrite_description
    )
    if wants_display_overwrite and not options.allow_overwrite_display_name:
        raise ValueError("--display-name-mode overwrite requires --allow-overwrite-display-name")
    if wants_description_overwrite and not options.allow_overwrite_description:
        raise ValueError("--description-mode overwrite requires --allow-overwrite-description")
    if (wants_display_overwrite or wants_description_overwrite) and (
        options.confirm_overwrite != CONFIRM_OVERWRITE_PHRASE
    ):
        raise ValueError(
            f'Overwrite requires --confirm-overwrite "{CONFIRM_OVERWRITE_PHRASE}"'
        )


def generate_plan(
    *,
    options: LoaderOptions,
    sources: dict[str, TableSource],
    data_product_table_fqns: set[str],
    current_tables: dict[str, dict[str, Any]],
) -> Plan:
    validate_overwrite_options(options)
    include_tables = {normalize_table_code(t, options.source_prefix) for t in options.include_table}
    source_items = sorted(sources.items())
    if include_tables:
        source_items = [(code, src) for code, src in source_items if code in include_tables]
    if options.limit:
        source_items = source_items[: options.limit]

    rows: list[PlanRow] = []
    matched_fqns: set[str] = set()
    unmatched_tables: list[str] = []
    unmatched_columns: list[str] = []

    for table_code, table_source in source_items:
        table_fqn = build_table_fqn(
            options.service_name,
            options.database_name,
            options.schema_name,
            table_source.warehouse_table_name,
        )
        if table_fqn not in data_product_table_fqns:
            unmatched_tables.append(table_code)
            continue
        current_table = current_tables.get(table_fqn)
        if not current_table:
            unmatched_tables.append(table_code)
            continue
        matched_fqns.add(table_fqn)
        rows.append(_plan_table_row(options, table_fqn, table_source, current_table))
        current_columns = {
            str(column.get("name", "")).lower(): column
            for column in current_table.get("columns", []) or []
        }
        for column_name, column_source in sorted(table_source.columns.items()):
            current_column = current_columns.get(column_name.lower())
            if not current_column:
                unmatched_columns.append(f"{table_code}.{column_name}")
                continue
            rows.append(
                _plan_column_row(
                    options,
                    table_fqn,
                    table_source.warehouse_table_name,
                    column_source.name,
                    column_source.label,
                    column_source.description,
                    column_source.source_ref,
                    current_column,
                )
            )

    tables_without_dictionary = sorted(data_product_table_fqns - matched_fqns)
    return Plan(
        data_product_fqn=options.data_product_fqn,
        rows=rows,
        unmatched_dictionary_tables=sorted(unmatched_tables),
        unmatched_dictionary_columns=sorted(unmatched_columns),
        tables_without_dictionary=tables_without_dictionary,
    )


def build_patch_payload(table: dict[str, Any], plan_rows: list[PlanRow]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    table_rows = [row for row in plan_rows if row.entity_type == "table" and row.will_write]
    for row in table_rows:
        if row.display_name_action == MetadataAction.WRITE:
            payload["displayName"] = row.proposed_display_name
        if row.description_action == MetadataAction.WRITE:
            payload["description"] = row.proposed_description

    column_rows = [
        row
        for row in plan_rows
        if row.entity_type == "column" and row.column_name and row.will_write
    ]
    if column_rows:
        columns = deepcopy(table.get("columns", []) or [])
        rows_by_column = {row.column_name.lower(): row for row in column_rows}
        for column in columns:
            row = rows_by_column.get(str(column.get("name", "")).lower())
            if not row:
                continue
            if row.display_name_action == MetadataAction.WRITE:
                column["displayName"] = row.proposed_display_name
            if row.description_action == MetadataAction.WRITE:
                column["description"] = row.proposed_description
        payload["columns"] = columns
    return payload


def build_json_patch_operations(
    table: dict[str, Any], plan_rows: list[PlanRow]
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for row in plan_rows:
        if row.entity_type == "table":
            if row.display_name_action == MetadataAction.WRITE:
                field = "displayName"
                op = "replace" if field in table else "add"
                operations.append(
                    {"op": op, "path": f"/{field}", "value": row.proposed_display_name}
                )
            if row.description_action == MetadataAction.WRITE:
                field = "description"
                op = "replace" if field in table else "add"
                operations.append(
                    {"op": op, "path": f"/{field}", "value": row.proposed_description}
                )
            continue

        if row.entity_type != "column" or not row.column_name:
            continue
        column_index, column = _find_column_index(table, row.column_name)
        if column_index is None:
            continue
        if row.display_name_action == MetadataAction.WRITE:
            field = "displayName"
            op = "replace" if field in table else "add"
            operations.append(
                {
                    "op": op if field in column else "add",
                    "path": f"/columns/{column_index}/{field}",
                    "value": row.proposed_display_name,
                }
            )
        if row.description_action == MetadataAction.WRITE:
            field = "description"
            operations.append(
                {
                    "op": "replace" if field in column else "add",
                    "path": f"/columns/{column_index}/{field}",
                    "value": row.proposed_description,
                }
            )
    return operations


def _find_column_index(table: dict[str, Any], column_name: str) -> tuple[int | None, dict[str, Any]]:
    for index, column in enumerate(table.get("columns", []) or []):
        if str(column.get("name", "")).lower() == column_name.lower():
            return index, column
    return None, {}


def rows_by_table(plan: Plan) -> dict[str, list[PlanRow]]:
    grouped: dict[str, list[PlanRow]] = {}
    for row in plan.rows:
        if row.will_write:
            grouped.setdefault(row.table_fqn, []).append(row)
    return grouped


def _plan_table_row(
    options: LoaderOptions,
    table_fqn: str,
    table_source: TableSource,
    current_table: dict[str, Any],
) -> PlanRow:
    physical_name = str(current_table.get("name") or table_source.warehouse_table_name)
    proposed_description = _format_description(
        current_table.get("description"), table_source.description, options.description_mode
    )
    display_action, display_reason = _display_name_action(
        current=current_table.get("displayName"),
        proposed=table_source.label,
        is_default=is_default_table_display_name(
            current_table.get("displayName"), physical_name, options.source_prefix
        ),
        mode=options.display_name_mode,
        overwrite_allowed=options.allow_overwrite_display_name,
    )
    description_action, description_reason = _description_action(
        current=current_table.get("description"),
        proposed=table_source.description,
        mode=options.description_mode,
        overwrite_allowed=options.allow_overwrite_description,
    )
    return PlanRow(
        entity_type="table",
        table_fqn=table_fqn,
        table_name=physical_name,
        current_display_name=current_table.get("displayName"),
        proposed_display_name=table_source.label,
        display_name_action=display_action,
        current_description_state=description_state(
            current_table.get("description"), proposed_description
        ),
        proposed_description=proposed_description,
        description_action=description_action,
        reason="; ".join(part for part in [display_reason, description_reason] if part),
        source_ref=table_source.source_ref,
    )


def _plan_column_row(
    options: LoaderOptions,
    table_fqn: str,
    table_name: str,
    column_name: str,
    proposed_label: str | None,
    proposed_description: str | None,
    source_ref: str | None,
    current_column: dict[str, Any],
) -> PlanRow:
    physical_name = str(current_column.get("name") or column_name)
    display_action, display_reason = _display_name_action(
        current=current_column.get("displayName"),
        proposed=proposed_label,
        is_default=is_default_column_display_name(current_column.get("displayName"), physical_name),
        mode=options.display_name_mode,
        overwrite_allowed=options.allow_overwrite_display_name,
    )
    proposed_description = _format_description(
        current_column.get("description"), proposed_description, options.description_mode
    )
    description_action, description_reason = _description_action(
        current=current_column.get("description"),
        proposed=proposed_description,
        mode=options.description_mode,
        overwrite_allowed=options.allow_overwrite_description,
    )
    return PlanRow(
        entity_type="column",
        table_fqn=table_fqn,
        table_name=table_name,
        column_name=physical_name,
        current_display_name=current_column.get("displayName"),
        proposed_display_name=proposed_label,
        display_name_action=display_action,
        current_description_state=description_state(
            current_column.get("description"), proposed_description
        ),
        proposed_description=proposed_description,
        description_action=description_action,
        reason="; ".join(part for part in [display_reason, description_reason] if part),
        source_ref=source_ref,
    )


def _display_name_action(
    *,
    current: str | None,
    proposed: str | None,
    is_default: bool,
    mode: DisplayNameMode,
    overwrite_allowed: bool,
) -> tuple[MetadataAction, str]:
    current_clean = clean_text(current)
    proposed_clean = clean_text(proposed)
    if not proposed_clean:
        return MetadataAction.SKIP_NO_SOURCE, "no source displayName"
    if current_clean == proposed_clean:
        return MetadataAction.SKIP_NOOP, "displayName already matches"
    if mode == DisplayNameMode.OVERWRITE:
        if overwrite_allowed:
            return MetadataAction.WRITE, "overwrite displayName"
        return MetadataAction.SKIP_CONFLICT, "displayName overwrite not allowed"
    if is_default:
        return MetadataAction.WRITE, "displayName is blank/default"
    return MetadataAction.SKIP_CONFLICT, "current displayName is curated"


def _description_action(
    *,
    current: str | None,
    proposed: str | None,
    mode: DescriptionMode,
    overwrite_allowed: bool,
) -> tuple[MetadataAction, str]:
    current_clean = clean_text(current)
    proposed_clean = clean_text(proposed)
    if not proposed_clean:
        return MetadataAction.SKIP_NO_SOURCE, "no source description"
    if current_clean == proposed_clean:
        return MetadataAction.SKIP_NOOP, "description already matches"
    if mode == DescriptionMode.APPEND_WITH_MARKER:
        if not current_clean:
            return MetadataAction.WRITE, "description is blank"
        if "<!-- legacy-data-dictionary-import:start -->" in current_clean:
            return MetadataAction.SKIP_NOOP, "legacy marker already present"
        return MetadataAction.WRITE, "append legacy description marker"
    if mode == DescriptionMode.OVERWRITE:
        if overwrite_allowed:
            return MetadataAction.WRITE, "overwrite description"
        return MetadataAction.SKIP_CONFLICT, "description overwrite not allowed"
    if is_blank(current_clean):
        return MetadataAction.WRITE, "description is blank"
    return MetadataAction.SKIP_CONFLICT, "current description is nonblank"


def _format_description(
    current: str | None, proposed: str | None, mode: DescriptionMode
) -> str | None:
    proposed_clean = clean_multiline_text(proposed)
    if not proposed_clean:
        return None
    current_clean = clean_multiline_text(current)
    if mode != DescriptionMode.APPEND_WITH_MARKER or not current_clean:
        return proposed_clean
    if LEGACY_DESCRIPTION_START in current_clean:
        return current_clean
    return (
        f"{current_clean}\n\n{LEGACY_DESCRIPTION_START}\n"
        f"{proposed_clean}\n{LEGACY_DESCRIPTION_END}"
    )
