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
