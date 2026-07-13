# Web Scraper 服务

基于 **Scrapling**、**FastAPI** 和 **异步调度** 的生产级网页抓取服务。

## 特性

- **Scrapling 引擎**：AsyncFetcher（静态 HTML）、PlayWrightFetcher（SPA/JS 渲染）、CamoufoxFetcher（反指纹）、StealthyFetcher（隐身模式）
- **爬虫框架**：BaseSpider 提供 `fetch()` / `parse()` / `pipeline()` 分离，通过装饰器自动注册
- **异步并发**：`asyncio.gather` + `Semaphore` 实现可控的并行抓取
- **RESTful API**：爬虫、任务、结果、指标的完整 CRUD，API Key 认证 + 速率限制
- **调度系统**：APScheduler（进程内）+ Celery（分布式），支持 cron/interval 触发器
- **数据管道**：Pydantic v2 校验、责任链清洗器、Redis URL 去重、内容哈希变更检测
- **存储**：PostgreSQL（asyncpg + SQLAlchemy 2.x）、Redis（去重/状态/队列/速率限制）、S3、Elasticsearch、MongoDB
- **Docker Compose**：一键启动 — API + worker + beat + Redis + PostgreSQL + Flower

## 快速开始

```bash
# 1. 安装依赖
cp .env.example .env
make install

# 2. 启动基础设施
make docker-up

# 3. 运行数据库迁移
make migrate

# 4. 导入示例爬虫
make seed

# 5. 启动 API 服务
make dev

# 6. 启动 Celery worker（单独终端）
make worker

# 7. 启动 Celery beat 调度器（单独终端）
make beat
```

API 文档地址：http://localhost:8000/docs

## 本地数据库（仅 PostgreSQL + Redis）

如只需启动数据库做本地开发或 smoke 验证，可用轻量 compose（不含 API/worker 等服务）：

```bash
docker compose -f docker-compose.dev.yml up -d       # 启动
docker compose -f docker-compose.dev.yml down        # 停止（保留数据）
docker compose -f docker-compose.dev.yml down -v     # 停止并清空数据
```

连接信息（与 `.env.example` 默认值一致）：

| 项 | 值 |
|----|----|
| PostgreSQL | `localhost:5432`，用户 `scraper` / 密码 `scraper_secret` |
| Redis | `localhost:6379` |

库说明：

| 库名 | 用途 |
|------|------|
| `scraper_db` | 应用主库（postgres 镜像首启自动创建） |
| `zbd_crawler_data` | 快照库，`web_snapshot(doc_id, snapshot, crawl_time)` 表所在库 |

> **注意**：postgres 镜像首启只创建 `POSTGRES_DB` 指定的 `scraper_db`。`zbd_crawler_data` 需手动创建：
> ```bash
> psql -h localhost -U scraper -d postgres -c "CREATE DATABASE zbd_crawler_data;"
> ```
> `web_snapshot` 表由爬虫首次运行时自动 `CREATE TABLE IF NOT EXISTS`，无需手动建表。
>
> 快照数据**在 `zbd_crawler_data` 库**，不在 `scraper_db`：
> ```bash
> psql -h localhost -U scraper -d zbd_crawler_data -c "SELECT doc_id, length(snapshot), crawl_time FROM web_snapshot LIMIT 5;"
> ```

## nfra 文档采集

采集国家金融监督管理总局（nfra.gov.cn）任职资格批复，写入 `zbd_crawler_data.djg_data`（一人一行）：

| itemId | 栏目 | make target |
|--------|------|-------------|
| 4110 | 总局机关 | `make crawl-nfra` |
| 4291 | 第二入口 | `make crawl-nfra-4291` |

```bash
# 默认 itemId=4110
make crawl-nfra

# itemId=4291
make crawl-nfra-4291

# 通过环境变量自定义 itemId / 页数
NFRA_ITEM_ID=4291 NFRA_PAGES=3 make crawl-nfra
```

**流程**：浏览器（AsyncStealthySession）发现列表（列表 API 需 JS 生成的会话 cookie）→ 标题过滤（含「任职资格」）→ 跳过已入库 doc_id → DynamicFetcher 打开详情 HTML → 混合抽取（meta 用代码选择器、人/职务/机构/日期用百炼 LLM `qwen3.5-35b-a3b`）→ 每个文档抽完即写入 `djg_data`（崩溃安全）。需在 `.env` 配置 `DASHSCOPE_API_KEY`。

**定时调度**：每日 8 点（Asia/Shanghai）APScheduler 自动采集 4110 + 4291 各 5 页；`NFRA_SCHEDULE_ENABLED=false` 关闭。手动触发与状态查询见 [nfra API](#nfra)。

**查询**：`GET /api/v1/nfra/djg/data` 按 `crawl_time` 范围分页查询 `djg_data`。

> 完整接口说明：[`docs/API.md`](docs/API.md)

## Docker Compose

```bash
# 启动所有服务
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

服务列表：
| 服务 | 端口 | 说明 |
|------|------|------|
| scraper-api | 8000 | FastAPI REST API |
| scraper-worker | — | Celery worker |
| scraper-beat | — | Celery beat 调度器 |
| scraper-flower | 5555 | Celery 监控面板 |
| postgres | 5432 | PostgreSQL 数据库 |
| redis | 6379 | Redis（去重/状态/队列/速率限制） |

## 爬虫开发指南

### 创建新爬虫

1. 在 `src/web_scraper_service/spiders/`（或 `examples/`）目录下创建文件

2. 定义爬虫类：

```python
from scrapling import Adaptor
from web_scraper_service.spiders.base import BaseSpider
from web_scraper_service.spiders.registry import register_spider

@register_spider
class MySpider(BaseSpider):
    name = "my_spider"
    start_urls = ["https://example.com"]
    use_playwright = False  # JS 渲染页面设为 True
    use_stealthy = True     # 启用隐身请求头

    async def parse(self, response: Adaptor, **kwargs):
        for item in response.css(".item"):
            yield {
                "url": kwargs.get("url", ""),
                "title": item.css(".title::text").get(),
                "price": item.css(".price::text").get(),
            }
```

3. 爬虫自动注册，可通过 API 调用。

### Fetcher 选择

| Fetcher | 适用场景 | 配置标志 |
|---------|----------|----------|
| `AsyncFetcher`（httpx） | 静态 HTML 页面 | `use_playwright=False` |
| `StealthyFetcher`（Playwright） | SPA、JS 渲染、反爬 | `use_playwright=True` |
| `CamoufoxFetcher` | 高防护站点（Cloudflare） | `use_camoufox=True` |
| `StealthyFetcher`（隐身请求头） | 中等反爬 | `use_stealthy=True` |

### Item 校验

在 `pipeline/validators.py` 中定义 Pydantic 模型并注册到 `ITEM_MODELS`：

```python
class MyItem(BaseItem):
    title: str
    price: float

ITEM_MODELS["my_spider"] = MyItem
```

### 数据管道流程

```
fetch → parse → validate（Pydantic）→ clean → dedup → store
```

## API 参考

所有接口需要 `X-API-Key` 请求头。

### 爬虫

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/spiders` | 创建爬虫 |
| GET | `/api/v1/spiders` | 爬虫列表 |
| GET | `/api/v1/spiders/{id}` | 爬虫详情 |
| PATCH | `/api/v1/spiders/{id}` | 更新爬虫配置 |
| DELETE | `/api/v1/spiders/{id}` | 删除爬虫 |
| POST | `/api/v1/spiders/{id}/run` | 立即触发抓取 |
| POST | `/api/v1/spiders/{id}/pause` | 暂停调度 |
| POST | `/api/v1/spiders/{id}/resume` | 恢复调度 |

### 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/jobs` | 任务列表 |
| GET | `/api/v1/jobs/{id}` | 任务详情 |
| POST | `/api/v1/jobs/{id}/cancel` | 取消运行中的任务 |

### 结果

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/results` | 查询结果（分页） |
| GET | `/api/v1/results/export?format=csv\|json` | 流式导出 |

### 指标

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/metrics` | 指标列表 |
| GET | `/api/v1/metrics/summary/{spider_name}` | 爬虫汇总 |

### nfra

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/nfra/djg/crawl` | 手动触发：`{item_id?, pages?}`（默认 4110/5），返回 `job_id` |
| GET | `/api/v1/nfra/djg/crawl/{job_id}` | 轮询任务状态（pending/running/success/failed） |
| GET | `/api/v1/nfra/djg/data` | 按 `crawl_time` 范围分页查询 `djg_data` |

> 各接口参数、请求体、响应示例、错误码详见 [`docs/API.md`](docs/API.md)

### 响应格式

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "pagination": {"page": 1, "size": 20, "total": 100}
}
```

### 错误码

| 范围 | 分类 |
|------|------|
| 1xxx | 爬虫错误（1001 未找到，1002 配置无效） |
| 2xxx | 任务错误（2001 已运行，2002 调度冲突） |
| 3xxx | 运行错误（3001 超时，3002 解析失败，3003 代理耗尽） |
| 4xxx | 存储错误（4001 写入失败，4002 连接断开） |

## 配置

所有配置通过环境变量设置，详见 `.env.example`。

关键配置项：
- `SCRAPLING_ADAPTIVE=true` — 启用自适应选择器，提升抓取容错性
- `PROXY_ENABLED=false` — 启用代理轮换
- `CAPTCHA_ENABLED=false` — 启用验证码求解（2captcha / Anti-Captcha）
- `DEFAULT_CONCURRENCY=5` — 每个爬虫的最大并发请求数
- `DEFAULT_DOWNLOAD_DELAY=0.5` — 请求间隔（秒）
- `SNAPSHOT_DATABASE_URL` — 快照库（默认同 postgres 的 `zbd_crawler_data`）
- `DASHSCOPE_API_KEY` — 百炼（Qwen）API key，nfra LLM 抽取用
- `BAILIAN_MODEL=qwen3.5-35b-a3b` — 抽取用的 LLM 模型
- `NFRA_SCHEDULE_ENABLED=true` — 每日 8 点自动采集开关
- `NFRA_SCHEDULE_CRON=0 8 * * *` — nfra 定时（Asia/Shanghai）
- `NFRA_SCHEDULE_PAGES=5` — 定时运行每个 itemId 的页数

## 许可证

MIT