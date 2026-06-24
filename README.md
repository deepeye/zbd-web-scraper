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

## License

MIT
# zbd-web-scraper
