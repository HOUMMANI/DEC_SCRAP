"""
Authentication module for Decypha.
Handles login and session management via Playwright.
"""

import os
import json
import logging
from pathlib import Path
from playwright.sync_api import Page, BrowserContext, sync_playwright

logger = logging.getLogger(__name__)

DECYPHA_URL = "https://www.decypha.com"
LOGIN_URL = f"{DECYPHA_URL}/login"
SESSION_FILE = Path(".session.json")


def save_session(context: BrowserContext) -> None:
    """Save cookies/session to disk for reuse."""
    cookies = context.cookies()
    SESSION_FILE.write_text(json.dumps(cookies, indent=2))
    logger.info("Session saved to %s", SESSION_FILE)


def load_session(context: BrowserContext) -> bool:
    """Load a previously saved session. Returns True if loaded."""
    if not SESSION_FILE.exists():
        return False
    try:
        cookies = json.loads(SESSION_FILE.read_text())
        context.add_cookies(cookies)
        logger.info("Session loaded from %s", SESSION_FILE)
        return True
    except Exception as exc:
        logger.warning("Could not load session: %s", exc)
        return False


def is_logged_in(page: Page) -> bool:
    """Check if the current page reflects a logged-in state."""
    try:
        # Decypha shows a user dashboard / profile icon when logged in
        page.goto(DECYPHA_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)
        url = page.url
        # If redirected to login page we are NOT logged in
        if "/login" in url or "/signin" in url:
            return False
        # Look for indicators that suggest an authenticated session
        indicators = [
            "a[href*='logout']",
            "a[href*='signout']",
            "[class*='user-menu']",
            "[class*='profile']",
            "[data-testid*='user']",
            "button:has-text('Logout')",
            "button:has-text('Sign out')",
        ]
        for selector in indicators:
            if page.locator(selector).count() > 0:
                return True
        return False
    except Exception as exc:
        logger.debug("is_logged_in check failed: %s", exc)
        return False


def login(page: Page, email: str, password: str) -> bool:
    """
    Perform login on Decypha.
    Returns True on success, False otherwise.
    """
    logger.info("Navigating to login page: %s", LOGIN_URL)
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(1500)

    # --- Fill email ---
    email_selectors = [
        "input[type='email']",
        "input[name='email']",
        "input[placeholder*='email' i]",
        "input[id*='email' i]",
    ]
    filled_email = False
    for sel in email_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            loc.fill(email)
            filled_email = True
            logger.debug("Filled email via selector: %s", sel)
            break

    if not filled_email:
        logger.error("Could not find email input field on %s", page.url)
        return False

    # --- Fill password ---
    password_selectors = [
        "input[type='password']",
        "input[name='password']",
        "input[placeholder*='password' i]",
        "input[id*='password' i]",
    ]
    filled_password = False
    for sel in password_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            loc.fill(password)
            filled_password = True
            logger.debug("Filled password via selector: %s", sel)
            break

    if not filled_password:
        logger.error("Could not find password input field on %s", page.url)
        return False

    # --- Submit form ---
    submit_selectors = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Login')",
        "button:has-text('Sign in')",
        "button:has-text('Log in')",
        "button:has-text('Se connecter')",
    ]
    clicked = False
    for sel in submit_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            loc.click()
            clicked = True
            logger.debug("Clicked submit via selector: %s", sel)
            break

    if not clicked:
        # Last resort: press Enter in the password field
        page.keyboard.press("Enter")
        logger.debug("Pressed Enter to submit form")

    # Wait for navigation
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        page.wait_for_timeout(3000)

    current_url = page.url
    logger.info("After login attempt, URL: %s", current_url)

    # Check for error messages
    error_selectors = [
        "[class*='error']",
        "[class*='alert']",
        "[role='alert']",
        "p:has-text('Invalid')",
        "p:has-text('incorrect')",
    ]
    for sel in error_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            text = loc.inner_text()
            if text.strip():
                logger.warning("Login error message detected: %s", text.strip())

    # Success = not on login page anymore
    if "/login" not in current_url and "/signin" not in current_url:
        logger.info("Login successful.")
        return True

    logger.error("Login failed - still on login page.")
    return False


def get_authenticated_page(
    playwright_instance,
    email: str,
    password: str,
    headless: bool = True,
    download_dir: str = "./downloads",
    browser_path: str | None = None,
):
    """
    Launch a browser, restore or create a session, and return
    (browser, context, page) ready for scraping.

    browser_path: optional explicit path to a Chromium/Chrome executable.
                  If None, Playwright uses its own bundled browser.
                  Can also be set via BROWSER_PATH env variable.
    """
    Path(download_dir).mkdir(parents=True, exist_ok=True)

    exe = browser_path or os.getenv("BROWSER_PATH") or None

    launch_kwargs: dict = {
        "headless": headless,
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    }
    if exe:
        launch_kwargs["executable_path"] = exe
        logger.info("Using custom browser: %s", exe)

    browser = playwright_instance.chromium.launch(**launch_kwargs)
    context = browser.new_context(
        accept_downloads=True,
        viewport={"width": 1280, "height": 900},
    )
    context.set_default_timeout(30_000)
    page = context.new_page()

    # Try loading a saved session first
    session_loaded = load_session(context)

    if session_loaded and is_logged_in(page):
        logger.info("Resumed existing session successfully.")
        return browser, context, page

    # Fresh login
    logger.info("Starting fresh login...")
    success = login(page, email, password)
    if not success:
        raise RuntimeError("Authentication failed. Check credentials.")

    save_session(context)
    return browser, context, page
