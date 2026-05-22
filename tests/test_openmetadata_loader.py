from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest
import httpx

from sx_data_dictionary.openmetadata_loader.apply import _description_matches, apply_plan
from sx_data_dictionary.openmetadata_loader.cli import _bootstrap_output_stem
from sx_data_dictionary.openmetadata_loader.models import (
    CONFIRM_OVERWRITE_PHRASE,
    DisplayNameMode,
    LoaderOptions,
    MetadataAction,
)
from sx_data_dictionary.openmetadata_loader.openmetadata import (
    OpenMetadataClient,
    OpenMetadataError,
    extract_table_asset_fqns,
    table_has_data_product,
)
from sx_data_dictionary.openmetadata_loader.normalize import (
    build_table_fqn,
    clean_description,
    normalize_column_name,
    normalize_table_name,
)
from sx_data_dictionary.openmetadata_loader.planner import (
    build_json_patch_operations,
    build_patch_payload,
    generate_plan,
    validate_overwrite_options,
)
from sx_data_dictionary.openmetadata_loader.sources import (
    load_annotations_json,
    load_csv_source,
    load_sqlite_source,
)


def test_normalization_rules() -> None:
    assert normalize_table_name("addon") == "csd_addon"
    assert normalize_table_name("csd_addon") == "csd_addon"
    assert normalize_column_name("addon_addonamt", "addon") == "addonamt"
    assert clean_description("Description:") is None
    assert clean_description("Description: Company number") == "Company number"
    assert (
        build_table_fqn("QAT Data Warehouse", "dw", "core", "csd_addon")
        == "QAT Data Warehouse.dw.core.csd_addon"
    )


def test_sqlite_source_parsing(tmp_path: Path) -> None:
    db_path = tmp_path / "annotations.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tables (
            table_id TEXT PRIMARY KEY,
            table_name TEXT,
            module_code TEXT,
            table_title TEXT
        );
        CREATE TABLE fields (
            id INTEGER PRIMARY KEY,
            module_code TEXT,
            table_id TEXT,
            field_name TEXT,
            label TEXT,
            help TEXT,
            description TEXT,
            content TEXT
        );
        INSERT INTO tables VALUES ('addon', 'addon', 'MI', 'ADDON - Addon Data');
        INSERT INTO fields VALUES (
            1, 'MI', 'addon', 'addon_addonamt', 'Addon Amt', '',
            'Description: dollar amount', ''
        );
        """
    )
    conn.commit()
    conn.close()

    sources = load_sqlite_source(db_path)

    assert sources["addon"].label == "Addon Data"
    assert sources["addon"].warehouse_table_name == "csd_addon"
    assert sources["addon"].columns["addonamt"].label == "Addon Amt"
    assert sources["addon"].columns["addonamt"].description == "dollar amount"


def test_json_and_csv_source_parsing(tmp_path: Path) -> None:
    json_path = tmp_path / "annotations.json"
    json_path.write_text(
        json.dumps(
            {
                "modules": {
                    "MI": {
                        "code": "MI",
                        "tables": {
                            "addon": {
                                "title": "ADDON - Addon Data",
                                "fields": {
                                    "addon_addonamt": {
                                        "label": "Addon Amt",
                                        "description": "Description: dollar amount",
                                    }
                                },
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert load_annotations_json(json_path)["addon"].columns["addonamt"].description == "dollar amount"

    csv_path = tmp_path / "dictionary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "table_code",
                "table_label",
                "table_description",
                "column_name",
                "column_label",
                "column_description",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "table_code": "addon",
                "table_label": "Addon Data",
                "table_description": "",
                "column_name": "addonamt",
                "column_label": "Addon Amt",
                "column_description": "Description: dollar amount",
            }
        )
    assert load_csv_source(csv_path)["addon"].columns["addonamt"].description == "dollar amount"


def test_plan_scopes_to_data_product_and_detects_conflicts(tmp_path: Path) -> None:
    source_path = tmp_path / "dictionary.csv"
    source_path.write_text("", encoding="utf-8")
    options = LoaderOptions(
        om_url="http://localhost:8585/api",
        data_product_fqn="CSD Core.Silver",
        input_path=source_path,
    )
    sources = {
        "addon": _source_table("addon"),
        "icsw": _source_table("icsw"),
    }
    table_fqn = "QAT Data Warehouse.dw.core.csd_addon"
    plan = generate_plan(
        options=options,
        sources=sources,
        data_product_table_fqns={table_fqn},
        current_tables={
            table_fqn: {
                "id": "table-id",
                "name": "csd_addon",
                "displayName": "Manually Curated Addon",
                "description": "curated",
                "columns": [
                    {
                        "name": "addonamt",
                        "displayName": "addonamt",
                        "description": "",
                        "dataType": "NUMBER",
                        "tags": [{"tagFQN": "PII.None"}],
                    }
                ],
            }
        },
    )

    table_row = next(row for row in plan.rows if row.entity_type == "table")
    column_row = next(row for row in plan.rows if row.entity_type == "column")
    assert table_row.display_name_action == MetadataAction.SKIP_CONFLICT
    assert table_row.description_action == MetadataAction.SKIP_NO_SOURCE
    assert column_row.display_name_action == MetadataAction.WRITE
    assert column_row.description_action == MetadataAction.WRITE
    assert plan.unmatched_dictionary_tables == ["icsw"]


def test_overwrite_requires_confirmation(tmp_path: Path) -> None:
    options = LoaderOptions(
        om_url="http://localhost:8585/api",
        data_product_fqn="CSD Core.Silver",
        input_path=tmp_path / "x.csv",
        display_name_mode=DisplayNameMode.OVERWRITE,
        allow_overwrite_display_name=True,
    )
    with pytest.raises(ValueError):
        validate_overwrite_options(options)

    options.confirm_overwrite = CONFIRM_OVERWRITE_PHRASE
    validate_overwrite_options(options)


def test_patch_payload_preserves_column_fields(tmp_path: Path) -> None:
    options = LoaderOptions(
        om_url="http://localhost:8585/api",
        data_product_fqn="CSD Core.Silver",
        input_path=tmp_path / "x.csv",
    )
    table_fqn = "QAT Data Warehouse.dw.core.csd_addon"
    table = {
        "id": "table-id",
        "name": "csd_addon",
        "displayName": "csd_addon",
        "columns": [
            {
                "name": "addonamt",
                "displayName": "",
                "description": "",
                "dataType": "NUMBER",
                "ordinalPosition": 1,
                "tags": [{"tagFQN": "PII.None"}],
            }
        ],
    }
    plan = generate_plan(
        options=options,
        sources={"addon": _source_table("addon")},
        data_product_table_fqns={table_fqn},
        current_tables={table_fqn: table},
    )

    payload = build_patch_payload(table, [row for row in plan.rows if row.will_write])

    assert payload["displayName"] == "Addon Data"
    assert payload["columns"][0]["displayName"] == "Addon Amt"
    assert payload["columns"][0]["description"] == "dollar amount"
    assert payload["columns"][0]["dataType"] == "NUMBER"
    assert payload["columns"][0]["tags"] == [{"tagFQN": "PII.None"}]

    operations = build_json_patch_operations(table, [row for row in plan.rows if row.will_write])
    assert operations[0] == {
        "op": "replace",
        "path": "/displayName",
        "value": "Addon Data",
    }
    assert operations[1] == {
        "op": "replace",
        "path": "/columns/0/displayName",
        "value": "Addon Amt",
    }
    assert operations[2] == {
        "op": "replace",
        "path": "/columns/0/description",
        "value": "dollar amount",
    }


def test_apply_plan_patches_and_verifies_safe_updates(tmp_path: Path) -> None:
    options = LoaderOptions(
        om_url="http://localhost:8585/api",
        data_product_fqn="ERP - CSD.CSD Core/Silver",
        input_path=tmp_path / "x.csv",
    )
    table_fqn = "QAT Data Warehouse.dw.core.csd_addon"
    table = {
        "id": "table-id",
        "name": "csd_addon",
        "displayName": "csd_addon",
        "description": "",
        "columns": [
            {
                "name": "addonamt",
                "displayName": "",
                "description": "",
                "dataType": "NUMBER",
                "ordinalPosition": 1,
            }
        ],
    }
    plan = generate_plan(
        options=options,
        sources={"addon": _source_table("addon")},
        data_product_table_fqns={table_fqn},
        current_tables={table_fqn: table},
    )
    client = _FakeClient(table_fqn, table)

    results = apply_plan(
        client=client,
        plan=plan,
        current_tables={table_fqn: table},
    )

    assert results[0].success is True
    assert client.patches[0]["displayName"] == "Addon Data"
    assert client.patches[0]["columns"][0]["description"] == "dollar amount"


def test_description_verification_accepts_openmetadata_html_wrapping() -> None:
    assert _description_matches("<p>dollar amount</p>", "dollar amount")
    assert _description_matches(
        "<p>first line</p><p>second line</p>",
        "first line second line",
    )


def test_extract_assets_and_http_error_handling() -> None:
    assert extract_table_asset_fqns(
        {
            "assets": [
                {
                    "type": "table",
                    "fullyQualifiedName": "QAT Data Warehouse.dw.core.csd_addon",
                },
                {
                    "entity": {
                        "entityType": "table",
                        "fullyQualifiedName": "QAT Data Warehouse.dw.core.csd_icsw",
                    }
                },
            ]
        }
    ) == {
        "QAT Data Warehouse.dw.core.csd_addon",
        "QAT Data Warehouse.dw.core.csd_icsw",
    }

    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="missing"))
    client = OpenMetadataClient("http://openmetadata/api")
    client.client = httpx.Client(base_url=client.base_url, transport=transport)
    with pytest.raises(OpenMetadataError, match="HTTP 404"):
        client.get_table("QAT Data Warehouse.dw.core.missing")
    client.close()


def test_table_has_data_product_matches_id_or_fqn() -> None:
    data_product = {
        "id": "dp-id",
        "name": " CSD Core/Silver",
        "fullyQualifiedName": " CSD Core/Silver",
    }
    assert table_has_data_product(
        {"dataProducts": [{"id": "dp-id", "fullyQualifiedName": "other"}]},
        data_product,
    )
    assert table_has_data_product(
        {"dataProducts": [{"fullyQualifiedName": " CSD Core/Silver"}]},
        data_product,
    )
    assert not table_has_data_product(
        {"dataProducts": [{"fullyQualifiedName": "Other Product"}]},
        data_product,
    )


def test_data_product_lookup_falls_back_to_ui_alias() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url))
        if "%20CSD%20Core%2FSilver" in str(request.url):
            assert "fields=assets" in str(request.url)
            return httpx.Response(200, json={"fullyQualifiedName": " CSD Core/Silver"})
        return httpx.Response(404, text="missing")

    client = OpenMetadataClient("http://openmetadata/api")
    client.client = httpx.Client(base_url=client.base_url, transport=httpx.MockTransport(handler))
    data_product = client.get_data_product_any("ERP - CSD.CSD Core/Silver")
    client.close()

    assert data_product["fullyQualifiedName"] == " CSD Core/Silver"
    assert len(seen_paths) > 1


def test_bootstrap_output_stem_uses_normalized_table_codes() -> None:
    assert _bootstrap_output_stem(["addon"], "csd_") == "om_loader_bootstrap_addon"
    assert _bootstrap_output_stem(["csd_addon"], "csd_") == "om_loader_bootstrap_addon"
    assert _bootstrap_output_stem(["addon", "icsw"], "csd_") == "om_loader_bootstrap_2_tables"


def _source_table(table_code: str):
    from sx_data_dictionary.openmetadata_loader.models import ColumnSource, TableSource

    return TableSource(
        table_code=table_code,
        warehouse_table_name=f"csd_{table_code}",
        label="Addon Data" if table_code == "addon" else "Warehouse Product Master",
        columns={
            "addonamt": ColumnSource(
                name="addonamt",
                label="Addon Amt",
                description="dollar amount",
            )
        }
        if table_code == "addon"
        else {},
    )


class _FakeClient:
    def __init__(self, table_fqn: str, table: dict) -> None:
        self.table_fqn = table_fqn
        self.table = json.loads(json.dumps(table))
        self.patches: list[dict] = []

    def patch_table_json_patch(self, table_id: str, operations: list[dict]) -> dict:
        assert table_id == self.table["id"]
        payload = {}
        for operation in operations:
            assert operation["op"] in {"add", "replace"}
            path = operation["path"].strip("/").split("/")
            if path[0] == "columns":
                self.table["columns"][int(path[1])][path[2]] = operation["value"]
            else:
                payload[path[0]] = operation["value"]
        self.patches.append(payload)
        self.table.update({key: value for key, value in payload.items() if key != "columns"})
        if any(operation["path"].startswith("/columns/") for operation in operations):
            payload["columns"] = self.table["columns"]
        return self.table

    def get_table(self, table_fqn: str) -> dict:
        assert table_fqn == self.table_fqn
        return self.table
