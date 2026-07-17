# Web Scraper Service — API 接口说明文档

> 交互式 API 文档（Swagger）：`http://localhost:8000/docs`、`http://localhost:8000/redoc`
> 本文档为静态中文说明，字段均从路由源码提取。

## 1. 概述

### Base URL

```
http://localhost:8000
```

### 认证

除 `GET /health` 外，所有接口均需在请求头携带 API Key：

```
X-API-Key: <API_KEY>
```

`API_KEY` 由 `.env` 的 `API_KEY` 配置。未配置时所有鉴权接口返回 `500`；缺失或错误返回 `401`。

### 统一响应格式

```json
{
  "code": 0,
  "message": "success",
  "data": {},
  "pagination": {"page": 1, "size": 20, "total": 100}
}
```

- `code`：`0` 成功；非 `0` 为业务错误码（见 [第 8 节](#8-错误码)）。
- `data`：业务数据，可为对象、数组或 `null`。
- `pagination`：仅分页接口返回。

### 分页约定

查询参数：

| 参数 | 类型 | 默认 | 约束 |
|------|------|------|------|
| `page` | int | 1 | ≥ 1 |
| `size` | int | 20 | 1–100 |

`offset = (page - 1) * size`。

### HTTP 状态码

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 400 | 参数非法 / 状态不允许操作 |
| 401 | 未认证（API Key 缺失或错误） |
| 404 | 资源不存在 |
| 409 | 资源冲突（如名称重复） |
| 422 | 请求体/参数格式不符（Pydantic 校验） |
| 500 | 服务端错误（如未配置 API_KEY、库连接失败） |

---

## 2. 健康检查

### `GET /health`

服务健康探活，**无需鉴权**。

**响应**

```json
{
  "status": "ok",
  "app": "web-scraper-service",
  "env": "development"
}
```

---

## 3. 爬虫管理 `/api/v1/spiders`

### 3.1 创建爬虫 `POST /api/v1/spiders`

**鉴权**：需 `X-API-Key`

**请求体**（`SpiderCreate`）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `name` | string | 是 | — | 爬虫唯一标识，1–255 字符 |
| `url` | string | 是 | — | 起始 URL |
| `schedule` | string | 否 | null | cron 表达式（5 或 6 字段），设置后自动注册定时 |
| `use_playwright` | bool | 否 | false | SPA/JS 渲染用 StealthyFetcher |
| `use_camoufox` | bool | 否 | false | 高防护站点用 Camoufox |
| `use_stealthy` | bool | 否 | false | 启用隐身请求头 |
| `concurrency` | int | 否 | 5 | 并发数 |
| `proxy_enabled` | bool | 否 | false | 启用代理 |
| `retry_times` | int | 否 | 3 | 重试次数 |
| `download_delay` | float | 否 | 0.5 | 请求间隔（秒） |
| `config` | object | 否 | null | 自定义参数（JSONB） |
| `callback_url` | string | 否 | null | 完成回调地址 |

**成功响应**（200）

```json
{
  "code": 0,
  "message": "success",
  "data": {"id": "<uuid>", "name": "my_spider", "url": "https://example.com"}
}
```

**错误**：`409` name 已存在。

---

### 3.2 爬虫列表 `GET /api/v1/spiders`

**鉴权**：需 `X-API-Key`　**分页**：是

**响应 data**（数组）：

```json
[{
  "id": "<uuid>", "name": "my_spider", "url": "https://example.com",
  "schedule": "0 */6 * * *", "use_playwright": false,
  "is_active": true, "created_at": "2026-06-25T..."
}]
```

---

### 3.3 爬虫详情 `GET /api/v1/spiders/{spider_id}`

**路径参数**：`spider_id` (uuid)

**响应 data**：爬虫完整配置（含 `use_camoufox`/`use_stealthy`/`concurrency`/`proxy_enabled`/`retry_times`/`download_delay`/`config`/`is_active`/`callback_url`/`created_at`/`updated_at`）。

**错误**：`404` 未找到。

---

### 3.4 更新爬虫 `PATCH /api/v1/spiders/{spider_id}`

**请求体**（`SpiderUpdate`，所有字段可选，`exclude_none`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `url` | string | |
| `schedule` | string | cron 表达式 |
| `use_playwright` / `use_camoufox` / `use_stealthy` | bool | |
| `concurrency` | int | |
| `proxy_enabled` | bool | |
| `retry_times` | int | |
| `download_delay` | float | |
| `config` | object | |
| `is_active` | bool | 调度开关（不影响手动触发） |
| `callback_url` | string | |

**成功响应**：`{"data": {"id": "<uuid>", "name": "my_spider"}}`

**错误**：`400` 无可更新字段；`404` 未找到。改 `schedule` 会重新注册定时任务。

---

### 3.5 删除爬虫 `DELETE /api/v1/spiders/{spider_id}`

**响应**：`{"data": {"deleted": true}}`。删除同时取消定时调度。

**错误**：`404` 未找到。

---

### 3.6 立即运行 `POST /api/v1/spiders/{spider_id}/run`

异步触发爬取（派发到 Celery）。

**响应**：`{"data": {"job_id": "<uuid>", "spider_name": "my_spider", "status": "pending"}}`

**错误**：`404` 未找到。

---

### 3.7 暂停调度 `POST /api/v1/spiders/{spider_id}/pause`

移除定时任务并置 `is_active=false`（不影响手动 `run`）。

**响应**：`{"data": {"spider_name": "my_spider", "paused": true}}`

---

### 3.8 恢复调度 `POST /api/v1/spiders/{spider_id}/resume`

若有 `schedule` 则重新注册定时，置 `is_active=true`。

**响应**：`{"data": {"spider_name": "my_spider", "resumed": true}}`

---

## 4. 任务管理 `/api/v1/jobs`

### 4.1 任务列表 `GET /api/v1/jobs`

**鉴权**：需　**分页**：是

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `spider_id` | uuid | 按爬虫过滤（可选） |

**响应 data**（数组）：

```json
[{
  "id": "<uuid>", "spider_id": "<uuid>", "spider_name": "my_spider",
  "status": "completed", "trigger_type": "manual",
  "requests_total": 10, "items_scraped": 10,
  "started_at": "2026-06-25T...", "finished_at": "2026-06-25T...",
  "created_at": "2026-06-25T..."
}]
```

`status`：`pending` / `running` / `paused` / `completed` / `failed` / `cancelled`
`trigger_type`：`manual` / `scheduled` / `api`

---

### 4.2 任务详情 `GET /api/v1/jobs/{job_id}`

**响应 data**：含完整统计字段——`requests_total`/`requests_success`/`requests_failed`/`items_scraped`/`items_stored`/`items_deduped`/`error_message`/`result`(JSONB)/`started_at`/`finished_at`。

**错误**：`404`（`{"code":2001,"message":"Job not found"}`）。

---

### 4.3 取消任务 `POST /api/v1/jobs/{job_id}/cancel`

**响应**：`{"data": {"job_id": "<uuid>", "status": "cancelled"}}`

**错误**：`404` 未找到；`400` 当前状态不可取消（仅 `pending`/`running` 可取消）。

---

## 5. 结果查询 `/api/v1/results`

### 5.1 结果列表 `GET /api/v1/results`

**鉴权**：需　**分页**：是

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `spider_name` | string | 是 | 按爬虫名过滤 |
| `start_date` | datetime | 否 | `created_at >= start_date` |
| `end_date` | datetime | 否 | `created_at <= end_date` |

**响应 data**（数组）：

```json
[{
  "id": "<uuid>", "job_id": "<uuid>", "spider_name": "my_spider",
  "url": "https://example.com/page", "data": {"title": "..."},
  "content_hash": "<sha256>", "created_at": "2026-06-25T..."
}]
```

---

### 5.2 结果导出 `GET /api/v1/results/export`

流式导出，**不分页**（上限 10000 条）。

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `spider_name` | string | — | 必填 |
| `format` | string | `csv` | `csv` 或 `json`（json 为 NDJSON 流） |
| `start_date` / `end_date` | datetime | — | 日期范围 |

**响应**：`text/csv`（带 `Content-Disposition`）或 `application/x-ndjson`。

**错误**：`404` 无结果；`422` format 非法。

---

## 6. 指标 `/api/v1/metrics`

### 6.1 指标列表 `GET /api/v1/metrics`

**鉴权**：需　**分页**：是（仅按 `spider_name` 过滤时分页生效）

**查询参数**：

| 参数 | 类型 | 说明 |
|------|------|------|
| `spider_name` | string | 按爬虫过滤（与 job_id 二选一） |
| `job_id` | uuid | 按任务过滤 |

**响应 data**（数组）：

```json
[{
  "id": "<uuid>", "job_id": "<uuid>", "spider_name": "my_spider",
  "metric_type": "requests", "value": 10.0,
  "labels": {"status": "ok"}, "recorded_at": "2026-06-25T..."
}]
```

---

### 6.2 爬虫指标汇总 `GET /api/v1/metrics/summary/{spider_name}`

**路径参数**：`spider_name`

**响应 data**：

```json
{
  "spider_name": "my_spider",
  "metrics": {"requests": 100.0, "items_stored": 80.0},
  "total_records": 12
}
```

---

## 7. nfra 采集 `/api/v1/nfra`

nfra 采集器独立于 spider 注册表，写入独立库 `zbd_crawler_data`（`djg_data`/`capital_change_data`/`equity_change_data`）。手动触发经 Celery 异步执行；定时调度每日 8 点（Asia/Shanghai）由 APScheduler 派发——默认派发任职资格采集（4110+4291）、注册资本/开业采集（`NFRA_CAPITAL_SCHEDULE_ENABLED` 控制，默认开）与股权变更采集（`NFRA_EQUITY_SCHEDULE_ENABLED` 控制，默认开）；`NFRA_SCHEDULE_ENABLED=false` 关闭全部。

> 运行需：API 服务（`make dev`）+ Celery worker（`make worker`）+ Redis + `.env` 配置 `DASHSCOPE_API_KEY`。

### 7.1 手动触发采集 `POST /api/v1/nfra/djg/crawl`

异步派发 Celery 任务，立即返回 `job_id`。

**鉴权**：需

**请求体**：

| 字段 | 类型 | 默认 | 约束 | 说明 |
|------|------|------|------|------|
| `item_id` | int | 4110 | ≥ 1 | 栏目 itemId（4110=总局机关，4291=第二入口） |
| `start_page` | int | 1 | ≥ 1 | 采集起始页 |
| `end_page` | int | 5 | ≥ 1 | 采集结束页 |

**成功响应**（200）

```json
{
  "code": 0, "message": "success",
  "data": {
    "job_id": "0e8eb68f-2898-44b6-ad67-2ccc443e7062",
    "item_id": 4291, "start_page": 1, "end_page": 1, "status": "pending"
  }
}
```

**错误**：`400` `end_page < start_page` 或 `item_id < 1`；`401` 未认证。

**示例**

```bash
curl -X POST http://localhost:8000/api/v1/nfra/djg/crawl \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"item_id": 4291, "start_page": 1, "end_page": 1}'
```

---

### 7.2 查询任务状态 `GET /api/v1/nfra/djg/crawl/{job_id}`

通过 Celery `AsyncResult` 查询手动触发的任务状态。

**路径参数**：`job_id` (string)

**响应 data**：

```json
{
  "job_id": "0e8eb68f-...",
  "status": "success",
  "result": {"discovered": 18, "pending": 6, "extracted_rows": 6, "stored": 6}
}
```

`status` 映射：

| Celery state | status | result |
|--------------|--------|--------|
| PENDING | `pending` | `null` |
| STARTED | `running` | `null` |
| SUCCESS | `success` | 统计 dict（见下） |
| FAILURE | `failed` | 异常信息（string） |
| RETRY | `retrying` | `null` |

`result`（仅 SUCCESS）字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `discovered` | int | 列表发现的文档总数 |
| `pending` | int | 标题含「任职资格」且未入库的待抓取数 |
| `extracted_rows` | int | LLM 抽取的行数 |
| `stored` | int | 实际写入 djg_data 的行数 |

> 任务结果在 Celery result backend（Redis DB1）保留 24h；超期状态不可查。worker 未运行时状态恒为 `pending`。

---

### 7.3 查询采集数据 `GET /api/v1/nfra/djg/data`

按发布日期范围查询 `djg_data` 表，翻页返回。

**鉴权**：需　**分页**：是

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `start_date` | datetime | null | `publish_date >= start_date`（含） |
| `end_date` | datetime | null | `publish_date <= end_date`（含） |
| `page` | int | 1 | 页码 |
| `size` | int | 20 | 每页数（1–100） |

排序：`publish_date DESC NULLS LAST, id DESC`（最新发布在前，未标注发布日期排最后）。`start_date > end_date` 返回空（不报错）。

**响应 data**（数组，每行一人）：

```json
[{
  "id": "1", "doc_id": 1258343,
  "issue_date": "2026年5月14日",
  "issuing_authority": "江苏金融监管局",
  "doc_number": "苏金复〔2026〕139号",
  "institution_name": "苏州银行股份有限公司",
  "person_name": "张伟", "position": "董事",
  "doc_title": "江苏金融监管局关于张伟等6人苏州银行董事、独立董事任职资格的批复",
  "doc_url": "https://www.nfra.gov.cn/...",
  "crawl_time": "2026-06-25 18:33:34.065244+00:00"
}]
```

**示例**

```bash
curl "http://localhost:8000/api/v1/nfra/djg/data?start_date=2026-06-25T00:00:00&end_date=2026-06-26T00:00:00&page=1&size=20" \
  -H "X-API-Key: $API_KEY"
```

**错误**：`401` 未认证；`422` 分页参数非法。

---

### 7.4 注册资本/开业采集 `/api/v1/nfra/capital/*`

采集 nfra「变更注册资本」与「总公司开业」批复，写入独立库 `zbd_crawler_data.capital_change_data`。手动触发经 Celery 异步执行；`item_id` 省略时同时采集 4110 和 4291。

**手动触发** `POST /api/v1/nfra/capital/crawl`（鉴权：需）

| 字段 | 类型 | 默认 | 约束 | 说明 |
|------|------|------|------|------|
| `item_id` | int \| null | null | ≥ 1 或 null | 栏目 itemId；null → 4110+4291 |
| `start_page` | int | 1 | ≥ 1 | 采集起始页 |
| `end_page` | int | 5 | ≥ 1 | 采集结束页 |

**响应**：`{"data": {"job_id": "<uuid>", "item_id": null, "start_page": 1, "end_page": 5, "status": "pending"}}`

**错误**：`400` `end_page < start_page` 或 `item_id < 1`；`401` 未认证。

**查询状态** `GET /api/v1/nfra/capital/crawl/{job_id}` — 状态映射同 7.2。`result`（仅 SUCCESS）字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `discovered` | int | 列表发现文档总数 |
| `qualified` | int | 标题含「注册资本」或「开业」的文档数 |
| `pending` | int | 未入库的待抓取数 |
| `extracted_rows` | int | LLM 抽取行数 |
| `stored` | int | 写入 capital_change_data 行数 |

**查询数据** `GET /api/v1/nfra/capital/data`（鉴权：需　分页：是）— 查询参数同 7.3（`start_date`/`end_date`/`page`/`size`），排序 `publish_date DESC NULLS LAST, id DESC`（最新发布在前，`publish_date` 为空排最后）。

**响应 data**（数组）：

```json
[{
  "id": "1", "doc_id": 1234814,
  "publish_date": "2025-11-20",
  "issue_date": "2025年11月20日",
  "issuing_authority": "江苏监管局",
  "doc_number": "苏金复〔2025〕411号",
  "change_type": "变更注册资本",
  "institution_name": "南京银行股份有限公司",
  "registered_capital_before": "10,007,016,973元",
  "registered_capital_change_method": "可转债转股",
  "change_amount": "",
  "registered_capital_after": "12,363,567,245元",
  "doc_title": "江苏金融监管局关于南京银行股份有限公司变更注册资本的批复",
  "doc_url": "https://www.nfra.gov.cn/...",
  "crawl_time": "2026-07-02 10:00:00+00:00"
}]
```

`change_type`：`变更注册资本` 或 `机构成立`。金额保留原文，不做数值归一化。

---

### 7.5 变更股权采集 `/api/v1/nfra/equity/*`

采集 nfra「股权变更」批复与「总公司开业」批复中的股东信息，写入独立库 `zbd_crawler_data.equity_change_data`。结构与 7.4 一致；`item_id` 省略时同时采集 4110 和 4291。

**手动触发** `POST /api/v1/nfra/equity/crawl`（鉴权：需）— 请求体同 7.4（`item_id`/`start_page`/`end_page`）。

**查询状态** `GET /api/v1/nfra/equity/crawl/{job_id}` — 状态映射同 7.2；`result` 字段同 7.4。

**查询数据** `GET /api/v1/nfra/equity/data`（鉴权：需　分页：是）— 查询参数同 7.3，排序 `publish_date DESC NULLS LAST, id DESC`。

**响应 data**（数组，每位股东一行）：

```json
[{
  "id": "1", "doc_id": 1258291,
  "publish_date": "2026-06-18",
  "issue_date": "2026年6月18日",
  "issuing_authority": "重庆监管局",
  "doc_number": "渝金管复〔2026〕58号",
  "change_type": "变更股权",
  "institution_name": "重庆小米消费金融有限公司",
  "shareholder_name": "小米通讯技术有限公司",
  "shareholding_before": "",
  "change_method": "转入",
  "transferred_shares": "15000股",
  "transferred_ratio": "",
  "shares_after": "90000股",
  "shareholding_after": "0.6",
  "contribution_amount": "",
  "doc_title": "重庆金融监管局关于重庆小米消费金融有限公司股权变更的批复",
  "doc_url": "https://www.nfra.gov.cn/...",
  "crawl_time": "2026-07-03 10:00:00+00:00"
}]
```

`change_type`：`变更股权` 或 `机构成立`（开业批复中的股东）；`change_method`：`转入` 或 `转出`。比例/股份/金额保留原文，不做数值归一化。

---

## 8. 错误码

业务错误码（`code` 字段）按区间分类：

| 范围 | 类别 | 示例 |
|------|------|------|
| 1xxx | 爬虫错误 | `1001` 未找到；`1002` 配置非法 |
| 2xxx | 任务错误 | `2001` 运行中 / 未找到；`2002` 调度冲突 |
| 3xxx | 运行时错误 | `3001` 超时；`3002` 解析失败；`3003` 代理耗尽 |
| 4xxx | 存储错误 | `4001` 写入失败；`4002` 连接断开 |

业务异常（`AppError`）响应示例：

```json
{"code": 1001, "message": "Spider not found", "data": {"detail": "Spider <id> does not exist"}}
```

HTTP 层错误（`HTTPException`）响应示例：

```json
{"detail": "Spider <id> not found"}
```

新增错误码须继承 `AppError` 并遵循区间约定。

---

## 9. 附录：数据模型

### SpiderModel（`spiders` 表，主库 `scraper_db`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | uuid PK | |
| `name` | string | 唯一索引 |
| `url` | text | |
| `schedule` | string | cron 表达式 |
| `use_playwright` / `use_camoufox` / `use_stealthy` | bool | fetcher 选择 |
| `concurrency` / `retry_times` | int | |
| `download_delay` | float | |
| `proxy_enabled` | bool | |
| `config` | jsonb | 自定义参数 |
| `is_active` | bool | 调度开关 |
| `callback_url` | text | |
| `created_at` / `updated_at` | timestamptz | |

### JobModel（`jobs` 表）

`id`、`spider_id`(FK)、`spider_name`、`status`、`trigger_type`、`requests_total`/`requests_success`/`requests_failed`、`items_scraped`/`items_stored`/`items_deduped`、`error_message`、`result`(jsonb)、`started_at`/`finished_at`/`created_at`。

### ItemModel（`items` 表）

`id`、`job_id`(FK)、`spider_name`、`url`、`data`(jsonb)、`content_hash`(sha256，去重)、`created_at`。

### djg_data（独立库 `zbd_crawler_data`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | bigserial PK | |
| `doc_id` | bigint | 文档 ID（多行可相同） |
| `issue_date` | text | 发文日期 |
| `issuing_authority` | text | 发文监管机构 |
| `doc_number` | text | 发文函号 |
| `institution_name` | text | 机构名称 |
| `person_name` | text | 人员姓名 |
| `position` | text | 职务 |
| `doc_title` | text | 发文名称 |
| `doc_url` | text | 发文链接 |
| `crawl_time` | timestamptz | 采集时间 |

唯一约束：`(doc_id, person_name)`。

### capital_change_data（独立库 `zbd_crawler_data`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | bigserial PK | |
| `doc_id` | bigint | 文档 ID（多行可相同） |
| `publish_date` | date | 发布日期（可空） |
| `issue_date` | text | 发文日期 |
| `issuing_authority` | text | 发文监管机构 |
| `doc_number` | text | 发文函号 |
| `change_type` | text | `变更注册资本` / `机构成立` |
| `institution_name` | text | 机构名称 |
| `registered_capital_before` | text | 变更前注册资本 |
| `registered_capital_change_method` | text | 变更方式 |
| `change_amount` | text | 变更金额 |
| `registered_capital_after` | text | 变更后注册资本 |
| `doc_title` / `doc_url` | text | 发文名称 / 链接 |
| `crawl_time` | timestamptz | 采集时间 |

唯一约束：`(doc_id, institution_name, change_type)`。

### equity_change_data（独立库 `zbd_crawler_data`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | bigserial PK | |
| `doc_id` | bigint | 文档 ID（多行可相同） |
| `publish_date` | date | 发布日期（可空） |
| `issue_date` | text | 发文日期 |
| `issuing_authority` | text | 发文监管机构 |
| `doc_number` | text | 发文函号 |
| `change_type` | text | `变更股权` / `机构成立` |
| `institution_name` | text | 机构名称 |
| `shareholder_name` | text | 股东名称 |
| `shareholding_before` | text | 变更前持股比例 |
| `change_method` | text | `转入` / `转出` |
| `transferred_shares` | text | 受让股份 |
| `transferred_ratio` | text | 受让比例 |
| `shares_after` | text | 变更后股份 |
| `shareholding_after` | text | 变更后持股比例 |
| `contribution_amount` | text | 出资额 |
| `doc_title` / `doc_url` | text | 发文名称 / 链接 |
| `crawl_time` | timestamptz | 采集时间 |

唯一约束：`(doc_id, institution_name, shareholder_name, change_method)`。
