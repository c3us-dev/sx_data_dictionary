from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from sx_data_dictionary.openmetadata_loader.apply import (
    apply_plan,
    backup_tables,
    write_json,
)
from sx_data_dictionary.openmetadata_loader.config import jwt_token_from_env, make_options
from sx_data_dictionary.openmetadata_loader.models import (
    DescriptionMode,
    DisplayNameMode,
    LoaderOptions,
)
from sx_data_dictionary.openmetadata_loader.normalize import build_table_fqn
from sx_data_dictionary.openmetadata_loader.openmetadata import (
    OpenMetadataClient,
    extract_table_asset_fqns,
)
from sx_data_dictionary.openmetadata_loader.planner import (
    generate_plan,
    rows_by_table,
    validate_overwrite_options,
)
from sx_data_dictionary.openmetadata_loader.sources import (
    load_active_table_codes_from_config,
    load_dictionary_source,
)

app = typer.Typer(help="Load SX data dictionary metadata into OpenMetadata safely.")
console = Console()


def _common_options(
    input_path: Path,
    data_product_fqn: str,
    om_url: str | None,
    service_name: str,
    database_name: str,
    schema_name: str,
    source_prefix: str,
    plan_output: Path | None,
    backup_output: Path | None,
    result_output: Path | None,
    table_list_config: Path | None,
    limit: int | None,
    include_table: list[str],
    description_mode: DescriptionMode,
    display_name_mode: DisplayNameMode,
    allow_overwrite_display_name: bool,
    allow_overwrite_description: bool,
    confirm_overwrite: str | None,
    yes: bool,
    continue_on_error: bool,
) -> LoaderOptions:
    return make_options(
        input_path=input_path,
        data_product_fqn=data_product_fqn,
        om_url=om_url,
        service_name=service_name,
        database_name=database_name,
        schema_name=schema_name,
        source_prefix=source_prefix,
        plan_output=plan_output,
        backup_output=backup_output,
        result_output=result_output,
        table_list_config=table_list_config,
        limit=limit,
        include_table=tuple(include_table),
        description_mode=description_mode,
        display_name_mode=display_name_mode,
        allow_overwrite_display_name=allow_overwrite_display_name,
        allow_overwrite_description=allow_overwrite_description,
        confirm_overwrite=confirm_overwrite,
        yes=yes,
        continue_on_error=continue_on_error,
    )


@app.command()
def plan(
    input_path: Annotated[Path, typer.Option("--input", exists=True, readable=True)],
    data_product_fqn: Annotated[str, typer.Option("--data-product-fqn")],
    om_url: Annotated[str | None, typer.Option("--om-url", envvar="OM_URL")] = None,
    jwt_token: Annotated[str | None, typer.Option("--jwt-token", envvar="OM_JWT_TOKEN")] = None,
    service_name: Annotated[str, typer.Option("--service-name")] = "QAT Data Warehouse",
    database_name: Annotated[str, typer.Option("--database-name")] = "dw",
    schema_name: Annotated[str, typer.Option("--schema-name")] = "core",
    source_prefix: Annotated[str, typer.Option("--source-prefix")] = "csd_",
    plan_output: Annotated[Path | None, typer.Option("--plan-output")] = None,
    table_list_config: Annotated[Path | None, typer.Option("--table-list-config")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    include_table: Annotated[list[str], typer.Option("--include-table")] = [],
    description_mode: Annotated[DescriptionMode, typer.Option("--description-mode")] = DescriptionMode.FILL_EMPTY,
    display_name_mode: Annotated[DisplayNameMode, typer.Option("--display-name-mode")] = DisplayNameMode.FILL_DEFAULT,
    allow_overwrite_display_name: Annotated[bool, typer.Option("--allow-overwrite-display-name")] = False,
    allow_overwrite_description: Annotated[bool, typer.Option("--allow-overwrite-description")] = False,
    confirm_overwrite: Annotated[str | None, typer.Option("--confirm-overwrite")] = None,
) -> None:
    options = _common_options(
        input_path,
        data_product_fqn,
        om_url,
        service_name,
        database_name,
        schema_name,
        source_prefix,
        plan_output,
        None,
        None,
        table_list_config,
        limit,
        include_table,
        description_mode,
        display_name_mode,
        allow_overwrite_display_name,
        allow_overwrite_description,
        confirm_overwrite,
        False,
        False,
    )
    client = OpenMetadataClient(options.om_url, jwt_token_from_env(jwt_token))
    try:
        generated = _generate_live_plan(client, options)
    finally:
        client.close()
    if plan_output:
        write_json(plan_output, generated.model_dump(mode="json"))
    _print_summary(generated)


@app.command()
def apply(
    input_path: Annotated[Path, typer.Option("--input", exists=True, readable=True)],
    data_product_fqn: Annotated[str, typer.Option("--data-product-fqn")],
    om_url: Annotated[str | None, typer.Option("--om-url", envvar="OM_URL")] = None,
    jwt_token: Annotated[str | None, typer.Option("--jwt-token", envvar="OM_JWT_TOKEN")] = None,
    service_name: Annotated[str, typer.Option("--service-name")] = "QAT Data Warehouse",
    database_name: Annotated[str, typer.Option("--database-name")] = "dw",
    schema_name: Annotated[str, typer.Option("--schema-name")] = "core",
    source_prefix: Annotated[str, typer.Option("--source-prefix")] = "csd_",
    plan_output: Annotated[Path | None, typer.Option("--plan-output")] = None,
    backup_output: Annotated[Path, typer.Option("--backup-output")] = Path("om_metadata_backup.json"),
    result_output: Annotated[Path, typer.Option("--result-output")] = Path("om_apply_result.json"),
    table_list_config: Annotated[Path | None, typer.Option("--table-list-config")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    include_table: Annotated[list[str], typer.Option("--include-table")] = [],
    description_mode: Annotated[DescriptionMode, typer.Option("--description-mode")] = DescriptionMode.FILL_EMPTY,
    display_name_mode: Annotated[DisplayNameMode, typer.Option("--display-name-mode")] = DisplayNameMode.FILL_DEFAULT,
    allow_overwrite_display_name: Annotated[bool, typer.Option("--allow-overwrite-display-name")] = False,
    allow_overwrite_description: Annotated[bool, typer.Option("--allow-overwrite-description")] = False,
    confirm_overwrite: Annotated[str | None, typer.Option("--confirm-overwrite")] = None,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    continue_on_error: Annotated[bool, typer.Option("--continue-on-error")] = False,
) -> None:
    options = _common_options(
        input_path,
        data_product_fqn,
        om_url,
        service_name,
        database_name,
        schema_name,
        source_prefix,
        plan_output,
        backup_output,
        result_output,
        table_list_config,
        limit,
        include_table,
        description_mode,
        display_name_mode,
        allow_overwrite_display_name,
        allow_overwrite_description,
        confirm_overwrite,
        yes,
        continue_on_error,
    )
    validate_overwrite_options(options)
    client = OpenMetadataClient(options.om_url, jwt_token_from_env(jwt_token))
    try:
        generated, current_tables = _generate_live_plan_with_tables(client, options)
        _print_summary(generated)
        writable_fqns = set(rows_by_table(generated))
        backup_tables(
            backup_output,
            {fqn: current_tables[fqn] for fqn in sorted(writable_fqns)},
        )
        if plan_output:
            write_json(plan_output, generated.model_dump(mode="json"))
        if writable_fqns and not yes:
            typer.confirm(
                f"Apply metadata updates to {len(writable_fqns)} OpenMetadata tables?",
                abort=True,
            )
        results = apply_plan(
            client=client,
            plan=generated,
            current_tables=current_tables,
            continue_on_error=continue_on_error,
        )
        write_json(result_output, [result.model_dump(mode="json") for result in results])
        console.print(f"Wrote backup to {backup_output}")
        console.print(f"Wrote apply result to {result_output}")
    finally:
        client.close()


@app.command("probe-patch")
def probe_patch(
    table_fqn: Annotated[str, typer.Option("--table-fqn")],
    om_url: Annotated[str | None, typer.Option("--om-url", envvar="OM_URL")] = None,
    jwt_token: Annotated[str | None, typer.Option("--jwt-token", envvar="OM_JWT_TOKEN")] = None,
    apply_probe: Annotated[bool, typer.Option("--apply-probe")] = False,
) -> None:
    client = OpenMetadataClient((om_url or "http://localhost:8585/api").rstrip("/"), jwt_token_from_env(jwt_token))
    try:
        table = client.get_table(table_fqn)
        proposed = table.get("displayName") or table.get("name")
        object_payload = {"displayName": proposed}
        json_patch = [{"op": "replace", "path": "/displayName", "value": proposed}]
        console.print_json(
            data={
                "table_id": table.get("id"),
                "object_body_candidate": object_payload,
                "json_patch_candidate": json_patch,
            }
        )
        if apply_probe:
            response = client.patch_table(str(table["id"]), object_payload)
            console.print("Object-body PATCH succeeded.")
            console.print_json(data=response)
    finally:
        client.close()


def _generate_live_plan(client: OpenMetadataClient, options: LoaderOptions):
    generated, _ = _generate_live_plan_with_tables(client, options)
    return generated


def _generate_live_plan_with_tables(
    client: OpenMetadataClient, options: LoaderOptions
):
    sources = load_dictionary_source(options.input_path, options.source_prefix)
    if options.table_list_config:
        allowed_codes = load_active_table_codes_from_config(options.table_list_config)
        sources = {code: source for code, source in sources.items() if code in allowed_codes}
    data_product = client.get_data_product_any(options.data_product_fqn)
    asset_fqns = {
        fqn
        for fqn in extract_table_asset_fqns(data_product)
        if fqn.startswith(
            build_table_fqn(
                options.service_name,
                options.database_name,
                options.schema_name,
                "",
            ).rstrip(".")
        )
    }
    current_tables = {fqn: client.get_table(fqn) for fqn in sorted(asset_fqns)}
    return (
        generate_plan(
            options=options,
            sources=sources,
            data_product_table_fqns=asset_fqns,
            current_tables=current_tables,
        ),
        current_tables,
    )


def _print_summary(generated) -> None:
    summary = generated.summary()
    table = Table(title="OpenMetadata Dictionary Loader Plan")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    for key, value in summary.items():
        table.add_row(key, str(value))
    console.print(table)
    write_rows = [row for row in generated.rows if row.will_write]
    if write_rows:
        preview = Table(title="Write Preview")
        preview.add_column("Entity")
        preview.add_column("Table FQN")
        preview.add_column("Column")
        preview.add_column("Display")
        preview.add_column("Description")
        for row in write_rows[:20]:
            preview.add_row(
                row.entity_type,
                row.table_fqn,
                row.column_name or "",
                row.display_name_action,
                row.description_action,
            )
        console.print(preview)
    console.print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    app()
