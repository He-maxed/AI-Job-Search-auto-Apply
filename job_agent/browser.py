from __future__ import annotations

import re
from typing import Any

MANUAL_CHALLENGE_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "bot check",
    "bot detection",
    "verify you are human",
    "security check",
    "mfa",
    "two-factor",
    "two factor",
    "2fa",
    "authenticator",
    "otp",
    "one-time code",
    "one time code",
    "verification code",
    "sms code",
    "challenge",
)


class BrowserError(RuntimeError):
    """Browser automation could not be set up or completed safely."""


class BrowserRunner:
    """The only surface an assistant may use on a page.

    Deliberately excludes any way to click buttons, submit forms, or confirm
    actions. The final submission must remain a manual human action.
    """

    def open(self, url: str) -> None:
        raise NotImplementedError

    def detect_manual_challenge(self) -> str | None:
        raise NotImplementedError

    def fill_text(self, hints: list[str], value: str) -> bool:
        raise NotImplementedError

    def set_checkbox(self, hints: list[str], checked: bool) -> bool:
        raise NotImplementedError

    def attach_file(self, hints: list[str], path: str, index: int) -> bool:
        raise NotImplementedError

    def page_text(self) -> str:
        raise NotImplementedError

    def keep_open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _contains_hint(text: str, hints: list[str]) -> bool:
    normalized = _normalize(text)
    return any(_normalize(hint) in normalized for hint in hints)


class PlaywrightBrowser(BrowserRunner):
    """Smallest reliable real browser via Playwright driving system Chrome."""

    def __init__(self, *, headless: bool = False, timeout_ms: int = 60000, page_load_timeout: int = 90000):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - exercised in fallback tests
            raise BrowserError(
                "Playwright is not installed. Install it with:\n"
                "  python -m pip install playwright\n"
                "and use the installed Chrome (or run: python -m playwright install chromium)."
            ) from exc
        self._playwright = sync_playwright().start()
        self._browser: Any = None
        launch_errors: list[str] = []
        for channel in ("chrome", None):
            try:
                if channel:
                    self._browser = self._playwright.chromium.launch(headless=headless, channel=channel)
                else:
                    self._browser = self._playwright.chromium.launch(headless=headless)
                break
            except Exception as exc:  # pragma: no cover - environment dependent
                launch_errors.append(f"{channel or 'bundled chromium'}: {exc}")
        if self._browser is None:
            self._playwright.stop()
            raise BrowserError(
                "Could not launch a browser. " + " | ".join(launch_errors)
            )
        self._context = self._browser.new_context(viewport={"width": 1440, "height": 900})
        self._page = self._context.new_page()
        self._timeout_ms = timeout_ms
        self._page_load_timeout = page_load_timeout

    def open(self, url: str) -> None:
        try:
            self._page.goto(url, timeout=self._page_load_timeout, wait_until="domcontentloaded")
        except Exception as exc:
            raise BrowserError(f"Could not open {url}: {exc}") from exc

    def _locate(self, hints: list[str], kind: str = "input") -> Any | None:
        try:
            for hint in hints:
                selector = f"{kind}[name*=\"{hint}\" i], {kind}[placeholder*=\"{hint}\" i]"
                locator = self._page.locator(selector).first
                if locator.count() and locator.is_visible():
                    return locator
            for hint in hints:
                try:
                    label = self._page.get_by_label(hint, exact=False).first
                    if label.count():
                        target = label.locator("input, textarea, select").first
                        if target.count() and target.is_visible():
                            return target
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def fill_text(self, hints: list[str], value: str) -> bool:
        target = self._locate(hints, "input") or self._locate(hints, "textarea")
        if target is None:
            return False
        try:
            target.fill(value, timeout=self._timeout_ms)
            return True
        except Exception:
            return False

    def set_checkbox(self, hints: list[str], checked: bool) -> bool:
        try:
            for hint in hints:
                locator = self._page.locator(f"input[type=checkbox][aria-label*=\"{hint}\" i]").first
                if locator.count():
                    target = locator
                else:
                    target = (
                        self._page.get_by_label(hint, exact=False).first.locator("input[type=checkbox]").first
                        if self._page.get_by_label(hint, exact=False).first.count()
                        else self._page.locator(f"input[type=checkbox][name*=\"{hint}\" i]").first
                    )
                if target.count() and target.is_visible():
                    is_checked = target.is_checked()
                    if is_checked != checked:
                        target.check(timeout=self._timeout_ms)
                    return True
        except Exception:
            return False
        return False

    def attach_file(self, hints: list[str], path: str, index: int) -> bool:
        try:
            file_inputs = self._page.locator("input[type=file]")
            matched = file_inputs.first
            for i in range(file_inputs.count()):
                candidate = file_inputs.nth(i)
                attribute = " ".join(
                    (candidate.get_attribute("name") or "")
                    + " "
                    + (candidate.get_attribute("accept") or "")
                )
                if _contains_hint(attribute, hints):
                    matched = candidate
                    break
            else:
                if index >= file_inputs.count():
                    return False
                matched = file_inputs.nth(index)
            matched.first.set_input_files(path)
            return True
        except Exception:
            return False

    def detect_manual_challenge(self) -> str | None:
        try:
            text = self.page_text()
            url_text = self._page.url
            haystack = (url_text + " " + text)[:30000].lower()
        except Exception:
            return None
        for marker in MANUAL_CHALLENGE_MARKERS:
            if marker in haystack:
                return marker
        return None

    def page_text(self) -> str:
        try:
            return self._page.inner_text("body", timeout=30000)
        except Exception:
            return ""

    def keep_open(self) -> None:
        print("  The browser stays open for your review. Press Enter in the terminal to close it.")
        try:
            input()
        except EOFError:
            pass

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._playwright.stop()


def create_runner(*, headless: bool = False) -> BrowserRunner:
    """Build the real runner. Raises BrowserError when unavailable."""
    return PlaywrightBrowser(headless=headless)