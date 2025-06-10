import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup
from bs4.element import Tag
from loguru import logger

from sx_data_dictionary.config import HTM_DIR, JSON_DIR, configure_logging

# make sure logging is setup
log_file = configure_logging()


def get_logger():
    """Create and return a logger configured for this module."""
    return logger.bind(module="htm_to_json")


def find_module_htm_files() -> List[Path]:
    log = get_logger()

    # Check if HTM directory exists
    if not HTM_DIR.exists():
        log.error(f"HTM directory not found: {HTM_DIR}")
        raise FileNotFoundError(f"HTM directory not found: {HTM_DIR}")

    # Find all files ending with 'tbl.htm' and starting with a capital letter
    # Note: 800 + table files, but only module level pages (that show their related tables)
    #   seem to start with a capital letter with only the XL exceptions filtered below.
    module_pattern = re.compile(r"^[A-Z].*tbl\.htm$")
    module_files = [
        file_path
        for file_path in HTM_DIR.glob("*.htm")
        if module_pattern.match(file_path.name)
    ]

    # also, specifically filter for:
    #  XL_instancetbl, XL_Languagetbl, XL_string_infotbl, XL_translationtbl
    # remove all module_files with an underscore in the name:
    module_files = [
        file_path for file_path in module_files if "_" not in file_path.name
    ]
    # this yields 39 modules for dictionary v11.21.6 as expected

    # Log the findings
    if not module_files:
        log.warning(f"No module HTM files found in {HTM_DIR}")
        raise ValueError(f"No module HTM files found in {HTM_DIR}")
    else:
        log.info(f"Found {len(module_files)} module HTM files")
        for file_path in module_files:
            log.debug(f"Module file: {file_path.name}")

    return module_files


def read_htm(file_path: Path) -> str:
    log = get_logger()
    try:
        with file_path.open("r", encoding="utf-8") as f:
            content = f.read()
        log.debug(f"Read {file_path.name} successfully")
        return content
    except Exception as e:
        log.error(f"Error reading {file_path.name}: {e}")
        raise


def extract_links(
    html_content: str, base_dir: Path, file_extension: str = ".htm"
) -> list:
    log = get_logger()
    links = []
    # parse the HTML content
    soup = BeautifulSoup(html_content, "html.parser")
    # find all href links
    for a_tag in soup.find_all("a", href=True):
        if isinstance(a_tag, Tag):
            href = a_tag.get("href")
            # filter links by extension
            if (
                href
                and isinstance(href, str)
                and href.lower().endswith(file_extension.lower())
            ):
                filename = Path(href).name
                full_path = base_dir / href
                links.append((filename, full_path))
                log.debug(f"Found link {filename}")
    log.info(f"Extracted {len(links)} links from HTML content")
    return links


def extract_module_tables() -> dict:
    """
    Function to extract all the table file names from a given module HTM
    based on hrefs
    :return: Dictionary with module codes (e.g. TAX for Taxtbl.htm) as keys
     and a list of table file names as values
    """
    log = get_logger()
    module_tables = {}

    # get module htm files
    module_files = find_module_htm_files()

    for module_file in module_files:
        # module code equals file name without 'tbl.htm'
        mod_code = module_file.name.replace("tbl.htm", "").upper()

        # store it for dictionary key
        log.info(f"Processing module {mod_code} from {module_file.name}")

        # read htm file
        try:
            content = read_htm(module_file)
        except Exception as e:
            log.error(f"Failed to read {module_file.name}: {e}")
            continue

        # extract links
        links = extract_links(content, HTM_DIR, ".htm")

        # add to module_tables dictionary
        module_tables[mod_code] = links
        log.info(f"Found {len(links)} table links for module {mod_code}")
    return module_tables


def extract_title(html_content: str) -> str:
    log = get_logger()
    soup = BeautifulSoup(html_content, "html.parser")
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
        log.debug(f"Extracted title: {title}")
        return title
    log.warning("No title found in HTML content")
    return ""


def parse_table_structure(file_path: Path) -> dict:
    log = get_logger()

    try:
        html_content = read_htm(file_path)

        title = extract_title(html_content)

        soup = BeautifulSoup(html_content, "html.parser")

        result = {
            "filename": file_path.name,
            "title": title,
            "indexes": [],
            "fields": [],
            "triggers": [],
        }

        # indexes in first table
        main_table = soup.find("table")
        if not main_table or not isinstance(main_table, Tag):
            log.warning(f"No table found in {file_path.name}")
            return result

        # find the row with "Indexes" header
        indexes_row = None
        for row in main_table.find_all("tr"):
            if row.get_text().strip().startswith("Indexes"):
                indexes_row = row
                break

        if indexes_row:
            # get the indexes section by extracting links from next row until blank row
            current_row = indexes_row.find_next("tr")
            while current_row and not current_row.get_text().strip() == "":
                row_html = str(current_row)
                index_links = extract_links(row_html, HTM_DIR)
                result["indexes"].extend(index_links)
                current_row = current_row.find_next("tr")

        # find the row with "Field" header
        fields_row = None
        for row in main_table.find_all("tr"):
            if row.get_text().strip().startswith("Field"):
                fields_row = row
                break

        if fields_row:
            # get the fields section by extracting links from next rows until blank row
            current_row = fields_row.find_next("tr")
            while current_row and not current_row.get_text().strip() == "":
                row_html = str(current_row)
                field_links = extract_links(row_html, HTM_DIR)
                result["fields"].extend(field_links)
                current_row = current_row.find_next("tr")

        # find the row with "Triggers:" header
        triggers_row = None
        for row in main_table.find_all("tr"):
            if row.get_text().strip().startswith("Triggers:"):
                triggers_row = row
                break

        if triggers_row:
            # get the triggers as text from the next rows
            current_row = triggers_row.find_next("tr")
            while current_row:
                if isinstance(current_row, Tag):
                    cells = current_row.find_all("td")
                    if cells:
                        trigger_info = [
                            cell.get_text().strip()
                            for cell in cells
                            if cell.get_text().strip()
                        ]
                        if trigger_info:
                            result["triggers"].append(trigger_info)
                    current_row = current_row.find_next("tr")
                else:
                    break

        # log the results
        log.info(
            f"Parsed {file_path.name}: {len(result['indexes'])} indexes, "
            f"{len(result['fields'])} fields, {len(result['triggers'])} triggers"
        )

        return result

    except Exception as e:
        log.error(f"Error parsing table structure for {file_path}: {e}")
        return {"filename": file_path.name, "error": str(e)}


def test_table_parsing():
    sample_table = HTM_DIR / "icswtbl.htm"
    if sample_table.exists():
        structure = parse_table_structure(sample_table)

        print(f"\nParsed table: {structure['title']}")

        print(f"Indexes ({len(structure['indexes'])}):")
        for filename, path in structure["indexes"]:
            print(f"  - {filename}")

        print(f"Fields ({len(structure['fields'])}):")
        for i, (filename, path) in enumerate(structure["fields"]):
            print(f"  - {filename}")
            if i >= 4:  # print first 5 only
                print(f"  - ...and {len(structure['fields']) - 5} more")
                break

        print(f"Triggers ({len(structure['triggers'])}):")
        for trigger in structure["triggers"]:
            print(f"  - {' '.join(trigger)}")
    else:
        print(f"Sample file not found: {sample_table}")


def aggregate_dictionary_data() -> dict:

    log = get_logger()
    log.info("Starting dictionary data aggregation")

    dictionary = {
        "metadata": {
            "generated_at": datetime.now().isoformat(),
            "version": "1.0",
        },
        "modules": {},
    }

    try:
        module_files = find_module_htm_files()
        log.info(f"Expected module count: {len(module_files)}")

        # get module tables
        module_tables = extract_module_tables()
        actual_module_count = len(module_tables)
        log.info(f"Found {actual_module_count} modules to process")

        for i, (module_code, tables) in enumerate(module_tables.items(), 1):
            log.info(
                f"Processing module {i}/{len(module_tables)}: {module_code} with {len(tables)} tables"
            )

            module_file_path = HTM_DIR / f"{module_code}tbl.htm"

            # get the module title
            module_title = ""
            if module_file_path.exists():
                try:
                    html_content = read_htm(module_file_path)
                    module_title = extract_title(html_content)
                except Exception as e:
                    log.error(f"Error reading module file {module_file_path}: {e}")

            # initialize module entry
            dictionary["modules"][module_code] = {
                "code": module_code,
                "title": module_title,
                "tables": {},
            }

            # process each table in the module
            tables_processed = 0
            tables_skipped = 0
            table_exceptions = []

            for table_filename, table_path in tables:
                try:
                    # parse table structure
                    table_structure = parse_table_structure(table_path)

                    # Convert Path objects to strings for JSON serialization
                    # For indexes, convert from [(filename, path), ...] to [(filename, str(path)), ...]
                    if "indexes" in table_structure:
                        serializable_indexes = []
                        for filename, path in table_structure["indexes"]:
                            serializable_indexes.append((filename, str(path)))
                        table_structure["indexes"] = serializable_indexes

                    # For fields, convert from [(filename, path), ...] to [(filename, str(path)), ...]
                    if "fields" in table_structure:
                        serializable_fields = []
                        for filename, path in table_structure["fields"]:
                            serializable_fields.append((filename, str(path)))
                        table_structure["fields"] = serializable_fields

                    # Add to dictionary
                    # Use the table name (filename without extension) as the key
                    table_name = Path(table_filename).stem
                    dictionary["modules"][module_code]["tables"][
                        table_name
                    ] = table_structure
                    tables_processed += 1

                except Exception as e:
                    log.error(f"Error processing table {table_filename}: {e}")
                    table_exceptions.append((str(table_filename), str(e)))
                    table_exceptions.append((str(table_filename), str(e)))

            log.info(
                f"Module {module_code}: {tables_processed} tables processed, "
                f"{tables_skipped} tables skipped, {len(table_exceptions)} tables failed"
            )

        # Add summary to metadata
        module_count = len(dictionary["modules"])
        total_tables = sum(
            len(module_info["tables"]) for module_info in dictionary["modules"].values()
        )

        dictionary["metadata"]["total_modules"] = module_count
        dictionary["metadata"]["total_tables"] = total_tables
        dictionary["metadata"]["failed_tables"] = len(table_exceptions)
        dictionary["metadata"]["failed_table_details"] = table_exceptions

        # Add info about expected vs actual module count
        dictionary["metadata"]["expected_modules"] = module_count

        log.info(f"Aggregation complete: {module_count} modules, {total_tables} tables")
        return dictionary

    except Exception as e:
        log.error(f"Error during dictionary aggregation: {e}")
        raise


def save_dictionary_to_json(dictionary: dict, output_path: Path) -> None:
    log = get_logger()
    log.info(f"Saving dictionary to {output_path}")

    # ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(dictionary, f, indent=2)
        log.info(f"Dictionary saved successfully to {output_path}")
    except Exception as e:
        log.error(f"Error saving dictionary to {output_path}: {e}")
        raise


def run_dictionary_pipeline(output_path: Optional[Path] = None) -> Path:
    log = get_logger()
    log.info("Starting HTM to JSON conversion pipeline")

    if output_path is None:
        # default output path
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = JSON_DIR / f"dictionary_{timestamp}.json"

    try:
        # aggregate dictionary data
        dictionary = aggregate_dictionary_data()

        # save to JSON
        save_dictionary_to_json(dictionary, output_path)

        log.info(f"Pipeline completed successfully. Output saved to {output_path}")
        return output_path
    except Exception as e:
        log.error(f"Pipeline failed: {e}")
        raise


if __name__ == "__main__":
    # output parsed module/table data to JSON
    try:
        output_file = run_dictionary_pipeline()
        print("\nDictionary generation complete!")
        print(f"Output saved to: {output_file}")
    except Exception as e:
        print(f"Error: {e}")


# if __name__ == "__main__":
#     try:
#         files = find_module_htm_files()
#         print(f"Found {len(files)} module HTM files:")
#         for file in files:
#             print(f"- {file.name}")
#
#         # Test extracting module tables
#         module_tables = extract_module_tables()
#         print("\nModule table counts:")
#         for module_code, tables in module_tables.items():
#             print(f"- {module_code}: {len(tables)} tables")
#             # Print first 5 tables as example
#             for i, (filename, path) in enumerate(tables[:5]):
#                 print(f"  - {filename}")
#             if len(tables) > 5:
#                 print(f"  - ...and {len(tables) - 5} more")
#     except Exception as e:
#         print(f"Error: {e}")
