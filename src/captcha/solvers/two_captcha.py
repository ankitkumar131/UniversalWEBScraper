"""2Captcha solver client (plain HTTP via httpx — no extra dependency).

Supports hCaptcha, reCAPTCHA v2 and Cloudflare Turnstile. Enabled only when
the user provides their own TWOCAPTCHA_API_KEY; off by default."""
from __future__ import annotations

import asyncio

import httpx
import structlog

log = structlog.get_logger(__name__)

_SUBMIT = "https://2captcha.com/in.php"
_RESOLVE = "https://2captcha.com/res.php"
_POLL_INTERVAL = 5.0
_MAX_WAIT = 180.0


class TwoCaptchaSolver:
    name = "twocaptcha"

    def __init__(self, api_key: str, max_wait: float = _MAX_WAIT):
        self.api_key = api_key
        self.max_wait = max_wait

    async def _get(self, url: str, params: dict) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            text = resp.text
        parts = text.split("|", 1)
        return {"ok": parts[0] == "OK", "value": parts[1] if len(parts) > 1 else text}

    async def solve(self, *, kind: str, sitekey: str, page_url: str) -> str:
        """Returns the solution token. kind: hcaptcha|recaptcha|turnstile."""
        method = {"hcaptcha": "hcaptcha", "recaptcha": "userrecaptcha",
                  "turnstile": "turnstile"}.get(kind)
        if not method:
            raise ValueError(f"unsupported captcha kind: {kind}")
        submit = await self._get(_SUBMIT, {
            "key": self.api_key, "method": method,
            "sitekey": sitekey, "pageurl": page_url, "json": 0,
        })
        if not submit["ok"]:
            raise RuntimeError(f"2captcha submit failed: {submit['value']}")
        task_id = submit["value"]
        log.info("captcha_submitted", kind=kind, task_id=task_id)

        waited = 0.0
        while waited < self.max_wait:
            await asyncio.sleep(_POLL_INTERVAL)
            waited += _POLL_INTERVAL
            result = await self._get(_RESOLVE, {"key": self.api_key, "action": "get", "id": task_id})
            if result["ok"]:
                log.info("captcha_solved", kind=kind, waited_seconds=waited)
                return result["value"]
            if result["value"] != "CAPCHA_NOT_READY":
                raise RuntimeError(f"2captcha error: {result['value']}")
        raise TimeoutError("captcha solving timed out")
