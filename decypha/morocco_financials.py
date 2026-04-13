"""
Morocco Financials Downloader
==============================
Scrapes Decypha for all listed Moroccan companies and downloads:
  - Income Statement (Compte de résultat)
  - Balance Sheet   (Bilan)
  - Cash Flow       (Flux de trésorerie)

Each company gets its own folder:
  downloads/morocco/{company_name}/
    ├── income_statement.xlsx
    ├── balance_sheet.xlsx
    └── cash_flow.xlsx
"""

import re
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field

from playwright.sync_api import Page

logger = logging.getLogger(__name__)

DECYPHA_URL = "https://www.decypha.com"

# ── URL patterns to try for the Moroccan company list ────────────────────────
MOROCCO_SCREENER_URLS = [
    f"{DECYPHA_URL}/screener?country=Morocco",
    f"{DECYPHA_URL}/screener?country=MA",
    f"{DECYPHA_URL}/screener?exchange=CSE",
    f"{DECYPHA_URL}/screener?exchange=BVC",
    f"{DECYPHA_URL}/screener?market=Morocco",
    f"{DECYPHA_URL}/companies?country=Morocco",
    f"{DECYPHA_URL}/companies?exchange=CSE",
    f"{DECYPHA_URL}/market-data/equities?country=Morocco",
    f"{DECYPHA_URL}/equities?country=MA",
]

# ── Financial statement tab labels (EN + FR) ─────────────────────────────────
STATEMENT_TABS = {
    "income_statement": [
        "Income Statement", "P&L", "Profit & Loss",
        "Compte de résultat", "Résultats", "CPC",
    ],
    "balance_sheet": [
        "Balance Sheet", "Bilan",
    ],
    "cash_flow": [
        "Cash Flow", "Cash Flow Statement",
        "Flux de trésorerie", "Tableau de flux",
    ],
}

# ── Selectors for export/download buttons ────────────────────────────────────
EXPORT_SELECTORS = [
    "button:has-text('Export')",
    "button:has-text('Download')",
    "button:has-text('Excel')",
    "button:has-text('CSV')",
    "a:has-text('Export')",
    "a:has-text('Download')",
    "a[href$='.xlsx']",
    "a[href$='.xls']",
    "a[href$='.csv']",
    "[title*='export' i]",
    "[title*='download' i]",
    "[aria-label*='export' i]",
    "[aria-label*='download' i]",
    "button[class*='export' i]",
    "button[class*='download' i]",
    "i[class*='download']",   # icon-only buttons
]


@dataclass
class Company:
    name: str
    url: str
    ticker: str = ""
    downloaded: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _safe_name(text: str) -> str:
    """Sanitise a string for use as a directory/filename."""
    return re.sub(r'[<>:"/\\|?*\s]+', "_", text).strip("._")


def _snap(page: Page, dest_dir: Path, label: str) -> None:
    """Save a debug screenshot (only when debug dir exists)."""
    dbg = dest_dir / "_debug"
    dbg.mkdir(parents=True, exist_ok=True)
    path = dbg / f"{label}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        logger.debug("Screenshot saved: %s", path)
    except Exception:
        pass


# ── Company list discovery ────────────────────────────────────────────────────

def _find_company_links(page: Page) -> list[dict]:
    """
    Generic approach: look for <a> tags that seem to be company links
    (contain a ticker + name pattern, or go to /company/{slug}).
    """
    companies = []
    seen = set()

    # Pattern 1: links going to /company/...
    for link in page.locator("a[href*='/company/']").all():
        href = link.get_attribute("href") or ""
        text = link.inner_text().strip()
        if not href or href in seen:
            continue
        seen.add(href)
        full = href if href.startswith("http") else f"{DECYPHA_URL}{href}"
        companies.append({"name": text or href.split("/")[-1], "url": full})

    # Pattern 2: table rows where each row = one company
    for row in page.locator("table tbody tr").all():
        cells = row.locator("td").all()
        link_el = row.locator("a").first
        href = link_el.get_attribute("href") if link_el.count() else ""
        if not href or href in seen:
            continue
        seen.add(href)
        name = link_el.inner_text().strip() or (cells[0].inner_text().strip() if cells else "")
        full = href if href.startswith("http") else f"{DECYPHA_URL}{href}"
        companies.append({"name": name, "url": full})

    logger.info("Found %d company links on this page", len(companies))
    return companies


def _scroll_load_all(page: Page, pause: float = 1.5, max_scrolls: int = 20) -> None:
    """Scroll to bottom repeatedly to trigger lazy-loaded content."""
    prev_height = 0
    for _ in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(pause)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == prev_height:
            break
        prev_height = new_height


def _try_load_more(page: Page) -> bool:
    """Click 'Load more' / 'Next page' buttons. Returns True if clicked."""
    for sel in [
        "button:has-text('Load more')",
        "button:has-text('Show more')",
        "a:has-text('Next')",
        "button:has-text('Next')",
        "[aria-label='Next page']",
        "li.next a",
        ".pagination .next a",
    ]:
        loc = page.locator(sel).first
        if loc.count() > 0 and loc.is_visible():
            try:
                loc.click()
                page.wait_for_load_state("networkidle", timeout=10_000)
                return True
            except Exception:
                pass
    return False


def discover_moroccan_companies(page: Page, debug_dir: Path | None = None) -> list[Company]:
    """
    Navigate to the Decypha screener / company list filtered for Morocco
    and return a list of Company objects.
    """
    companies: list[Company] = []

    for url in MOROCCO_SCREENER_URLS:
        logger.info("Trying screener URL: %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(2)

            if debug_dir:
                _snap(page, debug_dir, f"screener_{url.split('=')[-1]}")

            # Try to apply Morocco filter if it's a generic screener
            _try_apply_morocco_filter(page)

            # Collect all companies (with scroll + pagination)
            page_companies: list[dict] = []
            _scroll_load_all(page)
            page_companies.extend(_find_company_links(page))

            while _try_load_more(page):
                time.sleep(1)
                _scroll_load_all(page, pause=0.8, max_scrolls=5)
                page_companies.extend(_find_company_links(page))

            if page_companies:
                seen = set()
                for c in page_companies:
                    if c["url"] not in seen:
                        seen.add(c["url"])
                        companies.append(Company(name=c["name"], url=c["url"]))
                logger.info("Total companies found via %s: %d", url, len(companies))
                break  # stop trying URLs once we got results
        except Exception as exc:
            logger.debug("Screener URL %s failed: %s", url, exc)
            continue

    if not companies:
        logger.warning(
            "Could not discover companies automatically. "
            "Try running with --debug and check the screenshots."
        )

    return companies


def _try_apply_morocco_filter(page: Page) -> None:
    """
    If we landed on a generic screener, try to apply a 'Morocco' country filter.
    """
    filter_selectors = [
        ("select[name*='country' i]",   "Morocco"),
        ("select[id*='country' i]",     "Morocco"),
        ("input[placeholder*='country' i]", "Morocco"),
        ("input[placeholder*='market' i]",  "Morocco"),
    ]
    for sel, value in filter_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                loc.select_option(label=value)
                time.sleep(1)
                logger.debug("Applied Morocco filter via: %s", sel)
                break
            except Exception:
                try:
                    loc.fill(value)
                    page.keyboard.press("Enter")
                    time.sleep(1)
                    logger.debug("Typed Morocco filter via: %s", sel)
                    break
                except Exception:
                    pass

    # Click-based filter buttons
    for btn_text in ["Morocco", "Maroc", "MA"]:
        btn = page.locator(f"button:has-text('{btn_text}'), a:has-text('{btn_text}')").first
        if btn.count() > 0:
            try:
                btn.click()
                time.sleep(1)
                logger.debug("Clicked filter button: %s", btn_text)
                break
            except Exception:
                pass


# ── Per-company financial download ───────────────────────────────────────────

def _click_financial_tab(page: Page, tab_name: str, labels: list[str]) -> bool:
    """Click a financial tab by trying multiple label variants. Returns True on success."""
    for label in labels:
        for sel in [
            f"button:has-text('{label}')",
            f"a:has-text('{label}')",
            f"[role='tab']:has-text('{label}')",
            f"li:has-text('{label}')",
            f"span:has-text('{label}')",
        ]:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                try:
                    loc.click()
                    time.sleep(1.5)
                    logger.debug("Clicked tab '%s' via: %s", label, sel)
                    return True
                except Exception:
                    pass
    logger.debug("Tab '%s' not found on page", tab_name)
    return False


def _try_download_button(page: Page, dest_dir: Path, filename_stem: str) -> Path | None:
    """
    Try to click an export/download button and capture the resulting file.
    Returns the saved Path or None if no download occurred.
    """
    for sel in EXPORT_SELECTORS:
        loc = page.locator(sel).first
        if loc.count() == 0 or not loc.is_visible():
            continue
        try:
            with page.expect_download(timeout=15_000) as dl_info:
                loc.click()
            dl = dl_info.value
            ext = Path(dl.suggested_filename or "data.xlsx").suffix or ".xlsx"
            dest = dest_dir / f"{filename_stem}{ext}"
            dl.save_as(str(dest))
            logger.info("  Downloaded: %s", dest.name)
            return dest
        except Exception as exc:
            logger.debug("Export button %s failed: %s", sel, exc)
    return None


def _scrape_table_to_csv(page: Page, dest_dir: Path, filename_stem: str) -> Path | None:
    """
    Fallback: if no download button, scrape the visible HTML table
    and save it as CSV.
    """
    tables = page.locator("table").all()
    if not tables:
        logger.debug("No table found on page for %s", filename_stem)
        return None

    # Use the largest table (most rows)
    best = max(tables, key=lambda t: t.locator("tr").count())
    rows = best.locator("tr").all()

    data = []
    for row in rows:
        cells = row.locator("th, td").all()
        data.append([c.inner_text().strip() for c in cells])

    if not data:
        return None

    dest = dest_dir / f"{filename_stem}.csv"
    import csv
    with open(dest, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerows(data)
    logger.info("  Scraped table -> %s", dest.name)
    return dest


def _navigate_to_financials(page: Page, company_url: str) -> bool:
    """
    Given a company URL, navigate to its financials section.
    Returns True if we found a financials page.
    """
    # Try direct /financials sub-paths first
    financial_paths = [
        "/financials",
        "/financial-statements",
        "/financials/income-statement",
        "/statements",
    ]
    base = company_url.rstrip("/")
    for path in financial_paths:
        url = base + path
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(1.5)
            if page.url != company_url and "/login" not in page.url:
                logger.debug("Financials found at: %s", page.url)
                return True
        except Exception:
            pass

    # Fall back: visit base company URL and look for a Financials link/tab
    page.goto(company_url, wait_until="domcontentloaded", timeout=20_000)
    time.sleep(2)
    for label in ["Financials", "Financial Statements", "États financiers", "Données financières"]:
        for sel in [
            f"a:has-text('{label}')",
            f"button:has-text('{label}')",
            f"[role='tab']:has-text('{label}')",
        ]:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.click()
                time.sleep(2)
                return True

    return False  # couldn't find financials


def download_company_financials(
    page: Page,
    company: Company,
    base_dir: Path,
    debug: bool = False,
) -> Company:
    """
    For one company, download Balance Sheet, Income Statement, Cash Flow.
    Saves files in base_dir/{safe_company_name}/.
    """
    safe = _safe_name(company.name) or _safe_name(company.url.split("/")[-1])
    dest_dir = base_dir / safe
    dest_dir.mkdir(parents=True, exist_ok=True)
    logger.info("── %s ─────────────────────────────────", company.name)

    # Navigate to financials
    found = _navigate_to_financials(page, company.url)
    if not found:
        msg = f"Could not reach financials page for {company.name}"
        logger.warning(msg)
        company.errors.append(msg)
        return company

    if debug:
        _snap(page, dest_dir, "00_financials_landing")

    # Download each statement type
    for stmt_key, labels in STATEMENT_TABS.items():
        # Click the right tab
        tab_found = _click_financial_tab(page, stmt_key, labels)
        if not tab_found and stmt_key != "income_statement":
            # Income statement might already be visible by default
            logger.debug("Tab for %s not found, skipping tab click", stmt_key)

        if debug:
            _snap(page, dest_dir, f"01_{stmt_key}_tab")

        # Try to download; fall back to scraping table
        path = _try_download_button(page, dest_dir, stmt_key)
        if not path:
            path = _scrape_table_to_csv(page, dest_dir, stmt_key)

        if path:
            company.downloaded.append(str(path))
        else:
            msg = f"No data for {stmt_key}"
            logger.warning("  %s: %s", company.name, msg)
            company.errors.append(msg)

        time.sleep(0.5)

    return company


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run_morocco_financials(
    page: Page,
    base_dir: Path,
    debug: bool = False,
    max_companies: int | None = None,
    company_filter: str | None = None,
) -> list[Company]:
    """
    Full pipeline:
      1. Discover all Moroccan listed companies
      2. For each: download Income Statement, Balance Sheet, Cash Flow
      3. Return results with download paths and errors

    Args:
        page:           Authenticated Playwright page
        base_dir:       Root download directory (downloads/morocco/)
        debug:          Save screenshots at each step
        max_companies:  Limit to N companies (useful for testing)
        company_filter: Only process companies whose name contains this string
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    debug_dir = base_dir / "_debug" if debug else None

    logger.info("=== STEP 1: Discovering Moroccan companies ===")
    companies = discover_moroccan_companies(page, debug_dir=debug_dir)

    if not companies:
        logger.error("No companies found. Check screenshots in %s", debug_dir)
        return []

    # Apply filters
    if company_filter:
        companies = [c for c in companies if company_filter.lower() in c.name.lower()]
        logger.info("Filter '%s': %d companies remaining", company_filter, len(companies))

    if max_companies:
        companies = companies[:max_companies]
        logger.info("Limited to first %d companies", max_companies)

    logger.info("=== STEP 2: Downloading financials for %d companies ===", len(companies))

    results = []
    for i, company in enumerate(companies, 1):
        logger.info("[%d/%d] Processing: %s", i, len(companies), company.name)
        try:
            company = download_company_financials(page, company, base_dir, debug=debug)
        except Exception as exc:
            company.errors.append(f"Unexpected error: {exc}")
            logger.error("  Error processing %s: %s", company.name, exc)
        results.append(company)
        time.sleep(1)  # be polite to the server

    return results


def print_results(results: list[Company], base_dir: Path) -> None:
    """Print a summary table of what was downloaded."""
    total_files = sum(len(c.downloaded) for c in results)
    total_errors = sum(len(c.errors) for c in results)

    print(f"\n{'═' * 60}")
    print(f"  MOROCCO FINANCIALS - DOWNLOAD SUMMARY")
    print(f"{'═' * 60}")
    print(f"  Companies processed : {len(results)}")
    print(f"  Files downloaded    : {total_files}")
    print(f"  Errors              : {total_errors}")
    print(f"  Output directory    : {base_dir}")
    print(f"{'─' * 60}")

    for c in results:
        status = "OK " if c.downloaded and not c.errors else ("ERR" if c.errors else "---")
        files = ", ".join(Path(p).name for p in c.downloaded) or "none"
        print(f"  [{status}] {c.name:<35} {files}")

    print(f"{'═' * 60}\n")
