from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class OpenMetadataError(RuntimeError):
    pass


class OpenMetadataClient:
    def __init__(self, base_url: str, jwt_token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        headers = {"Accept": "application/json"}
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"
        self.client = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def get_data_product(self, fqn: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/dataProducts/name/{quote(fqn, safe='')}",
            params={"fields": "assets"},
        )

    def get_data_product_any(self, fqn: str) -> dict[str, Any]:
        candidates = []
        fqn_parts = [fqn, fqn.strip(), f" {fqn.strip()}"]
        if "." in fqn.strip():
            product_name = fqn.strip().split(".", 1)[1].strip()
            fqn_parts.extend([product_name, f" {product_name}"])
        for candidate in fqn_parts:
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                return self.get_data_product(candidate)
            except OpenMetadataError as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise OpenMetadataError("No data product FQN candidate was provided")

    def get_table(self, fqn: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/tables/name/{quote(fqn, safe='')}",
            params={"fields": "columns,tags,owners,domains,dataProducts"},
        )

    def list_tables(
        self,
        *,
        database_schema_fqn: str,
        fields: str = "columns,tags,owners,domains,dataProducts",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            params: dict[str, Any] = {
                "databaseSchema": database_schema_fqn,
                "fields": fields,
                "limit": limit,
            }
            if after:
                params["after"] = after
            page = self._request("GET", "/v1/tables", params=params)
            tables.extend(page.get("data") or [])
            after = (page.get("paging") or {}).get("after")
            if not after:
                return tables

    def patch_table(self, table_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/v1/tables/{table_id}", json=payload)

    def patch_table_json_patch(
        self, table_id: str, operations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/v1/tables/{table_id}",
            headers={"Content-Type": "application/json-patch+json"},
            content=json_dumps(operations),
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.client.request(method, path, **kwargs)
        if response.is_success:
            if not response.content:
                return {}
            return response.json()
        body = response.text[:2000]
        raise OpenMetadataError(
            f"{method} {path} failed with HTTP {response.status_code}: {body}"
        )


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value)


def extract_table_asset_fqns(data_product: dict[str, Any]) -> set[str]:
    assets = data_product.get("assets") or []
    fqns: set[str] = set()
    for asset in assets:
        entity = asset.get("entity") if isinstance(asset.get("entity"), dict) else asset
        entity_type = str(
            entity.get("type") or entity.get("entityType") or entity.get("entityTypeName") or ""
        ).lower()
        fqn = entity.get("fullyQualifiedName") or entity.get("fqn")
        href = entity.get("href")
        if not fqn and isinstance(asset.get("name"), str):
            fqn = asset["name"]
        if fqn and ("table" in entity_type or not entity_type):
            fqns.add(str(fqn))
        elif href and "/tables/" in str(href) and fqn:
            fqns.add(str(fqn))
    return fqns


def table_has_data_product(table: dict[str, Any], data_product: dict[str, Any]) -> bool:
    target_values = {
        str(value)
        for value in (
            data_product.get("id"),
            data_product.get("name"),
            data_product.get("fullyQualifiedName"),
            data_product.get("displayName"),
        )
        if value
    }
    for ref in table.get("dataProducts") or []:
        ref_values = {
            str(value)
            for value in (
                ref.get("id"),
                ref.get("name"),
                ref.get("fullyQualifiedName"),
                ref.get("displayName"),
            )
            if value
        }
        if target_values & ref_values:
            return True
    return False
