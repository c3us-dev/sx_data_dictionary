from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple
from bs4 import BeautifulSoup
import re
import json


# Repo-root-relative paths (based on this script's location)
REPO_ROOT = Path(__file__).resolve().parent
BASE = REPO_ROOT / "data" / "htm"  # input: extracted HTML lives here
OUT_DIR = REPO_ROOT / "data" / "json"  # output: generated md/jsonl live here


def read_soup(rel_path: str) -> BeautifulSoup:
    p = BASE / rel_path
    html = p.read_text(errors="ignore")
    return BeautifulSoup(html, "html.parser")


def norm_ws(s: str) -> str:
    return " ".join((s or "").split()).strip()


def parse_link_table(page: str) -> List[Tuple[str, str]]:
    """
    Parses pages like modlist.htm / ictbl.htm that are basically:
      <tr><td><a href="...">code</a></td><td>description</td></tr>
    Returns list of (href, label_text).
    """
    soup = read_soup(page)
    out: List[Tuple[str, str]] = []
    for a in soup.select("table a[href]"):
        href = a.get("href")
        text = norm_ws(a.get_text())
        if not href or not text:
            continue
        # skip framing links etc.
        if href.lower().startswith("index.html"):
            continue
        out.append((href, text))
    return out


def parse_module_list() -> List[Tuple[str, str, str]]:
    """
    From modlist.htm, returns list of (module_code, module_desc, module_page_href)
    Example: ("IC", "Inventory Control", "ictbl.htm")
    """
    soup = read_soup("modlist.htm")
    rows = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        a = tds[0].find("a", href=True)
        if not a:
            continue
        mod_code = norm_ws(a.get_text())
        mod_href = a["href"]
        mod_desc = norm_ws(tds[1].get_text())
        # Filter out "All Tables" and blank spacer rows
        if not mod_code or mod_code.lower() == "all tables":
            continue
        if not mod_href.lower().endswith(".htm"):
            continue
        rows.append((mod_code, mod_desc, mod_href))
    return rows


def parse_table_list(module_page: str) -> List[Tuple[str, str, str]]:
    """
    From e.g. ictbl.htm, returns list of (table_name, table_desc, table_page_href)
    Example: ("icsd", "IC Warehouse Master File", "icsdtbl.htm")
    """
    soup = read_soup(module_page)
    rows = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        a = tds[0].find("a", href=True)
        if not a:
            continue
        table_name = norm_ws(a.get_text())
        table_href = a["href"]
        table_desc = norm_ws(tds[1].get_text())
        if not table_name or not table_href.lower().endswith(".htm"):
            continue
        rows.append((table_name, table_desc, table_href))
    return rows


def parse_table_page(table_page: str) -> Dict:
    """
    From a *table* page like icsdtbl.htm, get:
      - table_title
      - indexes: list of (name, note)
      - fields: list of dict with name,label,type,format,href
    """
    soup = read_soup(table_page)
    title = norm_ws((soup.title.get_text() if soup.title else table_page))

    # indexes are usually a small block before the Field grid
    indexes: List[Tuple[str, str]] = []
    # heuristic: any link whose href looks like "<table>_k-....htm"
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "_k-" in href.lower() and href.lower().endswith(".htm"):
            name = norm_ws(a.get_text())
            # index note may be in same row
            note = ""
            tr = a.find_parent("tr")
            if tr:
                tds = tr.find_all("td")
                if len(tds) >= 2:
                    note = norm_ws(tds[1].get_text())
            indexes.append((name, note))

    # fields table: rows that have <a href="table_field.htm">field</a> + label + type + format
    fields: List[Dict] = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        a = tds[0].find("a", href=True)
        if not a:
            continue
        href = a["href"]
        field_name = norm_ws(a.get_text())
        label = norm_ws(tds[1].get_text())
        ftype = norm_ws(tds[2].get_text())
        fmt = norm_ws(tds[3].get_text())
        if not href.lower().endswith(".htm"):
            continue
        # Avoid index rows and other non-field rows
        if field_name.lower().startswith("k-"):
            continue
        if not field_name:
            continue
        fields.append(
            {
                "name": field_name,
                "label": label,
                "type": ftype,
                "format": fmt,
                "href": href,
            }
        )

    return {"title": title, "indexes": indexes, "fields": fields, "page": table_page}


def parse_field_page(field_page: str) -> Dict[str, str]:
    """
    From a *field* page like icsd_arptype.htm, extract the attribute table:
    Label, Type, Format, Decimals, Initial, Extent, Mandatory, Val Exp, Val Msg, Help, Trigger
    plus Description block if present.
    """
    soup = read_soup(field_page)

    title = norm_ws((soup.title.get_text() if soup.title else field_page))

    kv: Dict[str, str] = {"_title": title, "_page": field_page}

    # Many pages use a simple 2-column table of properties
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        left = norm_ws(tds[0].get_text()).rstrip(":")
        right = norm_ws(tds[1].get_text())
        if not left or not right:
            continue
        if left.lower() in {
            "label",
            "type",
            "format",
            "decimals",
            "initial",
            "extent",
            "mandatory",
            "val exp",
            "val msg",
            "help",
            "trigger",
            "description",
        }:
            kv[left] = right

    # Description sometimes appears as text after a "Description:" label rather than in the table
    desc_text = ""
    txt = soup.get_text("\n")
    m = re.search(r"\bDescription:\s*\n(.*)", txt, re.IGNORECASE | re.DOTALL)
    if m:
        chunk = m.group(1)
        chunk = re.split(r"\n\s*\n\s*\n", chunk, maxsplit=1)[0]
        desc_text = norm_ws(chunk)
    if desc_text and "Description" not in kv:
        kv["Description"] = desc_text

    return kv


def split_table_and_field(field_href: str) -> Tuple[str, str]:
    # e.g. icsd_arptype.htm -> ("icsd", "arptype")
    base = Path(field_href).stem
    if "_" in base:
        t, c = base.split("_", 1)
        return t.lower(), c.lower()
    return "", base.lower()


def main() -> None:
    if not BASE.exists():
        raise SystemExit(
            f"[ERROR] Expected extracted HTML at: {BASE}\n"
            "Decompile the CHM into ./data/htm first (see README)."
        )

    # Create output folder if missing
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    modules = parse_module_list()

    out_md: List[str] = []
    out_md.append("# SX.e Data Dictionary (extracted from CHM)")
    out_md.append("")
    out_md.append("This document was generated from the extracted HTML help files.")
    out_md.append("")

    jsonl_path = OUT_DIR / "sx_data_dictionary.jsonl"
    md_path = OUT_DIR / "sx_data_dictionary.md"

    with jsonl_path.open("w", encoding="utf-8") as jsonl_f:
        for mod_code, mod_desc, mod_page in modules:
            out_md.append(f"\n## Module {mod_code} — {mod_desc}\n")
            tables = parse_table_list(mod_page)
            for table_name, table_desc, table_page in tables:
                table_info = parse_table_page(table_page)

                out_md.append(f"\n### Table {table_name.upper()} — {table_desc}\n")
                out_md.append(f"- Source page: `{table_page}`")
                if table_info["indexes"]:
                    idx_bits = []
                    for name, note in table_info["indexes"]:
                        idx_bits.append(f"`{name}` ({note})" if note else f"`{name}`")
                    out_md.append(f"- Indexes: " + ", ".join(idx_bits))
                out_md.append("")

                out_md.append("| Column | Label | Type | Format | Help / Notes |")
                out_md.append("|---|---|---|---|---|")

                for f in table_info["fields"]:
                    field_detail = parse_field_page(f["href"])
                    help_text = (
                        field_detail.get("Help", "")
                        or field_detail.get("Description", "")
                        or ""
                    )
                    help_text = help_text.replace(
                        "|", "\\|"
                    )  # keep markdown table safe

                    out_md.append(
                        f"| `{f['name']}` | {f['label']} | {f['type']} | {f['format']} | {help_text} |"
                    )

                    record = {
                        "module": mod_code,
                        "table": table_name.lower(),
                        "table_description": table_desc,
                        "column": f["name"].lower(),
                        "label": f["label"],
                        "type": f["type"],
                        "format": f["format"],
                        "properties": {
                            k: v
                            for k, v in field_detail.items()
                            if not k.startswith("_")
                        },
                        "source_table_page": table_page,
                        "source_field_page": f["href"],
                    }
                    jsonl_f.write(json.dumps(record, ensure_ascii=False) + "\n")

    md_path.write_text("\n".join(out_md) + "\n", encoding="utf-8")

    print(f"Wrote: {md_path.resolve()}")
    print(f"Wrote: {jsonl_path.resolve()}")


if __name__ == "__main__":
    main()
