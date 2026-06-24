from __future__ import annotations

import re
from pathlib import Path

LEGACY_ENTRIES_HEADING = "Legacy Data Dictionary Entries:"


def clean_identifier(value: str | None) -> str:
    if value is None:
        return ""
    cleaned = value.strip().strip('"').strip("'").strip("[]").strip("`").strip()
    return cleaned.lower()


def normalize_table_name(table_code: str, source_prefix: str = "csd_") -> str:
    table = clean_identifier(table_code)
    if not table:
        return ""
    if table.startswith(source_prefix.lower()):
        return table
    return f"{source_prefix}{table}"


def normalize_table_code(table_code: str, source_prefix: str = "csd_") -> str:
    table = clean_identifier(table_code)
    prefix = source_prefix.lower()
    if table.startswith(prefix):
        return table[len(prefix) :]
    return table


def normalize_column_name(column_name: str, table_code: str | None = None) -> str:
    column = clean_identifier(Path(column_name).stem)
    if table_code:
        table = clean_identifier(table_code)
        prefixed = f"{table}_"
        if column.startswith(prefixed):
            return column[len(prefixed) :]
    return column


def build_table_fqn(
    service_name: str,
    database_name: str,
    schema_name: str,
    warehouse_table_name: str,
) -> str:
    return ".".join(
        [
            service_name.strip(),
            database_name.strip(),
            schema_name.strip(),
            warehouse_table_name.strip(),
        ]
    )


def clean_table_label(table_code: str, title: str | None) -> str | None:
    text = clean_text(title)
    if not text:
        return None
    code = clean_identifier(table_code).upper()
    match = re.match(rf"^{re.escape(code)}\s*[-:]\s*(.+)$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip() or None
    return text


def clean_description(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(r"^\s*Description:\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\s*Content:\s*", "", text, flags=re.IGNORECASE).strip()
    return text or None


def clean_legacy_section(value: str | None, section_label: str) -> str | None:
    text = clean_multiline_text(value)
    if not text:
        return None
    text = re.sub(
        rf"^\s*{re.escape(section_label)}:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    return text or None


def clean_multiline_text(value: str | None) -> str | None:
    if value is None:
        return None
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(value).splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    if not text:
        text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def format_legacy_entries(sections: list[tuple[str, str | None]]) -> str | None:
    body: list[str] = []
    for label, value in sections:
        cleaned = clean_legacy_section(value, label)
        if cleaned:
            body.append(f"{label}:\n{cleaned}")
    if not body:
        return None
    return f"{LEGACY_ENTRIES_HEADING}\n\n" + "\n\n".join(body)


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def description_state(current: str | None, proposed: str | None = None) -> str:
    current_clean = clean_text(current)
    proposed_clean = clean_text(proposed)
    if not current_clean:
        return "blank"
    if proposed_clean and current_clean == proposed_clean:
        return "identical"
    return "differs"


def is_blank(value: str | None) -> bool:
    return not clean_text(value)


def is_default_table_display_name(
    current: str | None,
    physical_name: str,
    source_prefix: str = "csd_",
    allow_unprefixed: bool = True,
) -> bool:
    current_clean = clean_text(current)
    if not current_clean:
        return True
    physical = clean_identifier(physical_name)
    current_norm = clean_identifier(current_clean)
    defaults = {physical, default_label_from_name(physical)}
    prefix = source_prefix.lower()
    if allow_unprefixed and physical.startswith(prefix):
        unprefixed = physical[len(prefix) :]
        defaults.add(unprefixed)
        defaults.add(default_label_from_name(unprefixed))
    return current_norm in {clean_identifier(item) for item in defaults}


def is_default_column_display_name(current: str | None, physical_name: str) -> bool:
    current_clean = clean_text(current)
    if not current_clean:
        return True
    return clean_identifier(current_clean) == clean_identifier(physical_name)


def default_label_from_name(name: str) -> str:
    return clean_identifier(name).replace("_", " ").title()
