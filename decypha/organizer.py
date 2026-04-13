"""
File organizer module.
Sorts downloaded files into a tidy directory structure.
"""

import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Map file extensions to human-readable category folder names
EXTENSION_CATEGORY = {
    ".xlsx": "excel",
    ".xls": "excel",
    ".csv": "csv",
    ".pdf": "pdf",
    ".zip": "archives",
    ".json": "json",
    ".xml": "xml",
    ".txt": "text",
}

# Keywords in filenames that hint at a thematic sub-folder
KEYWORD_SUBFOLDER = {
    "financial": "financials",
    "income": "financials",
    "balance": "financials",
    "cashflow": "financials",
    "ratio": "financials",
    "dividend": "financials",
    "annual": "annual_reports",
    "report": "reports",
    "screener": "screener",
    "market": "market_data",
    "index": "indices",
    "price": "market_data",
    "company": "companies",
    "profile": "companies",
}


def _detect_subfolder(filename: str) -> str:
    """Guess a thematic sub-folder based on keywords in the filename."""
    lower = filename.lower()
    for keyword, folder in KEYWORD_SUBFOLDER.items():
        if keyword in lower:
            return folder
    return "misc"


def organize_file(src: Path, base_dir: Path, by_date: bool = True) -> Path:
    """
    Move *src* into a structured location under *base_dir*.

    Structure:
        base_dir/
          YYYY-MM/          (if by_date=True)
            <category>/
              <subfolder>/
                <filename>

    Returns the new destination path.
    """
    ext = src.suffix.lower()
    category = EXTENSION_CATEGORY.get(ext, "other")
    subfolder = _detect_subfolder(src.name)

    if by_date:
        date_part = datetime.now().strftime("%Y-%m")
        dest_dir = base_dir / date_part / category / subfolder
    else:
        dest_dir = base_dir / category / subfolder

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    # Avoid collisions
    counter = 1
    stem = src.stem
    while dest.exists():
        dest = dest_dir / f"{stem}_{counter}{ext}"
        counter += 1

    shutil.move(str(src), str(dest))
    logger.info("Organized: %s  ->  %s", src.name, dest)
    return dest


def organize_directory(source_dir: Path, dest_dir: Path, by_date: bool = True) -> list[Path]:
    """
    Recursively organize all files in *source_dir* into *dest_dir*.
    Returns list of new paths.
    """
    organized = []
    for f in source_dir.rglob("*"):
        if f.is_file():
            try:
                new_path = organize_file(f, dest_dir, by_date=by_date)
                organized.append(new_path)
            except Exception as exc:
                logger.warning("Could not organize %s: %s", f, exc)
    return organized


def print_tree(base_dir: Path, max_depth: int = 4) -> None:
    """Print a simple directory tree for the download folder."""
    print(f"\nDownload directory: {base_dir}\n")

    def _tree(path: Path, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return
        entries = sorted(path.iterdir())
        for i, entry in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            print(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                _tree(entry, prefix + extension, depth + 1)

    if base_dir.exists():
        _tree(base_dir)
    else:
        print(f"  (directory not found: {base_dir})")
    print()


def summary_report(base_dir: Path) -> dict:
    """
    Return a dict summarising what was downloaded.
    Keys: category names, values: count of files.
    """
    counts: dict[str, int] = {}
    for f in base_dir.rglob("*"):
        if f.is_file():
            ext = f.suffix.lower()
            cat = EXTENSION_CATEGORY.get(ext, "other")
            counts[cat] = counts.get(cat, 0) + 1
    return counts
