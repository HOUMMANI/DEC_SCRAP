#!/usr/bin/env python3
"""
Decypha Download Agent
======================
Main entry point.  Run with:

    python agent.py --help

or configure via .env file (copy .env.example -> .env).
"""

import os
import sys
import logging
import argparse
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from decypha.auth import get_authenticated_page
from decypha.downloader import (
    run_full_download,
    download_section,
    download_company_data,
    DATA_SECTIONS,
)
from decypha.organizer import organize_directory, print_tree, summary_report
from decypha.morocco_financials import (
    run_morocco_financials,
    print_results as print_morocco_results,
)

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── CLI ───────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent.py",
        description="Decypha automated download agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # États financiers de TOUTES les entreprises marocaines
  python agent.py --morocco

  # États financiers d'une seule entreprise marocaine
  python agent.py --morocco --filter "Attijariwafa"

  # Tester sur 3 entreprises seulement (avant de tout lancer)
  python agent.py --morocco --limit 3 --debug

  # Télécharger une section générique
  python agent.py --section financials

  # Télécharger une URL Decypha spécifique
  python agent.py --url "https://www.decypha.com/company/attijariwafa/financials"

  # Navigateur visible (debug)
  python agent.py --morocco --no-headless
""",
    )

    # Auth
    auth = p.add_argument_group("Authentication")
    auth.add_argument("--email", help="Decypha login (or set DECYPHA_EMAIL in .env)")
    auth.add_argument("--password", help="Decypha password (or set DECYPHA_PASSWORD in .env)")

    # ── Morocco mode (primary use-case) ──────────────────────────────────────
    morocco = p.add_argument_group("Morocco financial statements (main use-case)")
    morocco.add_argument(
        "--morocco",
        action="store_true",
        help="Download Income Statement + Balance Sheet + Cash Flow for ALL Moroccan companies",
    )
    morocco.add_argument(
        "--filter",
        metavar="NAME",
        help="Only process companies whose name contains NAME (case-insensitive)",
    )
    morocco.add_argument(
        "--limit",
        metavar="N",
        type=int,
        help="Process at most N companies (useful for testing)",
    )
    morocco.add_argument(
        "--debug",
        action="store_true",
        help="Save a screenshot at each step for debugging",
    )

    # ── Generic actions ───────────────────────────────────────────────────────
    action = p.add_argument_group("Other download actions")
    action.add_argument("--all", action="store_true", help="Download from all Decypha sections")
    action.add_argument(
        "--section",
        metavar="NAME",
        help=f"Download a specific section. Available: {', '.join(DATA_SECTIONS)}",
    )
    action.add_argument(
        "--company",
        metavar="ID",
        help="Download data for a company by Decypha slug/ID",
    )
    action.add_argument(
        "--url",
        metavar="URL",
        help="Download from any Decypha URL",
    )
    action.add_argument(
        "--list-sections",
        action="store_true",
        help="List available sections and exit",
    )

    # Output
    output = p.add_argument_group("Output")
    output.add_argument(
        "--download-dir",
        default=None,
        metavar="DIR",
        help="Directory for downloads (default: ./downloads or DOWNLOAD_DIR in .env)",
    )
    output.add_argument(
        "--no-organize",
        action="store_true",
        help="Skip automatic file organisation after download",
    )
    output.add_argument(
        "--no-headless",
        action="store_true",
        help="Show the browser window (useful for debugging)",
    )
    output.add_argument(
        "--tree",
        action="store_true",
        help="Print download directory tree when done",
    )

    return p


def resolve_credentials(args) -> tuple[str, str]:
    """Get login/password from args → .env → interactive prompt."""
    email = args.email or os.getenv("DECYPHA_EMAIL") or ""
    password = args.password or os.getenv("DECYPHA_PASSWORD") or ""

    if not email:
        email = input("Decypha login: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("Decypha password: ")

    if not email or not password:
        logger.error("Login and password are required.")
        sys.exit(1)

    return email, password


def download_from_url(page, url: str, download_dir: Path) -> list[Path]:
    """Navigate to a custom URL and attempt downloads."""
    from decypha.downloader import click_download_buttons, scrape_direct_links
    logger.info("Navigating to custom URL: %s", url)
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2000)
    saved = []
    saved += click_download_buttons(page, download_dir / "custom")
    saved += scrape_direct_links(page, download_dir / "custom")
    return saved


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    # --list-sections
    if args.list_sections:
        print("\nAvailable Decypha sections:")
        for name, path in DATA_SECTIONS.items():
            print(f"  {name:<20} -> https://www.decypha.com{path}")
        print()
        return

    # Require at least one action
    if not (args.morocco or args.all or args.section or args.company or args.url):
        parser.print_help()
        sys.exit(0)

    # Resolve settings
    email, password = resolve_credentials(args)
    headless = not args.no_headless
    download_dir = Path(
        args.download_dir or os.getenv("DOWNLOAD_DIR", "./downloads")
    ).resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = download_dir / "_raw"
    browser_path   = os.getenv("BROWSER_PATH") or None
    chrome_profile = os.getenv("CHROME_PROFILE") or None

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("Download directory : %s", download_dir)
    if chrome_profile:
        logger.info("Mode               : Chrome profile (session existante)")
    else:
        logger.info("Mode               : Login email/password")

    all_files: list[Path] = []

    with sync_playwright() as pw:
        browser, context, page = get_authenticated_page(
            pw,
            email=email,
            password=password,
            headless=headless,
            download_dir=str(raw_dir),
            browser_path=browser_path,
            chrome_profile=chrome_profile,
        )

        try:
            # ── Morocco financials (main use-case) ────────────────────────────
            if args.morocco:
                morocco_dir = download_dir / "morocco"
                results = run_morocco_financials(
                    page,
                    base_dir=morocco_dir,
                    debug=args.debug,
                    max_companies=args.limit,
                    company_filter=args.filter,
                )
                print_morocco_results(results, morocco_dir)

                # Flatten all downloaded paths for the summary below
                for r in results:
                    all_files.extend(Path(p) for p in r.downloaded)

            # ── Generic actions ───────────────────────────────────────────────
            elif args.all:
                all_files = run_full_download(page, raw_dir)

            elif args.section:
                if args.section not in DATA_SECTIONS:
                    logger.error(
                        "Unknown section '%s'. Valid: %s",
                        args.section,
                        ", ".join(DATA_SECTIONS),
                    )
                    sys.exit(1)
                all_files = download_section(
                    page,
                    args.section,
                    DATA_SECTIONS[args.section],
                    raw_dir,
                )

            elif args.company:
                all_files = download_company_data(page, args.company, raw_dir)

            elif args.url:
                all_files = download_from_url(page, args.url, raw_dir)

        finally:
            context.close()
            browser.close()

    logger.info("Total files downloaded: %d", len(all_files))

    # Organise generic downloads (Morocco files are already organised)
    if not args.morocco and not args.no_organize and all_files:
        logger.info("Organising downloaded files...")
        organized = organize_directory(raw_dir, download_dir)
        logger.info("Organised %d file(s).", len(organized))

    # File-type summary
    counts = summary_report(download_dir)
    if counts:
        print("\n--- Download summary ---")
        for cat, count in sorted(counts.items()):
            print(f"  {cat:<12} : {count} file(s)")
        print(f"  {'TOTAL':<12} : {sum(counts.values())} file(s)")

    if args.tree:
        print_tree(download_dir)

    print(f"\nAll done. Files saved to: {download_dir}\n")


if __name__ == "__main__":
    main()
