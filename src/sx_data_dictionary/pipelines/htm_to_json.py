from pathlib import Path
from typing import List
import re
from loguru import logger
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from sx_data_dictionary.config import HTM_DIR


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

    # regex for module code
    module_code_pattern = re.compile(r"^([A-Z]+)tbl\.htm$")

    for module_file in module_files:
        # extract the code
        module_code_match = module_code_pattern.match(module_file.name)
        if not module_code_match:
            log.warning(f"Module code not found in file_name: {module_file.name}")
            continue

        # store it for dictionary key
        module_code = module_code_match.group(1)
        log.info(f"Processing module {module_code} from {module_file.name}")

        # read htm file
        try:
            content = read_htm(module_file)
        except Exception as e:
            log.error(f"Failed to read {module_file.name}: {e}")
            continue

        # extract links
        links = extract_links(content, HTM_DIR, ".htm")

        # add to module_tables dictionary
        module_tables[module_code] = links
        log.info(f"Found {len(links)} table links for module {module_code}")
    return module_tables


if __name__ == "__main__":
    try:
        files = find_module_htm_files()
        print(f"Found {len(files)} module HTM files:")
        for file in files:
            print(f"- {file.name}")
        
        # Test extracting module tables
        module_tables = extract_module_tables()
        print("\nModule table counts:")
        for module_code, tables in module_tables.items():
            print(f"- {module_code}: {len(tables)} tables")
            # Print first 5 tables as example
            for i, (filename, path) in enumerate(tables[:5]):
                print(f"  - {filename}")
            if len(tables) > 5:
                print(f"  - ...and {len(tables) - 5} more")
    except Exception as e:
        print(f"Error: {e}")
