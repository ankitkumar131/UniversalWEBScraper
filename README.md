# 🕷️ Universal Web Scraper

A **resilient, scalable, intelligent web scraping platform** — the working implementation of the system design in [`sd.txt`](sd.txt).

Navigate to any website → auto-dismiss pop-ups/cookie walls → handle bot challenges → auto-paginate → extract structured data (selectors, JSON-LD, or LLM) → store it in a queryable format. Dashboard, REST API, and CLI included.

```
Client/Trigger (API · Dashboard · CLI · Celery)
        │
Orchestration (job queue · per-domain rate limiting · circuit breakers · domain difficulty scoring)
        │
Browser Engine Pool ── middleware pipeline per request ──────────────────┐
   1 proxy+identity → 2 stealth patches → 3 page load → 4 challenge     │
   solver → 5 popup/cookie dismissal → 6 content readiness →            │
   7 extraction (JSON-LD → selectors → API → LLM/heuristic) →           │
   8 pagination (next · URL pattern · infinite scroll · load-more · API) │
        │                                                                │
Data pipeline (dedupe → transform → validate → enrich)                   │
        │
Storage (SQLite/PostgreSQL results · artifact store · optional S3) · Monitoring (Prometheus /metrics, JSON logs)
```

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# (optional but recommended) browser engine:
.venv/bin/python -m playwright install chromium
# ↳ if Playwright's CDN is unreachable (locked-down network), use the
#   self-contained npm-bundled runtime instead:
.venv/bin/pip install brotli && .venv/bin/python scripts/setup_browser.py

# run the dashboard + API
.venv/bin/python -m src.cli serve          # → http://localhost:8000
```

Or with Docker: `docker compose up --build` (API + Celery worker + Redis).

### CLI

```bash
.venv/bin/python -m src.cli scrape https://quotes.toscrape.com/ --max-pages 3
# custom extraction schema:
.venv/bin/python -m src.cli scrape https://example.com/products \
  --schema '{"items": ".product", "fields": {"name": "h3", "price": ".price", "link": "a @href"}}'
.venv/bin/python -m src.cli scrape https://example.com --rendering full_browser --screenshot -o out.json
.venv/bin/python -m src.cli jobs            # list past jobs
```

## How it works

### Tiered rendering (cost ladder)

| Tier | Engine | When |
|---|---|---|
| 0 | `httpx` direct | default first try (`rendering: auto`) |
| 1 | `httpx` + proxy | after blocks at tier 0 |
| 2 | stealth Playwright | JS challenges, infinite scroll, load-more, empty HTML |
| 3 | stealth browser + proxy + CAPTCHA service | hardest sites |

The engine **auto-escalates** on challenge/block/empty signals and **remembers per-domain difficulty** (`easy`/`medium`/`hard`) in the DB, so the next job starts at the right tier. `rendering: full_browser` starts at tier 2/3 directly.

### Middleware pipeline (per request)

Every page load runs an ordered, composable pipeline (`src/middleware/`):

1. **proxy_injection** — proxy + paired identity (UA/screen/timezone/locale/WebGL consistent)
2. **stealth_patching** — init-script patches: `navigator.webdriver`, plugins, languages, `window.chrome`, canvas noise, WebGL vendor/renderer spoof, WebRTC leak guard, notifications auto-deny, JS dialogs neutralized
3. **page_loading** — wait strategies: `network_idle` / `domcontentloaded` / `selector` / `timeout` / `custom` (DOM-stability)
4. **challenge_solver** — Cloudflare/DataDome/PX/CAPTCHA detection (markers + status + elements) → wait-out JS challenges (real browser clears them in 3–5 s) → 2Captcha/Anti-Captcha solving (hCaptcha, reCAPTCHA, Turnstile) when a key is configured → escalate tier
5. **popup_handler** — 4-layer dismissal: known-pattern DB (`config/known_popups.yaml`) → generic overlay detection (fixed/high-z-index/dialog-role/token classes) → close-button click (selector + text + aria) → Escape → force-hide CSS + scroll unlock
6. **cookie_consent** — CMP database (`config/known_cookies.yaml`: OneTrust, CookieBot, TrustArc, Quantcast, Didomi, CookieYes, Osano, …) with accept/reject/dismiss strategies + direct consent-cookie injection fallback
7. **content_readiness** — waits for spinners/skeletons to disappear and item count to stabilize
8. **extraction** — hybrid: JSON-LD/microdata/OG → configured CSS/XPath selectors (with compound fallbacks and `@attr` refs) → intercepted API JSON payloads → LLM (any OpenAI-compatible endpoint) or deterministic heuristic fallback
9. **pagination** — auto-detects the type and drives it: next-button clicks (with content-change verification), URL patterns (`?page=`, `/page/N`, `?offset=`), infinite scroll, Load More, API replay. Hash-based dedupe catches loops and repeated items; termination on max pages/items/time/empty-streaks

### Post-processing & storage

`dedupe → transform (trim, URL resolution, number/date/price coercion) → validate (required fields) → enrich (source URL, domain, timestamp, job id)` → SQLite/PostgreSQL `job_results` + raw HTML/screenshot artifacts on disk (or S3/MinIO).

### Resilience

- **Retries** with exponential backoff + jitter; strategy escalation on every retry
- **Circuit breaker per domain** (failure-rate sliding window → open → half-open probe)
- **Per-domain rate limiting** (token bucket + jitter, robots.txt `Crawl-delay` aware, Redis-backed when configured)
- **robots.txt compliance on by default** (`RESPECT_ROBOTS=true`; per-job override available) — politeness is a feature, not an afterthought

## Configuration

Defaults in [`config/default.yaml`](config/default.yaml); override with env vars (`.env.example`):

| Variable | Meaning |
|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///…` (default) or `postgresql+asyncpg://…` |
| `REDIS_URL` | shared rate-limit/robots cache + Celery broker |
| `PROXY_LIST` / `PROXY_LIST_FILE` | proxy URLs (works with BrightData/Oxylabs/any HTTP proxy) |
| `CAPTCHA_PROVIDER=twocaptcha` + `TWOCAPTCHA_API_KEY` | CAPTCHA solving (off by default) |
| `LLM_PROVIDER=openai_compatible` + `OPENAI_API_KEY` (`OPENAI_BASE_URL`, `LLM_MODEL`) | LLM extraction (off by default; local Llama/Mistral endpoints work) |
| `RESPECT_ROBOTS` | robots.txt compliance (default `true`) |

### API (auto-docs at `/docs`)

```
POST   /api/jobs                 {url, rendering?, extraction?, pagination?, …} → 202 job
GET    /api/jobs?state=…         list jobs
GET    /api/jobs/{id}            job state/stats/errors
POST   /api/jobs/{id}/cancel     cancel running job
DELETE /api/jobs/{id}            delete job + results
GET    /api/jobs/{id}/results    extracted items
GET    /api/jobs/{id}/artifacts  raw HTML / screenshots
GET    /api/results?domain=&search=   cross-job result query
GET    /api/domains/{domain}     learned difficulty for a domain
GET    /api/health · /metrics    health / Prometheus
```

## Project layout

```
src/
├── api/            FastAPI app, routes, schemas, dashboard (static/)
├── browser/        pool, context factory, stealth (patches/fingerprint/behavior), interceptors
├── captcha/        detector, 2Captcha solver, token injector
├── core/           engine (orchestrator), job manager, schemas, config, politeness, circuit breaker
├── extraction/     selector / structured-data / LLM / API / hybrid extractors
├── middleware/     pipeline + page loader + challenge solver + popups + consent + readiness
├── monitoring/     metrics, structured logging, health
├── pagination/     detector + 5 strategies + dedupe
├── pipeline/       dedupe / transform / validate / enrich
├── proxy/          pool manager, rotator, health checker, identity, providers
├── queue/          local runner, Celery app/tasks, rate limiter
├── storage/        database (SQLAlchemy async), artifacts, redis cache
└── cli.py          command-line interface
config/             default.yaml + knowledge bases (popups, cookie CMPs, user agents, fingerprints)
kubernetes/         deployment/hpa/configmap/secrets manifests
tests/              56 unit + integration tests (pytest)
```

## Testing

```bash
.venv/bin/python -m pytest tests/ -q            # unit + lightweight integration (no browser)
.venv/bin/python -m pytest tests/ -q -m browser # + full browser pipeline test
```

The integration suite spins up a local HTTP site (paginated listings, JSON-LD, cookie banner, pagination loops) and runs the real engine end-to-end: extraction across pages, dedupe of looping pagination, robots.txt disallow, 404 handling, and the full browser middleware pipeline (modal dismissal, Load More, screenshots).

## Scaling

- **Single node**: `python -m src.cli serve` (in-process runner, SQLite). Fine for thousands of pages/day.
- **Scale-out**: `QUEUE_MODE=celery` + Redis broker → run N worker containers (`docker compose up --scale worker=5` or the K8s manifests with HPA on CPU/memory). Workers are stateless; per-domain politeness is shared through Redis.
- **Browser workers** cost ~2–4 GB RAM each (3–5 concurrent tabs); lightweight workers are nearly free — the tiered renderer keeps most traffic off browsers.

## Legal & ethical use

Scraping public data is legal in many jurisdictions but may be restricted by terms of service, robots.txt, or law. This project ships with politeness defaults (robots.txt respected, ≤1 req/s/domain, jittered delays, honest error messages) — keep them on unless you have the right to do otherwise. Don't scrape personal data without a lawful basis, don't republish copyrighted content, and don't use CAPTCHA-solving/proxy features against sites you don't have permission to test. You are responsible for how you use this software.

## License

MIT
