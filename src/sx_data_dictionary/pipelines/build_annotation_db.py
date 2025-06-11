import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from sx_data_dictionary.config import JSON_DIR, configure_logging

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
        help TEXT,
        description TEXT,
        content TEXT,
        FOREIGN KEY (module_code) REFERENCES modules(module_code),
        FOREIGN KEY (table_id) REFERENCES tables(table_id)
    )
    """
    )

    # create indexes for faster querying
    cursor.execute("CREATE INDEX idx_fields_module ON fields(module_code)")
    cursor.execute("CREATE INDEX idx_fields_table ON fields(table_id)")
    cursor.execute("CREATE INDEX idx_fields_name ON fields(field_name)")
    cursor.execute("CREATE INDEX idx_tables_name ON tables(table_name)")

    # insert data
    modules_inserted = 0
    tables_inserted = 0
    fields_inserted = 0

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

            # insert field data
            for field_name, field_data in table_data.get("fields", {}).items():
                cursor.execute(
                    "INSERT INTO fields (module_code, table_id, field_name, label, help, description, content) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        module_code,
                        table_id,
                        field_name,
                        field_data.get("label", ""),
                        field_data.get("help", ""),
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
        f.help,
        f.description,
        f.content
    FROM fields f
    JOIN modules m ON f.module_code = m.module_code
    JOIN tables t ON f.table_id = t.table_id
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
        f"Inserted {modules_inserted} modules, {tables_inserted} tables, {fields_inserted} fields"
    )

    return output_path
