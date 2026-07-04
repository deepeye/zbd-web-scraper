from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────
    app_name: str = "web-scraper-service"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = False

    # ── API Server ─────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1
    api_key: str = ""

    # ── Rate Limiting ──────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_default: str = "60/minute"
    rate_limit_storage_uri: str = "redis://localhost:6379/3"

    # ── PostgreSQL ─────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "scraper"
    postgres_password: str = "scraper_secret"
    postgres_db: str = "scraper_db"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis ──────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}"

    # ── Celery ─────────────────────────────────────────────
    celery_concurrency: int = 4
    flower_port: int = 5555

    @property
    def celery_broker_url(self) -> str:
        return f"{self.redis_url}/2"

    @property
    def celery_result_backend(self) -> str:
        return f"{self.redis_url}/1"

    # ── Proxy ──────────────────────────────────────────────
    proxy_enabled: bool = False
    proxy_pool_url: str = ""
    proxy_rotation_strategy: Literal["round-robin", "random"] = "round-robin"
    proxy_list: str = ""

    @property
    def proxies(self) -> list[str]:
        if not self.proxy_list:
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]

    # ── Captcha ────────────────────────────────────────────
    captcha_enabled: bool = False
    captcha_service: Literal["2captcha", "anticaptcha"] = "2captcha"
    twocaptcha_api_key: str = ""
    anticaptcha_api_key: str = ""

    # ── Snapshot DB (独立库，存爬取快照) ──────────────────
    @property
    def snapshot_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/zbd_crawler_data"
        )

    # ── Bailian (Qwen LLM 抽取，OpenAI 兼容) ────────────────
    dashscope_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    bailian_model: str = "qwen3.5-35b-a3b"

    # ── nfra 定时调度 ───────────────────────────────────────
    nfra_schedule_enabled: bool = True
    nfra_schedule_cron: str = "0 8 * * *"
    nfra_schedule_pages: int = 5
    # 注册资本/开业定时：依赖 nfra_schedule_enabled（总开关）+ cron/pages。
    nfra_capital_schedule_enabled: bool = True
    # 股权变更/开业股东定时：同上。
    nfra_equity_schedule_enabled: bool = True

    # ── S3 ─────────────────────────────────────────────────
    s3_enabled: bool = False
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = "scraper-assets"
    s3_region: str = "us-east-1"

    # ── Elasticsearch ──────────────────────────────────────
    es_enabled: bool = False
    es_hosts: str = "http://localhost:9200"

    # ── MongoDB ────────────────────────────────────────────
    mongo_enabled: bool = False
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "scraper_db"

    # ── Scrapling / Fetcher defaults ───────────────────────
    scrapling_adaptive: bool = True
    playwright_headless: bool = True
    camoufox_headless: bool = True
    default_timeout: int = 30
    default_retry_times: int = 3
    default_retry_delay: float = 1.0
    default_concurrency: int = 5
    default_download_delay: float = 0.5


settings = Settings()
