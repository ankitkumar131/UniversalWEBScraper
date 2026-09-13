"""Human-like behavior simulation: Bézier mouse curves, variable scrolling,
realistic delays (normal distribution, sd.txt "Behavior Simulator")."""
from __future__ import annotations

import asyncio
import math
import random

import structlog

log = structlog.get_logger(__name__)


async def human_delay(mean_ms: float = 1200, sigma_ms: float = 600, lo: float = 0.25,
                      hi: float = 3.0) -> None:
    """Random pause between actions, normal distribution clamped to [lo, hi] s."""
    val = random.gauss(mean_ms, sigma_ms) / 1000.0
    await asyncio.sleep(min(max(val, lo), hi))


async def human_mouse_move(page, target_x: int | None = None, target_y: int | None = None,
                           steps: int = 12) -> None:
    """Move the mouse along a quadratic Bézier curve (never linear)."""
    try:
        w, h = page.viewport_size.get("width", 1280), page.viewport_size.get("height", 800)
    except Exception:
        w, h = 1280, 800
    x0, y0 = random.randint(0, w // 2), random.randint(0, h // 2)
    x1 = target_x if target_x is not None else random.randint(0, w - 1)
    y1 = target_y if target_y is not None else random.randint(0, h - 1)
    cx, cy = (x0 + x1) / 2 + random.uniform(-120, 120), (y0 + y1) / 2 + random.uniform(-90, 90)

    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        bx = int(mt * mt * x0 + 2 * mt * t * cx + t * t * x1)
        by = int(mt * mt * y0 + 2 * mt * t * cy + t * t * y1)
        try:
            await page.mouse.move(bx, by, steps=1)
        except Exception:
            return
        await asyncio.sleep(random.uniform(0.01, 0.04))
    # occasional overshoot-and-correct, like a real hand
    if random.random() < 0.25:
        try:
            await page.mouse.move(x1 + random.randint(-14, 14), y1 + random.randint(-10, 10))
            await asyncio.sleep(random.uniform(0.03, 0.09))
            await page.mouse.move(x1, y1)
        except Exception:
            pass


async def human_scroll(page, total_pixels: int = 1400) -> None:
    """Scroll with variable speed and micro-pauses."""
    remaining = total_pixels
    while remaining > 0:
        chunk = min(remaining, random.randint(180, 420))
        try:
            await page.mouse.wheel(0, chunk)
        except Exception:
            return
        remaining -= chunk
        await asyncio.sleep(random.uniform(0.15, 0.5))


async def human_type(page, text: str, selector: str | None = None) -> None:
    """Type with variable inter-key delay (~90-220ms, occasional longer pause)."""
    element = None
    if selector:
        element = page.locator(selector).first
        await element.click()
    for ch in text:
        target = element if element is not None else page.keyboard
        await target.type(ch, delay=random.randint(60, 180))
        if random.random() < 0.05:
            await asyncio.sleep(random.uniform(0.3, 0.8))


async def viewport_focus_jitter(page) -> None:
    """Simulate focus/blur events a real user generates."""
    try:
        if random.random() < 0.3:
            await page.evaluate("() => window.dispatchEvent(new Event('blur'))")
            await asyncio.sleep(random.uniform(0.2, 0.7))
            await page.evaluate("() => window.dispatchEvent(new Event('focus'))")
    except Exception:
        pass


def jittered_wait_ms(base_ms: int, spread: float = 0.4) -> int:
    """Add random jitter to a delay to avoid periodic patterns."""
    return max(50, int(base_ms * (1 + random.uniform(-spread, spread))))


def bezier_point(t: float, p0: tuple, p1: tuple, p2: tuple) -> tuple[int, int]:
    """Pure helper (unit-tested): quadratic Bézier point at parameter t."""
    mt = 1 - t
    x = mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0]
    y = mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]
    return int(round(x)), int(round(y))


def gaussian_delay_ms(mean_ms: float, sigma_ms: float, lo_ms: float, hi_ms: float) -> float:
    """Pure helper (unit-tested): clamped normal delay in ms."""
    val = random.gauss(mean_ms, sigma_ms)
    return min(max(val, lo_ms), hi_ms)


_ = math  # keep math import for potential extensions
