from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


CONFIRM_OVERWRITE_PHRASE = "OVERWRITE OPENMETADATA CURATED METADATA"


class MetadataAction(StrEnum):
    WRITE = "write"
    SKIP_NOOP = "skip_noop"
    SKIP_CONFLICT = "skip_conflict"
    SKIP_NO_SOURCE = "skip_no_source"
    SKIP_NOT_DEFAULT = "skip_not_default"
    SKIP_NOT_BLANK = "skip_not_blank"
    SKIP_UNMATCHED = "skip_unmatched"


class DescriptionMode(StrEnum):
    FILL_EMPTY = "fill-empty"
    APPEND_WITH_MARKER = "append-with-marker"
    OVERWRITE = "overwrite"


class DisplayNameMode(StrEnum):
    FILL_DEFAULT = "fill-default"
    OVERWRITE = "overwrite"


class ColumnSource(BaseModel):
    name: str
    label: str | None = None
    description: str | None = None
    help: str | None = None
    content: str | None = None
    source_ref: str | None = None


class TableIndexFieldSource(BaseModel):
    sequence: int | None = None
    name: str
    order: str | None = None
    abbreviated: str | None = None


class TableIndexSource(BaseModel):
    name: str
    primary: bool | None = None
    unique: bool | None = None
    word: bool | None = None
    fields: list[TableIndexFieldSource] = Field(default_factory=list)


class TableSource(BaseModel):
    table_code: str
    warehouse_table_name: str
    label: str | None = None
    description: str | None = None
    indexes: list[TableIndexSource] = Field(default_factory=list)
    columns: dict[str, ColumnSource] = Field(default_factory=dict)
    source_ref: str | None = None


class LoaderOptions(BaseModel):
    om_url: str
    data_product_fqn: str
    service_name: str = "QAT Data Warehouse"
    database_name: str = "dw"
    schema_name: str = "core"
    source_prefix: str = "csd_"
    input_path: Path
    plan_output: Path | None = None
    backup_output: Path | None = None
    result_output: Path | None = None
    table_list_config: Path | None = None
    require_data_product_membership: bool = True
    limit: int | None = None
    include_table: tuple[str, ...] = ()
    description_mode: DescriptionMode = DescriptionMode.FILL_EMPTY
    display_name_mode: DisplayNameMode = DisplayNameMode.FILL_DEFAULT
    allow_overwrite_display_name: bool = False
    allow_overwrite_description: bool = False
    confirm_overwrite: str | None = None
    yes: bool = False
    continue_on_error: bool = False


class PlanRow(BaseModel):
    entity_type: str
    table_fqn: str
    table_name: str
    column_name: str | None = None
    current_display_name: str | None = None
    proposed_display_name: str | None = None
    display_name_action: MetadataAction = MetadataAction.SKIP_NO_SOURCE
    current_description_state: str = "blank"
    proposed_description: str | None = None
    description_action: MetadataAction = MetadataAction.SKIP_NO_SOURCE
    reason: str = ""
    source_ref: str | None = None

    @property
    def will_write(self) -> bool:
        return (
            self.display_name_action == MetadataAction.WRITE
            or self.description_action == MetadataAction.WRITE
        )


class Plan(BaseModel):
    data_product_fqn: str
    rows: list[PlanRow]
    unmatched_dictionary_tables: list[str] = Field(default_factory=list)
    unmatched_dictionary_columns: list[str] = Field(default_factory=list)
    tables_without_dictionary: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, int]:
        writes = [row for row in self.rows if row.will_write]
        return {
            "plan_rows": len(self.rows),
            "tables_updated": len({row.table_fqn for row in writes}),
            "metadata_writes": sum(
                int(row.display_name_action == MetadataAction.WRITE)
                + int(row.description_action == MetadataAction.WRITE)
                for row in self.rows
            ),
            "no_ops": sum(
                int(row.display_name_action == MetadataAction.SKIP_NOOP)
                + int(row.description_action == MetadataAction.SKIP_NOOP)
                for row in self.rows
            ),
            "conflicts_skipped": sum(
                int(row.display_name_action == MetadataAction.SKIP_CONFLICT)
                + int(row.description_action == MetadataAction.SKIP_CONFLICT)
                for row in self.rows
            ),
            "unmatched_dictionary_tables": len(self.unmatched_dictionary_tables),
            "unmatched_dictionary_columns": len(self.unmatched_dictionary_columns),
            "tables_without_dictionary": len(self.tables_without_dictionary),
        }


class ApplyResult(BaseModel):
    table_fqn: str
    success: bool
    patched: bool = False
    error: str | None = None
    response: dict[str, Any] | None = None
