# Linux docker compose 部署 — 设计文档

## 概述

在 x86_64 Linux 服务器上通过 `docker compose` 部署本采集系统与接口服务：API + Celery worker + beat + flower + PostgreSQL + Redis。服务器本地构建镜像。

## 现状问题（已核查）

1. **Dockerfile**：`uv sync --no-install-project` 导致项目未装入 .venv，`uvicorn web_scraper_service.main:app` 在容器内不可导入；装了 Playwright 系统依赖但**未运行 `scrapling install`**（无浏览器二进制，nfra 爬虫失败）；缺 CJK 字体。
2. **docker-compose.yml**：`env_file: .env` 用 `localhost`，容器内 postgres/redis 应为服务名；`zbd_crawler_data` 库未创建（postgres 镜像只建 `scraper_db`）；API 无 healthcheck；无 `.dockerignore`（构建上下文携带 `.venv`/`.git`，臃肿）。

## 决策（已与用户确认）

- 镜像：服务器本地构建（`docker compose build && up`）。
- 架构：x86_64 / amd64。
- 浏览器：`scrapling install` 在构建时 baked 进镜像（amd64 chromium + patchright）。
- 密钥：服务器上 `.env`（从 `.env.docker.example` 复制，填密钥），gitignored。
- postgres/redis 端口默认对宿主暴露；生产可收为内部（DEPLOY.md 注明）。
- API 默认直暴露 `${API_PORT:-8000}`；nginx+HTTPS 可选（DEPLOY.md 给片段）。

## Dockerfile 修复

```dockerfile
# ── Stage 1: Build ──────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev          # 含项目 editable 安装

# ── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    # Playwright/Chromium 运行依赖
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libasound2 libxshmfence1 \
    libxkbcommon0 libgtk-3-0 \
    # CJK 字体（中文页渲染）
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV SCRAPLING_HOME=/app/.scrapling

COPY --from=builder /app/.venv /app/.venv
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini scripts/ ./

# 下载浏览器二进制到 $SCRAPLING_HOME（构建时 baked，运行时直接用）
RUN scrapling install

EXPOSE 8000
CMD ["uvicorn", "web_scraper_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

要点：
- builder 去掉 `--no-install-project`，先 `COPY src/` 再 `uv sync`，项目 editable 装入 .venv → `uvicorn web_scraper_service.main:app` 可导入。
- runtime 设 `SCRAPLING_HOME=/app/.scrapling`，`scrapling install` 在 runtime 阶段（系统依赖已装、.venv 已复制）下载浏览器到该路径，baked 进镜像（避免每容器启动重下）。
- 加 `libxkbcommon0`/`libgtk-3-0`（patchright chromium 可能需要）+ `fonts-noto-cjk`。
- `scripts/` 含 `crawl_nfra.py`（CLI 备用）。

## docker-compose.yml 修复

```yaml
services:
  scraper-api:
    build: { context: ., dockerfile: Dockerfile }
    ports: ["${API_PORT:-8000}:8000"]
    env_file: .env
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
    volumes: ["./logs:/app/logs"]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 5s
      retries: 5

  scraper-worker:
    build: { context: ., dockerfile: Dockerfile }
    command: celery -A web_scraper_service.scheduler.engine:celery_app worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-2}
    env_file: .env
    depends_on:
      redis: { condition: service_healthy }
      postgres: { condition: service_healthy }
    volumes: ["./logs:/app/logs"]
    restart: unless-stopped
    # worker 默认不映射端口

  scraper-beat:
    build: { context: ., dockerfile: Dockerfile }
    command: celery -A web_scraper_service.scheduler.engine:celery_app beat --loglevel=info
    env_file: .env
    depends_on: [redis]
    restart: unless-stopped

  scraper-flower:
    build: { context: ., dockerfile: Dockerfile }
    command: celery -A web_scraper_service.scheduler.engine:celery_app flower --port=${FLOWER_PORT:-5555}
    ports: ["${FLOWER_PORT:-5555}:5555"]
    env_file: .env
    depends_on: [redis]
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-scraper}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-scraper_secret}
      POSTGRES_DB: ${POSTGRES_DB:-scraper_db}
    ports: ["${POSTGRES_PORT:-5432}:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-scraper}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports: ["${REDIS_PORT:-6379}:6379"]
    volumes: [redisdata:/data]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
  redisdata:
```

要点：
- API 加 healthcheck（python urllib，免装 curl）。
- worker 默认 concurrency=2（浏览器+LLM 重，与 CLI 默认一致）。
- postgres 挂 `./docker/init-db.sql` 自动建 `zbd_crawler_data`。
- `env_file: .env` 配合 `.env.docker.example`（服务名 hosts）。

## 新增文件

### `.dockerignore`

```
.venv/
.git/
tests/
docs/
logs/
.superpowers/
.claude/
.spec-workflow/
.env
.env.old
__pycache__/
*.pyc
.mypy_cache/
.pytest_cache/
docker-compose.dev.yml
uv.lock.bak
```

### `docker/init-db.sql`

```sql
-- postgres 镜像首启（空数据卷）时执行一次
CREATE DATABASE zbd_crawler_data;
```

### `.env.docker.example`

```
# ── 应用 ────────────────────────────────────────────────────
APP_NAME=web-scraper-service
APP_ENV=production
DEBUG=false
LOG_LEVEL=INFO
LOG_JSON=true

# ── API ─────────────────────────────────────────────────────
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=1
API_KEY=<改成你的强随机 key>

# ── PostgreSQL（容器间用服务名 postgres）────────────────────
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=scraper
POSTGRES_PASSWORD=<改成强密码>
POSTGRES_DB=scraper_db

# ── Redis（容器间用服务名 redis）────────────────────────────
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=

# ── Celery ──────────────────────────────────────────────────
CELERY_CONCURRENCY=2
FLOWER_PORT=5555

# ── nfra 定时调度 ───────────────────────────────────────────
NFRA_SCHEDULE_ENABLED=true
NFRA_SCHEDULE_CRON=0 8 * * *
NFRA_SCHEDULE_PAGES=5

# ── Bailian LLM 抽取 ────────────────────────────────────────
DASHSCOPE_API_KEY=<填你的百炼 key>
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=qwen3.5-35b-a3b
```

### `docs/DEPLOY.md`

Linux 部署指南，步骤：
1. 装 Docker + docker compose（x86_64）。
2. `git clone && cd zbd-web-scraper`。
3. `cp .env.docker.example .env`，填 `API_KEY`/`DASHSCOPE_API_KEY`/`POSTGRES_PASSWORD`。
4. `docker compose build`（含 `scrapling install`，首次几分钟，镜像约 +300MB 浏览器）。
5. `docker compose up -d`。
6. `curl localhost:8000/health` 验证。
7. 手动触发采集：`POST /api/v1/nfra/crawl` → 轮询状态 → 查 `djg_data`。
8. 日常：每日 8 点定时自动跑；`docker compose logs -f scraper-worker` 看采集日志。

含可选 nginx+HTTPS 反代片段、生产收紧 postgres/redis 端口的说明（去掉 `ports:` 仅内部网络）。

## 数据流

```
宿主 8000 → scraper-api (uvicorn)
                ├─ APScheduler 每日 8 点 → Celery .delay()
                └─ /api/v1/nfra/* 路由
scraper-beat → Celery beat（备用，当前 APScheduler 在 API 进程内）
scraper-worker ← Redis(broker) ← .delay()
                ├─ run_crawl (AsyncStealthySession 列表 + AsyncDynamicSession 详情)
                └─ extract_rows_llm (百炼 LLM)
postgres ← scraper-api (主库 scraper_db) / scraper-worker (zbd_crawler_data.djg_data)
redis   ← Celery broker/backend + 限流
```

## 错误处理 / 注意

| 场景 | 处理 |
|------|------|
| 首次构建慢 | `scrapling install` 下浏览器 ~300MB，仅首次；后续构建有缓存 |
| 浏览器缺失 | 镜像 baked，worker 启动即可用 |
| zbd_crawler_data 未建 | init-db.sql 首启自动建（仅空数据卷时） |
| 密钥泄漏 | `.env` gitignored；不入库 |
| 端口暴露 | postgres/redis 默认对宿主暴露，生产建议去 `ports:` 收内部 |
| 容器时区 | APScheduler cron 用 `Asia/Shanghai`（显式 timezone），不受容器 UTC 影响 |

## 改动文件清单

| 文件 | 动作 |
|------|------|
| `Dockerfile` | 修改 — 项目安装 + scrapling install + CJK 字体 + SCRAPLING_HOME |
| `docker-compose.yml` | 修改 — API healthcheck + init-db 挂载 + worker concurrency 默认 2 |
| `.dockerignore` | 新建 |
| `docker/init-db.sql` | 新建 |
| `.env.docker.example` | 新建 |
| `docs/DEPLOY.md` | 新建 |

## 不改动

- `docker-compose.dev.yml`（本地开发用，保留）。
- `.env.example`（本地开发用，保留）。
- 源码、测试、迁移脚本——不动。
