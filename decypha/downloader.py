"""
Downloader module for Decypha.
Discovers and downloads files from the platform after authentication.
"""

import re
import time
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse
from playwright.sync_api import Page, Download

logger = logging.getLogger(__name__)

DECYPHA_URL = "https://www.decypha.com"

# Sections of Decypha that typically contain downloadable data
DATA_SECTIONS = {
    "financials": "/financials",
    "screener": "/screener",
    "market_data": "/market-data",
    "reports": "/research-reports",
    "company": "/company",
    "indices": "/indices",
}

# File extensions to capture as downloads
DOWNLOAD_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".zip", ".json"}


def _safe_filename(name: str) -> str:
    """Strip illegal characters from a filename."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip("._")


def download_file(download: Download, dest_dir: Path) -> Path:
    """
    Handle a Playwright Download event and save to dest_dir.
    Returns the saved file path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    suggested = download.suggested_filename or "decypha_file"
    filename = _safe_filename(suggested)
    dest = dest_dir / filename

    # Avoid overwriting – append counter suffix
    counter = 1
    stem = dest.stem
    suffix = dest.suffix
    while dest.exists():
        dest = dest_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    download.save_as(str(dest))
    logger.info("Saved: %s", dest)
    return dest


def click_download_buttons(page: Page, dest_dir: Path) -> list[Path]:
    """
    Find and click all download/export buttons on the current page.
    Returns list of downloaded file paths.
    """
    saved = []
    download_btn_selectors = [
        "button:has-text('Download')",
        "button:has-text('Export')",
        "a:has-text('Download')",
        "a:has-text('Export')",
        "button[title*='download' i]",
        "button[title*='export' i]",
        "[class*='download']",
        "[class*='export']",
        "button:has-text('Excel')",
        "button:has-text('CSV')",
        "a[href$='.xlsx']",
        "a[href$='.xls']",
        "a[href$='.csv']",
        "a[href$='.pdf']",
    ]

    for selector in download_btn_selectors:
        buttons = page.locator(selector).all()
        for btn in buttons:
            try:
                if not btn.is_visible():
                    continue
                with page.expect_download(timeout=15_000) as dl_info:
                    btn.click()
                dl = dl_info.value
                path = download_file(dl, dest_dir)
                saved.append(path)
                time.sleep(1)
            except Exception as exc:
                logger.debug("Button click did not trigger download: %s", exc)

    return saved


def scrape_direct_links(page: Page, dest_dir: Path) -> list[Path]:
    """
    Find direct <a href> links pointing to downloadable files and fetch them
    using the authenticated browser session.
    """
    saved = []
    links = page.locator("a[href]").all()
    for link in links:
        try:
            href = link.get_attribute("href") or ""
            ext = Path(urlparse(href).path).suffix.lower()
            if ext not in DOWNLOAD_EXTENSIONS:
                continue
            full_url = urljoin(DECYPHA_URL, href)
            logger.info("Downloading direct link: %s", full_url)
            with page.expect_download(timeout=20_000) as dl_info:
                link.click()
            dl = dl_info.value
            path = download_file(dl, dest_dir)
            saved.append(path)
            time.sleep(0.5)
        except Exception as exc:
            logger.debug("Direct link download failed: %s", exc)

    return saved


def download_section(page: Page, section_name: str, section_path: str, base_dir: Path) -> list[Path]:
    """
    Navigate to a Decypha section and attempt to download all available files.
    Files are saved under base_dir/section_name/.
    """
    dest = base_dir / section_name
    url = urljoin(DECYPHA_URL, section_path)
    logger.info("=== Scraping section '%s' at %s ===", section_name, url)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)
    except Exception as exc:
        logger.warning("Could not load section %s: %s", section_name, exc)
        return []

    saved = []
    saved += click_download_buttons(page, dest)
    saved += scrape_direct_links(page, dest)
    logger.info("Section '%s': %d file(s) downloaded.", section_name, len(saved))
    return saved


def download_company_data(page: Page, company_id: str, base_dir: Path) -> list[Path]:
    """
    Download all available data for a specific company by its Decypha ID or slug.
    """
    dest = base_dir / "companies" / _safe_filename(company_id)
    url = urljoin(DECYPHA_URL, f"/company/{company_id}")
    logger.info("Fetching company page: %s", url)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)
    except Exception as exc:
        logger.warning("Could not load company %s: %s", company_id, exc)
        return []

    saved = []
    saved += click_download_buttons(page, dest)
    saved += scrape_direct_links(page, dest)
    logger.info("Company '%s': %d file(s) downloaded.", company_id, len(saved))
    return saved


def run_full_download(page: Page, base_dir: Path, sections: dict | None = None) -> list[Path]:
    """
    Run downloads across all configured sections.
    Returns all downloaded file paths.
    """
    all_files = []
    target_sections = sections or DATA_SECTIONS
    for name, path in target_sections.items():
        files = download_section(page, name, path, base_dir)
        all_files.extend(files)
    return all_files
