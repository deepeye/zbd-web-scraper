# Web Scraper Service

Production-grade web scraping service powered by **Scrapling**, **FastAPI**, and **async scheduling**.

## Features

- **Scrapling Engine**: Fetcher (static HTML), PlayWrightFetcher (SPA/JS-rendered), CamoufoxFetcher (anti-fingerprint), StealthyFetcher (stealth mode)
- **Spider Framework**: BaseSpider with `fetch()` / `parse()` / `pipeline()` separation, auto-register via decorator
- **Async Concurrency**: `asyncio.gather` + `Semaphore` for controlled parallel crawling
- **RESTful API**: Full CRUD for spiders, jobs, results, metrics with API key auth + rate limiting
- **Scheduling**: APScheduler (in-process) + Celery (distributed) with cron/interval triggers
- **Data Pipeline**: Pydantic v2 validation, chain-of-responsibility cleaners, Redis URL dedup, content hash change detection
- **Storage**: PostgreSQL (asyncpg + SQLAlchemy 2.x), Redis (dedup/state/queue/rate-limit), S3, Elasticsearch, MongoDB
- **Docker Compose**: One-command startup — API + worker + beat + Redis + PostgreSQL + Flower

## Quick Start

```bash
# 1. Install dependencies
cp .env.example .env
make install

# 2. Start infrastructure
make docker-up

# 3. Run database migrations
make migrate

# 4. Seed example spiders
make seed

# 5. Start API server
make dev

# 6. Start Celery worker (separate terminal)
make worker

# 7. Start Celery beat scheduler (separate terminal)
make beat
```

API docs available at http://localhost:8000/docs

## Local Database (PostgreSQL + Redis only)

For local dev or smoke testing when you only need the databases (not API/worker services), use the lightweight compose:

```bash
docker compose -f docker-compose.dev.yml up -d       # start
docker compose -f docker-compose.dev.yml down        # stop (keep data)
docker compose -f docker-compose.dev.yml down -v     # stop & wipe data
```

Connection info (matches `.env.example` defaults):

| Item | Value |
|------|-------|
| PostgreSQL | `localhost:5432`, user `scraper` / password `scraper_secret` |
| Redis | `localhost:6379` |

Databases:

| Database | Purpose |
|----------|---------|
| `scraper_db` | App main DB (auto-created on first postgres start) |
| `zbd_crawler_data` | Snapshot DB — holds the `web_snapshot(doc_id, snapshot, crawl_time)` table |

> **Note:** postgres only auto-creates `scraper_db` (the `POSTGRES_DB`). `zbd_crawler_data` must be created manually:
> ```bash
> psql -h localhost -U scraper -d postgres -c "CREATE DATABASE zbd_crawler_data;"
> ```
> The `web_snapshot` table is auto-created (`CREATE TABLE IF NOT EXISTS`) on the crawler's first run.
>
> Snapshot data lives in `zbd_crawler_data`, **not** `scraper_db`:
> ```bash
> psql -h localhost -U scraper -d zbd_crawler_data -c "SELECT doc_id, length(snapshot), crawl_time FROM web_snapshot LIMIT 5;"
> ```

## nfra Document Crawling

Crawl 国家金融监督管理总局 (nfra.gov.cn) 任职资格批复 into `zbd_crawler_data.djg_data` (one row per person).

| itemId | 栏目 | make target |
|--------|------|-------------|
| 4110 | 总局机关 | `make crawl-nfra` |
| 4291 | (second entry) | `make crawl-nfra-4291` |

```bash
# default: itemId=4110
make crawl-nfra

# itemId=4291
make crawl-nfra-4291

# custom itemId / pages via env
NFRA_ITEM_ID=4291 NFRA_PAGES=3 make crawl-nfra
```

**Flow**: list discovery via browser (AsyncStealthySession — list API is JS-cookie-gated) → title filter ("任职资格") → skip already-stored doc_ids → open each detail HTML with DynamicFetcher → hybrid extraction (code selectors for meta + Bailian LLM `qwen3.5-35b-a3b` for person/position/institution/date) → write `djg_data` per-doc as each is extracted (crash-safe). Requires `DASHSCOPE_API_KEY` in `.env`.

**Scheduling**: daily 8am (Asia/Shanghai) APScheduler auto-crawls both itemId 4110 + 4291, 5 pages each. Disable via `NFRA_SCHEDULE_ENABLED=false`. Manual trigger + status via API (see [nfra API](#nfra)).

**Query**: `GET /api/v1/nfra/data` returns `djg_data` by `crawl_time` range with pagination.

> Full API detail: [`docs/API.md`](docs/API.md).

## Docker Compose

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

Services:
| Service | Port | Description |
|---------|------|-------------|
| scraper-api | 8000 | FastAPI REST API |
| scraper-worker | — | Celery worker |
| scraper-beat | — | Celery beat scheduler |
| scraper-flower | 5555 | Celery monitoring dashboard |
| postgres | 5432 | PostgreSQL database |
| redis | 6379 | Redis (dedup/state/queue/rate-limit) |

## Spider Development Guide

### Create a new spider

1. Create a file in `src/web_scraper_service/spiders/` (or `examples/`)

2. Define your spider class:

```python
from scrapling import Adaptor
from web_scraper_service.spiders.base import BaseSpider
from web_scraper_service.spiders.registry import register_spider

@register_spider
class MySpider(BaseSpider):
    name = "my_spider"
    start_urls = ["https://example.com"]
    use_playwright = False  # Set True for JS-rendered pages
    use_stealthy = True     # Enable stealth headers

    async def parse(self, response: Adaptor, **kwargs):
        for item in response.css(".item"):
            yield {
                "url": kwargs.get("url", ""),
                "title": item.css(".title::text").get(),
                "price": item.css(".price::text").get(),
            }
```

3. The spider is auto-registered and available via API.

### Fetcher selection

| Fetcher | Use case | Config flag |
|---------|----------|-------------|
| `AsyncFetcher` (httpx) | Static HTML pages | `use_playwright=False` |
| `StealthyFetcher` (Playwright) | SPA, JS-rendered, anti-bot | `use_playwright=True` |
| `CamoufoxFetcher` | High-protection sites (Cloudflare) | `use_camoufox=True` |
| `StealthyFetcher` (stealth headers) | Medium anti-bot | `use_stealthy=True` |

### Item validation

Define a Pydantic model in `pipeline/validators.py` and register it in `ITEM_MODELS`:

```python
class MyItem(BaseItem):
    title: str
    price: float

ITEM_MODELS["my_spider"] = MyItem
```

### Data pipeline flow

```
fetch → parse → validate (Pydantic) → clean → dedup → store
```

## API Reference

All endpoints require `X-API-Key` header.

### Spiders

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/spiders` | Create spider |
| GET | `/api/v1/spiders` | List spiders |
| GET | `/api/v1/spiders/{id}` | Get spider detail |
| PATCH | `/api/v1/spiders/{id}` | Update spider config |
| DELETE | `/api/v1/spiders/{id}` | Delete spider |
| POST | `/api/v1/spiders/{id}/run` | Trigger immediate crawl |
| POST | `/api/v1/spiders/{id}/pause` | Pause scheduling |
| POST | `/api/v1/spiders/{id}/resume` | Resume scheduling |

### Jobs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/jobs` | List jobs |
| GET | `/api/v1/jobs/{id}` | Job detail |
| POST | `/api/v1/jobs/{id}/cancel` | Cancel running job |

### Results

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/results` | Query results (paginated) |
| GET | `/api/v1/results/export?format=csv\|json` | Stream export |

### Metrics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/metrics` | List metrics |
| GET | `/api/v1/metrics/summary/{spider_name}` | Spider summary |

### nfra

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/nfra/crawl` | Manual trigger: `{item_id?, pages?}` (defaults 4110/5), returns `job_id` |
| GET | `/api/v1/nfra/crawl/{job_id}` | Poll Celery job status (pending/running/success/failed) |
| GET | `/api/v1/nfra/data` | Query `djg_data` by `crawl_time` range, paginated |

> Per-endpoint params, request bodies, response examples, error codes: [`docs/API.md`](docs/API.md)

### Response format

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "pagination": {"page": 1, "size": 20, "total": 100}
}
```

### Error codes

| Range | Category |
|-------|----------|
| 1xxx | Spider errors (1001 not found, 1002 invalid config) |
| 2xxx | Job errors (2001 already running, 2002 schedule conflict) |
| 3xxx | Runtime errors (3001 timeout, 3002 parse fail, 3003 proxy exhausted) |
| 4xxx | Storage errors (4001 write fail, 4002 connection broken) |

## Configuration

All configuration via environment variables. See `.env.example` for full reference.

Key settings:
- `SCRAPLING_ADAPTIVE=true` — Enable adaptive selectors for resilient scraping
- `PROXY_ENABLED=false` — Enable proxy rotation
- `CAPTCHA_ENABLED=false` — Enable captcha solving (2captcha / Anti-Captcha)
- `DEFAULT_CONCURRENCY=5` — Max concurrent requests per spider
- `DEFAULT_DOWNLOAD_DELAY=0.5` — Delay between requests (seconds)
- `SNAPSHOT_DATABASE_URL` — Snapshot DB (defaults to `zbd_crawler_data` on same postgres)
- `DASHSCOPE_API_KEY` — Bailian (Qwen) API key for nfra LLM extraction
- `BAILIAN_MODEL=qwen3.5-35b-a3b` — LLM model for extraction
- `NFRA_SCHEDULE_ENABLED=true` — daily 8am auto-crawl toggle
- `NFRA_SCHEDULE_CRON=0 8 * * *` — nfra daily schedule (Asia/Shanghai)
- `NFRA_SCHEDULE_PAGES=5` — pages per itemId in scheduled run

## License

MIT
# zbd-web-scraper
