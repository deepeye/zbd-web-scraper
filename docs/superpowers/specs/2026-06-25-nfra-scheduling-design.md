# nfra 采集任务手动与定时调度 — 设计文档

## 概述

为 nfra 采集器新增手动启动（HTTP 接口，可指定页数）与定时调度（每日 8 点，默认前 5 页）。复用项目现有 Celery + APScheduler 基础设施，不把 nfra 塞进 BaseSpider 框架（nfra 写独立 `djg_data` 表，与 ItemModel 管道不匹配）。

## 决策（已与用户确认）

1. 定时任务每日 8 点同时采集 **4110 + 4291** 两个 itemId，各 5 页。
2. 手动 API `POST /api/v1/nfra/crawl {item_id?, pages?}`，`item_id` 默认 4110、`pages` 默认 5，均可选；异步返回 `job_id`。
3. 手动启动后提供 `GET /api/v1/nfra/crawl/{job_id}` 状态查询接口（Celery `AsyncResult`）。

## 架构

新增专用 Celery 任务 `nfra_crawl_task(item_id, pages)` 执行 `run_crawl`。手动 API 派发该任务并返回 `job_id`；定时任务每日 8 点（Asia/Shanghai）派发两个任务（4110 + 4291，各 5 页）。状态接口通过 Celery `AsyncResult` 查询。定时触发在 FastAPI 进程内 APScheduler，实际执行在 Celery worker。

## 组件

### 1. Celery 任务（`scheduler/engine.py`，与现有 `crawl_task` 并列）

```python
@celery_app.task(bind=True, max_retries=1, default_retry_delay=60)
def nfra_crawl_task(self, item_id: int, pages: int) -> dict[str, Any]:
    from web_scraper_service.crawlers.nfra import run_crawl
    loop = asyncio.new_event_loop()
    try:
        stats = loop.run_until_complete(run_crawl(item_id=item_id, pages=pages))
    finally:
        loop.close()
    return stats
```

镜像现有 `crawl_task` 的 `asyncio.new_event_loop` 模式；`max_retries=1`（`run_crawl` 内部已对单 doc 容错，任务级仅轻度重试）。

### 2. 定时调度（`scheduler/engine.py` 新增 `init_nfra_schedule()`）

在 lifespan `init_scheduler()` 之后调用：
- 用 `CronTrigger.from_crontab(settings.nfra_schedule_cron, timezone="Asia/Shanghai")` 注册一个 APScheduler 任务。
- 触发时派发 `nfra_crawl_task.delay(4110, settings.nfra_schedule_pages)` 与 `nfra_crawl_task.delay(4291, settings.nfra_schedule_pages)`。
- `nfra_schedule_enabled`（默认 True）控制是否注册；未注册时日志提示。
- 复用模块级 `_scheduler`（APScheduler 实例，`init_scheduler` 已启动）。

### 3. API 路由（新建 `api/v1/nfra.py`，注册到 main.py）

- `POST /api/v1/nfra/crawl`
  - body：`{"item_id": int = 4110, "pages": int = 5}`（均可选）
  - 校验 `pages >= 1`，否则 400。
  - 生成 `job_id = uuid4()`，`nfra_crawl_task.apply_async(args=[item_id, pages], task_id=job_id)`。
  - 返回 `{"job_id", "item_id", "pages", "status": "pending"}`。需 `X-API-Key`。
- `GET /api/v1/nfra/crawl/{job_id}`
  - `AsyncResult(job_id, app=celery_app)` 读 `state`（PENDING/STARTED/SUCCESS/FAILURE/RETRY）+ `result`（stats dict，仅 SUCCESS；FAILURE 时为异常信息）。
  - 返回 `{"job_id", "status", "result"}`。需 `X-API-Key`。

### 4. 配置（`config.py` + `.env.example`）

- `nfra_schedule_enabled: bool = True`
- `nfra_schedule_cron: str = "0 8 * * *"`
- `nfra_schedule_pages: int = 5`

`.env.example` 加：
```
NFRA_SCHEDULE_ENABLED=true
NFRA_SCHEDULE_CRON=0 8 * * *
NFRA_SCHEDULE_PAGES=5
```

### 5. main.py lifespan

`init_scheduler()` 后调用 `init_nfra_schedule()`；`app.include_router(nfra.router, prefix="/api/v1")`。

## 数据流

```
手动：POST /api/v1/nfra/crawl {item_id,pages}
  → nfra_crawl_task.apply_async(task_id=job_id)   [Celery worker 执行 run_crawl]
  → 返回 job_id
  → GET /api/v1/nfra/crawl/{job_id} 查 AsyncResult 状态

定时：APScheduler cron 0 8 * * * (Asia/Shanghai)   [FastAPI 进程内]
  → nfra_crawl_task.delay(4110, 5) + nfra_crawl_task.delay(4291, 5)
  → Celery worker 执行
```

## 运行要求

- API 服务器（`make dev`）：提供手动接口 + 承载 APScheduler 定时触发。
- Celery worker（`make worker`）：实际执行 `run_crawl`（浏览器+LLM，长任务）。
- Redis（`docker-compose.dev.yml`）：Celery broker/backend。
- `DASHSCOPE_API_KEY` 已配置。

## 错误处理

| 场景 | 处理 |
|------|------|
| 手动 API 参数非法（pages<1） | 400 |
| 未配置 API_KEY | 500（现有 `verify_api_key` 行为） |
| Celery worker 未跑 | 任务 PENDING，状态接口返回 pending |
| 定时派发失败 | 日志记录，APScheduler 下个周期重试 |
| run_crawl 内部单 doc 失败 | 已有容错（返回 []，不中断） |

## 测试

- `tests/test_api/test_nfra.py`：
  - POST 接口：mock `nfra_crawl_task.apply_async` 返回 job_id、默认参数填充（item_id=4110/pages=5）、`pages<1` 报 400、需 API key。
  - GET 状态接口：mock `AsyncResult` 各 state（PENDING/SUCCESS/FAILURE），返回结构正确。
  - `init_nfra_schedule`：mock APScheduler `_scheduler.add_job`，确认注册了 cron + 派发两个 itemId（4110/4291）。
- 既有 29 测试不回归。

## 改动文件清单

| 文件 | 动作 |
|------|------|
| `src/web_scraper_service/scheduler/engine.py` | 修改 — 加 `nfra_crawl_task` + `init_nfra_schedule()` |
| `src/web_scraper_service/api/v1/nfra.py` | 新建 — POST/GET 路由 |
| `src/web_scraper_service/main.py` | 修改 — 注册路由 + lifespan 调 `init_nfra_schedule` |
| `src/web_scraper_service/config.py` | 修改 — 加 3 个 nfra_schedule_* 配置 |
| `.env.example` | 修改 — 加 NFRA_SCHEDULE_* |
| `tests/test_api/test_nfra.py` | 新建 — API + 调度注册单测 |

## 注意事项

- 定时用 APScheduler（FastAPI 进程内）派发到 Celery worker 执行，与现有 `add_scheduled_spider` 模式一致；worker 必须运行否则任务滞留 PENDING。
- 时区：CronTrigger 显式 `timezone="Asia/Shanghai"`，避免服务器 UTC 导致 8 点偏移。
- 状态查询依赖 Celery result backend（redis db1，`result_expires=86400`）；任务完成 24h 后状态不可查。
- `nfra_crawl_task.apply_async(task_id=job_id)` 使 `AsyncResult(job_id)` 可追踪。
- nfra 路由与现有 spider 路由独立，不复用 `/spiders/{id}/run`（nfra 不在 spider registry）。
