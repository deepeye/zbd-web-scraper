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
│   ├── deps.py          # FastAPI 依赖注入（DB/仓库/认证/分页）
│   ├── response.py      # 统一响应格式
│   └── v1/              # API v1 路由
│       ├── spiders.py   # 爬虫 CRUD + run/pause/resume
│       ├── jobs.py      # 任务查询与取消
│       ├── results.py   # 结果查询与导出
│       └── metrics.py   # 指标查询
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
│   ├── engine.py        # Celery app + APScheduler 封装
│   ├── jobs.py          # 任务分发逻辑
│   └── triggers.py      # Cron/Interval 触发器解析
└── storage/             # 数据持久化
    ├── database.py      # SQLAlchemy 2.x async session
    ├── redis.py         # Redis 连接管理
    ├── models.py        # ORM 模型（Spider/Job/Item/Metrics）
    └── repositories.py  # 仓库模式数据访问
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

### 4. 调度接口

双调度系统：
- **APScheduler**（in-process）：负责从 DB 加载 cron 表达式并触发 Celery task
- **Celery**（分布式）：`crawl_task` 实际执行爬虫，`clean_task` 后处理

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

`crawl_task` 是同步 Celery task，内部通过 `asyncio.new_event_loop()` 运行异步爬虫。**不要在 task 中直接调用 `asyncio.run()`**，避免嵌套事件循环问题。

### 4. Redis DB 分配

| DB | 用途 |
|----|------|
| 0 | URL 去重（Bloom Filter：`dedup:{spider_name}`） |
| 1 | Job 状态缓存（TTL 24h）+ Celery result backend |
| 2 | Celery broker（任务队列） |
| 3 | API 限流（SlowAPI Redis storage） |

修改 Redis 配置时注意不要冲突。

### 5. Playwright / Camoufox 依赖

`use_playwright=True` 或 `use_camoufox=True` 会动态 import `StealthyFetcher`。确保部署环境已安装浏览器依赖（`scrapling[all]` 包含 Playwright，Camoufox 需额外配置）。

### 6. 代理池

`PROXY_LIST` 支持逗号分隔的 HTTP/SOCKS5 代理列表。`proxy_rotation_strategy` 仅支持 `round-robin` 和 `random`，在 `fetchers/proxy.py` 中实现。

### 7. 配置加载优先级

`config.py` 使用 `pydantic-settings`：
1. 环境变量（大写，支持 `__` 嵌套）
2. `.env` 文件
3. 默认值

生产环境务必设置 `API_KEY`，空值时 API 返回 500。

### 8. 错误码规范

| 范围 | 类别 | 示例 |
|------|------|------|
| 1xxx | Spider 错误 | 1001 未找到，1002 配置非法 |
| 2xxx | Job 错误 | 2001 运行中，2002 调度冲突 |
| 3xxx | 运行时错误 | 3001 超时，3002 解析失败，3003 代理耗尽 |
| 4xxx | 存储错误 | 4001 写入失败，4002 连接断开 |

新增错误码时请继承 `AppError` 并遵循区间约定。

### 9. 开发常用命令

```bash
make install      # uv sync
make dev          # 启动 API（热重载）
make worker       # 启动 Celery worker
make beat         # 启动 Celery beat
make test         # pytest + coverage
make lint         # ruff + mypy
make migrate      # alembic upgrade head
make docker-up    # 启动全套基础设施
```

---

## 扩展建议

- 新增爬虫：继承 `BaseSpider` → 实现 `parse()` → `@register_spider` → `main.py` import
- 新增校验模型：继承 `BaseItem` → 注册到 `ITEM_MODELS`
- 新增存储后端：在 `storage/` 下实现对应仓库，参考 `repositories.py` 模式
- 新增调度触发器：在 `scheduler/triggers.py` 扩展 `parse_cron` 逻辑
