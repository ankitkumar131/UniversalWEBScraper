"""CLI tool: submit scrape jobs from the command line.

Examples:
  python -m src.cli scrape https://quotes.toscrape.com/
  python -m src.cli scrape https://news.ycombinator.com/ --schema '{"items":"tr.athing","fields":{"title":".titleline a"}}' --max-pages 1
  python -m src.cli scrape https://example.com --rendering full_browser --screenshot
  python -m src.cli jobs
  python -m src.cli job <job_id>
  python -m src.cli serve
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from src import __version__
from src.core.config import get_settings
from src.core.engine import ScrapeEngine
from src.core.job_manager import JobManager
from src.core.schemas import ExtractionConfig, PaginationConfig, ScrapeJobConfig, WaitStrategy
from src.storage.artifacts import ArtifactStore
from src.storage.database import create_tables, dispose_engine, init_engine, session_factory


def _json_arg(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        raise SystemExit(f"invalid JSON: {e}") from e


async def _run_scrape(args: argparse.Namespace) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    init_engine(settings.database_url)
    await create_tables()
    jobs = JobManager(session_factory(), ArtifactStore(settings.artifacts_dir))
    engine = ScrapeEngine.build(settings, job_manager=jobs)

    schema = _json_arg(args.schema)
    extraction = {}
    if schema:
        extraction = {"schema": schema}
    if args.llm_prompt:
        extraction |= {"llm_prompt": args.llm_prompt}
    if args.mode:
        extraction |= {"mode": args.mode}

    config = ScrapeJobConfig(
        rendering=args.rendering or "auto",
        wait_strategy=WaitStrategy(type="network_idle", timeout_ms=args.timeout_ms),
        extraction=ExtractionConfig(**extraction) if extraction else ExtractionConfig(),
        pagination=PaginationConfig(strategy=args.pagination, max_pages=args.max_pages),
        popup_handling=not args.no_popup_handling,
        respect_robots=False if args.ignore_robots else None,
    )
    job = ScrapeJob(url=args.url, config=config)
    print(f"job_id: {job.job_id}")
    try:
        job = await engine.run(job)
    finally:
        await engine.close()
        await dispose_engine()

    print(f"state:  {job.state.value}")
    print(f"stats:  {json.dumps(job.stats, indent=2)}")
    if job.error_log:
        print("errors:", file=sys.stderr)
        for err in job.error_log:
            print(f"  - {err}", file=sys.stderr)

    items, total = await jobs.get_results(job.job_id, limit=args.output_limit)
    if args.output:
        Path = __import__("pathlib").Path
        out = Path(args.output)
        if out.suffix == ".csv":
            import csv

            fields: list[str] = []
            for item in items:
                fields.extend(k for k in item["data"] if k not in fields)
            with out.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                for item in items:
                    writer.writerow(item["data"])
        else:
            out.write_text(json.dumps([i["data"] for i in items], indent=2, ensure_ascii=False),
                           encoding="utf-8")
        print(f"wrote {len(items)} items -> {out}")
    else:
        print(json.dumps([i["data"] for i in items], indent=2, ensure_ascii=False))
    return 0 if job.state.value in ("completed", "partially_completed") else 1


async def _list_jobs(args: argparse.Namespace) -> int:
    settings = get_settings()
    init_engine(settings.database_url)
    await create_tables()
    jobs = JobManager(session_factory())
    items, total = await jobs.list_jobs(state=args.state, limit=args.limit)
    print(f"total: {total}")
    for job in items:
        print(f"{job.job_id[:8]}  {job.state.value:<14} {job.created_at:%Y-%m-%d %H:%M}  "
              f"{job.stats.get('items_extracted', 0):>5} items  {job.url[:70]}")
    await dispose_engine()
    return 0


async def _show_job(job_id: str) -> int:
    settings = get_settings()
    init_engine(settings.database_url)
    jobs = JobManager(session_factory())
    job = await jobs.get(job_id)
    if job is None:
        print(f"job {job_id} not found", file=sys.stderr)
        return 1
    print(json.dumps({
        "job_id": job.job_id, "url": job.url, "state": job.state.value,
        "stats": job.stats, "errors": job.error_log,
    }, indent=2))
    items, total = await jobs.get_results(job.job_id, limit=20)
    print(f"results: {total} (showing {len(items)})")
    for item in items:
        print(" ", json.dumps(item["data"], ensure_ascii=False)[:160])
    await dispose_engine()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uwscraper",
                                     description="Universal Web Scraper CLI")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="scrape a URL now")
    scrape.add_argument("url")
    scrape.add_argument("--schema", help='extraction schema JSON, e.g. \'{"items":".product","fields":{"name":"h3"}}\'')
    scrape.add_argument("--mode", choices=["css_selectors", "xpath", "llm_auto", "hybrid"])
    scrape.add_argument("--llm-prompt", help="natural-language extraction instructions (LLM mode)")
    scrape.add_argument("--rendering", choices=["auto", "lightweight", "full_browser"])
    scrape.add_argument("--pagination", default="auto_detect",
                        choices=["auto_detect", "next_button", "url_pattern",
                                 "infinite_scroll", "load_more", "none"])
    scrape.add_argument("--max-pages", type=int, default=10)
    scrape.add_argument("--timeout-ms", type=int, default=30000)
    scrape.add_argument("--no-popup-handling", action="store_true")
    scrape.add_argument("--ignore-robots", action="store_true")
    scrape.add_argument("--screenshot", action="store_true")
    scrape.add_argument("--output", "-o", help="write items to file (json or csv by extension)")
    scrape.add_argument("--output-limit", type=int, default=1000)
    scrape.set_defaults(func=_run_scrape)

    jobs_cmd = sub.add_parser("jobs", help="list jobs")
    jobs_cmd.add_argument("--state")
    jobs_cmd.add_argument("--limit", type=int, default=30)
    jobs_cmd.set_defaults(func=_list_jobs)

    job_cmd = sub.add_parser("job", help="show job detail")
    job_cmd.add_argument("job_id")
    job_cmd.set_defaults(func=lambda args: _show_job(args.job_id))

    serve = sub.add_parser("serve", help="run the API server + dashboard")
    serve.set_defaults(func=lambda args: _serve())
    return parser


def _serve() -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run("src.api.main:app", host=settings.get("app.host", "0.0.0.0"),
                port=int(settings.get("app.port", 8000)))
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    func = args.func
    if asyncio.iscoroutinefunction(func):
        if func is _show_job or getattr(args, "command", None) == "job":
            sys.exit(asyncio.run(_show_job(args.job_id)))
        sys.exit(asyncio.run(func(args)))
    sys.exit(func(args))


if __name__ == "__main__":
    main()
