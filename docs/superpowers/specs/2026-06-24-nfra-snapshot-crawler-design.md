# nfra.gov.cn 文档快照采集脚本 — 设计文档

## 概述

采集国家金融监督管理总局（nfra.gov.cn）"总局机关"栏目（itemId=4110）最新若干页文档列表，
对每个文档抓取详情接口原始响应，写入独立数据库 `zbd_crawler_data` 的 `web_snapshot` 表。

## 已验证的站点约束

通过实际探测（2026-06-24）确认：

| 接口 | URL 模板 | plain HTTP | 说明 |
|------|----------|------------|------|
| 列表 | `/cn/static/data/DocInfo/SelectDocByItemIdAndChild/data_itemId={itemId},pageIndex={page},pageSize=18.json` | **404** | 服务端校验 JS 生成的会话 cookie（`_gscu_/_gscs_`），plain HTTP 一律 404 |
| 详情 | `/cn/static/data/DocInfo/SelectByDocId/data_docId={docId}.json` | **200** | 不校验 cookie，普通 HTTP 直接返回 JSON |

HTML 列表页 `/cn/view/pages/ItemList.html` 用 plain HTTP 可正常加载（200），但不会 set 任何 cookie —— `_gscu_/_gscs_` 由客户端 JS 生成。

**结论**：列表发现必须用浏览器（执行 JS 生成 cookie），详情抓取用 HTTP（更快更轻）。

## 范围与形式

- **形式**：独立脚本 `scripts/crawl_nfra.py`，通过 `make crawl-nfra` 或 `python scripts/crawl_nfra.py` 运行，不依赖 FastAPI/Celery 启动。
- **范围**：采集最新 N 页（默认 5 页 × 18 条/页 ≈ 90 条 docId）。`--pages` 与 `--item-id` 为 CLI 参数可调。
- **存储语义**：跳过已存在。`web_snapshot` 以 `doc_id` 为主键；详情抓取前先查表过滤出已存在 docId（省 HTTP 请求），写入用 `INSERT ... ON CONFLICT (doc_id) DO NOTHING`（并发安全）。

## 架构与数据流

```
crawl_nfra.py (async main)
 │
 ├─ 阶段 1：列表发现（AsyncStealthySession 浏览器，持久会话）
 │    ├─ fetch(ItemList.html?itemId={itemId})      → 触发 JS 生成 cookie
 │    └─ for page in 1..N:
 │          fetch(list_api_url(itemId, page))      → body 为 JSON 字符串
 │          parse → rows: List[int] (docId)
 │
 ├─ 阶段 2：过滤
 │    ├─ 去重列表内 docId
 │    └─ 查询 web_snapshot 已存在 doc_id → 过滤出待抓取 docId 集合
 │
 ├─ 阶段 3：详情抓取（FetcherSession，HTTP，impersonate=chrome）
 │    └─ Semaphore(5) + download_delay 并发抓取每个待抓 docId
 │          fetch(detail_api_url(docId)) → 原始响应体 = snapshot
 │
 └─ 阶段 4：写入
      └─ INSERT INTO web_snapshot(doc_id, snapshot, crawl_time)
         VALUES (...)  ON CONFLICT (doc_id) DO NOTHING
```

### URL 模板

- 列表：`https://www.nfra.gov.cn/cn/static/data/DocInfo/SelectDocByItemIdAndChild/data_itemId={itemId},pageIndex={page},pageSize=18.json`
- 详情：`https://www.nfra.gov.cn/cn/static/data/DocInfo/SelectByDocId/data_docId={docId}.json`
- 列表页 HTML（用于触发 cookie）：`https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=923&itemId={itemId}&itemUrl=ItemListRightList.html&itemName=zhujiguan`

### 请求头

复用 curl 中的关键头：`User-Agent`（Chrome 149 macOS）、`Referer`（对应 ItemList/ItemDetail 页）、`X-Requested-With: XMLHttpRequest`。浏览器阶段由 Scrapling 注入；HTTP 阶段在 FetcherSession 上设置。

## 列表 JSON 响应结构

```json
{
  "rptCode": 200,
  "msg": "成功",
  "data": {
    "total": 28908,
    "rows": [
      { "docId": 1258731, "docTitle": "...", "publishDate": "2026-05-08 17:03:00", ... }
    ]
  }
}
```

解析规则：取 `data.rows[*].docId`。`rptCode != 200` 或 `rows` 为空视为该页无数据。

## 组件

### 1. `src/web_scraper_service/storage/snapshot.py`（新建）

独立库的 engine、模型、仓库。不复用主库 `scraper_db` 的 engine。

- **Engine**：`create_async_engine(snapshot_database_url, pool_size=10, ...)`，独立于 `storage/database.py`。
- **模型 `WebSnapshot`**：
  - `doc_id: int` — 主键（BigInteger）
  - `snapshot: Text` — 详情接口原始响应体
  - `crawl_time: DateTime(timezone=True)` — `server_default=func.now()`
- **仓库 `SnapshotRepo`**：
  - `async existing_doc_ids(doc_ids: set[int]) -> set[int]` — 批量查已存在
  - `async insert_many(rows: list[dict]) -> int` — 批量插入，`ON CONFLICT (doc_id) DO NOTHING`
  - `init_table()` — `CREATE TABLE IF NOT EXISTS`（独立库不走 Alembic）

### 2. `src/web_scraper_service/config.py`（修改）

新增配置项：
- `snapshot_database_url: str` — 默认从现有 postgres 凭据派生，db 名 `zbd_crawler_data`：
  `postgresql+asyncpg://{user}:{pwd}@{host}:{port}/zbd_crawler_data`

### 3. `scripts/crawl_nfra.py`（新建）

编排与 CLI：
- CLI 参数：`--pages`（默认 5）、`--item-id`（默认 4110）、`--concurrency`（默认 5）、`--download-delay`（默认 0.5）
- 阶段 1：`AsyncStealthySession` 持久会话，先 fetch HTML 页触发 cookie，再循环 fetch 列表 API URL，解析 JSON 提取 docId
- 阶段 2：去重 + 过滤已存在
- 阶段 3：`FetcherSession`（HTTP）并发抓详情，原始响应体作为 snapshot
- 阶段 4：批量写入 `web_snapshot`
- 日志：使用 loguru，输出每阶段进度与最终统计（发现/待抓/写入数）

### 4. 配置与环境（修改）

- `.env.example`：新增 `SNAPSHOT_DATABASE_URL`（注释说明默认派生自主库凭据）
- `Makefile`：新增 `crawl-nfra` target：
  ```make
  crawl-nfra:
  	uv run python scripts/crawl_nfra.py --pages $(NFRA_PAGES) --item-id $(NFRA_ITEM_ID)
  ```

## 错误处理

| 场景 | 处理 |
|------|------|
| 列表单页失败 | tenacity 重试 2 次；某页连续失败则停止翻页（视为到尾） |
| 列表 cookie 未生成（仍 404/非 JSON） | 重试 2 次后报错退出，提示运行 `scrapling install` |
| 详情页失败 | tenacity 指数退避重试 3 次；最终失败记录日志跳过，不中断 |
| 写入单行失败 | 回滚该行，继续其余，记录失败 docId |

## 测试

`tests/test_nfra/test_parse.py`（新建）—— 纯单元，不打网络：
- 列表 JSON 解析：正确提取 docId 列表；`rptCode != 200` 返回空；`rows` 缺失返回空
- 分页 URL 构造：`build_list_url(itemId, page)` 正确含逗号分隔参数
- 详情 URL 构造：`build_detail_url(docId)` 正确
- skip-existing 过滤：给定已存在集合，过滤逻辑正确

浏览器列表路径与网络路径保持薄；解析/构造逻辑独立可测。
可选：`--pages 1` 小规模实跑验收（不在自动测试中）。

## 改动文件清单

| 文件 | 动作 |
|------|------|
| `scripts/crawl_nfra.py` | 新建 — 编排 + CLI |
| `src/web_scraper_service/storage/snapshot.py` | 新建 — 独立库 engine/model/repo |
| `src/web_scraper_service/config.py` | 修改 — 加 `snapshot_database_url` |
| `.env.example` | 修改 — 加 `SNAPSHOT_DATABASE_URL` |
| `Makefile` | 修改 — 加 `crawl-nfra` target |
| `tests/test_nfra/test_parse.py` | 新建 — 解析与过滤单测 |

## 注意事项

- 运行列表阶段需先执行 `scrapling install` 装好浏览器（StealthyFetcher 依赖）。
- `web_snapshot` 与主库 `scraper_db` 是两个独立数据库，连接池互不影响。
- `doc_id` 为 int，主键；表结构由脚本启动时 `CREATE TABLE IF NOT EXISTS` 创建，不纳入 Alembic 迁移。
- 列表 URL 含未编码逗号是站点原始格式（curl 探测时 `%2C` 与 `,` 均尝试，行为一致），保持原样。
- 默认 5 页约 90 条 docId；若大量已存在则实际抓取数会少。
