#!/usr/bin/env python
"""
SX Data Dictionary Tool - Main Script

This script provides a command-line interface to the SX Data Dictionary tool,
which processes HTML dictionary files into useful formats for reference and querying.
"""

# TODO: verify beyond db generation, generated this quickly to test but need to ensure
#  it works as expected for all use cases.

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger

from sx_data_dictionary.config import JSON_DIR, configure_logging
from sx_data_dictionary.pipelines.annotation_skim import run_annotation_pipeline
from sx_data_dictionary.pipelines.build_annotation_db import (
    create_sqlite_from_annotations,
)
from sx_data_dictionary.pipelines.htm_to_json import run_dictionary_pipeline

# Configure logging
log_file = configure_logging()


def get_logger():
    """Create and return a logger configured for this module."""
    return logger.bind(module="sx_dictionary_tool")


def get_latest_file(directory: Path, pattern: str) -> Optional[Path]:
    """Find the most recently modified file matching a pattern in a directory."""
    files = list(directory.glob(pattern))
    if not files:
        return None

    # Sort by modification time (newest first)
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return files[0]


def run_dictionary_extraction(
    output_path: Optional[Path] = None, force_refresh: bool = False
) -> Path:
    """
    Run the dictionary extraction pipeline (HTM to JSON).

    Args:
        output_path: Path to save the dictionary JSON file (optional)
        force_refresh: Whether to force running the pipeline even if recent output exists

    Returns:
        Path to the dictionary JSON file
    """
    log = get_logger()

    if not force_refresh:
        # Check if recent dictionary file exists (less than 1 day old)
        recent_dict = get_latest_file(JSON_DIR, "dictionary_*.json")
        if recent_dict:
            file_age = datetime.now() - datetime.fromtimestamp(
                recent_dict.stat().st_mtime
            )
            if file_age < timedelta(days=1):
                log.info(
                    f"Using existing dictionary file: {recent_dict} (age: {file_age})"
                )
                return recent_dict

    # Run the extraction
    log.info("Running dictionary extraction pipeline")
    return run_dictionary_pipeline(output_path)


def run_annotations_extraction(
    dictionary_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    force_refresh: bool = False,
) -> Path:
    """
    Run the annotations extraction pipeline.

    Args:
        dictionary_path: Path to the dictionary JSON file (optional)
        output_path: Path to save the annotations JSON file (optional)
        force_refresh: Whether to force running the pipeline even if recent output exists

    Returns:
        Path to the annotations JSON file
    """
    log = get_logger()

    if not force_refresh:
        # Check if recent annotations file exists (less than 1 day old)
        recent_annotations = get_latest_file(JSON_DIR, "annotations_*.json")
        if recent_annotations:
            file_age = datetime.now() - datetime.fromtimestamp(
                recent_annotations.stat().st_mtime
            )
            if file_age < timedelta(days=1):
                log.info(
                    f"Using existing annotations file: {recent_annotations} (age: {file_age})"
                )
                return recent_annotations

    # Get dictionary file if not specified
    if not dictionary_path:
        dictionary_path = get_latest_file(JSON_DIR, "dictionary_*.json")
        if not dictionary_path:
            log.info("No dictionary file found, running dictionary extraction first")
            dictionary_path = run_dictionary_extraction(force_refresh=force_refresh)

    # Run the extraction
    log.info(
        f"Running annotations extraction pipeline using dictionary: {dictionary_path}"
    )
    return run_annotation_pipeline(dictionary_path, output_path)


def run_database_creation(
    annotations_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
    schema_prefix: Optional[str] = None,
    force_refresh: bool = False,
) -> Path:
    """
    Run the SQLite database creation pipeline.

    Args:
        annotations_path: Path to the annotations JSON file (optional)
        output_path: Path to save the SQLite database (optional)
        schema_prefix: Optional schema prefix to add to table names (e.g., "sxe.")
        force_refresh: Whether to force running the pipeline even if recent output exists

    Returns:
        Path to the SQLite database file
    """
    log = get_logger()

    # Default database path if not specified
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        db_name = f"annotations_{timestamp}.db"
        if schema_prefix:
            # Use the schema name in the DB filename
            clean_schema = schema_prefix.replace(".", "")
            db_name = f"annotations_{clean_schema}_{timestamp}.db"
        output_path = JSON_DIR.parent / "sqlite" / db_name

        # Ensure directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

    if not force_refresh and output_path.exists():
        log.info(f"SQLite database already exists at {output_path}")
        return output_path

    # Get annotations file if not specified
    if not annotations_path:
        annotations_path = get_latest_file(JSON_DIR, "annotations_*.json")
        if not annotations_path:
            log.info("No annotations file found, running annotations extraction first")
            annotations_path = run_annotations_extraction(force_refresh=force_refresh)

    # Create the database
    log.info(f"Creating SQLite database from annotations: {annotations_path}")
    return create_sqlite_from_annotations(annotations_path, output_path, schema_prefix)


def run_full_pipeline(
    dictionary_output: Optional[Path] = None,
    annotations_output: Optional[Path] = None,
    database_output: Optional[Path] = None,
    schema_prefix: Optional[str] = None,
    force_refresh: bool = False,
) -> Tuple[Path, Path, Path]:
    """
    Run the full pipeline: HTM to JSON, annotations extraction, and SQLite creation.

    Args:
        dictionary_output: Path to save the dictionary JSON file (optional)
        annotations_output: Path to save the annotations JSON file (optional)
        database_output: Path to save the SQLite database (optional)
        schema_prefix: Optional schema prefix to add to table names (e.g., "sxe.")
        force_refresh: Whether to force running all pipelines even if recent output exists

    Returns:
        Tuple of (dictionary_path, annotations_path, database_path)
    """
    log = get_logger()
    log.info("Starting full pipeline")

    # Run dictionary extraction
    dictionary_path = run_dictionary_extraction(dictionary_output, force_refresh)
    log.info(f"Dictionary extraction complete: {dictionary_path}")

    # Run annotations extraction
    annotations_path = run_annotations_extraction(
        dictionary_path, annotations_output, force_refresh
    )
    log.info(f"Annotations extraction complete: {annotations_path}")

    # Create SQLite database
    database_path = run_database_creation(
        annotations_path, database_output, schema_prefix, force_refresh
    )
    log.info(f"Database creation complete: {database_path}")

    return dictionary_path, annotations_path, database_path


def main():
    """Main entry point for the command-line interface."""
    parser = argparse.ArgumentParser(
        description="SX Data Dictionary Tool - Process and query data dictionary files"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Dictionary extraction command
    dictionary_parser = subparsers.add_parser(
        "dictionary", help="Extract dictionary from HTML files"
    )
    dictionary_parser.add_argument(
        "--output", "-o", type=Path, help="Path to save dictionary JSON"
    )
    dictionary_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force refresh even if recent file exists",
    )

    # Annotations extraction command
    annotations_parser = subparsers.add_parser(
        "annotations", help="Extract annotations from dictionary"
    )
    annotations_parser.add_argument(
        "--dictionary", "-d", type=Path, help="Path to dictionary JSON file"
    )
    annotations_parser.add_argument(
        "--output", "-o", type=Path, help="Path to save annotations JSON"
    )
    annotations_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force refresh even if recent file exists",
    )

    # SQLite database creation command
    sqlite_parser = subparsers.add_parser(
        "database", help="Create SQLite database from annotations"
    )
    sqlite_parser.add_argument(
        "--annotations", "-a", type=Path, help="Path to annotations JSON file"
    )
    sqlite_parser.add_argument(
        "--output", "-o", type=Path, help="Path to save SQLite database"
    )
    sqlite_parser.add_argument(
        "--schema",
        "-s",
        type=str,
        help="Schema prefix to add to table names (e.g., 'sxe')",
    )
    sqlite_parser.add_argument(
        "--force", "-f", action="store_true", help="Force refresh even if file exists"
    )

    # Run full pipeline
    full_parser = subparsers.add_parser(
        "full", help="Run the full pipeline: dictionary, annotations, and database"
    )
    full_parser.add_argument(
        "--dictionary-output", type=Path, help="Path to save dictionary JSON"
    )
    full_parser.add_argument(
        "--annotations-output", type=Path, help="Path to save annotations JSON"
    )
    full_parser.add_argument(
        "--database-output", type=Path, help="Path to save SQLite database"
    )
    full_parser.add_argument(
        "--schema",
        "-s",
        type=str,
        help="Schema prefix to add to table names (e.g., 'sxe')",
    )
    full_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force refresh even if recent files exist",
    )

    # Examples command
    # examples_parser = subparsers.add_parser("examples", help="Show usage examples")

    args = parser.parse_args()

    if len(sys.argv) == 1:
        parser.print_help()
        return

    try:
        if args.command == "dictionary":
            output_path = run_dictionary_extraction(args.output, args.force)
            print("\nDictionary extraction complete!")
            print(f"Output saved to: {output_path}")

        elif args.command == "annotations":
            output_path = run_annotations_extraction(
                args.dictionary, args.output, args.force
            )
            print("\nAnnotations extraction complete!")
            print(f"Output saved to: {output_path}")

        elif args.command == "database":
            output_path = run_database_creation(
                args.annotations, args.output, args.schema, args.force
            )
            print("\nDatabase creation complete!")
            print(f"Output saved to: {output_path}")

        elif args.command == "full":
            dict_path, ann_path, db_path = run_full_pipeline(
                args.dictionary_output,
                args.annotations_output,
                args.database_output,
                args.schema,
                args.force,
            )
            print("\nFull pipeline complete!")
            print(f"Dictionary saved to: {dict_path}")
            print(f"Annotations saved to: {ann_path}")
            print(f"Database saved to: {db_path}")

        elif args.command == "examples":
            print_examples()

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def print_examples():
    """Print usage examples for the command-line interface."""
    examples = """
SX Data Dictionary Tool - Usage Examples
=======================================

1. Extract dictionary from HTML files:
   python -m sx_data_dictionary.main dictionary
   python -m sx_data_dictionary.main dictionary --force

2. Extract annotations from dictionary:
   python -m sx_data_dictionary.main annotations
   python -m sx_data_dictionary.main annotations --dictionary data/json/dictionary_20250612_123456.json

3. Create SQLite database from annotations:
   python -m sx_data_dictionary.main database
   python -m sx_data_dictionary.main database --schema sxe
   python -m sx_data_dictionary.main database --annotations data/json/annotations_20250612_123456.json

4. Run the full pipeline:
   python -m sx_data_dictionary.main full
   python -m sx_data_dictionary.main full --schema sxe
   python -m sx_data_dictionary.main full --force

5. Force refresh specific pipeline:
   python -m sx_data_dictionary.main dictionary --force
   python -m sx_data_dictionary.main annotations --force
   python -m sx_data_dictionary.main database --force

6. Specify custom output paths:
   python -m sx_data_dictionary.main dictionary --output data/json/custom_dictionary.json
   python -m sx_data_dictionary.main database --output data/sqlite/custom_database.db
"""
    print(examples)


if __name__ == "__main__":
    main()
