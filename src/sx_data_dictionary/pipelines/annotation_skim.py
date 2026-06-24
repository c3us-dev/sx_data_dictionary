import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, Tag
from loguru import logger

from sx_data_dictionary.config import JSON_DIR, configure_logging
from sx_data_dictionary.pipelines.htm_to_json import read_htm

log_file = configure_logging()


def get_logger():
    return logger.bind(module="annotation_skim")


def parse_field_annotations(html_content: str) -> dict:

    log = get_logger()

    soup = BeautifulSoup(html_content, "html.parser")

    # initiallize annotations dictionary
    annotations = {
        "label": "",
        "type": "",
        "format": "",
        "decimals": "",
        "initial": "",
        "extent": "",
        "mandatory": "",
        "val_exp": "",
        "val_msg": "",
        "help": "",
        "trigger": "",
        "indexes": [],
        "description": "",
        "content": "",
    }
    metadata_fields = {
        "label": "label",
        "type": "type",
        "format": "format",
        "decimals": "decimals",
        "initial": "initial",
        "extent": "extent",
        "mandatory": "mandatory",
        "val exp": "val_exp",
        "val msg": "val_msg",
        "help": "help",
        "trigger": "trigger",
    }

    # extract field metadata from the first table
    try:
        tables = soup.find_all("table")
        if len(tables) > 1 and isinstance(tables[1], Tag):
            metadata_table = tables[1]
            rows = (
                metadata_table.find_all("tr")  # type:ignore
                if hasattr(metadata_table, "find_all")
                else []
            )

            for row in rows:
                if not isinstance(row, Tag):
                    continue

                cells = row.find_all("td")
                if len(cells) >= 2:
                    field_name = cells[0].get_text(strip=True).lower()
                    field_value = cells[1].get_text(strip=True)

                    field_name = field_name.replace(":", "").strip()

                    if field_name in metadata_fields:
                        annotations[metadata_fields[field_name]] = field_value
    except Exception as e:
        log.warning(f"Error extracting metadata: {e}")

    try:
        for table in soup.find_all("table"):
            if not isinstance(table, Tag):
                continue
            table_text = table.get_text(" ", strip=True)
            if not table_text.startswith("Indexes:"):
                continue
            annotations["indexes"] = [
                link.get_text(strip=True)
                for link in table.find_all("a")
                if link.get_text(strip=True)
            ]
            break
    except Exception as e:
        log.warning(f"Error extracting field indexes: {e}")

    # extract description and content from the last table
    try:
        tables = soup.find_all("table")
        if tables and isinstance(tables[-1], Tag):
            description_table = tables[-1]
            rows = (
                description_table.find_all("tr")  # type:ignore
                if hasattr(description_table, "find_all")
                else []
            )

            current_section = None
            section_text = []

            for row in rows:
                if not isinstance(row, Tag):
                    continue

                row_text = row.get_text(strip=True)

                # check section header
                if "Description:" in row_text:
                    current_section = "description"
                    section_text = []
                    continue
                elif "Content:" in row_text:
                    # Save the previous section if any
                    if current_section == "description" and section_text:
                        annotations["description"] = "\n".join(section_text)
                        section_text = []

                    current_section = "content"
                    continue

                # build section text to avoid missing any content
                if current_section and row_text:
                    section_text.append(row_text)

            # save the last section
            if current_section == "description" and section_text:
                annotations["description"] = "\n".join(section_text)
            elif current_section == "content" and section_text:
                annotations["content"] = "\n".join(section_text)
    except Exception as e:
        log.warning(f"Error extracting description/content: {e}")

    # clean up the extracted text
    for key in annotations:
        if annotations[key]:
            if key == "indexes":
                continue

            # Preserve useful line breaks in Description/Content code lists.
            if key in {"description", "content"}:
                lines = [
                    re.sub(r"[ \t]+", " ", line).strip()
                    for line in str(annotations[key]).splitlines()
                ]
                text = "\n".join(line for line in lines if line)
            else:
                text = re.sub(r"\s+", " ", annotations[key])

            # normalize unicode characters for consistent display
            text = unicodedata.normalize("NFC", text)

            annotations[key] = text

    return annotations


def process_field_annotations(dictionary_path: Path) -> dict:
    # loop through the dictionary JSON and extract field annotations
    log = get_logger()
    log.info(f"Processing field annotations from {dictionary_path}")

    try:
        with open(dictionary_path, "r", encoding="utf-8") as f:
            dictionary = json.load(f)
    except Exception as e:
        log.error(f"Error loading dictionary JSON: {e}")
        raise

    # initialize results structure
    field_annotations = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "version": "1.0",
            "source_dictionary": str(dictionary_path),
        },
        "modules": {},
    }

    # get counts for progress reporting
    total_modules = len(dictionary["modules"])
    total_tables = sum(
        len(module_data["tables"]) for _, module_data in dictionary["modules"].items()
    )

    # estimate total fields
    total_fields = 0
    for module_code, module_data in dictionary["modules"].items():
        for table_name, table_data in module_data["tables"].items():
            total_fields += len(table_data.get("fields", []))

    log.info(
        f"Estimating {total_fields} fields to process across {total_modules} modules and {total_tables} tables"
    )

    fields_processed = 0
    fields_failed = 0

    # process each module
    for module_code, module_data in dictionary["modules"].items():
        log.info(f"Processing module: {module_code} ({module_data['title']})")

        field_annotations["modules"][module_code] = {
            "code": module_code,
            "title": module_data["title"],
            "tables": {},
        }

        # process each table in the module
        for table_name, table_data in module_data["tables"].items():
            log.debug(f"Processing table: {table_name}")

            # clean up table names if they have 'tbl' from filename
            clean_table_name = table_name
            if clean_table_name.lower().endswith("tbl"):
                clean_table_name = clean_table_name[:-3]
                log.debug(f"Cleaned table name: {clean_table_name} from {table_name}")

            field_annotations["modules"][module_code]["tables"][clean_table_name] = {
                "title": table_data.get("title", ""),
                "indexes": table_data.get("indexes", []),
                "triggers": table_data.get("triggers", []),
                "fields": {},
            }

            # process each field in the table
            for field_filename, field_path_str in table_data.get("fields", []):
                # convert string path back to Path object
                field_path = Path(field_path_str)
                field_name = Path(field_filename).stem

                try:
                    if field_path.exists():
                        html_content = read_htm(field_path)

                        annotations = parse_field_annotations(html_content)

                        field_annotations["modules"][module_code]["tables"][
                            clean_table_name
                        ]["fields"][field_name] = annotations
                        fields_processed += 1

                        if fields_processed % 100 == 0:
                            log.info(
                                f"Processed {fields_processed}/{total_fields} fields"
                            )
                    else:
                        log.warning(f"Field file not found: {field_path}")
                        fields_failed += 1
                except Exception as e:
                    log.error(
                        f"Error processing field {field_name} in table {clean_table_name}: {e}"
                    )
                    fields_failed += 1

    # add metadata summary
    field_annotations["metadata"]["total_modules"] = total_modules
    field_annotations["metadata"]["total_tables"] = total_tables
    field_annotations["metadata"]["total_fields_processed"] = fields_processed
    field_annotations["metadata"]["total_fields_failed"] = fields_failed

    log.info(
        f"Annotation processing complete: {fields_processed} fields processed, {fields_failed} fields failed"
    )

    return field_annotations


def save_annotations_to_json(annotations: dict, output_path: Path) -> None:
    log = get_logger()
    log.info(f"Saving annotations to {output_path}")

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(annotations, f, indent=2, ensure_ascii=False)
        log.info(f"Annotations saved successfully to {output_path}")
    except Exception as e:
        log.error(f"Error saving annotations to {output_path}: {e}")
        raise


def run_annotation_pipeline(
    dictionary_path: Optional[Path] = None, output_path: Optional[Path] = None
) -> Path:
    log = get_logger()
    log.info("Starting annotation extraction pipeline")

    # default dictionary path is the most recent dictionary JSON
    if dictionary_path is None:
        json_files = list(JSON_DIR.glob("dictionary_*.json"))
        if not json_files:
            raise FileNotFoundError(f"No dictionary JSON files found in {JSON_DIR}")

        # sort by modification time (newest first)
        json_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        dictionary_path = json_files[0]
        log.info(f"Using most recent dictionary: {dictionary_path}")

    # default output path
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = JSON_DIR / f"annotations_{timestamp}.json"

    try:
        annotations = process_field_annotations(dictionary_path)

        save_annotations_to_json(annotations, output_path)

        log.info(
            f"Annotation pipeline completed successfully. Output saved to {output_path}"
        )
        return output_path

    except Exception as e:
        log.error(f"Annotation pipeline failed: {e}")
        raise


if __name__ == "__main__":
    try:
        output_file = run_annotation_pipeline()
        print("\nAnnotation extraction complete!")
        print(f"Output saved to: {output_file}")
    except Exception as e:
        print(f"Error: {e}")
