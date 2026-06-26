# Linux docker compose 部署 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 x86_64 Linux 服务器上用 `docker compose` 一键部署 API + Celery worker/beat/flower + PostgreSQL + Redis；修复 Dockerfile（项目可导入 + 浏览器 baked）与 compose（healthcheck + zbd_crawler_data 自动建库 + 服务名 hosts）。

**Architecture:** 服务器本地 `docker compose build && up`。Dockerfile 两阶段：builder 用 `uv sync`（含项目 editable 安装）；runtime 装 Playwright/Chromium 系统依赖 + CJK 字体 + `scrapling install`（浏览器 baked 进 `/root/.cache/ms-playwright`）。compose 挂 `docker/init-db.sql` 自动建 `zbd_crawler_data`，API 加 healthcheck，`.env.docker.example` 提供服务名 hosts 模板。

**Tech Stack:** Docker（multi-stage）· docker compose v2 · python:3.12-slim · scrapling 0.4.9 · postgres:16-alpine · redis:7-alpine

## Global Constraints

- 目标架构 x86_64 / amd64；服务器本地构建（`docker compose build`）。
- Dockerfile `uv sync` **不带** `--no-install-project`（项目须 editable 装入 .venv，`uvicorn web_scraper_service.main:app` 可导入）。
- runtime 阶段运行 `scrapling install`（默认路径 `/root/.cache/ms-playwright`，容器以 root 运行，无需自定义 env）。
- 不改源码、不改 `docker-compose.dev.yml`、不改 `.env.example`（本地开发用，保留）。
- `zbd_crawler_data` 经 `docker/init-db.sql`（`CREATE DATABASE zbd_crawler_data;`）在 postgres 首启空数据卷时建一次。
- 容器内 postgres/redis 用服务名 `postgres`/`redis`（`.env.docker.example` 设 `POSTGRES_HOST=postgres`、`REDIS_HOST=redis`）。
- 约束引自 spec：`docs/superpowers/specs/2026-06-26-linux-docker-deploy-design.md`。

## File Structure

| 文件 | 职责 |
|------|------|
| `Dockerfile` | 两阶段构建；项目安装 + 浏览器 baked + CJK 字体 |
| `.dockerignore` | 构建上下文精简（排除 .venv/.git/tests/.env 等） |
| `docker-compose.yml` | 服务编排；API healthcheck + init-db 挂载 + worker concurrency 2 |
| `docker/init-db.sql` | `CREATE DATABASE zbd_crawler_data;` |
| `.env.docker.example` | docker 部署 env 模板（服务名 hosts + 密钥占位） |
| `docs/DEPLOY.md` | Linux 部署指南 + 可选 nginx 片段 |

---

### Task 1: Dockerfile + .dockerignore

**Files:**
- Modify: `Dockerfile`（整体重写两阶段）
- Create: `.dockerignore`

**Interfaces:**
- Produces: 可构建的镜像（API 可启动 + worker 浏览器可用）

- [ ] **Step 1: 重写 Dockerfile**

整体替换 `Dockerfile` 内容为：

```dockerfile
# ── Stage 1: Build ──────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# ── Stage 2: Runtime ────────────────────────────────────────
FROM python:3.12-slim

# Playwright/Chromium 运行依赖 + CJK 字体（中文页渲染）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libasound2 libxshmfence1 \
    libxkbcommon0 libgtk-3-0 \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini scripts/ ./

# 下载浏览器二进制（playwright + patchright chromium），baked 进镜像默认缓存路径
RUN scrapling install

EXPOSE 8000
CMD ["uvicorn", "web_scraper_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

要点：builder 先 `COPY src/` 再 `uv sync`（不带 `--no-install-project`）→ 项目 editable 装入 .venv；runtime 复制 .venv + src + migrations + scripts，`scrapling install` 在构建时下浏览器到 `/root/.cache/ms-playwright`（容器 root 用户默认路径）。

- [ ] **Step 2: 创建 .dockerignore**

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

- [ ] **Step 3: 验证构建上下文精简**

Run: `docker build --no-cache --check . 2>&1 | tail -5 || true`（若 docker 不支持 --check 则跳过）
或检查：`du -sh .venv .git 2>/dev/null` 确认这些大目录会被 .dockerignore 排除（构建上下文不再携带）。

- [ ] **Step 4: 本地构建镜像（验证 Dockerfile 可构建）**

Run: `docker build -t zbd-scraper:dev . 2>&1 | tail -15`
Expected: 构建成功，末尾出现 `Successfully tagged zbd-scraper:dev`（或 buildkit 的 `naming to ... done`）。`scrapling install` 步骤会下载浏览器（几分钟）。

- [ ] **Step 5: 验证镜像内 API 可启动**

Run:
```bash
docker run --rm -d --name zbd-test -p 18000:8000 \
  -e API_KEY=test-key -e POSTGRES_HOST=none -e REDIS_HOST=none \
  zbd-scraper:dev
sleep 4
docker logs zbd-test 2>&1 | grep -iE 'All services|error|traceback' | head
docker stop zbd-test
```
Expected: 日志含 `All services initialized`（init_db/init_redis 会因 host=none 失败，但 import + uvicorn 启动成功即证明项目可导入；若 init 失败导致退出，记录错误——核心是验证 `uvicorn web_scraper_service.main:app` 能 import 启动）。

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "build: fix Dockerfile (project install + scrapling browser + CJK fonts) + .dockerignore"
```

---

### Task 2: docker-compose.yml + docker/init-db.sql + .env.docker.example

**Files:**
- Modify: `docker-compose.yml`（API healthcheck + init-db 挂载 + worker concurrency 默认 2）
- Create: `docker/init-db.sql`
- Create: `.env.docker.example`

**Interfaces:**
- Consumes: Task 1 的 Dockerfile（镜像）
- Produces: `docker compose up` 可拉起全套服务

- [ ] **Step 1: 创建 docker/init-db.sql**

```bash
mkdir -p docker
```

`docker/init-db.sql` 内容：

```sql
-- postgres 镜像首启（空数据卷）时执行一次，创建快照库
CREATE DATABASE zbd_crawler_data;
```

- [ ] **Step 2: 创建 .env.docker.example`

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
API_KEY=change-me-to-a-strong-random-key

# ── PostgreSQL（容器间用服务名 postgres）────────────────────
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=scraper
POSTGRES_PASSWORD=change-me-to-a-strong-password
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
DASHSCOPE_API_KEY=fill-your-dashscope-key
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=qwen3.5-35b-a3b
```

- [ ] **Step 3: 修改 docker-compose.yml — scraper-api 加 healthcheck**

将 `scraper-api` 服务块改为（加 healthcheck，其余不变）：

```yaml
  scraper-api:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "${API_PORT:-8000}:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 10s
      timeout: 5s
      retries: 5
```

- [ ] **Step 4: 修改 docker-compose.yml — worker concurrency 默认 2`

将 `scraper-worker` 的 `command` 行改为：

```yaml
    command: celery -A web_scraper_service.scheduler.engine:celery_app worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-2}
```

- [ ] **Step 5: 修改 docker-compose.yml — postgres 挂载 init-db.sql`

将 `postgres` 服务块的 `volumes` 改为（加 init-db 挂载）：

```yaml
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql:ro
```

- [ ] **Step 6: 验证 compose 语法**

Run: `docker compose config --quiet 2>&1 | tail -5`
Expected: 无输出（语法正确）或仅 env_file 警告（无 .env 时）。

- [ ] **Step 7: 本地拉起验证（DB + API healthcheck）**

Run:
```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up -d postgres redis scraper-api 2>&1 | tail -10
sleep 15
curl -s http://localhost:8000/health; echo
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker down 2>&1 | tail -3
rm -f .env.docker
```
Expected: `/health` 返回 `{"status":"ok",...}`；postgres/redis/api 均 healthy/running；`zbd_crawler_data` 库被 init-db.sql 创建（可 `docker compose exec postgres psql -U scraper -l | grep zbd_crawler_data` 验证）。

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml docker/init-db.sql .env.docker.example
git commit -m "build: compose healthcheck + init-db.sql + .env.docker.example"
```

---

### Task 3: docs/DEPLOY.md 部署指南

**Files:**
- Create: `docs/DEPLOY.md`

- [ ] **Step 1: 创建 docs/DEPLOY.md**

```markdown
# Linux 部署指南（docker compose）

目标：x86_64 Linux 服务器，`docker compose` 一键部署 API + Celery worker/beat/flower + PostgreSQL + Redis。

## 前置

- Linux x86_64 服务器，已装 Docker Engine + docker compose v2（`docker compose version`）。
- 服务器可访问外网（首次构建需下载浏览器 ~300MB + python 依赖）。
- 准备：百炼 `DASHSCOPE_API_KEY`、一个强随机 `API_KEY`、一个强 `POSTGRES_PASSWORD`。

## 步骤

```bash
# 1. 拉代码
git clone <repo-url> zbd-web-scraper
cd zbd-web-scraper

# 2. 配 .env（从 docker 模板复制，填密钥）
cp .env.docker.example .env
# 编辑 .env，填：
#   API_KEY=<强随机>
#   POSTGRES_PASSWORD=<强密码>
#   DASHSCOPE_API_KEY=<百炼 key>
vi .env

# 3. 构建镜像（含 scrapling install，首次约 5-10 分钟）
docker compose build

# 4. 启动全套服务
docker compose up -d

# 5. 验证
curl localhost:8000/health            # {"status":"ok",...}
docker compose ps                     # 全部 healthy/running
docker compose logs -f scraper-api    # 看 "All services initialized" + "Scheduled nfra daily crawl"
```

## 数据库

- 主库 `scraper_db`：postgres 镜像首启自动建，应用启动时 `Base.metadata.create_all` 自动建表。
- 快照库 `zbd_crawler_data`：`docker/init-db.sql` 首启自动 `CREATE DATABASE`；`web_snapshot`/`djg_data` 表由应用首次运行时 `CREATE TABLE IF NOT EXISTS` 建。
- 数据持久化在 docker volume `pgdata`/`redisdata`（`docker compose down` 保留，`-v` 清空）。

## 运行

- **手动触发采集**：
  ```bash
  curl -X POST localhost:8000/api/v1/nfra/crawl \
    -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
    -d '{"item_id": 4291, "pages": 1}'
  # 轮询状态（替换 job_id）
  curl localhost:8000/api/v1/nfra/crawl/<job_id> -H "X-API-Key: $API_KEY"
  ```
- **定时**：每日 8 点（Asia/Shanghai）APScheduler 自动采集 4110 + 4291 各 5 页。`NFRA_SCHEDULE_ENABLED=false` 关闭。
- **查询数据**：`GET /api/v1/nfra/data?start_date=...&end_date=...`（见 `docs/API.md`）。
- **Worker 日志**：`docker compose logs -f scraper-worker`。
- **Flower 监控**：`http://<server>:5555`。

## 生产加固（可选）

- **不暴露 DB/Redis 端口**：编辑 `docker-compose.yml`，删去 `postgres`/`redis` 的 `ports:`（仅容器内网可达）。
- **HTTPS 反代**：前置 nginx：
  ```nginx
  server {
    listen 443 ssl;
    server_name scraper.example.com;
    ssl_certificate     /etc/ssl/scraper.crt;
    ssl_certificate_key /etc/ssl/scraper.key;
    location / {
      proxy_pass http://127.0.0.1:8000;
      proxy_set_header Host $host;
      proxy_set_header X-Real-IP $remote_addr;
    }
  }
  ```
- **密钥**：`.env` 保持 600 权限，不入 git（已 .gitignore）。
- **资源**：worker `CELERY_CONCURRENCY=2`（浏览器+LLM 重），按服务器内存调整。

## 常见问题

| 现象 | 原因 / 处理 |
|------|------|
| worker 日志 "Executable doesn't exist" | 镜像未跑 `scrapling install`；重新 `docker compose build` |
| API 500 "API key not configured" | `.env` 未设 `API_KEY` |
| 采集 LLM 报 401 | `DASHSCOPE_API_KEY` 错误或未设 |
| 定时未触发 | `NFRA_SCHEDULE_ENABLED=false` 或 APScheduler 未启（看 api 日志） |
| `zbd_crawler_data` 不存在 | 数据卷已存在旧数据，init-db.sql 不重跑；`docker compose down -v` 后重启 |

## 架构

```
宿主 :8000 → scraper-api (uvicorn, APScheduler 每日 8 点)
                ↓ Celery .delay()
scraper-worker ← redis(broker) → run_crawl(浏览器+LLM) → postgres(zbd_crawler_data.djg_data)
scraper-beat  → Celery beat（备用）
scraper-flower:5555 → 监控
postgres / redis → docker volume
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/DEPLOY.md
git commit -m "docs: add Linux docker compose deployment guide"
```

---

### Task 4: 端到端 smoke（本地构建 + 拉起 + 触发采集）

**Files:** 无（仅运行验证）

**说明：** 本机为 arm64 Mac，构建出 arm64 镜像（非目标 x86_64），但可验证 Dockerfile/compose 逻辑、镜像可启动、浏览器可装。真实 x86_64 smoke 在服务器上执行（见 `docs/DEPLOY.md`）。

- [ ] **Step 1: 完整构建**

Run: `docker compose --env-file .env.docker build 2>&1 | tail -5`
Expected: 构建成功（若 Task 1/2 已分别构建过，此步用缓存更快）。

- [ ] **Step 2: 全套拉起**

Run:
```bash
cp .env.docker.example .env.docker
# 填真实 DASHSCOPE_API_KEY 以便触发采集（其余可用占位）
docker compose --env-file .env.docker up -d 2>&1 | tail -10
sleep 20
docker compose --env-file .env.docker ps
```
Expected: postgres/redis/api/worker/beat/flower 全部 running/healthy。

- [ ] **Step 3: 验证 API + 定时注册**

Run:
```bash
curl -s http://localhost:8000/health; echo
docker compose --env-file .env.docker logs scraper-api 2>&1 | grep -iE 'All services|Scheduled nfra' | head
```
Expected: `/health` ok；api 日志含 `All services initialized` + `Scheduled nfra daily crawl`。

- [ ] **Step 4: 验证 zbd_crawler_data 建库**

Run: `docker compose --env-file .env.docker exec postgres psql -U scraper -l | grep zbd_crawler_data`
Expected: 输出 `zbd_crawler_data` 行。

- [ ] **Step 5: 触发一次小规模采集（验证 worker 浏览器+LLM 端到端）**

Run:
```bash
API_KEY=$(grep ^API_KEY .env.docker | cut -d= -f2)
curl -s -X POST http://localhost:8000/api/v1/nfra/crawl \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"item_id": 4291, "pages": 1}'
```
Expected: 返回 `job_id`。数分钟后 `GET /api/v1/nfra/crawl/<job_id>` 变 `success`；`djg_data` 有新行。
（若 DASHSCOPE_API_KEY 未填真实值，此步 LLM 会 401，任务 failed——记录但非部署问题。）

- [ ] **Step 6: 清理**

Run:
```bash
docker compose --env-file .env.docker down 2>&1 | tail -3
rm -f .env.docker
```

- [ ] **Step 7: 全量测试无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全 PASS（41，未改源码）

- [ ] **Step 8: Commit（如有小修）**

```bash
git add -A
git commit -m "test: verify docker compose end-to-end smoke" || echo "nothing to commit"
```

---

## Self-Review 记录

- **Spec 覆盖**：Dockerfile 项目安装+scrapling install+CJK 字体(Task1)✓；.dockerignore(Task1)✓；compose healthcheck+init-db 挂载+worker concurrency 2(Task2)✓；docker/init-db.sql(Task2)✓；.env.docker.example(Task2)✓；docs/DEPLOY.md 含步骤+数据库+运行+生产加固+FAQ+架构(Task3)✓；构建+拉起+触发 smoke(Task4)✓；不改源码/dev compose/.env.example(spec §不改动)✓。
- **类型一致**：无新代码接口；compose 服务名 scraper-api/scraper-worker/postgres/redis 在 Task2/4 一致；env 变量名 API_KEY/POSTGRES_HOST/REDIS_HOST/DASHSCOPE_API_KEY/NFRA_SCHEDULE_* 与 config.py 一致。
- **API 已验证**：`scrapling install` 默认缓存到 `/root/.cache/ms-playwright`（容器 root 用户），无需自定义 env；playwright+patchright 均用此路径。builder 去 `--no-install-project` + 先 COPY src/ → 项目 editable 装 .venv → `uvicorn web_scraper_service.main:app` 可导入。
- **无占位符**：所有步骤含完整文件内容与确切命令；.env.docker.example 的 `change-me-*`/`fill-your-*` 是用户填入占位（非计划占位）。
