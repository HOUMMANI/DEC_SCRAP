"""
Authentication module for Decypha.
Supports two modes:
  1. Chrome profile mode  → uses your existing logged-in Chrome session (recommended)
  2. Login mode           → automates login with email/password
"""

import os
import json
import logging
from pathlib import Path
from playwright.sync_api import Page, BrowserContext, BrowserType

logger = logging.getLogger(__name__)

DECYPHA_URL = "https://www.decypha.com"
SESSION_FILE = Path(".session.json")

# Default Chrome profile paths per OS
_CHROME_PROFILES = {
    "win32":  r"C:\Users\{user}\AppData\Local\Google\Chrome\User Data",
    "darwin": "/Users/{user}/Library/Application Support/Google/Chrome",
    "linux":  "/home/{user}/.config/google-chrome",
}

_CHROME_EXE = {
    "win32":  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "darwin": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "linux":  "/usr/bin/google-chrome",
}


def _default_chrome_profile() -> str | None:
    """Return the default Chrome user-data-dir for this OS."""
    import sys, getpass
    plat = sys.platform
    template = _CHROME_PROFILES.get(plat if plat != "win32" else "win32")
    if not template:
        return None
    return template.replace("{user}", getpass.getuser())


def _default_chrome_exe() -> str | None:
    """Return the default Chrome executable path for this OS."""
    import sys
    plat = sys.platform
    key = plat if plat in _CHROME_EXE else ("win32" if "win" in plat else "linux")
    path = _CHROME_EXE.get(key, "")
    return path if Path(path).exists() else None


# ── MODE 1 : Chrome profile (session already logged in) ──────────────────────

def get_page_from_chrome_profile(
    playwright_instance,
    chrome_profile_dir: str | None = None,
    chrome_exe: str | None = None,
    headless: bool = False,
    download_dir: str = "./downloads",
):
    """
    Open Chrome using your existing profile (already logged into Decypha).
    Chrome must be CLOSED before calling this.

    Returns (browser, context, page).
    """
    Path(download_dir).mkdir(parents=True, exist_ok=True)

    profile_dir = (
        chrome_profile_dir
        or os.getenv("CHROME_PROFILE")
        or _default_chrome_profile()
    )
    exe = (
        chrome_exe
        or os.getenv("BROWSER_PATH")
        or _default_chrome_exe()
    )

    if not profile_dir:
        raise RuntimeError(
            "Chrome profile directory not found. "
            "Set CHROME_PROFILE in your .env file."
        )

    logger.info("Opening Chrome profile: %s", profile_dir)
    if exe:
        logger.info("Chrome executable: %s", exe)

    launch_kwargs: dict = {
        "user_data_dir": profile_dir,
        "headless": headless,
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        "accept_downloads": True,
        "viewport": {"width": 1280, "height": 900},
    }
    if exe:
        launch_kwargs["executable_path"] = exe

    # launch_persistent_context returns a BrowserContext directly
    context = playwright_instance.chromium.launch_persistent_context(**launch_kwargs)
    page = context.new_page()
    return None, context, page   # browser=None for persistent context


# ── MODE 2 : Classic login ────────────────────────────────────────────────────

def save_session(context: BrowserContext) -> None:
    cookies = context.cookies()
    SESSION_FILE.write_text(json.dumps(cookies, indent=2))
    logger.info("Session saved to %s", SESSION_FILE)


def load_session(context: BrowserContext) -> bool:
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
    try:
        page.goto(DECYPHA_URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2000)
        if "/login" in page.url or "/signin" in page.url:
            return False
        for sel in [
            "a[href*='logout']", "a[href*='signout']",
            "[class*='user-menu']", "[class*='profile']",
            "button:has-text('Logout')", "button:has-text('Sign out')",
        ]:
            if page.locator(sel).count() > 0:
                return True
        return False
    except Exception as exc:
        logger.debug("is_logged_in check failed: %s", exc)
        return False


def login(page: Page, email: str, password: str) -> bool:
    logger.info("Navigating to login page...")
    page.goto(f"{DECYPHA_URL}/login", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(1500)

    for sel in ["input[type='email']", "input[name='email']",
                "input[type='text']", "input[name='username']",
                "input[placeholder*='email' i]", "input[placeholder*='login' i]"]:
        loc = page.locator(sel).first
        if loc.count():
            loc.fill(email)
            break
    else:
        logger.error("No email/username field found")
        return False

    for sel in ["input[type='password']", "input[name='password']"]:
        loc = page.locator(sel).first
        if loc.count():
            loc.fill(password)
            break
    else:
        logger.error("No password field found")
        return False

    for sel in ["button[type='submit']", "input[type='submit']",
                "button:has-text('Login')", "button:has-text('Sign in')",
                "button:has-text('Log in')"]:
        loc = page.locator(sel).first
        if loc.count():
            loc.click()
            break
    else:
        page.keyboard.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        page.wait_for_timeout(3000)

    if "/login" not in page.url and "/signin" not in page.url:
        logger.info("Login successful.")
        return True

    logger.error("Login failed — still on login page.")
    return False


def get_authenticated_page(
    playwright_instance,
    email: str = "",
    password: str = "",
    headless: bool = True,
    download_dir: str = "./downloads",
    browser_path: str | None = None,
    chrome_profile: str | None = None,
):
    """
    Unified entry point.
    - If chrome_profile is set (or CHROME_PROFILE in env): use existing Chrome session.
    - Otherwise: do a fresh login with email/password.
    """
    # Chrome profile mode
    profile = chrome_profile or os.getenv("CHROME_PROFILE") or ""
    if profile:
        return get_page_from_chrome_profile(
            playwright_instance,
            chrome_profile_dir=profile,
            chrome_exe=browser_path or os.getenv("BROWSER_PATH") or None,
            headless=headless,
            download_dir=download_dir,
        )

    # Classic login mode
    Path(download_dir).mkdir(parents=True, exist_ok=True)
    exe = browser_path or os.getenv("BROWSER_PATH") or None
    launch_kwargs: dict = {"headless": headless, "args": ["--no-sandbox"]}
    if exe:
        launch_kwargs["executable_path"] = exe

    browser = playwright_instance.chromium.launch(**launch_kwargs)
    context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
    context.set_default_timeout(30_000)
    page = context.new_page()

    if load_session(context) and is_logged_in(page):
        logger.info("Resumed existing session.")
        return browser, context, page

    if not email or not password:
        raise RuntimeError("No Chrome profile and no credentials provided.")

    if not login(page, email, password):
        raise RuntimeError("Login failed. Check credentials.")

    save_session(context)
    return browser, context, page
