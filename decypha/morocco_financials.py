"""
Morocco Financials Downloader
==============================
Télécharge pour chaque entreprise cotée à la BVC (Bourse de Casablanca) :
  - Income Statement  (Compte de résultat / CPC)
  - Balance Sheet     (Bilan)
  - Cash Flow         (Flux de trésorerie)

URL pattern Decypha :
  https://www.decypha.com/en/financial/snp/cse/{TICKER}?viewType=&currency=MAD

Structure de sortie :
  downloads/morocco/{TICKER}_{NOM}/
      income_statement.xlsx  (ou .csv si pas de bouton export)
      balance_sheet.xlsx
      cash_flow.xlsx
"""

import re
import time
import logging
import csv
from pathlib import Path
from dataclasses import dataclass, field

from playwright.sync_api import Page

logger = logging.getLogger(__name__)

BASE_URL = "https://www.decypha.com"

# ── Liste complète des entreprises cotées à la BVC (Casablanca Stock Exchange) ──
# Format : (TICKER, NOM)
CSE_COMPANIES = [
    ("ADH",        "Douja Prom Addoha"),
    ("AFM",        "Afma"),
    ("AFI",        "Africa Industries"),
    ("AGMA",       "Agma Lahlou-Tazi"),
    ("ADI",        "Alliances Développement Immobilier"),
    ("ALU",        "Aluminium du Maroc"),
    ("AKT",        "Akdital"),
    ("ARADEI",     "Aradei Capital"),
    ("ATH",        "Auto Hall"),
    ("ATW",        "Attijariwafa Bank"),
    ("BAL",        "Balima"),
    ("BCP",        "Banque Centrale Populaire"),
    ("BMCE",       "Bank of Africa"),
    ("BMCI",       "BMCI"),
    ("BOA",        "Bank of Africa"),
    ("BVC",        "Bourse de Casablanca"),
    ("CAM",        "Crédit Agricole du Maroc"),
    ("CDM",        "Crédit du Maroc"),
    ("CFG",        "CFG Bank"),
    ("CGI",        "Colorado Group Immobilier"),
    ("CIH",        "CIH Bank"),
    ("CMT",        "Compagnie Minière de Touissit"),
    ("CMA",        "Ciments du Maroc"),
    ("CNIA",       "CNIA Saada Assurance"),
    ("CO",         "Colorado"),
    ("COL",        "Colorado"),
    ("CSR",        "Cosumar"),
    ("CTM",        "CTM"),
    ("DAR",        "Dari Couspate"),
    ("DLT",        "Delta Holding"),
    ("DIS",        "Disway"),
    ("DWY",        "Disway"),
    ("EQD",        "Eqdom"),
    ("FBR",        "Fenie Brossette"),
    ("FEN",        "Fenie Brossette"),
    ("FER",        "Fertima"),
    ("GAZ",        "Afriquia Gaz"),
    ("HPS",        "Hightech Payment Systems"),
    ("IAM",        "Maroc Telecom"),
    ("IB",         "IB Maroc"),
    ("IMR",        "Immorente Invest"),
    ("INV",        "Involys"),
    ("JLEC",       "Jorf Lasfar Energy Company"),
    ("LBV",        "Label Vie"),
    ("LES",        "Lesieur Cristal"),
    ("LHM",        "Lafarge Holcim Maroc"),
    ("LYD",        "Lydec"),
    ("M2M",        "M2M Group"),
    ("MAB",        "Maghrebail"),
    ("MAN",        "Managem"),
    ("MDP",        "Med Paper"),
    ("MNG",        "Managem"),
    ("MOX",        "Maghreb Oxygène"),
    ("MUT",        "Mutandis"),
    ("NEX",        "Nexans Maroc"),
    ("OUL",        "Les Eaux Minérales d'Oulmès"),
    ("PAL",        "Palmeraie Développement"),
    ("PRO",        "Promopharm"),
    ("RDS",        "Résidences Dar Saada"),
    ("REB",        "Rebab Company"),
    ("RIS",        "Risma"),
    ("SAF",        "Salafin"),
    ("SAH",        "Saham Finances"),
    ("SAM",        "SAMIR"),
    ("SCE",        "Sonasid"),
    ("SMI",        "Société Métallurgique d'Imiter"),
    ("SNA",        "Snep"),
    ("SOT",        "Sothema"),
    ("STR",        "Stroc Industrie"),
    ("TAQ",        "TAQA Morocco"),
    ("TAQA",       "TAQA Morocco"),
    ("TGC",        "TGCC"),
    ("TGCC",       "TGCC"),
    ("TIM",        "Timar"),
    ("TQM",        "TotalEnergies Marketing Maroc"),
    ("UNI",        "UNIM"),
    ("WAA",        "Wafa Assurance"),
    ("ZDJ",        "Zellidja"),
]

# Dédoublonnage par ticker
_seen_tickers: set[str] = set()
CSE_COMPANIES_UNIQUE: list[tuple[str, str]] = []
for _t, _n in CSE_COMPANIES:
    if _t not in _seen_tickers:
        _seen_tickers.add(_t)
        CSE_COMPANIES_UNIQUE.append((_t, _n))
CSE_COMPANIES = CSE_COMPANIES_UNIQUE


# ── URL builders ──────────────────────────────────────────────────────────────
def financial_url(ticker: str, view_type: str = "") -> str:
    """
    Build the Decypha financial URL for a given ticker.
    view_type : "", "IS", "BS", "CF" (Income Statement, Balance Sheet, Cash Flow)
    """
    return (
        f"{BASE_URL}/en/financial/snp/cse/{ticker}"
        f"?viewType={view_type}&currency=MAD&fromPeriod=&toPeriod="
    )


# ── Tab labels for each statement (EN + FR + Decypha-specific) ────────────────
STATEMENT_CONFIG = {
    "income_statement": {
        "view_type": "IS",          # essayé dans l'URL
        "tab_labels": [
            "Income Statement", "P&L", "Profit & Loss",
            "Compte de résultat", "Résultats", "CPC",
            "IS", "Income",
        ],
        "filename": "income_statement",
    },
    "balance_sheet": {
        "view_type": "BS",
        "tab_labels": [
            "Balance Sheet", "Bilan", "BS",
        ],
        "filename": "balance_sheet",
    },
    "cash_flow": {
        "view_type": "CF",
        "tab_labels": [
            "Cash Flow", "Cash Flow Statement",
            "Flux de trésorerie", "Tableau de flux",
            "CF", "Cash",
        ],
        "filename": "cash_flow",
    },
}

# ── Export / download button selectors ───────────────────────────────────────
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
    "i.fa-download",
    "span.fa-download",
    "button:has(i.fa-download)",
    "button:has(svg[data-icon='download'])",
]


@dataclass
class CompanyResult:
    ticker: str
    name: str
    downloaded: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", text).strip("._")


def _snap(page: Page, dest_dir: Path, label: str) -> None:
    """Save a debug screenshot."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(dest_dir / f"{label}.png"), full_page=True)
    except Exception:
        pass


# ── Download helpers ──────────────────────────────────────────────────────────

def _try_export_button(page: Page, dest_dir: Path, stem: str) -> Path | None:
    """Click the first visible export button and capture the download."""
    for sel in EXPORT_SELECTORS:
        locs = page.locator(sel).all()
        for loc in locs:
            if not loc.is_visible():
                continue
            try:
                with page.expect_download(timeout=15_000) as dl_info:
                    loc.click()
                dl = dl_info.value
                ext = Path(dl.suggested_filename or "data.xlsx").suffix or ".xlsx"
                dest = dest_dir / f"{stem}{ext}"
                dl.save_as(str(dest))
                logger.info("    Exported: %s", dest.name)
                return dest
            except Exception as exc:
                logger.debug("    Export selector %s failed: %s", sel, exc)
    return None


def _scrape_table_to_csv(page: Page, dest_dir: Path, stem: str) -> Path | None:
    """
    Fallback: scrape the largest visible HTML table and save as CSV.
    """
    tables = page.locator("table").all()
    if not tables:
        # Try common data container selectors
        for container_sel in [
            "[class*='financial-table']",
            "[class*='data-table']",
            "[class*='statement']",
            ".table-responsive table",
        ]:
            tables = page.locator(container_sel + " table, " + container_sel).all()
            if tables:
                break

    if not tables:
        return None

    # Take the table with the most rows
    best = max(tables, key=lambda t: t.locator("tr").count())
    rows = best.locator("tr").all()
    if not rows:
        return None

    data = []
    for row in rows:
        cells = row.locator("th, td").all()
        data.append([c.inner_text().strip() for c in cells])

    if len(data) < 2:  # empty or header-only
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}.csv"
    with open(dest, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(data)
    logger.info("    Scraped table -> %s", dest.name)
    return dest


def _click_tab(page: Page, labels: list[str]) -> bool:
    """Try clicking a tab by label text. Returns True if clicked."""
    for label in labels:
        for sel in [
            f"button:has-text('{label}')",
            f"a:has-text('{label}')",
            f"[role='tab']:has-text('{label}')",
            f"li:has-text('{label}')",
            f"span:has-text('{label}')",
            f"div[class*='tab']:has-text('{label}')",
            f"[class*='nav-item']:has-text('{label}')",
        ]:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                try:
                    loc.click()
                    time.sleep(1.5)
                    logger.debug("    Clicked tab: %s", label)
                    return True
                except Exception:
                    pass
    return False


# ── Per-company downloader ────────────────────────────────────────────────────

def download_company_financials(
    page: Page,
    ticker: str,
    company_name: str,
    base_dir: Path,
    debug: bool = False,
) -> CompanyResult:
    """
    Download all 3 financial statements for one company.
    Strategy:
      1. Navigate to the base financial page (viewType=)
      2. For each statement: try URL with viewType param first,
         then try clicking the tab,
         then try export button,
         then fall back to table scraping.
    """
    result = CompanyResult(ticker=ticker, name=company_name)
    folder = _safe_name(f"{ticker}_{company_name}")
    dest_dir = base_dir / folder
    dest_dir.mkdir(parents=True, exist_ok=True)

    logger.info("── [%s] %s", ticker, company_name)

    # Load the base page first to confirm the company exists
    base_page_url = financial_url(ticker)
    try:
        page.goto(base_page_url, wait_until="domcontentloaded", timeout=25_000)
        time.sleep(2)
    except Exception as exc:
        result.errors.append(f"Cannot load page: {exc}")
        logger.warning("    Cannot load: %s", exc)
        return result

    if "/login" in page.url or "/signin" in page.url:
        result.errors.append("Session expired - redirected to login")
        logger.error("    Session expired!")
        return result

    if debug:
        _snap(page, dest_dir / "_debug", "00_base")

    # ── Download each statement ───────────────────────────────────────────────
    for stmt_key, cfg in STATEMENT_CONFIG.items():
        logger.info("  -> %s", stmt_key)
        stem = cfg["filename"]
        got_file: Path | None = None

        # Strategy A: navigate directly with viewType in URL
        url_with_type = financial_url(ticker, cfg["view_type"])
        try:
            page.goto(url_with_type, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(2)
            if debug:
                _snap(page, dest_dir / "_debug", f"01_{stmt_key}_url")
        except Exception:
            # Fall back to base page + tab click
            page.goto(base_page_url, wait_until="domcontentloaded", timeout=20_000)
            time.sleep(2)

        # Strategy B: click the tab if URL param didn't switch the view
        if not got_file:
            _click_tab(page, cfg["tab_labels"])
            if debug:
                _snap(page, dest_dir / "_debug", f"02_{stmt_key}_tab")

        # Strategy C: click export button → file download
        got_file = _try_export_button(page, dest_dir, stem)

        # Strategy D: scrape the HTML table
        if not got_file:
            got_file = _scrape_table_to_csv(page, dest_dir, stem)

        if got_file:
            result.downloaded.append(str(got_file))
        else:
            msg = f"No data found for {stmt_key}"
            result.errors.append(msg)
            logger.warning("    %s", msg)

        time.sleep(0.8)

    return result


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run_morocco_financials(
    page: Page,
    base_dir: Path,
    debug: bool = False,
    max_companies: int | None = None,
    company_filter: str | None = None,
) -> list[CompanyResult]:
    """
    Full pipeline: iterate over all CSE companies and download their
    Income Statement, Balance Sheet and Cash Flow statements.

    Args:
        page:           Authenticated Playwright page
        base_dir:       Root output directory (e.g. downloads/morocco/)
        debug:          Save screenshots at each step
        max_companies:  Cap the number of companies (for test runs)
        company_filter: Only process companies whose ticker or name contains this string
    """
    base_dir.mkdir(parents=True, exist_ok=True)

    companies = list(CSE_COMPANIES)

    # Apply filters
    if company_filter:
        f = company_filter.upper()
        companies = [
            (t, n) for t, n in companies
            if f in t.upper() or f in n.upper()
        ]
        logger.info("Filter '%s': %d companies", company_filter, len(companies))

    if max_companies:
        companies = companies[:max_companies]

    logger.info("=== Starting Morocco Financials: %d companies ===", len(companies))

    results = []
    for i, (ticker, name) in enumerate(companies, 1):
        logger.info("[%d/%d] %s - %s", i, len(companies), ticker, name)
        try:
            result = download_company_financials(
                page, ticker, name, base_dir, debug=debug
            )
        except Exception as exc:
            result = CompanyResult(ticker=ticker, name=name)
            result.errors.append(f"Unexpected error: {exc}")
            logger.error("  Unexpected error for %s: %s", ticker, exc)
        results.append(result)
        time.sleep(1)

    return results


def print_results(results: list[CompanyResult], base_dir: Path) -> None:
    """Print a formatted summary."""
    total_files  = sum(len(r.downloaded) for r in results)
    total_errors = sum(len(r.errors) for r in results)
    ok_count     = sum(1 for r in results if r.downloaded and not r.errors)

    print(f"\n{'═' * 65}")
    print("  MOROCCO FINANCIALS — RÉSULTAT")
    print(f"{'═' * 65}")
    print(f"  Entreprises traitées : {len(results)}")
    print(f"  OK (≥1 fichier)      : {ok_count}")
    print(f"  Fichiers téléchargés : {total_files}")
    print(f"  Erreurs              : {total_errors}")
    print(f"  Dossier de sortie    : {base_dir}")
    print(f"{'─' * 65}")

    for r in results:
        if r.downloaded and not r.errors:
            status = "OK "
        elif r.downloaded:
            status = "PAR"   # partial
        else:
            status = "ERR"
        files = ", ".join(Path(p).name for p in r.downloaded) or "—"
        print(f"  [{status}] {r.ticker:<8} {r.name:<35} {files}")

    print(f"{'═' * 65}\n")
