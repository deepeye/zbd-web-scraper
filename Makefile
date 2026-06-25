.PHONY: install dev run test lint migrate docker-up docker-down

install:
	uv sync

dev:
	uv run uvicorn web_scraper_service.main:app --reload --host 0.0.0.0 --port 8000

run:
	uv run uvicorn web_scraper_service.main:app --host 0.0.0.0 --port 8000 --workers $(API_WORKERS)

test:
	uv run pytest -xvs --cov=web_scraper_service --cov-report=term-missing

lint:
	uv run ruff check src/ tests/
	uv run mypy src/

migrate:
	uv run alembic upgrade head

migrate-create:
	uv run alembic revision --autogenerate -m "$(msg)"

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-build:
	docker compose build

docker-logs:
	docker compose logs -f

worker:
	uv run celery -A web_scraper_service.scheduler.engine:celery_app worker --loglevel=info --concurrency=$(CELERY_CONCURRENCY)

beat:
	uv run celery -A web_scraper_service.scheduler.engine:celery_app beat --loglevel=info

flower:
	uv run celery -A web_scraper_service.scheduler.engine:celery_app flower --port=5555

seed:
	uv run python scripts/seed_spiders.py

crawl-nfra:
	uv run python scripts/crawl_nfra.py --pages $(or ${NFRA_PAGES},5) --item-id $(or ${NFRA_ITEM_ID},4110)

crawl-nfra-4291:
	uv run python scripts/crawl_nfra.py --pages $(or ${NFRA_PAGES},5) --item-id 4291
