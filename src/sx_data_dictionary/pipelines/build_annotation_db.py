import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from loguru import logger

from sx_data_dictionary.config import HTM_DIR, JSON_DIR, configure_logging

log_file = configure_logging()


def get_logger():
    """Create and return a logger configured for this module."""
    return logger.bind(module="build_annotation_db")


def create_sqlite_from_annotations(
    annotations_path: Path,
    output_path: Optional[Path] = None,
    schema_prefix: Optional[str] = None,
) -> Path:
    """
    Builds an SQLite database for annotations specifically (up-to-date data type/table
    info should really be fetched directly from the database, not from the legacy
    data dictionary).

    Optionally can supply a schema prefix that will be prepended to all table names
    to smooth/mirror references to 'sxe.icsw' instead of 'icsw' etc.
    """

    log = get_logger()
    log.info(f"Creating SQLite database from annotations: {annotations_path}")

    # load the annotations file
    try:
        with open(annotations_path, "r", encoding="utf-8") as f:
            annotations = json.load(f)
    except Exception as e:
        log.error(f"Error loading annotations JSON: {e}")
        raise

    # default output path
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_name = f"annotations_{timestamp}.db"
        if schema_prefix:
            # Use the schema name in the DB filename
            clean_schema = schema_prefix.replace(".", "")
            db_name = f"annotations_{clean_schema}_{timestamp}.db"
        output_path = JSON_DIR.parent / "sqlite" / db_name

        # ensure directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

    # create/connect to the database
    conn = sqlite3.connect(str(output_path))
    cursor = conn.cursor()

    # create tables
    cursor.execute(
        """
    CREATE TABLE modules (
        module_code TEXT PRIMARY KEY,
        module_title TEXT
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE tables (
        table_id TEXT PRIMARY KEY,
        table_name TEXT,
        module_code TEXT,
        table_title TEXT,
        FOREIGN KEY (module_code) REFERENCES modules(module_code)
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE fields (
        id INTEGER PRIMARY KEY,
        module_code TEXT,
        table_id TEXT,
        field_name TEXT,
        label TEXT,
        field_type TEXT,
        format TEXT,
        decimals TEXT,
        initial TEXT,
        extent TEXT,
        mandatory TEXT,
        val_exp TEXT,
        val_msg TEXT,
        help TEXT,
        trigger TEXT,
        indexes TEXT,
        description TEXT,
        content TEXT,
        FOREIGN KEY (module_code) REFERENCES modules(module_code),
        FOREIGN KEY (table_id) REFERENCES tables(table_id)
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE table_indexes (
        id INTEGER PRIMARY KEY,
        module_code TEXT,
        table_id TEXT,
        index_name TEXT,
        is_primary INTEGER,
        is_unique INTEGER,
        is_word INTEGER,
        source_file TEXT,
        FOREIGN KEY (module_code) REFERENCES modules(module_code),
        FOREIGN KEY (table_id) REFERENCES tables(table_id)
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE table_index_fields (
        id INTEGER PRIMARY KEY,
        index_id INTEGER,
        field_sequence INTEGER,
        field_name TEXT,
        field_order TEXT,
        abbreviated TEXT,
        FOREIGN KEY (index_id) REFERENCES table_indexes(id)
    )
    """
    )

    # create indexes for faster querying
    cursor.execute("CREATE INDEX idx_fields_module ON fields(module_code)")
    cursor.execute("CREATE INDEX idx_fields_table ON fields(table_id)")
    cursor.execute("CREATE INDEX idx_fields_name ON fields(field_name)")
    cursor.execute("CREATE INDEX idx_tables_name ON tables(table_name)")
    cursor.execute("CREATE INDEX idx_table_indexes_table ON table_indexes(table_id)")
    cursor.execute("CREATE INDEX idx_table_index_fields_index ON table_index_fields(index_id)")

    # insert data
    modules_inserted = 0
    tables_inserted = 0
    fields_inserted = 0
    indexes_inserted = 0
    index_fields_inserted = 0

    # process schema prefix if provided
    if schema_prefix:
        if not schema_prefix.endswith("."):
            schema_prefix += "."
        log.info(f"Using schema prefix: {schema_prefix}")

    # insert module data
    for module_code, module_data in annotations["modules"].items():
        cursor.execute(
            "INSERT INTO modules (module_code, module_title) VALUES (?, ?)",
            (module_code, module_data.get("title", "")),
        )
        modules_inserted += 1

        # insert table data
        for table_name, table_data in module_data["tables"].items():
            # add schema prefix if specified
            table_id = table_name  # original table name used as ID
            display_table_name = (
                f"{schema_prefix or ''}{table_name}" if schema_prefix else table_name
            )

            cursor.execute(
                "INSERT INTO tables (table_id, table_name, module_code, table_title) VALUES (?, ?, ?, ?)",
                (
                    table_id,
                    display_table_name,
                    module_code,
                    table_data.get("title", ""),
                ),
            )
            tables_inserted += 1

            for raw_index in table_data.get("indexes", []):
                index = parse_index_entry(raw_index, table_id)
                cursor.execute(
                    """
                    INSERT INTO table_indexes (
                        module_code, table_id, index_name, is_primary, is_unique,
                        is_word, source_file
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        module_code,
                        table_id,
                        index["name"],
                        _bool_to_int(index.get("primary")),
                        _bool_to_int(index.get("unique")),
                        _bool_to_int(index.get("word")),
                        index.get("source_file", ""),
                    ),
                )
                index_id = cursor.lastrowid
                indexes_inserted += 1
                for field in index.get("fields", []):
                    cursor.execute(
                        """
                        INSERT INTO table_index_fields (
                            index_id, field_sequence, field_name, field_order,
                            abbreviated
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            index_id,
                            field.get("sequence"),
                            field.get("name", ""),
                            field.get("order", ""),
                            field.get("abbreviated", ""),
                        ),
                    )
                    index_fields_inserted += 1

            # insert field data
            for field_name, field_data in table_data.get("fields", {}).items():
                cursor.execute(
                    """
                    INSERT INTO fields (
                        module_code, table_id, field_name, label, field_type,
                        format, decimals, initial, extent, mandatory, val_exp,
                        val_msg, help, trigger, indexes, description, content
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        module_code,
                        table_id,
                        field_name,
                        field_data.get("label", ""),
                        field_data.get("type", ""),
                        field_data.get("format", ""),
                        field_data.get("decimals", ""),
                        field_data.get("initial", ""),
                        field_data.get("extent", ""),
                        field_data.get("mandatory", ""),
                        field_data.get("val_exp", ""),
                        field_data.get("val_msg", ""),
                        field_data.get("help", ""),
                        field_data.get("trigger", ""),
                        json.dumps(field_data.get("indexes", [])),
                        field_data.get("description", ""),
                        field_data.get("content", ""),
                    ),
                )
                fields_inserted += 1

    # create helper views

    # all columns with full info
    cursor.execute(
        """
    CREATE VIEW field_details AS
    SELECT 
        m.module_code,
        t.table_name,
        f.field_name,
        f.label,
        f.field_type,
        f.format,
        f.decimals,
        f.initial,
        f.extent,
        f.mandatory,
        f.val_exp,
        f.val_msg,
        f.help,
        f.trigger,
        f.indexes,
        f.description,
        f.content
    FROM fields f
    JOIN modules m ON f.module_code = m.module_code
    JOIN tables t ON f.table_id = t.table_id
    """
    )

    cursor.execute(
        """
    CREATE VIEW index_details AS
    SELECT
        m.module_code,
        t.table_name,
        i.index_name,
        i.is_primary,
        i.is_unique,
        i.is_word,
        i.source_file,
        f.field_sequence,
        f.field_name,
        f.field_order,
        f.abbreviated
    FROM table_indexes i
    JOIN modules m ON i.module_code = m.module_code
    JOIN tables t ON i.table_id = t.table_id
    LEFT JOIN table_index_fields f ON i.id = f.index_id
    """
    )

    # add metadata table
    cursor.execute(
        """
    CREATE TABLE metadata (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """
    )

    # insert metadata
    metadata = {
        "generated_at": datetime.now().isoformat(),
        "source_file": str(annotations_path),
        "schema_prefix": schema_prefix or "",
        "modules_count": modules_inserted,
        "tables_count": tables_inserted,
        "fields_count": fields_inserted,
        "indexes_count": indexes_inserted,
        "index_fields_count": index_fields_inserted,
    }

    for key, value in metadata.items():
        cursor.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?)", (key, str(value))
        )

    # commit the changes
    conn.commit()
    conn.close()

    log.info(f"SQLite database created at {output_path}")
    log.info(
        "Inserted "
        f"{modules_inserted} modules, {tables_inserted} tables, "
        f"{fields_inserted} fields, {indexes_inserted} indexes, "
        f"{index_fields_inserted} index fields"
    )

    return output_path


def parse_index_entry(raw_index: object, table_id: str) -> dict:
    if isinstance(raw_index, dict):
        return raw_index
    if isinstance(raw_index, (list, tuple)) and raw_index:
        filename = str(raw_index[0])
        path = resolve_index_path(filename, raw_index[1] if len(raw_index) > 1 else None)
        parsed = parse_index_file(path) if path else {}
        parsed["name"] = clean_index_name(filename, table_id)
        parsed["source_file"] = filename
        return parsed
    return {"name": "", "source_file": ""}


def resolve_index_path(filename: str, raw_path: object | None) -> Path | None:
    candidates = []
    if raw_path:
        candidates.append(Path(str(raw_path)))
    candidates.append(HTM_DIR / Path(filename).name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def parse_index_file(path: Path) -> dict:
    soup = BeautifulSoup(path.read_text(encoding="iso-8859-1", errors="ignore"), "html.parser")
    text = soup.get_text("\n", strip=True)
    fields = []
    rows = soup.find_all("tr")
    for row in rows:
        cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
        if len(cells) < 4 or not cells[0].strip().isdigit():
            continue
        fields.append(
            {
                "sequence": int(cells[0]),
                "name": cells[1],
                "order": cells[2],
                "abbreviated": cells[3],
            }
        )
    return {
        "name": clean_index_name(path.name, ""),
        "primary": parse_yes_no_line(text, "Primary"),
        "unique": parse_yes_no_line(text, "Unique"),
        "word": parse_yes_no_line(text, "Word"),
        "fields": fields,
    }


def parse_yes_no_line(text: str, label: str) -> bool | None:
    for line in text.splitlines():
        if not line.lower().startswith(label.lower()):
            continue
        value = line.split(":", 1)[-1].strip().lower()
        if value in {"yes", "y", "true", "1"}:
            return True
        if value in {"no", "n", "false", "0"}:
            return False
    return None


def clean_index_name(filename: str, table_id: str) -> str:
    name = Path(filename).stem.lower()
    prefix = f"{table_id.lower()}_"
    if table_id and name.startswith(prefix):
        return name[len(prefix) :]
    return name


def _bool_to_int(value: object) -> int | None:
    if value is None:
        return None
    return int(bool(value))
