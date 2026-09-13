"""Browser instance pool manager.

Launches one Chromium via Playwright (its own download when available, or the
self-contained runtime from scripts/setup_browser.py — see data/browser_runtime.json),
enforces a concurrency limit on contexts, and recycles the browser after N
pages to bound memory."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import structlog

from src.browser.stealth.fingerprint import default_fingerprint
from src.browser.stealth.patches import stealth_init_script

log = structlog.get_logger(__name__)

_BASE_ARGS = [
    "--no-sandbox", "--no-zygote", "--disable-setuid-sandbox",
    "--disable-dev-shm-usage", "--disable-gpu", "--disable-blink-features=AutomationControlled",
]


class BrowserPool:
    def __init__(self, settings, max_contexts: int | None = None):
        self.settings = settings
        self._max_contexts = max_contexts or int(settings.get("engine.max_contexts", 4))
        self._sem = asyncio.Semaphore(self._max_contexts)
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._pages_served = 0
        self._recycle_after = int(settings.get("engine.recycle_browser_after_pages", 200))
        self._launch_args: list[str] | None = None
        self._launch_env: dict[str, str] | None = None
        self._executable: str | None = None
        self._closed = False
        # tolerate broken/intercepted TLS (corporate MITM proxies); can be
        # flipped at runtime after a cert failure for auto-recovery
        self.ignore_https_errors = bool(settings.get("engine.ignore_https_errors", False))

    @property
    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def _resolve_runtime(self) -> None:
        """Find a usable Chromium executable: Playwright's own install first,
        then the bundled runtime descriptor (data/browser_runtime.json)."""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                executable = pw.chromium.executable_path
                if executable and Path(executable).exists():
                    self._executable = executable
                    log.info("browser_pool_using_playwright_chromium", path=executable)
                    return
        except Exception:
            pass

        cfg_path = self.settings.repo_root / "data" / "browser_runtime.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            if Path(cfg["executable"]).exists():
                self._executable = cfg["executable"]
                lib_dirs = ":".join(cfg.get("lib_dirs", []))
                env = {**os.environ, "HOME": "/tmp"}
                if lib_dirs:
                    env["LD_LIBRARY_PATH"] = lib_dirs
                if cfg.get("font_dir"):
                    env["FONTCONFIG_PATH"] = cfg["font_dir"]
                self._launch_env = env
                log.info("browser_pool_using_bundled_runtime", path=self._executable)
                return
        self._executable = None  # let Playwright resolve (and fail loudly if missing)

    async def start(self) -> bool:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return True
            if self._closed:
                return False
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                log.warning("playwright_not_installed")
                return False
            if self._launch_args is None:
                self._resolve_runtime()
            self._pw = await async_playwright().start()
            engine = self.settings.get("engine", {})
            kwargs: dict[str, Any] = {"headless": engine.get("headless", True), "args": _BASE_ARGS}
            if self._executable:
                kwargs["executable_path"] = self._executable
            if self._launch_env:
                kwargs["env"] = self._launch_env
            try:
                self._browser = await self._pw.chromium.launch(**kwargs)
            except Exception as e:
                log.error("browser_launch_failed", error=str(e))
                await self._pw.stop()
                self._pw = None
                return False
            log.info("browser_pool_started", contexts=self._max_contexts)
            return True

    async def new_context(self, proxy_url: str | None = None, fingerprint: dict | None = None):
        """Acquire a slot and create a stealth-hardened browser context."""
        await self.start()
        assert self._browser is not None, "browser failed to start"
        await self._sem.acquire()
        try:
            fp = fingerprint or default_fingerprint()
            kwargs: dict[str, Any] = {
                "user_agent": fp["user_agent"],
                "viewport": {"width": fp.get("viewport_width", 1440),
                             "height": fp.get("viewport_height", 900)},
                "screen": {"width": fp.get("screen_width", 1920),
                           "height": fp.get("screen_height", 1080)},
                "locale": fp.get("locale", "en-US"),
                "timezone_id": fp.get("timezone", "America/New_York"),
                "color_scheme": "light",
                "java_script_enabled": True,
                "ignore_https_errors": self.ignore_https_errors,
            }
            if proxy_url:
                from playwright.async_api import ProxySettings

                parsed = _parse_proxy(proxy_url)
                kwargs["proxy"] = ProxySettings(**parsed)
            context = await self._browser.new_context(**kwargs)
            await context.add_init_script(stealth_init_script(fp))

            # block media for speed when configured
            if self.settings.get("engine.block_media"):
                await context.route(
                    "**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2,mp4,webm}",
                    lambda route: route.abort(),
                )
            return context, fp
        except Exception:
            self._sem.release()
            raise

    def release(self) -> None:
        self._sem.release()
        self._pages_served += 1

    async def maybe_recycle(self) -> None:
        if self._pages_served >= self._recycle_after:
            async with self._lock:
                if self._pages_served >= self._recycle_after and self._browser:
                    log.info("browser_pool_recycling", pages=self._pages_served)
                    try:
                        await self._browser.close()
                    except Exception:
                        pass
                    self._browser = None
                    self._pages_served = 0

    @property
    def pages_served(self) -> int:
        return self._pages_served

    async def stop(self) -> None:
        async with self._lock:
            self._closed = True
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    pass
            self._browser = None
            self._pw = None
        log.info("browser_pool_stopped")


def _parse_proxy(url: str) -> dict[str, str]:
    from urllib.parse import urlsplit

    parts = urlsplit(url if "://" in url else f"http://{url}")
    return {
        "server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port or 80}",
        **({"username": parts.username} if parts.username else {}),
        **({"password": parts.password} if parts.password else {}),
    }
