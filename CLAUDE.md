# web-scraper-service

## 项目概述

生产级异步爬虫服务，基于 **FastAPI + Scrapling + Celery + APScheduler** 构建，提供 RESTful API 管理爬虫生命周期、调度任务、存储结果和监控指标。

**核心职责：**
- 爬虫注册与管理（CRUD + 定时调度）
- 异步分布式爬取（支持静态/HTML/SPA/反检测场景）
- 数据流水线（校验 → 清洗 → 去重 → 存储）
- 任务调度（即时触发 / Cron 定时 / Celery 分布式队列）
- 结果存储与导出（PostgreSQL + Redis + 可选 S3/ES/Mongo）

---

## 模块架构

```
src/web_scraper_service/
├── main.py              # FastAPI 应用入口，lifespan 管理
├── config.py            # Pydantic-Settings 统一配置
├── core/                # 基础工具
│   ├── exceptions.py    # 业务异常体系（1xxx/2xxx/3xxx/4xxx）
│   ├── logging.py       # Loguru + structlog 结构化日志
│   └── metrics.py       # 爬取指标收集
├── api/                 # RESTful API 层
│   ├── middleware.py    # 请求ID/耗时/CORS
│   ├── deps.py          # FastAPI 依赖注入（DB/仓库/认证/分页/快照库 session）
│   ├── response.py      # 统一响应格式
│   └── v1/              # API v1 路由
│       ├── spiders.py   # 爬虫 CRUD + run/pause/resume
│       ├── jobs.py      # 任务查询与取消
│       ├── results.py   # 结果查询与导出
│       ├── metrics.py   # 指标查询
│       └── nfra.py      # nfra 手动触发/状态/数据查询
├── crawlers/            # 独立采集器（不走 BaseSpider 框架）
│   ├── nfra.py          # nfra 编排：列表发现→过滤→详情抽取→写 djg_data
│   ├── nfra_extractor.py # 任职资格：代码侧选择器 + 百炼 LLM 抽取
│   ├── nfra_capital.py  # 注册资本/开业编排→写 capital_change_data
│   ├── nfra_capital_extractor.py # 注册资本/开业 LLM 抽取
│   ├── nfra_equity.py   # 股权变更/开业股东编排→写 equity_change_data
│   └── nfra_equity_extractor.py  # 股权变更/开业股东 LLM 抽取
├── spiders/             # 爬虫框架
│   ├── base.py          # BaseSpider 抽象基类
│   ├── registry.py      # 爬虫注册表（装饰器自动注册）
│   └── examples/        # 示例爬虫
├── fetchers/            # 请求层
│   ├── http.py          # HttpFetcher（httpx + 重试 + 代理）
│   ├── browser.py       # Playwright/Camoufox 浏览器 fetcher
│   └── proxy.py         # 代理池管理
├── pipeline/            # 数据处理流水线
│   ├── validators.py    # Pydantic v2 模型校验
│   ├── cleaners.py      # 数据清洗链
│   └── dedup.py         # Redis Bloom Filter URL 去重
├── scheduler/           # 调度引擎
│   ├── engine.py        # Celery app + APScheduler + nfra_crawl_task + init_nfra_schedule
│   ├── jobs.py          # 任务分发逻辑
│   └── triggers.py      # Cron/Interval 触发器解析
└── storage/             # 数据持久化
    ├── database.py      # SQLAlchemy 2.x async session（主库 scraper_db）
    ├── redis.py         # Redis 连接管理
    ├── models.py        # ORM 模型（Spider/Job/Item/Metrics）
    ├── repositories.py  # 仓库模式数据访问
    ├── snapshot.py      # 独立库 zbd_crawler_data：web_snapshot 快照表
    ├── djg_data.py      # 独立库 zbd_crawler_data：djg_data 任职资格抽取表
    ├── capital_change_data.py # 独立库：capital_change_data 注册资本/开业表
    └── equity_change_data.py  # 独立库：equity_change_data 股权变更/股东表
```

---

## 关键接口规范

### 1. 爬虫定义接口

所有爬虫必须继承 `BaseSpider` 并通过 `@register_spider` 注册：

```python
from scrapling.engines.toolbelt.custom import Response
from web_scraper_service.spiders.base import BaseSpider
from web_scraper_service.spiders.registry import register_spider

@register_spider
class MySpider(BaseSpider):
    name = "my_spider"           # 唯一标识，必填
    start_urls = ["..."]         # 起始 URL 列表
    concurrency = 5              # 并发数（Semaphore 控制）
    download_delay = 0.5         # 请求间隔（秒）
    use_playwright = False       # True → StealthyFetcher（SPA）
    use_camoufox = False         # True → Camoufox（高防护站点）
    use_stealthy = False         # True → stealth headers
    retry_times = 3
    proxy_enabled = False

    async def parse(self, response: Response, **kwargs) -> Any:
        """解析页面，yield dict 或 dataclass。"""
        ...

    async def pipeline(self, item: dict) -> dict | None:
        """后处理，返回 None 丢弃该 item。"""
        ...
```

**约束：**
- `name` 全局唯一，重复注册会覆盖并发出警告。
- `parse()` 必须实现，签名固定为 `async def parse(self, response: Response, **kwargs)`。
- `pipeline()` 可选，但返回 `None` 的 item 会被丢弃不入库。
- 并发控制通过 `asyncio.Semaphore(concurrency)` 实现，**不是线程池**。

### 2. 数据校验接口

在 `pipeline/validators.py` 中定义 Pydantic 模型并注册到 `ITEM_MODELS`：

```python
class MyItem(BaseItem):
    url: str
    title: str
    price: float

ITEM_MODELS["my_spider"] = MyItem
```

未注册的爬虫会使用 `BaseItem` 仅做基础 URL 校验。

### 3. API 接口

所有端点需要 `X-API-Key` Header，响应格式统一：

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "pagination": {"page": 1, "size": 20, "total": 100}
}
```

**Spider 管理：**
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/spiders` | 创建爬虫，可选 `schedule` cron 表达式 |
| GET | `/api/v1/spiders` | 分页列表 |
| GET | `/api/v1/spiders/{id}` | 详情 |
| PATCH | `/api/v1/spiders/{id}` | 更新配置（`exclude_none`） |
| DELETE | `/api/v1/spiders/{id}` | 删除并取消调度 |
| POST | `/api/v1/spiders/{id}/run` | 即时触发爬取（Celery 异步） |
| POST | `/api/v1/spiders/{id}/pause` | 暂停调度 |
| POST | `/api/v1/spiders/{id}/resume` | 恢复调度 |

**Job 管理：**
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/jobs` | 列表，支持 `spider_id` 过滤 |
| GET | `/api/v1/jobs/{id}` | 详情（含成功/失败/去重计数） |
| POST | `/api/v1/jobs/{id}/cancel` | 取消 pending/running 任务 |

**nfra 采集：**（独立于 spider 注册表，写 `zbd_crawler_data` 独立库）
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/nfra/djg/crawl` | 任职资格：手动触发 `{item_id?, pages?}`（默认 4110/5），返回 `job_id` |
| GET | `/api/v1/nfra/djg/crawl/{job_id}` | 轮询 Celery 任务状态 |
| GET | `/api/v1/nfra/djg/data` | 按 `crawl_time` 范围分页查询 `djg_data` |
| POST | `/api/v1/nfra/capital/crawl` | 注册资本/开业：手动触发 `{item_id?, pages?}`（item_id 省略→4110+4291） |
| GET | `/api/v1/nfra/capital/crawl/{job_id}` | 轮询任务状态 |
| GET | `/api/v1/nfra/capital/data` | 分页查询 `capital_change_data` |
| POST | `/api/v1/nfra/equity/crawl` | 股权变更/开业股东：手动触发 `{item_id?, pages?}`（item_id 省略→4110+4291） |
| GET | `/api/v1/nfra/equity/crawl/{job_id}` | 轮询任务状态 |
| GET | `/api/v1/nfra/equity/data` | 分页查询 `equity_change_data` |

> 完整接口字段/示例见 `docs/API.md`。

### 4. 调度接口

双调度系统：
- **APScheduler**（in-process，AsyncIOScheduler）：从 DB 加载 spider cron + 注册 nfra 每日定时
- **Celery**（分布式）：`crawl_task` 执行 BaseSpider 爬虫；`nfra_crawl_task` 执行 nfra 采集（`run_crawl`）；`clean_task` 后处理

**nfra 定时**：`init_nfra_schedule()` 在 lifespan 启动时注册 cron `0 8 * * *`（Asia/Shanghai），每日 8 点派发 `nfra_crawl_task.delay(4110, pages)` 与 `(4291, pages)`；`NFRA_CAPITAL_SCHEDULE_ENABLED=true`（默认）时一并派发 `nfra_capital_crawl_task.delay(None, pages)`，`NFRA_EQUITY_SCHEDULE_ENABLED=true`（默认）时一并派发 `nfra_equity_crawl_task.delay(None, pages)`（均采集 4110+4291）。`NFRA_SCHEDULE_ENABLED=false` 关闭整个 nfra 定时（含 capital/equity）；`NFRA_CAPITAL_SCHEDULE_ENABLED`/`NFRA_EQUITY_SCHEDULE_ENABLED` 单独控制各类。手动 `POST /api/v1/nfra/djg/crawl` 用 `apply_async(task_id=job_id)` 派发，`GET /crawl/{job_id}` 经 `AsyncResult` 查状态。

调度状态变更流：
```
create spider + schedule → APScheduler add_job
update schedule → remove_job → add_job
delete/pause spider → remove_job
resume spider → add_job（如果有 schedule）
run spider → 直接 dispatch_crawl → Celery task
```

### 5. 数据库模型

**SpiderModel** — 爬虫配置主表：
- `name` 唯一索引，不可重复
- `config` JSONB 存储自定义参数
- `is_active` 控制调度开关，不影响手动触发

**JobModel** — 任务执行记录：
- `status`: pending → running → completed/failed/cancelled
- `trigger_type`: manual | scheduled | api
- `result` JSONB 存储 `CrawlMetrics` 汇总

**ItemModel** — 爬取结果：
- `content_hash` 用于去重检测（SHA-256）
- `data` JSONB 存储结构化数据

**独立库 `zbd_crawler_data`（非主库 `scraper_db`）：**
- `web_snapshot(doc_id, snapshot, crawl_time)` — nfra 详情页原始响应快照（`storage/snapshot.py`）。`doc_id` 主键，`ON CONFLICT DO NOTHING` 跳过。
- `djg_data(id, doc_id, issue_date, issuing_authority, doc_number, institution_name, person_name, position, doc_title, doc_url, crawl_time)` — nfra 结构化抽取结果（`storage/djg_data.py`）。唯一约束 `(doc_id, person_name)`，一人一行，`ON CONFLICT DO NOTHING` 跳过。
- `capital_change_data(...)` — 注册资本/开业抽取（`storage/capital_change_data.py`）。唯一约束 `(doc_id, institution_name, change_type)`，`change_type` 为 `变更注册资本`/`机构成立`，`ON CONFLICT DO NOTHING` 跳过。
- `equity_change_data(...)` — 股权变更/开业股东抽取（`storage/equity_change_data.py`）。唯一约束 `(doc_id, institution_name, shareholder_name, change_method)`，`change_method` 为 `转入`/`转出`，`ON CONFLICT DO NOTHING` 跳过。四表均经 `SnapshotSession` 访问，`CREATE TABLE IF NOT EXISTS` 自动建表，不走 Alembic。

---

## 注意事项

### 1. 爬虫注册时机

示例爬虫在 `main.py` lifespan 中通过硬编码 `import` 注册。新增自定义爬虫时，**必须在 lifespan 的 import 段添加对应模块**，否则无法通过 API 调度。

```python
# main.py startup
import web_scraper_service.spiders.examples.static_spider  # noqa: F401
import web_scraper_service.spiders.examples.spa_spider      # noqa: F401
# TODO: 添加你的爬虫 import
```

### 2. 异步 Session 生命周期

所有仓库方法使用 `await self.session.flush()` 而非 `commit()`，**事务由 FastAPI 依赖中的 `get_session` 上下文管理器统一提交/回滚**。在 Celery task 中需自行管理 session。

### 3. Celery Task 中的事件循环

`crawl_task` / `nfra_crawl_task` 是同步 Celery task，内部通过 `asyncio.new_event_loop()` 运行异步爬虫。**不要在 task 中直接调用 `asyncio.run()`**，避免嵌套事件循环问题。

### 4. Redis DB 分配

| DB | 用途 |
|----|------|
| 0 | URL 去重（Bloom Filter：`dedup:{spider_name}`） |
| 1 | Job 状态缓存（TTL 24h）+ Celery result backend |
| 2 | Celery broker（任务队列） |
| 3 | API 限流（SlowAPI Redis storage） |

修改 Redis 配置时注意不要冲突。

### 5. Playwright / Camoufox / Scrapling 浏览器依赖

`use_playwright=True` 或 `use_camoufox=True` 会动态 import `StealthyFetcher`。nfra 采集器用 `AsyncStealthySession`（列表，patchright chromium）与 `AsyncDynamicSession`（详情，playwright chromium）。**部署须先 `scrapling install` 下载浏览器**（Docker 镜像构建时 baked 进 `$SCRAPLING_HOME`；本地开发手动跑 `scrapling install`）。

### 6. 独立快照库 zbd_crawler_data

`web_snapshot` 与 `djg_data` 在独立库 `zbd_crawler_data`（非主库 `scraper_db`），经 `SnapshotSession`（`storage/snapshot.py`）访问。postgres 镜像首启只建 `scraper_db`，`zbd_crawler_data` 需手动或经 init-db.sql 创建（见 `docker/init-db.sql`）。`SNAPSHOT_DATABASE_URL` 默认从 postgres 凭据派生。

### 7. nfra 采集器独立于 BaseSpider

`crawlers/nfra.py` 的 `run_crawl` 不走 `BaseSpider`/registry/ItemModel 管道，直接经 Celery `nfra_crawl_task` 执行，写独立 `djg_data` 表。注册资本/开业（`crawlers/nfra_capital.py` + `nfra_capital_crawl_task`）与股权变更/开业股东（`crawlers/nfra_equity.py` + `nfra_equity_crawl_task`）同理，分别写 `capital_change_data`、`equity_change_data`。三类采集均复用 `discover_doc_rows`/`build_detail_html_url`，详情抽取用**渲染后 DOM**（`resp.html_content`，非原始 `resp.body`）——选择器针对渲染 DOM 设计。

### 8. APScheduler 版本

`pyproject.toml` 锁 `apscheduler>=3.10,<4`。**不要升到 4.x**：4.x alpha API 与 `engine.py` 用的 3.x API（`apscheduler.schedulers.asyncio.AsyncIOScheduler`、`CronTrigger.from_crontab`）不兼容，升级会致 `init_scheduler` 静默失败、调度全停。

### 9. 百炼 LLM 抽取

nfra 详情字段经百炼 `qwen3.5-35b-a3b`（OpenAI 兼容 API）抽取。需在 `.env` 配 `DASHSCOPE_API_KEY`、`BAILIAN_BASE_URL`、`BAILIAN_MODEL`。抽取规则（prompt + 选择器）固化在 `crawlers/nfra_extractor.py`。

### 10. 代理池

`PROXY_LIST` 支持逗号分隔的 HTTP/SOCKS5 代理列表。`proxy_rotation_strategy` 仅支持 `round-robin` 和 `random`，在 `fetchers/proxy.py` 中实现。

使用 qg.net 动态代理池时，`PROXY_POOL_URL` 需配置为完整 URL，包含 `num=1`（每次取 1 个 IP）和 `keep_alive=1440`（有效期 1440 分钟），例如：

```env
PROXY_POOL_URL=https://exclusive.proxy.qg.net/get?key=YOUR_KEY&num=1&keep_alive=1440
```

目标站点返回 `403 Forbidden` 时，`crawlers/nfra.py` 会将当前代理标记失败、切换到池中下一个代理；若代理全部耗尽，则等待 5 分钟后重新从 `PROXY_POOL_URL` 获取新代理。

### 11. 配置加载优先级

`config.py` 使用 `pydantic-settings`：
1. 环境变量（大写，支持 `__` 嵌套）
2. `.env` 文件
3. 默认值

生产环境务必设置 `API_KEY`，空值时 API 返回 500。`SNAPSHOT_DATABASE_URL` 为 `@property`（空 env 不会覆盖派生默认值）。

### 12. 错误码规范

| 范围 | 类别 | 示例 |
|------|------|------|
| 1xxx | Spider 错误 | 1001 未找到，1002 配置非法 |
| 2xxx | Job 错误 | 2001 运行中，2002 调度冲突 |
| 3xxx | 运行时错误 | 3001 超时，3002 解析失败，3003 代理耗尽 |
| 4xxx | 存储错误 | 4001 写入失败，4002 连接断开 |

新增错误码时请继承 `AppError` 并遵循区间约定。

### 13. 开发常用命令

```bash
make install      # uv sync
make dev          # 启动 API（热重载）
make worker       # 启动 Celery worker（执行采集）
make beat         # 启动 Celery beat
make test         # pytest + coverage
make lint         # ruff + mypy
make migrate      # alembic upgrade head
make docker-up    # 启动全套基础设施
make crawl-nfra        # nfra 采集 itemId=4110（默认 5 页）
make crawl-nfra-4291   # nfra 采集 itemId=4291
make crawl-nfra-capital  # 注册资本/开业采集（默认 4110+4291）
make crawl-nfra-equity   # 股权变更/开业股东采集（默认 4110+4291）
# NFRA_ITEM_ID=4291 NFRA_PAGES=3 make crawl-nfra   # 自定义
```

本地数据库（仅 PostgreSQL+Redis）：`docker compose -f docker-compose.dev.yml up -d`。完整接口说明见 `docs/API.md`。

---

## 扩展建议

- 新增爬虫：继承 `BaseSpider` → 实现 `parse()` → `@register_spider` → `main.py` import
- 新增校验模型：继承 `BaseItem` → 注册到 `ITEM_MODELS`
- 新增存储后端：在 `storage/` 下实现对应仓库，参考 `repositories.py` 模式
- 新增调度触发器：在 `scheduler/triggers.py` 扩展 `parse_cron` 逻辑
