from __future__ import annotations

import os
from pathlib import Path

from sx_data_dictionary.openmetadata_loader.models import (
    DescriptionMode,
    DisplayNameMode,
    LoaderOptions,
)


def make_options(
    *,
    input_path: Path,
    data_product_fqn: str,
    om_url: str | None = None,
    service_name: str = "QAT Data Warehouse",
    database_name: str = "dw",
    schema_name: str = "core",
    source_prefix: str = "csd_",
    plan_output: Path | None = None,
    backup_output: Path | None = None,
    result_output: Path | None = None,
    table_list_config: Path | None = None,
    limit: int | None = None,
    include_table: tuple[str, ...] = (),
    description_mode: DescriptionMode = DescriptionMode.FILL_EMPTY,
    display_name_mode: DisplayNameMode = DisplayNameMode.FILL_DEFAULT,
    allow_overwrite_display_name: bool = False,
    allow_overwrite_description: bool = False,
    confirm_overwrite: str | None = None,
    yes: bool = False,
    continue_on_error: bool = False,
) -> LoaderOptions:
    return LoaderOptions(
        om_url=(om_url or os.environ.get("OM_URL") or "http://localhost:8585/api").rstrip("/"),
        data_product_fqn=data_product_fqn,
        service_name=service_name,
        database_name=database_name,
        schema_name=schema_name,
        source_prefix=source_prefix,
        input_path=input_path,
        plan_output=plan_output,
        backup_output=backup_output,
        result_output=result_output,
        table_list_config=table_list_config,
        limit=limit,
        include_table=include_table,
        description_mode=description_mode,
        display_name_mode=display_name_mode,
        allow_overwrite_display_name=allow_overwrite_display_name,
        allow_overwrite_description=allow_overwrite_description,
        confirm_overwrite=confirm_overwrite,
        yes=yes,
        continue_on_error=continue_on_error,
    )


def jwt_token_from_env(jwt_token: str | None = None) -> str | None:
    return jwt_token or os.environ.get("OM_JWT_TOKEN")

