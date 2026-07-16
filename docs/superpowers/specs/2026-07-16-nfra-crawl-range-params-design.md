# nfra 爬虫 API 参数：从 pages 改为 start_page / end_page

**日期：** 2026-07-16  
**状态：** 设计已批准，待实现  
**标签：** nfra, api, crawler, refactor

---

## 1. 目标

将三个 nfra 爬虫 API（`/api/v1/nfra/djg/crawl`、`/api/v1/nfra/capital/crawl`、`/api/v1/nfra/equity/crawl`）的 `pages` 参数替换为 `start_page` / `end_page`，使调用方可以指定任意页范围（如第 3-7 页），不再只能从第 1 页开始。

## 2. 范围

全栈替换——涉及以下 7 个边界点，每个点都是机械的 `pages` → `start_page + end_page` 替换，无逻辑重构：

| # | 层 | 当前 | 改后 | 备注 |
|---|------|-------|------|------|
| 1 | `crawlers/nfra.py:discover_doc_rows` | `(item_id, pages)` 循环 `range(1, pages+1)` | `(item_id, start_page, end_page)` 循环 `range(start_page, end_page+1)` | 最底层 |
| 2 | `crawlers/nfra.py:run_crawl` | `pages: int = 5` | `start_page: int = 1, end_page: int = 5` | 上游调用传参 |
| 3 | `crawlers/nfra_capital.py:run_crawl` | 同上 | 同上 | 同上 |
| 4 | `crawlers/nfra_equity.py:run_crawl` | 同上 | 同上 | 同上 |
| 5 | `scripts/crawl_nfra.py` CLI | `--pages` | `--start-page` / `--end-page` | 三个脚本 |
| 6 | `scripts/crawl_nfra_capital.py` CLI | 同上 | 同上 | 同上 |
| 7 | `scripts/crawl_nfra_equity.py` CLI | 同上 | 同上 | 同上 |
| 8 | `scheduler/engine.py` Celery tasks | `(item_id, pages)` + `--pages N` | `(item_id, start_page, end_page)` + `--start-page N --end-page N` | 三个 task |
| 9 | `scheduler/engine.py:init_nfra_schedule` | `pages = settings.nfra_schedule_pages` | `start_page, end_page` 取自配置 | 定时调度 |
| 10 | `api/v1/nfra.py` Request models | `pages: int = Field(default=5)` | `start_page: int = Field(default=1, ge=1)`, `end_page: int = Field(default=5, ge=1)` | 三个 model |
| 11 | `config.py` | `nfra_schedule_pages: int = 5` | `nfra_schedule_start_page: int = 1`, `nfra_schedule_end_page: int = 5` | 定时配置 |
| 12 | `Makefile` | `NFRA_PAGES` | `NFRA_START_PAGE` / `NFRA_END_PAGE` | 四个 target |

## 3. 详细设计

### 3.1 API 请求体

```python
# 旧
class CrawlRequest(BaseModel):
    item_id: int = Field(default=4110)
    pages: int = Field(default=5)

# 新
class CrawlRequest(BaseModel):
    item_id: int = Field(default=4110)
    start_page: int = Field(default=1, ge=1)
    end_page: int = Field(default=5, ge=1)
```

三个 request model（`CrawlRequest` / `CapitalCrawlRequest` / `EquityCrawlRequest`）同步修改。

**验证规则：** `end_page >= start_page`，否则返回 400。已有 `pages < 1` 的验证改为 `start_page < 1` / `end_page < start_page`。

**响应：** `"end_page"` 替代 `"pages"`。

### 3.2 Celery Task

三个 task 签名：

```python
# 旧
nfra_crawl_task(self, item_id: int, pages: int)
nfra_capital_crawl_task(self, item_id: int | None, pages: int)
nfra_equity_crawl_task(self, item_id: int | None, pages: int)

# 新
nfra_crawl_task(self, item_id: int, start_page: int, end_page: int)
nfra_capital_crawl_task(self, item_id: int | None, start_page: int, end_page: int)
nfra_equity_crawl_task(self, item_id: int | None, start_page: int, end_page: int)
```

子进程命令构建中，`--pages N` → `--start-page N --end-page N`；`timeout` 计算用 `pages_count = end_page - start_page + 1`。

### 3.3 CLI 脚本

```bash
# 旧
python scripts/crawl_nfra.py --pages 5 --item-id 4110
# 新
python scripts/crawl_nfra.py --start-page 1 --end-page 5 --item-id 4110
```

三个脚本的 `--pages` 参数拆为 `--start-page`（默认 1）和 `--end-page`（默认 5）。

### 3.4 定时调度配置

```python
# config.py
# 旧
nfra_schedule_pages: int = 5
# 新
nfra_schedule_start_page: int = 1
nfra_schedule_end_page: int = 5
```

`init_nfra_schedule()` 中 `pages = settings.nfra_schedule_pages` 改为分别取 `start_page` / `end_page`；`delay(iid, pages)` 改为 `delay(iid, start_page, end_page)`。

### 3.5 Makefile

所有 `crawl-nfra*` target 的 `NFRA_PAGES` 环境变量改为 `NFRA_START_PAGE` + `NFRA_END_PAGE`：

```makefile
crawl-nfra:
	uv run python scripts/crawl_nfra.py \
		--start-page $(or ${NFRA_START_PAGE},1) \
		--end-page $(or ${NFRA_END_PAGE},5) \
		--item-id $(or ${NFRA_ITEM_ID},4110)
```

### 3.6 翻页逻辑

`discover_doc_rows()` 循环范围从 `range(1, pages + 1)` 变为 `range(start_page, end_page + 1)`。日志消息中的措辞相应调整。

## 4. 错误处理

| 场景 | 错误 | 说明 |
|------|--------|------|
| `start_page < 1` | 400 | 页码必须 >= 1 |
| `end_page < start_page` | 400 | end_page 必须 >= start_page |
| API 服务器运行中升级 | 无 | 旧 `pages` 参数不存在兼容模式；配置/脚本同步部署 |

> **向后兼容：** 此变更不保留 `pages` 参数。调用方需在部署后更新请求体。由于这是内部 API（非公开 SDK），在单次部署窗口内同步更新调用方即可。

## 5. 测试策略

- 单元：更新 `discover_doc_rows` / `parse_doc_rows` 测试（如有）以覆盖不同 (start_page, end_page) 组合
- 手动 smoke：用 `make crawl-nfra NFRA_START_PAGE=2 NFRA_END_PAGE=3` 验证仅爬第 2-3 页
- 三个采集器（djg / capital / equity）各 smoke 一次

## 6. 涉及文件清单

| 文件 | 改动 |
|------|------|
| `src/web_scraper_service/api/v1/nfra.py` | 三个 request model + 验证 |
| `src/web_scraper_service/crawlers/nfra.py` | `discover_doc_rows` + `run_crawl` 签名 |
| `src/web_scraper_service/crawlers/nfra_capital.py` | `run_crawl` 签名 |
| `src/web_scraper_service/crawlers/nfra_equity.py` | `run_crawl` 签名 |
| `src/web_scraper_service/scheduler/engine.py` | 三个 task + `init_nfra_schedule` + subprocess cmd |
| `src/web_scraper_service/config.py` | 配置项 |
| `scripts/crawl_nfra.py` | CLI 参数 |
| `scripts/crawl_nfra_capital.py` | CLI 参数 |
| `scripts/crawl_nfra_equity.py` | CLI 参数 |
| `Makefile` | four targets |
| `.env.example`（如有） | 更新配置注释 |
