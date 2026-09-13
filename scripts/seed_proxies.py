#!/usr/bin/env python3
"""Seed the proxy pool from a file, env var, or inline list.

Usage:
    python scripts/seed_proxies.py proxies.txt
    python scripts/seed_proxies.py http://u:p@host:1 http://u:p@host:2 ...
    PROXY_LIST_FILE=proxies.txt python scripts/seed_proxies.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings  # noqa: E402
from src.proxy.providers.generic import load_proxies  # noqa: E402
from src.storage.database import create_tables, dispose_engine, init_engine  # noqa: E402
from src.storage.models import ProxyRow  # noqa: E402


async def main(argv: list[str]) -> int:
    settings = get_settings()
    explicit = [a for a in argv if not a.endswith(".txt") and "/" in a]
    file_args = [a for a in argv if a.endswith(".txt")]
    proxies = load_proxies(
        explicit=explicit or None,
        list_file=file_args[0] if file_args else settings.get("proxy.list_file"),
    )
    if not proxies:
        print("no proxies provided (pass a file, URLs, or set PROXY_LIST_FILE)")
        return 1

    init_engine(settings.database_url)
    await create_tables()
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(expire_on_commit=False)
    async with session_factory() as session:
        existing = {row.url for row in
                    (await session.execute(select(ProxyRow))).scalars()}
        added = 0
        for url in proxies:
            if url in existing:
                continue
            tier = "mobile" if any(k in url.lower() for k in ("mobile", "lte")) else (
                "residential" if "residential" in url.lower() else "datacenter")
            session.add(ProxyRow(url=url, tier=tier, healthy=True))
            added += 1
        await session.commit()
    await dispose_engine()
    print(f"seeded {added} new proxies ({len(proxies)} provided, "
          f"{len(proxies) - added} already known)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
